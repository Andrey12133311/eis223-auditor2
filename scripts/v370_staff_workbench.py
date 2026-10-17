"""Owner-scoped employee workstation for EIS 223-FZ.

Features are real workflows: bounded bulk queue, saved checklists/templates,
manual issues pending review, personal priorities, legal categories, discussions
with assigned manager, related purchases, original-document metadata search.
Does not fabricate legal findings or overwrite existing EIS originals.
"""
from __future__ import annotations
from datetime import date,timedelta
from uuid import UUID,uuid4
import re
import json
import sqlite3
from fastapi import APIRouter,HTTPException,Request,Response
from pydantic import BaseModel,Field,field_validator
from scripts.account_api_v1 import connect,current_account,require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor,_member,_audit
from v342_thematic_queue import CATEGORIES,LEGAL_FORMS

router=APIRouter(prefix="/me/workbench",tags=["staff-workbench"])
team_router=APIRouter(prefix="/manager/workbench",tags=["manager-staff-workbench"])
REG=re.compile(r"^3[0-9]{10}$")
INN=re.compile(r"^[0-9]{10}(?:[0-9]{2})?$")
EIS_URL=re.compile(r"^https://(?:[a-z0-9-]+\.)?zakupki\.gov\.ru/",re.I)
CHECKS={
    "notice":"Соответствие извещения и документации",
    "dates":"Сроки подачи заявок и их изменения",
    "clarifications":"Разъяснения документации и сроки",
    "amendments":"Публикация изменений и продление сроков",
    "participants":"Требования к участникам и ограничения конкуренции",
    "sme":"Правила закупок среди МСП",
    "national":"Национальный режим",
    "security":"Обеспечение заявки и договора",
    "contracts":"Условия проекта договора и оплаты",
    "protocols":"Протоколы, отмена закупки и сроки публикации",
    "pricing":"Цена и соответствие извещению",
    "documents":"Полнота доступных оригиналов и архивов",
}

def owner(request):
    return current_account(request)[0]

def case_owner(request,reg):
    if not REG.fullmatch(reg):raise HTTPException(422,"Неверный номер закупки ЕИС")
    uid=owner(request)
    with connect() as db:
        found=db.execute(
            "SELECT 1 FROM account_manual_checks WHERE user_id=%s AND reg_number=%s",
            (uid,reg)).fetchone()
    if not found:raise HTTPException(404,"Проверка не найдена в личном кабинете")
    return uid

def eis(request):
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if ns is None:raise HTTPException(503,"Загрузчик ЕИС временно недоступен")
    return ns

@router.get("/options")
def options(request:Request):
    owner(request)
    return {"categories":CATEGORIES,"legal_forms":LEGAL_FORMS,
            "checklist":CHECKS}

class Bulk(BaseModel):
    reg_numbers:list[str]=Field(min_length=1,max_length=30)
    @field_validator("reg_numbers")
    @classmethod
    def verify(cls,values):
        if any(not REG.fullmatch(x) for x in values) or len(set(values))!=len(values):
            raise ValueError("Номера закупок должны быть уникальными 11-значными номерами ЕИС")
        return values

class Template(BaseModel):
    name:str=Field(min_length=3,max_length=120)
    categories:list[str]=Field(default_factory=lambda:list(CATEGORIES),max_length=10)
    legal_forms:list[str]=Field(default_factory=lambda:list(LEGAL_FORMS),max_length=5)
    @field_validator("categories")
    @classmethod
    def cats(cls,v):
        if not v or len(set(v))!=len(v) or set(v)-set(CATEGORIES):
            raise ValueError("Некорректные категории")
        return v
    @field_validator("legal_forms")
    @classmethod
    def forms(cls,v):
        if not v or len(set(v))!=len(v) or set(v)-set(LEGAL_FORMS):
            raise ValueError("Некорректные формы")
        return v

class ChecklistUpdate(BaseModel):
    state:str=Field(pattern="^(pending|verified|needs_attention|not_applicable)$")
    comment:str=Field(default="",max_length=800)

class ManualIssue(BaseModel):
    title:str=Field(min_length=8,max_length=220)
    law:str=Field(min_length=6,max_length=400)
    evidence:str=Field(min_length=30,max_length=4000)
    source_url:str=Field(min_length=25,max_length=1200)
    @field_validator("source_url")
    @classmethod
    def source(cls,url):
        if not EIS_URL.match(url):
            raise ValueError("Укажите оригинальную ссылку ЕИС на доказательство")
        return url

class Preferences(BaseModel):
    priority_regs:list[str]=Field(default_factory=list,max_length=30)
    pinned_legal_categories:list[str]=Field(default_factory=list,max_length=10)
    notification_days:int=Field(default=3,ge=0,le=30)
    @field_validator("priority_regs")
    @classmethod
    def regs(cls,v):
        if len(set(v))!=len(v) or any(not REG.fullmatch(reg) for reg in v):
            raise ValueError("Укажите уникальные номера ЕИС")
        return v
    @field_validator("pinned_legal_categories")
    @classmethod
    def legal(cls,v):
        if len(set(v))!=len(v) or set(v)-set(CATEGORIES):
            raise ValueError("Некорректные категории")
        return v

class Discussion(BaseModel):
    body:str=Field(min_length=4,max_length=2000)

def _working_records(request,uid):
    from scripts.v366_account_manual import MANUAL_OWNED,_read_only_status
    with connect() as db:
        raw=db.execute(MANUAL_OWNED,(uid,)).fetchall()
    records=[{"reg_number":r[0],"job_id":r[1],"created_at":r[2],
              "last_started_at":r[3]} for r in raw]
    return _read_only_status(eis(request),records) if records else []

@router.get("/overview")
def overview(request:Request,response:Response):
    uid=owner(request)
    response.headers.update(no_store_headers())
    rows=_working_records(request,uid)
    with connect() as db:
        assignments=db.execute(
            "SELECT status,COUNT(*) FROM account_manager_tasks WHERE user_id=%s GROUP BY status",
            (uid,)).fetchall()
        campaigns=db.execute(
            "SELECT status,COUNT(*) FROM account_manager_campaign_targets WHERE user_id=%s GROUP BY status",
            (uid,)).fetchall()
        memos=db.execute(
            "SELECT COUNT(*) FROM account_memos WHERE user_id=%s",(uid,)).fetchone()[0]
    return {
      "checks_total":len(rows),
      "reviews_completed":sum(bool(x.get("legal_review_completed")) for x in rows),
      "with_findings":sum(int(x.get("violations",0)>0) for x in rows),
      "unique_confirmed_findings":sum(int(x.get("violations",0)) for x in rows),
      "documents_checked":sum(int(x.get("documents_checked",0)) for x in rows),
      "attention_required":sum(bool(x.get("documents_found",0)>x.get("documents_checked",0))
                                 for x in rows),
      "assignments":dict(assignments),
      "campaigns":dict(campaigns),
      "memo_count":memos,
      "note":"Только сохранённые проверки вашей учётной записи; отсутствие документов не считается завершением аудита.",
    }

@router.post("/bulk")
def bulk(request:Request,data:Bulk):
    require_origin(request)
    uid=owner(request)
    ns=eis(request)
    from v301_search_jobs import _enqueue
    from scripts.v366_account_manual import link_run
    response=[]
    # Submit to the EXISTING real EIS queue, not a separate fake worker.
    for reg in data.reg_numbers:
        try:
            job,created=_enqueue(ns,"reg",reg,1,1,"",True)
            link_run(uid,reg,job["id"])
            response.append({"reg_number":reg,"status":job["status"],
                             "created":created,"job_id":job["id"]})
        except Exception as exc:
            response.append({"reg_number":reg,"status":"error",
                             "detail":"Задание отклонено; проверьте очередь и доступность ЕИС"})
    return {"requested":len(data.reg_numbers),"accepted":sum(x["status"]!="error" for x in response),
            "results":response,"notes":"Количество подтверждённых нарушений не изменяется при постановке задачи."}

@router.get("/templates")
def templates(request:Request):
    uid=owner(request)
    with connect() as db:
        rows=db.execute(
            """SELECT id,name,categories,legal_forms,created_at FROM account_staff_templates
               WHERE user_id=%s ORDER BY created_at DESC LIMIT 60""",(uid,)).fetchall()
    return [{"id":str(r[0]),"name":r[1],"categories":r[2],"legal_forms":r[3],
             "created_at":r[4].isoformat()} for r in rows]

@router.post("/templates",status_code=201)
def create_template(request:Request,data:Template):
    require_origin(request);uid=owner(request);key=uuid4()
    with connect() as db:
        total=db.execute("SELECT COUNT(*) FROM account_staff_templates WHERE user_id=%s",
                         (uid,)).fetchone()[0]
        if total>=40:raise HTTPException(409,"Не больше 40 шаблонов")
        db.execute(
            """INSERT INTO account_staff_templates(id,user_id,name,categories,legal_forms)
               VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)""",
            (key,uid,data.name.strip(),json.dumps(data.categories,ensure_ascii=False),
             json.dumps(data.legal_forms,ensure_ascii=False)))
    return {"id":str(key),"saved":True}

@router.delete("/templates/{template_id}")
def delete_template(request:Request,template_id:UUID):
    require_origin(request);uid=owner(request)
    with connect() as db:
        result=db.execute(
            "DELETE FROM account_staff_templates WHERE user_id=%s AND id=%s",
            (uid,template_id))
    return {"removed":bool(result.rowcount)}

@router.get("/{reg}/checklist")
def checklist(request:Request,reg:str):
    uid=case_owner(request,reg)
    with connect() as db:
        done=db.execute(
            """SELECT question_key,state,comment,updated_at FROM account_staff_checklists
               WHERE user_id=%s AND reg_number=%s""",(uid,reg)).fetchall()
    recorded={r[0]:r for r in done}
    return [{"key":key,"question":title,
             "state":recorded[key][1] if key in recorded else "pending",
             "comment":recorded[key][2] if key in recorded else "",
             "updated_at":recorded[key][3].isoformat() if key in recorded else None}
            for key,title in CHECKS.items()]

@router.put("/{reg}/checklist/{key}")
def update_checklist(request:Request,reg:str,key:str,data:ChecklistUpdate):
    require_origin(request)
    uid=case_owner(request,reg)
    if key not in CHECKS:raise HTTPException(422,"Неизвестный контрольный вопрос")
    if data.state in ("verified","needs_attention") and len(data.comment.strip())<5:
        raise HTTPException(422,"Укажите проверенный факт или причину замечания")
    with connect() as db:
        db.execute(
            """INSERT INTO account_staff_checklists(user_id,reg_number,question_key,state,comment)
               VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(user_id,reg_number,question_key) DO UPDATE
               SET state=EXCLUDED.state,comment=EXCLUDED.comment,updated_at=CURRENT_TIMESTAMP""",
            (uid,reg,key,data.state,data.comment.strip()))
    return {"saved":True,"not_automatically_a_violation":True}

@router.get("/preferences")
def preferences(request:Request):
    uid=owner(request)
    with connect() as db:
        row=db.execute(
            "SELECT priority_regs,pinned_legal_categories,notification_days FROM account_staff_preferences WHERE user_id=%s",
            (uid,)).fetchone()
    return {"priority_regs":row[0] if row else [],"pinned_legal_categories":row[1] if row else [],
            "notification_days":row[2] if row else 3}

@router.put("/preferences")
def update_preferences(request:Request,data:Preferences):
    require_origin(request);uid=owner(request)
    with connect() as db:
        owned={r[0] for r in db.execute(
            "SELECT reg_number FROM account_manual_checks WHERE user_id=%s",(uid,)).fetchall()}
        if set(data.priority_regs)-owned:
            raise HTTPException(422,"В приоритетах можно указать только свои запущенные проверки")
        db.execute(
            """INSERT INTO account_staff_preferences
               (user_id,priority_regs,pinned_legal_categories,notification_days)
               VALUES(%s,%s::jsonb,%s::jsonb,%s)
               ON CONFLICT(user_id) DO UPDATE SET
                priority_regs=EXCLUDED.priority_regs,
                pinned_legal_categories=EXCLUDED.pinned_legal_categories,
                notification_days=EXCLUDED.notification_days""",
            (uid,json.dumps(data.priority_regs),json.dumps(data.pinned_legal_categories),
             data.notification_days))
    return {"saved":True,"priority_is_display_order_not_fabricated_scheduler":True}

@router.post("/{reg}/manual-findings",status_code=201)
def add_manual_finding(request:Request,reg:str,data:ManualIssue):
    require_origin(request);uid=case_owner(request,reg)
    ref=uuid4()
    with connect() as db:
        db.execute(
            """INSERT INTO account_staff_manual_findings
               (id,user_id,reg_number,title,law,evidence,source_url)
               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (ref,uid,reg,data.title.strip(),data.law.strip(),
             data.evidence.strip(),data.source_url))
    return {"id":str(ref),"status":"needs_review",
            "note":"Добавлено на юридическое рассмотрение; не учитывается среди подтверждённых нарушений."}

@router.get("/{reg}/manual-findings")
def manual_findings(request:Request,reg:str):
    uid=case_owner(request,reg)
    with connect() as db:
        rows=db.execute(
            """SELECT id,title,law,evidence,source_url,status,created_at
               FROM account_staff_manual_findings WHERE user_id=%s AND reg_number=%s
               ORDER BY created_at DESC LIMIT 100""",(uid,reg)).fetchall()
    return [{"id":str(r[0]),"title":r[1],"law":r[2],"evidence":r[3],
             "source_url":r[4],"status":r[5],"created_at":r[6].isoformat()} for r in rows]

@router.get("/{reg}/related")
def related(request:Request,reg:str):
    case_owner(request,reg)
    con=eis(request)["conn"]()
    try:
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=1500")
        con.execute("PRAGMA query_only=ON")
        p=con.execute("SELECT customer_inn,title FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        if not p or not p["customer_inn"]:return {"purchases":[],"source":"Нет подтверждённого ИНН"}
        rows=con.execute(
            """SELECT reg_number,title,customer,customer_inn,price
               FROM purchases WHERE customer_inn=? AND reg_number<>?
               ORDER BY reg_number DESC LIMIT 40""",
            (p["customer_inn"],reg)).fetchall()
        return {"purchases":[dict(r) for r in rows],"customer_inn":p["customer_inn"],
                "source":"Только уже собранные данные ЕИС"}
    finally:con.close()

@router.get("/{reg}/search")
def search(request:Request,reg:str,q:str):
    case_owner(request,reg)
    if not q or len(q)>110 or len(q)<3:
        raise HTTPException(422,"Запрос от 3 до 110 символов")
    con=eis(request)["conn"]()
    try:
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        words=re.sub(r"[^\wа-яА-ЯёЁ\s-]"," ",q).strip().lower()
        if len(words)<3:raise HTTPException(422,"Уточните поисковый запрос")
        # Search saved extracted snippets and source-backed document metadata;
        # never claim this searches unreadable or inaccessible original files.
        result=[]
        for table,col in [("findings","evidence"),("checks","evidence"),("docs","name")]:
            cols={r[1] for r in con.execute("PRAGMA table_info("+table+")")}
            if col not in cols:continue
            rows=con.execute(
                "SELECT "+col+" AS content FROM "+table+
                " WHERE reg_number=? AND lower("+col+") LIKE ? LIMIT 35",
                (reg,"%"+words.replace("%","").replace("_","")+"%")).fetchall()
            for r in rows:
                if r["content"]:
                    result.append({"type":table,"excerpt":str(r["content"])[:600]})
        return {"results":result[:65],
                "scope":"Извлечённые фрагменты доказательств и названия сохранённых документов; сканы без текста не включены"}
    finally:con.close()

def _thread(db,uid,reg):
    return db.execute(
        """SELECT d.id,d.author_id,u.email,d.body,d.created_at
           FROM account_staff_discussions d
           JOIN account_users u ON u.id=d.author_id
           WHERE d.user_id=%s AND d.reg_number=%s
           ORDER BY d.created_at ASC LIMIT 180""",(uid,reg)).fetchall()

def _as_thread(rows):
    return [{"id":str(r[0]),"author_id":str(r[1]),"author":r[2],
             "body":r[3],"created_at":r[4].isoformat()} for r in rows]

@router.get("/{reg}/discussion")
def my_thread(request:Request,reg:str):
    uid=case_owner(request,reg)
    with connect() as db:return _as_thread(_thread(db,uid,reg))

@router.post("/{reg}/discussion",status_code=201)
def my_post(request:Request,reg:str,data:Discussion):
    require_origin(request);uid=case_owner(request,reg)
    with connect() as db:
        db.execute(
            """INSERT INTO account_staff_discussions(id,user_id,reg_number,author_id,body)
               VALUES(%s,%s,%s,%s,%s)""",(uuid4(),uid,reg,uid,data.body.strip()))
    return {"saved":True}

@team_router.get("/{uid}/{reg}/discussion")
def manager_thread(request:Request,uid:UUID,reg:str):
    actor,role=supervisor(request)
    with connect() as db:
        worker=_member(db,actor,role,uid)
        if not db.execute(
            "SELECT 1 FROM account_manual_checks WHERE user_id=%s AND reg_number=%s",
            (worker,reg)).fetchone():raise HTTPException(404,"Проверка не найдена")
        return _as_thread(_thread(db,worker,reg))

@team_router.post("/{uid}/{reg}/discussion",status_code=201)
def manager_post(request:Request,uid:UUID,reg:str,data:Discussion):
    require_origin(request);actor,role=supervisor(request)
    with connect() as db:
        worker=_member(db,actor,role,uid)
        if not db.execute(
            "SELECT 1 FROM account_manual_checks WHERE user_id=%s AND reg_number=%s",
            (worker,reg)).fetchone():raise HTTPException(404,"Проверка не найдена")
        db.execute(
            """INSERT INTO account_staff_discussions(id,user_id,reg_number,author_id,body)
               VALUES(%s,%s,%s,%s,%s)""",(uuid4(),worker,reg,actor,data.body.strip()))
        _audit(db,actor,worker,"discussion",reg,"comment",data.body[:240])
    return {"saved":True}
