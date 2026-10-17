"""Manager thematic assignments: selected legal topics, company forms and a target.

The goal is a workload benchmark, NEVER a request to fabricate findings.
The existing v342 engine performs real document-backed audits in SQLite.
Personal ownership remains protected by account_thematic_batches in Postgres.
"""
from __future__ import annotations
from datetime import date
import json
import secrets
import sqlite3
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor, _member, _audit, _uuid

router=APIRouter(prefix="/manager/campaigns",tags=["manager-thematic-targets"])
staff_router=APIRouter(prefix="/me/campaign-goals",tags=["staff-thematic-targets"])

from v342_thematic_queue import CATEGORIES, LEGAL_FORMS

class CampaignCreate(BaseModel):
    assignee_id:UUID
    title:str=Field(min_length=5,max_length=170)
    categories:list[str]=Field(min_length=1,max_length=10)
    legal_forms:list[str]=Field(min_length=1,max_length=5)
    target_violations:int=Field(ge=1,le=10000)
    due_date:date|None=None
    note:str=Field(default="",max_length=1800)

    @field_validator("categories")
    @classmethod
    def validate_categories(cls,values):
        if len(set(values))!=len(values) or set(values)-set(CATEGORIES):
            raise ValueError("Выбраны неизвестные или повторяющиеся категории")
        return values

    @field_validator("legal_forms")
    @classmethod
    def validate_forms(cls,values):
        if len(set(values))!=len(values) or set(values)-set(LEGAL_FORMS):
            raise ValueError("Выбраны неизвестные или повторяющиеся формы заказчиков")
        return values

class CampaignState(BaseModel):
    action:str=Field(pattern="^(submit|complete|return|cancel)$")
    comment:str=Field(default="",max_length=1500)

def _engine(request):
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if not ns:raise HTTPException(503,"Серверный обработчик ЕИС не подключён")
    return ns

def _insert_real_batch(ns,batch,categories,forms):
    """Idempotent; enforce v342 max-three active checks in one sqlite transaction."""
    from v342_thematic_queue import db,now
    con=db(ns)
    try:
        con.execute("BEGIN IMMEDIATE")
        exists=con.execute("SELECT 1 FROM thematic_batches WHERE id=?",(batch,)).fetchone()
        if exists:
            con.commit();return "active"
        count=con.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0]
        if count>=3:
            raise HTTPException(429,"Уже работают три тематические проверки. Завершите одну перед запуском следующей.")
        con.execute(
            """INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms)
               VALUES(?,?,?,'active',?)""",
            (batch,json.dumps(sorted(categories),ensure_ascii=False),now(),
             json.dumps(sorted(forms),ensure_ascii=False)))
        con.commit()
        return "active"
    except Exception:
        con.rollback()
        raise
    finally:con.close()

def _campaign_rows(db,where,args):
    return db.execute(
        """SELECT t.id,t.manager_id,t.user_id,u.email,t.batch_id,t.title,t.categories,
                  t.legal_forms,t.target_violations,t.due_date,t.note,t.status,
                  t.created_at,t.submitted_at,t.completed_at
           FROM account_manager_campaign_targets t
           JOIN account_users u ON u.id=t.user_id """+where+
           " ORDER BY t.created_at DESC LIMIT 150",args).fetchall()

def _format(row,batch=None):
    actual=max(0,int((batch or {}).get("violations") or 0))
    target=row[8]
    return {
        "id":str(row[0]),"manager_id":str(row[1]),"user_id":str(row[2]),
        "employee":row[3],"batch_id":row[4],"title":row[5],
        "categories":row[6],"legal_forms":row[7],
        "category_names":[CATEGORIES.get(x,x) for x in row[6]],
        "target_violations":target,"actual_violations":actual,
        "progress_percent":min(100,round(100*actual/target,1)),
        "target_met":actual>=target,
        "verified_procurements":int((batch or {}).get("purchases") or 0),
        "due_date":row[9].isoformat() if row[9] else None,
        "note":row[10],"status":row[11],
        "engine_status":(batch or {}).get("status","not_started"),
        "created_at":row[12].isoformat(),
        "submitted_at":row[13].isoformat() if row[13] else None,
        "completed_at":row[14].isoformat() if row[14] else None,
        "overdue":bool(row[9] and row[9]<date.today() and row[11] not in ("completed","cancelled")),
        "disclaimer":"План нарушений — целевой показатель. Фактические нарушения подтверждаются только документами ЕИС."
    }

def _read(rows,request):
    if not rows:return []
    from v342_thematic_queue import thematic_history_records
    try:
        batches=thematic_history_records(_engine(request),[r[4] for r in rows])["batches"]
    except sqlite3.Error as exc:
        raise HTTPException(503,"Не удалось прочитать результаты тематических проверок") from exc
    indexed={b["id"]:b for b in batches}
    return [_format(row,indexed.get(row[4])) for row in rows]

@router.get("/options")
def options(request:Request):
    supervisor(request)
    return {"categories":CATEGORIES,"legal_forms":LEGAL_FORMS}

@router.post("",status_code=201)
def create(data:CampaignCreate,request:Request):
    require_origin(request)
    actor,role=supervisor(request)
    if data.due_date and data.due_date<date.today():
        raise HTTPException(422,"Срок задания не может быть в прошлом")
    ns=_engine(request)
    goal=uuid4()
    batch=secrets.token_hex(12)
    with connect() as db:
        employee=_member(db,actor,role,data.assignee_id)
        leader=db.execute(
            "SELECT manager_id FROM account_manager_teams WHERE user_id=%s",(employee,)
        ).fetchone()
        if not leader or (role!="admin" and leader[0]!=actor):
            raise HTTPException(409,"У сотрудника не назначен этот руководитель")
        db.execute(
            """INSERT INTO account_manager_campaign_targets
               (id,manager_id,user_id,batch_id,title,categories,legal_forms,
                target_violations,due_date,note)
               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)""",
            (goal,leader[0],employee,batch,data.title.strip(),
             json.dumps(data.categories,ensure_ascii=False),
             json.dumps(data.legal_forms,ensure_ascii=False),
             data.target_violations,data.due_date,data.note.strip()))
        # This is a REAL campaign owned by the employee; no personal-ID prompt.
        db.execute(
            """INSERT INTO account_thematic_batches(user_id,batch_id)
               VALUES(%s,%s) ON CONFLICT DO NOTHING""",(employee,batch))
        _audit(db,actor,employee,"campaign",goal,"assign",
               "Формы: "+",".join(data.legal_forms)+"; категории: "+",".join(data.categories)+
               "; план нарушений: "+str(data.target_violations))
    # If SQLite is full / at concurrency limit, keep the record as an explicit
    # retryable setup failure; never falsely report that checking has started.
    try:
        _insert_real_batch(ns,batch,data.categories,data.legal_forms)
    except Exception as exc:
        with connect() as db:
            db.execute("UPDATE account_manager_campaign_targets SET status='setup_failed' WHERE id=%s",(goal,))
        if isinstance(exc,HTTPException):
            raise HTTPException(exc.status_code,
                "Поручение сохранено, но проверка не стартовала: "+str(exc.detail)+
                ". Повторите запуск в списке поручений.") from exc
        raise HTTPException(503,
            "Поручение сохранено; обработчик ЕИС не смог создать проверку. Доступен повторный запуск.") from exc
    with connect() as db:
        db.execute("UPDATE account_manager_campaign_targets SET status='active' WHERE id=%s",(goal,))
    return {"id":str(goal),"batch_id":batch,"status":"active",
            "actual_violations":0,"target_violations":data.target_violations}

@router.get("")
def manager_list(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=_campaign_rows(db,"" if role=="admin" else "WHERE t.manager_id=%s",
                            () if role=="admin" else (actor,))
    return _read(rows,request)

@staff_router.get("/violations")
def staff_campaign_violations(request:Request,response:Response):
    """Present real thematic findings as ordinary violation cards, not tasks.

    Only results from EIS thematic batches explicitly assigned to this
    authenticated employee by the supervisor are queried. Duplicate legal
    episodes from overlapping campaigns are displayed only once.
    """
    uid=current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=db.execute(
            """SELECT batch_id,title FROM account_manager_campaign_targets
               WHERE user_id=%s AND status NOT IN ('setup_failed','preparing')
               ORDER BY created_at DESC LIMIT 45""",(uid,)).fetchall()
    if not rows:
        return {"findings":[],"count":0,"truncated":False,
                "scope":"Нарушения по тематическим проверкам, назначенным руководителем"}
    from v342_thematic_queue import db as eis_db
    from v352_dashboard_truth import collapse_legal_events
    from v365_consultant_refs import enrich
    con=eis_db(_engine(request))
    try:
        con.execute("PRAGMA busy_timeout=2000")
        con.execute("PRAGMA query_only=ON")
        ids=[r[0] for r in rows]
        placeholders=",".join("?" for _ in ids)
        data=[dict(r) for r in con.execute(
            """SELECT batch_id,reg,category,title,law,evidence,code,
                      document,source_url,law_url FROM thematic_findings
               WHERE batch_id IN ("""+placeholders+""")
               ORDER BY rowid DESC LIMIT 1200""",ids)]
    except sqlite3.Error as exc:
        raise HTTPException(503,"Результаты тематической проверки ЕИС временно недоступны") from exc
    finally:con.close()
    by_reg={}
    for item in data:
        by_reg.setdefault(item["reg"],[]).append(item)
    prepared=[]
    for reg,findings in by_reg.items():
        first_batch=str(findings[0].get("batch_id") or "") if findings else ""
        for item in collapse_legal_events(findings):
            enriched=enrich(item)
            from urllib.parse import urlsplit
            url=str(item.get("source_url") or "").strip()
            try:
                parts=urlsplit(url)
                safe_source=parts.scheme=="https" and (
                    parts.hostname=="zakupki.gov.ru" or
                    str(parts.hostname or "").endswith(".zakupki.gov.ru")
                ) and not parts.username and not parts.password
            except ValueError:
                safe_source=False
            law_url=str(enriched.get("consultant_url") or "")
            if not law_url.startswith("https://www.consultant.ru/"):
                law_url=""
            prepared.append({
                "reg_number":reg,
                "title":str(item.get("title") or "Нарушение")[:280],
                "category":str(item.get("category") or "")[:90],
                "law":str(enriched.get("law") or "")[:350],
                "evidence":str(item.get("evidence") or "")[:3000],
                "document":str(item.get("document") or "")[:220],
                "source_url":url if safe_source else "",
                "law_url":law_url,
                "categories":list(item.get("categories") or []),
                "source_representations":int(item.get("raw_count",1)),
                "needs_review":not (
                    str(enriched.get("law") or "").strip() and
                    str(item.get("evidence") or "").strip() and
                    (str(item.get("document") or "").strip() or safe_source)
                ),
                # Use the exact batch which supplied the selected deduplicated
                # incident, not the first batch for this procurement. Otherwise
                # the 'view evidence' link may open a different thematic check.
                "batch_id":str(item.get("batch_id") or first_batch)[:24],
            })
    return {
        "findings":prepared[:500],
        "count":len(prepared[:500]),
        "truncated":len(data)>=1200 or len(prepared)>500,
        "scope":"Подтверждённые анализатором нарушения по тематическим проверкам сотрудника",
        "note":"Результаты с одинаковыми обстоятельствами объединены; предложения без достаточных доказательств требуют проверки."
    }


@staff_router.get("")
def staff_list(request:Request,response:Response):
    owner=current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=_campaign_rows(db,"WHERE t.user_id=%s",(owner,))
    return _read(rows,request)

def _goal(db,actor,role,identifier,lock=True):
    uid=_uuid(identifier)
    suffix=" FOR UPDATE" if lock else ""
    row=db.execute(
        """SELECT id,manager_id,user_id,batch_id,categories,legal_forms,status
           FROM account_manager_campaign_targets WHERE id=%s"""+suffix,(uid,)
    ).fetchone()
    if row is None:raise HTTPException(404,"Поручение не найдено")
    if role=="manager" and row[1]!=actor:
        raise HTTPException(404,"Поручение не принадлежит вашему подразделению")
    _member(db,actor,role,row[2])
    return row

@router.post("/{identifier}/retry")
def retry(identifier:str,request:Request):
    require_origin(request)
    actor,role=supervisor(request)
    ns=_engine(request)
    with connect() as db:
        row=_goal(db,actor,role,identifier)
        if row[6] not in ("setup_failed","preparing"):
            raise HTTPException(409,"Это поручение не ожидает запуска")
        batch,categories,forms=row[3],row[4],row[5]
    _insert_real_batch(ns,batch,categories,forms)
    with connect() as db:
        db.execute("UPDATE account_manager_campaign_targets SET status='active' WHERE id=%s",(row[0],))
        _audit(db,actor,row[2],"campaign",row[0],"retry_start")
    return {"status":"active","batch_id":batch}

@router.post("/{identifier}/decision")
def decision(identifier:str,command:CampaignState,request:Request):
    require_origin(request)
    actor,role=supervisor(request)
    with connect() as db:
        row=_goal(db,actor,role,identifier)
        current=row[6]
        legal={
            "return":("submitted",),
            "complete":("submitted",),
            "cancel":("active","submitted","setup_failed","preparing"),
        }
        if command.action not in legal:
            raise HTTPException(422,"Для сдачи задания используйте личный кабинет сотрудника")
        if current not in legal[command.action]:
            raise HTTPException(409,"Изменение статуса сейчас невозможно")
        if command.action=="return" and len(command.comment.strip())<5:
            raise HTTPException(422,"Укажите обоснование возврата")
        next_status={"return":"active","complete":"completed","cancel":"cancelled"}[command.action]
        db.execute(
            """UPDATE account_manager_campaign_targets SET status=%s,
               completed_at=CASE WHEN %s='completed' THEN CURRENT_TIMESTAMP
                         ELSE completed_at END
               WHERE id=%s""",(next_status,next_status,row[0]))
        _audit(db,actor,row[2],"campaign",row[0],command.action,command.comment)
    if next_status in ("cancelled","completed"):
        # Stop worker only; original stored findings are not deleted.
        from v342_thematic_queue import db as eis_db
        c=eis_db(_engine(request))
        try:
            c.execute("UPDATE thematic_batches SET status='stopped' WHERE id=?",(row[3],))
            c.commit()
        finally:c.close()
    return {"status":next_status,"evidence_preserved":True}

@staff_router.post("/{identifier}/submit")
def submit(identifier:str,request:Request):
    require_origin(request)
    owner=current_account(request)[0]
    uid=_uuid(identifier)
    with connect() as db:
        row=db.execute(
            """SELECT user_id,status,target_violations FROM account_manager_campaign_targets
               WHERE id=%s FOR UPDATE""",(uid,)).fetchone()
        if not row or row[0]!=owner:
            raise HTTPException(404,"Поручение не найдено")
        if row[1]!="active":
            raise HTTPException(409,"Проверка не запущена или уже передана")
        db.execute(
            """UPDATE account_manager_campaign_targets
               SET status='submitted',submitted_at=CURRENT_TIMESTAMP WHERE id=%s""",(uid,))
        _audit(db,owner,owner,"campaign",uid,"submit",
               "Результат передан, выполнение плана не утверждается автоматически")
    return {"status":"submitted","target_is_not_a_quota":True}
