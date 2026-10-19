"""Additional employee workflows: reminders, audit log, verified case library,
manual recheck, personal thematic template execution and memo preflight.
"""
from __future__ import annotations
from datetime import date,timedelta
from uuid import UUID,uuid4
from hashlib import sha256
from html import unescape
import json
import re
import sqlite3
import secrets
import zipfile
import io
import xml.etree.ElementTree as ET
from fastapi import APIRouter,HTTPException,Request,Response
from scripts.account_api_v1 import connect,current_account,require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v370_staff_workbench import case_owner,eis,owner
from scripts.v370_manager_campaigns import _insert_real_batch

router=APIRouter(prefix="/me/workbench",tags=["employee-extras"])

@router.post("/{reg}/recheck",status_code=202)
def recheck(request:Request,reg:str):
    require_origin(request)
    uid=case_owner(request,reg)
    from scripts.v366_account_manual import _start_linked_recheck
    job,created,legal=_start_linked_recheck(eis(request),uid,reg,2)
    return {"job_id":job["id"],"status":job["status"],
            "new_job":created,"originals_retained":True,"legal_recheck":legal}

@router.post("/templates/{template_id}/start",status_code=201)
def launch_template(request:Request,template_id:UUID):
    require_origin(request)
    uid=owner(request)
    with connect() as db:
        item=db.execute(
            """SELECT name,categories,legal_forms FROM account_staff_templates
               WHERE user_id=%s AND id=%s""",(uid,template_id)).fetchone()
        if not item:raise HTTPException(404,"Личный шаблон не найден")
    batch=secrets.token_hex(12)
    ns=eis(request)
    with connect() as db:
        db.execute(
            """INSERT INTO account_thematic_batches(user_id,batch_id)
               VALUES(%s,%s)""",(uid,batch))
    try:
        _insert_real_batch(ns,batch,item[1],item[2],owner_id=uid)
    except Exception:
        # A template without a real engine batch must not appear as completed.
        with connect() as db:
            db.execute(
                "DELETE FROM account_thematic_batches WHERE user_id=%s AND batch_id=%s",
                (uid,batch))
        raise
    return {"batch_id":batch,"status":"active",
            "note":"Запущена настоящая тематическая проверка по сохранённому шаблону"}

@router.get("/calendar")
def calendar(request:Request,response:Response):
    uid=owner(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        preferences=db.execute(
            "SELECT notification_days FROM account_staff_preferences WHERE user_id=%s",
            (uid,)).fetchone()
        lead=int(preferences[0]) if preferences else 3
        tasks=db.execute(
            """SELECT reg_number,title,due_date,status,'single'
               FROM account_manager_tasks WHERE user_id=%s
               AND due_date IS NOT NULL AND status NOT IN ('cancelled','completed')
               UNION ALL
               SELECT batch_id,title,due_date,status,'thematic'
               FROM account_manager_campaign_targets WHERE user_id=%s
               AND due_date IS NOT NULL AND status NOT IN ('cancelled','completed')
               ORDER BY due_date ASC LIMIT 180""",(uid,uid)).fetchall()
    today=date.today()
    events=[]
    for identifier,title,due,state,kind in tasks:
        remaining=(due-today).days
        events.append({"reference":identifier,"title":title,"due_date":due.isoformat(),
                       "kind":kind,"status":state,"days_left":remaining,
                       "overdue":remaining<0,
                       "remind":remaining<=lead and state not in ("completed","cancelled")})
    # Purchase-level EIS submission dates are separately labelled:
    # they are not internal obligations of the employee.
    return {"events":events,"notification_days":lead,
            "note":"Сроки поручений — внутренние. Сроки заявок по ЕИС смотрите в электронном деле."}

@router.get("/history")
def personal_history(request:Request,response:Response):
    uid=owner(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        own=db.execute(
            """SELECT entity_type,entity_id,action,details,created_at
               FROM account_manager_actions
               WHERE actor_id=%s OR target_user_id=%s
               ORDER BY created_at DESC LIMIT 180""",(uid,uid)).fetchall()
        notes=db.execute(
            """SELECT reg_number,body,created_at FROM account_case_notes
               WHERE user_id=%s ORDER BY created_at DESC LIMIT 80""",(uid,)).fetchall()
        updates=db.execute(
            """SELECT reg_number,question_key,state,updated_at
               FROM account_staff_checklists WHERE user_id=%s
               ORDER BY updated_at DESC LIMIT 80""",(uid,)).fetchall()
    entries=[{"kind":r[0],"reference":r[1],"action":r[2],
              "detail":r[3],"date":r[4].isoformat()} for r in own]
    entries.extend({"kind":"case_note","reference":r[0],"action":"note",
                    "detail":r[1][:180],"date":r[2].isoformat()} for r in notes)
    entries.extend({"kind":"checklist","reference":r[0],"action":r[1]+": "+r[2],
                    "detail":"","date":r[3].isoformat()} for r in updates)
    return sorted(entries,key=lambda v:v["date"],reverse=True)[:180]

@router.get("/approved-library")
def approved_library(request:Request):
    uid=owner(request)
    with connect() as db:
        rows=db.execute(
            """SELECT reg_number,title,legal_basis,decision,comment,updated_at
               FROM account_manager_finding_reviews
               WHERE user_id=%s AND decision='approved'
               ORDER BY updated_at DESC LIMIT 160""",(uid,)).fetchall()
    return [{"reg_number":r[0],"title":r[1],"law":r[2],
             "decision":r[3],"review_comment":r[4],
             "date":r[5].isoformat()} for r in rows]

def memo_lint(odt:bytes):
    """Formal quality checks only; not a fabricated substantive legal opinion."""
    if not 300<len(odt)<=3*1024*1024:
        raise HTTPException(422,"Некорректная служебная записка ODT")
    try:
        with zipfile.ZipFile(io.BytesIO(odt)) as archive:
            if archive.read("mimetype").strip()!=b"application/vnd.oasis.opendocument.text":
                raise ValueError("ODT format required")
            xml=archive.read("content.xml")
            if len(xml)>8*1024*1024:raise ValueError("Excessive XML")
        root=ET.fromstring(xml)
        content=" ".join(v.strip() for v in root.itertext() if v.strip())
    except (zipfile.BadZipFile,KeyError,ET.ParseError,ValueError) as exc:
        raise HTTPException(422,"Невозможно проверить содержимое ODT") from exc
    warnings=[]
    if len(content)<250:warnings.append("Недостаточно содержимого")
    if not re.search(r"(?:223[-–]ФЗ|223[-–]ФЗ|Закона о закупках)",content,re.I):
        warnings.append("Не обнаружена ссылка на Закон о закупках")
    if not re.search(r"\bст(?:атья|атьи|атье|\.)\s*\d+",content,re.I):
        warnings.append("Не найдена конкретная статья закона")
    if not re.search(r"(?:закупк[аиу]|извещени[ея])\s*(?:№|N|номер)?\s*3\d{10}",content,re.I):
        warnings.append("Проверьте регистрационный номер закупки в тексте")
    if len(content)>200000:warnings.append("Необычно большой объём служебной записки")
    return {"format":"ODT","chars":len(content),"warnings":warnings,
            "status":"requires_review" if warnings else "format_checks_passed",
            "note":"Проверена только структура; правовая корректность и доказательства требуют проверки человеком."}

@router.get("/memos/{memo_id}/quality")
def memo_quality(request:Request,memo_id:UUID):
    uid=owner(request)
    with connect() as db:
        row=db.execute(
            """SELECT b.odt FROM account_memos m
               JOIN account_memo_blobs b ON b.memo_id=m.id
               WHERE m.id=%s AND m.user_id=%s""",(memo_id,uid)).fetchone()
    if not row:raise HTTPException(404,"Сохранённая записка не найдена")
    return memo_lint(bytes(row[0]))
