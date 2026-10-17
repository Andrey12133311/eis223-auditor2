"""Private electronic procurement case file for real account-owned checks.

The case reads existing EIS artifacts, normalized violations, the v339 fact
comparison, and verified EIS change history. Nothing is reclassified as a new
violation and no EIS document is rewritten. Notes are append-only in the
separate PostgreSQL account database.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor, _member

router = APIRouter(prefix="/me/cases", tags=["my-procurement-case"])
manager_router = APIRouter(prefix="/manager/cases", tags=["manager-procurement-case"])
REG = re.compile(r"^3[0-9]{10}$")
KEY = re.compile(r"^(?:[0-9a-f]{64})?$")


def _check_reg(reg):
    if not REG.fullmatch(str(reg or "")):
        raise HTTPException(422,"Укажите действительный регистрационный номер ЕИС")
    return reg


def _owner(request, reg):
    _check_reg(reg)
    user_id = current_account(request)[0]
    with connect() as db:
        owned = db.execute(
            """SELECT 1 FROM account_manual_checks
               WHERE user_id=%s AND reg_number=%s""",
            (user_id,reg)).fetchone()
    if not owned:
        raise HTTPException(404,"Самостоятельная проверка не найдена в личном кабинете")
    return user_id


def _owned_notes(db,owner,reg):
    rows=db.execute(
        """SELECT id,finding_key,body,created_at FROM account_case_notes
           WHERE user_id=%s AND reg_number=%s
           ORDER BY created_at DESC LIMIT 150""",
        (owner,reg)).fetchall()
    return [{"id":str(r[0]),"finding_key":r[1],"body":r[2],
             "created_at":r[3].isoformat()} for r in rows]


def _validated_source(url):
    from v343_fast_cards import source_link
    return str(url or "") if source_link(url,"Источник ЕИС") else ""


def _timeline(p, reg):
    from v330_amendments_223 import day,manifest_changes
    timeline=[]
    for key,label in (
        ("published_at","Размещение закупки"),
        ("submission_start","Начало подачи заявок"),
        ("submission_end","Окончание подачи заявок")):
        val=day(p.get(key))
        if val:
            timeline.append({"day":val.isoformat(),"title":label,
                             "source":"Карточка ЕИС",
                             "url":_validated_source(p.get("url")),
                             "qualification":"Сведения карточки"})
    for ch in manifest_changes(reg):
        src=_validated_source(ch.get("source"))
        if not src:continue
        decision=ch.get("decision_date")
        published=ch.get("publication_date")
        end=ch.get("submission_end")
        for d,title in (
            (decision,"Решение об изменении документации"),
            (published,"Публикация изменений в ЕИС"),
            (end,"Окончание подачи заявок после изменения")):
            if d:
                timeline.append({
                    "day":d.isoformat(),"title":title,
                    "source":str(ch.get("document") or "Изменения ЕИС")[:190],
                    "url":src,
                    "qualification":"Источник: история изменений ЕИС",
                })
        if decision and published:
            elapsed=(published-decision).days
            timeline.append({
                "day":published.isoformat(),
                "title":"Интервал решения и публикации: "+str(elapsed)+" календарных дней",
                "source":str(ch.get("document") or "Изменения ЕИС")[:190],
                "url":src,
                "qualification":"Требует оценки нормы и исключений; само по себе не новое нарушение",
            })
    unique={}
    for event in timeline:
        unique.setdefault((event["day"],event["title"],event["url"]),event)
    return sorted(unique.values(),key=lambda x:(x["day"],x["title"]))[:120]


def _case_snapshot(ns,reg):
    """No storage writes and no fallback to fabricated documents or legal acts."""
    from v352_dashboard_truth import collapse_legal_events
    from v365_consultant_refs import references
    c=ns["conn"]()
    try:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=1500")
        c.execute("PRAGMA query_only=ON")
        purchase=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        if purchase is None:raise HTTPException(404,"Карточка закупки пока не загружена")
        p=dict(purchase)
        confirmed=[dict(r) for r in c.execute(
            """SELECT title,law,evidence,document,source_url,code,law_url
               FROM findings WHERE reg_number=? AND kind='violation'
               ORDER BY id DESC LIMIT 350""",(reg,))]
        confirmed.extend(dict(r) for r in c.execute(
            """SELECT item AS title,law,evidence,document,source_url,
                      field_path AS code,law_url
               FROM checks WHERE reg_number=? AND status='violation'
               ORDER BY id DESC LIMIT 350""",(reg,)))
        checks=[dict(r) for r in c.execute(
            """SELECT item,status,law,evidence,document,source_url,field_path,law_url
               FROM checks WHERE reg_number=? AND status='check'
               ORDER BY id DESC LIMIT 350""",(reg,))]
        docs=[dict(r) for r in c.execute(
            """SELECT name,doc_type,status,url,text_chars,published_at
               FROM docs WHERE reg_number=? ORDER BY id DESC LIMIT 180""",(reg,))]
        audit_table=c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='v332_audit_queue' AND type='table'"
        ).fetchone()
        audit=(c.execute(
            """SELECT state,documents_read,documents_unreadable,updated_at
               FROM v332_audit_queue WHERE reg=? ORDER BY updated_at DESC LIMIT 1""",
            (reg,)).fetchone() if audit_table else None)
        unique=collapse_legal_events(confirmed)
        findings=[]
        for item in unique:
            refs=references(item)
            source=_validated_source(item.get("source_url"))
            supporting=[]
            for other in item.get("supporting_sources",[])[:25]:
                supporting.append({"document":str(other.get("document") or "")[:240],
                                   "url":_validated_source(other.get("url"))})
            issues=[]
            if not str(item.get("law") or "").strip():
                issues.append("Не указана правовая норма")
            if not str(item.get("evidence") or "").strip():
                issues.append("Не приведено доказательство")
            if not item.get("document") and not source:
                issues.append("Не определён документ-источник")
            if item.get("law") and not refs:
                issues.append("Нет ссылки на точную норму")
            findings.append({
                "title":str(item.get("title") or "Нарушение")[:400],
                "law":str(item.get("law") or "")[:450],
                "evidence":str(item.get("evidence") or "")[:3200],
                "document":str(item.get("document") or "")[:260],
                "source_url":source,
                "consultant_references":refs[:4],
                "source_representations":int(item.get("raw_count",1)),
                "supporting_sources":supporting,
                "warnings":issues,
            })
        comparisons=[]
        review=[]
        seen=set()
        for ck in checks:
            code=str(ck.get("field_path") or "")
            entry={
                "title":str(ck.get("item") or "Требует проверки")[:350],
                "law":str(ck.get("law") or "")[:450],
                "evidence":str(ck.get("evidence") or "")[:1600],
                "document":str(ck.get("document") or "")[:260],
                "source_url":_validated_source(ck.get("source_url")),
                "code":code[:200],
                "consultant_references":references(ck)[:4],
            }
            identity=(code,entry["title"],entry["document"],entry["evidence"][:240])
            if identity in seen:continue
            seen.add(identity)
            (comparisons if code.startswith("v339-") else review).append(entry)
        raw_count=len(confirmed)
        timeline=_timeline(p,reg)
        documents=[{
            "name":str(d.get("name") or "")[:250],
            "type":str(d.get("doc_type") or "")[:80],
            "status":str(d.get("status") or "")[:60],
            "url":_validated_source(d.get("url")),
            "text_chars":max(0,int(d.get("text_chars") or 0)),
            "published_at":str(d.get("published_at") or "")[:36]
        } for d in docs]
        return {
            "reg_number":reg,
            "purchase":{
                "title":str(p.get("title") or "")[:400],
                "customer":str(p.get("customer") or "")[:300],
                "customer_inn":str(p.get("customer_inn") or "")[:20],
                "method":str(p.get("method") or "")[:200],
                "price":str(p.get("price") or p.get("price_num") or "")[:120],
                "url":_validated_source(p.get("url")),
            },
            "audit":{
                "state":audit["state"] if audit else "unknown",
                "documents_read":int(audit["documents_read"] or 0) if audit else 0,
                "documents_unreadable":int(audit["documents_unreadable"] or 0) if audit else 0,
                "updated_at":str(audit["updated_at"] or "") if audit else "",
            },
            "findings":findings,
            "findings_count":len(findings),
            "original_findings_records":raw_count,
            "duplicates_grouped":max(0,raw_count-len(findings)),
            "comparisons":comparisons[:90],
            "review_items":review[:90],
            "documents":documents,
            "timeline":timeline,
            "note":"Сопоставления и неполные сведения требуют проверки. Они не увеличивают число подтверждённых нарушений.",
        }
    finally:
        c.close()


class NoteData(BaseModel):
    body:str=Field(min_length=4,max_length=3000)
    finding_key:str=Field(default="",max_length=64)


@router.get("/{reg}/data")
def case_data(reg:str,request:Request,response:Response):
    owner=_owner(request,reg)
    response.headers.update(no_store_headers())
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if ns is None:raise HTTPException(503,"Анализ ЕИС временно недоступен")
    try:report=_case_snapshot(ns,reg)
    except sqlite3.Error as exc:
        raise HTTPException(503,"Не удалось получить результаты проверки") from exc
    with connect() as db:
        report["notes"]=_owned_notes(db,owner,reg)
    return report


@router.post("/{reg}/notes",status_code=201)
def add_case_note(reg:str,data:NoteData,request:Request):
    require_origin(request)
    owner=_owner(request,reg)
    if not KEY.fullmatch(data.finding_key):
        raise HTTPException(422,"Неверная ссылка на доказательство")
    note=str(data.body).strip()
    if len(note)<4:raise HTTPException(422,"Укажите замечание")
    note_id=uuid4()
    with connect() as db:
        db.execute(
            """INSERT INTO account_case_notes(id,user_id,reg_number,finding_key,body)
               VALUES(%s,%s,%s,%s,%s)""",
            (note_id,owner,reg,data.finding_key,note))
    return {"id":str(note_id),"saved":True,"history_preserved":True}


@manager_router.get("/{worker}/{reg}/notes")
def manager_case_notes(worker:str,reg:str,request:Request,response:Response):
    actor,role=supervisor(request)
    _check_reg(reg)
    response.headers.update(no_store_headers())
    with connect() as db:
        employee=_member(db,actor,role,worker)
        exists=db.execute(
            """SELECT 1 FROM account_manual_checks
               WHERE user_id=%s AND reg_number=%s""",(employee,reg)).fetchone()
        if not exists:raise HTTPException(404,"Проверка сотрудника не найдена")
        return _owned_notes(db,employee,reg)


@router.get("/{reg}",response_class=HTMLResponse)
def case_page(reg:str,request:Request):
    _owner(request,reg)
    from scripts.v369_case_ui import render_case
    return HTMLResponse(render_case(reg),headers={"Cache-Control":"private,no-store"})
