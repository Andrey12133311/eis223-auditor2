"""Verified legal evidence review and factual manager dashboard.

No rewriting of 223-FZ findings, procurement originals or global violation
counters. Manager decisions have separate status and immutable action history.
"""
from __future__ import annotations

import asyncio
from datetime import date
from hashlib import sha256
import json
import sqlite3
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel,Field
from scripts.account_api_v1 import connect,current_account,require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor,_member,_uuid,_audit,_task_dict,_task_rows
from scripts.v367_private_features import _pdf_from_odt

router=APIRouter(prefix="/manager",tags=["manager-legal-control"])


def _db_state(request):
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if ns is None:raise HTTPException(503,"База результатов ЕИС временно недоступна")
    return ns


def _legal_items(ns,reg):
    """Exactly the real findings/checks used for legal counts and cards."""
    from v352_dashboard_truth import collapse_legal_events
    from v365_consultant_refs import references
    c=ns["conn"]()
    try:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=1500")
        c.execute("PRAGMA query_only=ON")
        rows=[]
        tables={r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('findings','checks')")}
        if "findings" in tables:
            for r in c.execute(
                """SELECT * FROM findings WHERE reg_number=? AND kind='violation'
                   ORDER BY rowid DESC LIMIT 350""",(reg,)):
                rows.append(dict(r))
        if "checks" in tables:
            for r in c.execute(
                """SELECT * FROM checks WHERE reg_number=? AND status IN ('violation','check')
                   ORDER BY rowid DESC LIMIT 350""",
                (reg,)):
                if r["status"] == "violation":
                    item=dict(r)
                    item["title"]=item.get("item") or "Нарушение"
                    rows.append(item)
        result=[]
        for item in collapse_legal_events(rows):
            legal=str(item.get("law") or "").strip()
            evidence=str(item.get("evidence") or "").strip()
            title=str(item.get("title") or "").strip()
            document=str(item.get("document") or "").strip()
            url=str(item.get("source_url") or "").strip()
            digest=sha256(
                json.dumps([reg,title,legal,evidence,document,url],
                           ensure_ascii=False,separators=(",",":")).encode("utf-8")
            ).hexdigest()
            warnings=[]
            if not legal:warnings.append("Не указана правовая норма")
            if not evidence:warnings.append("Отсутствует текст доказательства")
            if not (document or url):warnings.append("Не определён источник доказательства")
            if legal and not references(item):
                warnings.append("Нужно уточнить статью и часть нормы")
            result.append({
                "reg_number":reg,"finding_key":digest,"title":title,
                "law":legal,"evidence":evidence[:3200],"document":document,
                "source_url":url,"references":references(item),
                "supporting_sources":item.get("supporting_sources",[]),
                "source_representations":int(item.get("raw_count",1)),
                "warnings":warnings,
            })
        return result
    finally:
        c.close()


def _authorized_reg(db,actor,role,worker,reg):
    worker=_member(db,actor,role,worker)
    if not isinstance(reg,str) or len(reg)!=11 or not reg.startswith("3") or not reg.isdigit():
        raise HTTPException(422,"Некорректный номер закупки ЕИС")
    exists=db.execute(
        """SELECT 1 FROM account_manual_checks
           WHERE user_id=%s AND reg_number=%s""",(worker,reg)).fetchone()
    if not exists:
        exists=db.execute(
            "SELECT 1 FROM account_finding_submissions WHERE user_id=%s AND reg_number=%s",
            (worker,reg)).fetchone()
    if not exists:
        raise HTTPException(404,"Сотрудник не направлял нарушения этой закупки")
    return worker


@router.get("/findings/{user_id}/{reg_number}")
def finding_list(user_id:str,reg_number:str,request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        worker=_authorized_reg(db,actor,role,user_id,reg_number)
        saved=db.execute(
            """SELECT finding_key,decision,comment,reviewer_id,updated_at
               FROM account_manager_finding_reviews
               WHERE user_id=%s AND reg_number=%s""",(worker,reg_number)).fetchall()
        owns_check=bool(db.execute(
            "SELECT 1 FROM account_manual_checks WHERE user_id=%s AND reg_number=%s",
            (worker,reg_number)).fetchone())
        submissions=db.execute(
            "SELECT finding_key,status,version FROM account_finding_submissions WHERE user_id=%s AND reg_number=%s",
            (worker,reg_number)).fetchall()
    submission_by_key={r[0]:{"status":r[1],"version":r[2]} for r in submissions}
    approved={r[0]:{"decision":r[1],"comment":r[2],
                    "reviewer_id":str(r[3]),"updated_at":r[4].isoformat()} for r in saved}
    try:items=_legal_items(_db_state(request),reg_number)
    except sqlite3.Error as exc:
        raise HTTPException(503,"Не удалось открыть доказательства ЕИС") from exc
    if not owns_check:
        items=[item for item in items if item["finding_key"] in submission_by_key]
    for item in items:
        item["review"]=approved.get(item["finding_key"])
        item["submission"]=submission_by_key.get(item["finding_key"])
    return {"reg_number":reg_number,"user_id":str(worker),"findings":items,
            "generated_from":"current_eis_evidence",
            "note":"Автоматическое выявление и решение руководителя учитываются отдельно"}


class FindingDecision(BaseModel):
    submission_version:int|None=Field(default=None,ge=1)
    decision:str=Field(pattern="^(approved|returned|rejected)$")
    comment:str=Field(default="",max_length=1500)


@router.post("/findings/{user_id}/{reg_number}/{finding_key}/decision")
def finding_decision(user_id:str,reg_number:str,finding_key:str,
                     data:FindingDecision,request:Request):
    require_origin(request)
    actor,role=supervisor(request)
    if len(finding_key)!=64 or any(c not in "0123456789abcdef" for c in finding_key):
        raise HTTPException(422,"Некорректный идентификатор доказательства")
    if data.decision in ("returned","rejected") and len(data.comment.strip())<5:
        raise HTTPException(422,"Укажите причину возврата или отклонения")
    with connect() as db:
        worker=_authorized_reg(db,actor,role,user_id,reg_number)
        # Serialize with employee submissions without requiring a personal check.
        db.execute("SELECT id FROM account_users WHERE id=%s FOR UPDATE",
                   (worker,)).fetchone()
        # Assignment can change while waiting for the account lock.
        _member(db,actor,role,worker)
        submission=db.execute(
            "SELECT status,version FROM account_finding_submissions WHERE user_id=%s AND reg_number=%s AND finding_key=%s",
            (worker,reg_number,finding_key)).fetchone()
        if not submission and not db.execute(
                "SELECT 1 FROM account_manual_checks WHERE user_id=%s AND reg_number=%s",
                (worker,reg_number)).fetchone():
            raise HTTPException(404,"Сотрудник не направлял это нарушение")
        if submission:
            if data.decision == "returned":
                raise HTTPException(422,"Для направленного нарушения доступны согласование или отказ с причиной")
            if data.submission_version != submission[1]:
                raise HTTPException(409,"Согласование изменилось; обновите очередь")
            if submission[0] == data.decision:
                return {"decision":data.decision,"evidence_unchanged":True,"already_reviewed":True}
            if submission[0] != "submitted":
                raise HTTPException(409,"Окончательное решение по нарушению уже сохранено")
        # The key must still match a verified live finding; stale approvals
        # cannot be silently carried to modified evidence.
        try:items=_legal_items(_db_state(request),reg_number)
        except sqlite3.Error as exc:
            raise HTTPException(503,"Проверка текущих доказательств недоступна") from exc
        row=next((item for item in items if item["finding_key"]==finding_key),None)
        if not row:
            raise HTTPException(409,"Доказательство изменилось; обновите карточку")
        if data.decision=="approved" and row["warnings"]:
            raise HTTPException(409,
                "Нельзя подтвердить нарушение без полноценного правового основания и доказательств")
        db.execute(
            """INSERT INTO account_manager_finding_reviews
               (user_id,reg_number,finding_key,title,legal_basis,evidence_digest,
                reviewer_id,decision,comment)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(user_id,reg_number,finding_key) DO UPDATE SET
                reviewer_id=EXCLUDED.reviewer_id,decision=EXCLUDED.decision,
                comment=EXCLUDED.comment,updated_at=CURRENT_TIMESTAMP""",
            (worker,reg_number,finding_key,row["title"],row["law"],
             sha256(row["evidence"].encode("utf-8")).hexdigest(),
             actor,data.decision,data.comment.strip()))
        db.execute("UPDATE account_finding_submissions SET status=%s WHERE user_id=%s AND reg_number=%s AND finding_key=%s",
                   (data.decision,worker,reg_number,finding_key))
        _audit(db,actor,worker,"finding",finding_key,data.decision,
               f"{reg_number}: {data.comment.strip()}")
    return {"decision":data.decision,"evidence_unchanged":True}


@router.get("/dashboard")
def dashboard(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        team_query=("SELECT user_id FROM account_manager_teams"
                    if role=="admin" else
                    "SELECT user_id FROM account_manager_teams WHERE manager_id=%s")
        team_ids=[r[0] for r in db.execute(
            team_query,() if role=="admin" else (actor,)).fetchall()]
        # Summary counts cover all authorized tasks, not the 400-row list page.
        # Preserve the task endpoint's current-team and creator restrictions.
        task_where=("WHERE t.manager_id=%s AND mt.manager_id=%s"
                    if role!="admin" else "")
        task_summary=db.execute(
            """SELECT t.status,COUNT(*),COUNT(*) FILTER (
                   WHERE t.due_date < %s AND t.status NOT IN ('completed','cancelled'))
               FROM account_manager_tasks t
               JOIN account_users u ON u.id=t.user_id
               LEFT JOIN account_manager_teams mt ON mt.user_id=t.user_id
               """+task_where+" GROUP BY t.status",
            (date.today(),actor,actor) if role!="admin" else (date.today(),)).fetchall()
        memo_where="" if role=="admin" else " AND t.manager_id=%s"
        memo_rows=db.execute(
            """SELECT r.status,COUNT(*) FROM account_manager_memo_reviews r
               JOIN account_manager_teams t ON t.user_id=r.owner_id
               WHERE 1=1"""+memo_where+""" GROUP BY r.status""",
            () if role=="admin" else (actor,)).fetchall()
        approved=db.execute(
            """SELECT r.decision,COUNT(*) FROM account_manager_finding_reviews r
               JOIN account_manager_teams t ON t.user_id=r.user_id
               WHERE 1=1"""+memo_where+""" GROUP BY r.decision""",
            () if role=="admin" else (actor,)).fetchall()
        if team_ids:
            people_sql=",".join("%s" for _ in team_ids)
            manual_rows=db.execute(
                """SELECT user_id,reg_number,job_id,created_at,last_started_at,COUNT(*) OVER ()
                   FROM account_manual_checks WHERE user_id IN ("""+people_sql+
                ") ORDER BY last_started_at DESC LIMIT 350",tuple(team_ids)).fetchall()
        else:
            manual_rows=[]
        activities=db.execute(
            """SELECT date_trunc('day',created_at)::date,COUNT(*)
               FROM account_manager_tasks
               """+("WHERE manager_id=%s " if role!="admin" else "")+
               """GROUP BY 1 ORDER BY 1 DESC LIMIT 14""",
            (actor,) if role!="admin" else ()).fetchall()
    manual_truncated=bool(manual_rows and int(manual_rows[0][5])>len(manual_rows))
    task_totals={row[0]:int(row[1]) for row in task_summary}
    status_counts={state:task_totals.get(state,0)
                   for state in ("assigned","in_progress","submitted","returned",
                                 "completed","cancelled")}
    docs=None
    violations=None
    checked_purchases=None
    if not manual_rows:
        docs=0;violations=0;checked_purchases=0
    else:
        ns=getattr(request.app.state,"eis223_manual_ns",None)
        if ns is not None:
            from scripts.v366_account_manual import _read_only_status
            entries=[{"reg_number":r[1],"job_id":r[2],
                      "created_at":r[3],"last_started_at":r[4]} for r in manual_rows]
            try:
                actual=_read_only_status(ns,entries)
                docs=sum(x["documents_checked"] for x in actual)
                # The same procurement across different workers is counted
                # once, rather than presenting an artificial team gain.
                by_reg={}
                for x in actual:
                    by_reg[x["reg_number"]]=x
                docs=sum(x["documents_checked"] for x in by_reg.values())
                violations=sum(x["violations"] for x in by_reg.values())
                checked_purchases=sum(bool(x["legal_review_completed"]) for x in by_reg.values())
            except (sqlite3.Error,KeyError,TypeError):
                pass
    memo_counts=dict(memo_rows)
    review_counts=dict(approved)
    return {
        "staff":len(team_ids),
        "tasks_total":sum(task_totals.values()),
        "tasks_overdue":sum(int(row[2]) for row in task_summary),
        "tasks_pending":status_counts["submitted"],
        "tasks_status":status_counts,
        "memos_pending":memo_counts.get("submitted",0),
        "memos_approved":memo_counts.get("approved",0),
        "manager_approved_findings":review_counts.get("approved",0),
        "manager_returned_findings":review_counts.get("returned",0),
        "real_manual_checks":len({r[1] for r in manual_rows}),
        "real_documents_checked":docs,
        "real_confirmed_findings":violations,
        "verified_legal_reviews":checked_purchases,
        "tasks_by_day":[{"day":r[0].isoformat(),"count":r[1]} for r in activities],
        "manual_checks_truncated":manual_truncated,
        "metric_note":("Числа из базы ЕИС. Отсутствующие сведения не заменяются оценками."+
                       (" Показатели документов и нарушений рассчитаны по последним 350 самостоятельным проверкам команды."
                        if manual_truncated else "")),
    }


@router.get("/alerts")
def manager_alerts(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        tasks=[_task_dict(r) for r in _task_rows(db,actor,role)]
        where="" if role=="admin" else " AND tm.manager_id=%s"
        memos=db.execute(
            """SELECT m.id,m.reg_number,u.email,r.submitted_at FROM account_manager_memo_reviews r
               JOIN account_memos m ON m.id=r.memo_id
               JOIN account_users u ON u.id=r.owner_id
               JOIN account_manager_teams tm ON tm.user_id=r.owner_id
               WHERE r.status='submitted'"""+where+
               " ORDER BY r.submitted_at DESC LIMIT 35",
            () if role=="admin" else (actor,)).fetchall()
    alerts=[{"type":"deadline","title":"Просрочено поручение",
             "description":t["title"]+" · "+t["employee"],
             "reg_number":t["reg_number"],"date":t["due_date"]}
            for t in tasks if t["overdue"]]
    alerts.extend({
        "type":"task","title":"Проверка ожидает рассмотрения",
        "description":t["title"]+" · "+t["employee"],
        "reg_number":t["reg_number"],"date":t["updated_at"]
    } for t in tasks if t["status"]=="submitted")
    alerts.extend({
        "type":"memo","title":"Служебная записка на согласовании",
        "description":"Закупка "+r[1]+" · "+r[2],"memo_id":str(r[0]),
        "date":r[3].isoformat()
    } for r in memos)
    return alerts[:100]


@router.get("/history")
def manager_history(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    condition=("WHERE actor_id=%s OR target_user_id IN "
               "(SELECT user_id FROM account_manager_teams WHERE manager_id=%s)"
               if role!="admin" else "")
    with connect() as db:
        rows=db.execute(
            """SELECT a.created_at,a.actor_id,u.email,a.target_user_id,
                      a.entity_type,a.entity_id,a.action,a.details
               FROM account_manager_actions a
               LEFT JOIN account_users u ON u.id=a.actor_id
               """+condition+" ORDER BY a.created_at DESC LIMIT 150",
            (actor,actor) if role!="admin" else ()).fetchall()
    return [{"created_at":r[0].isoformat(),"actor":r[2],"type":r[4],
             "entity":r[5],"action":r[6],"details":r[7]} for r in rows]


@router.get("/memos/{memo_id}/download/{format}")
async def manager_memo_download(memo_id:str,format:str,request:Request):
    actor,role=supervisor(request)
    if format not in ("odt","pdf"):
        raise HTTPException(422,"Поддерживаются ODT и PDF")
    document=_uuid(memo_id)
    with connect() as db:
        where="" if role=="admin" else "AND t.manager_id=%s"
        row=db.execute(
            """SELECT m.reg_number,b.odt,b.pdf FROM account_memos m
               JOIN account_memo_blobs b ON b.memo_id=m.id
               JOIN account_manager_memo_reviews r ON r.memo_id=m.id
               JOIN account_manager_teams t ON t.user_id=m.user_id
               WHERE m.id=%s """+where,
            (document,) if role=="admin" else (document,actor)).fetchone()
    if not row:
        raise HTTPException(404,"Записка не назначена вашему подразделению")
    data=bytes(row[1]) if format=="odt" else (
        bytes(row[2]) if row[2] is not None else
        await asyncio.to_thread(_pdf_from_odt,bytes(row[1])))
    from fastapi.responses import Response as FileResponse
    return FileResponse(
        content=data,
        media_type="application/pdf" if format=="pdf"
                   else "application/vnd.oasis.opendocument.text",
        headers={"Cache-Control":"private,no-store","X-Content-Type-Options":"nosniff",
                 "Content-Disposition":
                  f'attachment; filename="manager_memo_{row[0]}.{format}"'})
