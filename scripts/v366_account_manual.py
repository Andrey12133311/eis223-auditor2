"""Real self-initiated 223-FZ checks, linked to authenticated account owners.

PostgreSQL retains ONLY the account -> real EIS job association. Shared
procurement documents and findings remain in the existing EIS SQLite database.
Never enumerate someone else's manual checks or convert old bookmarks to runs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_session_policy_v1 import no_store_headers

router = APIRouter(prefix="/me/manual-checks", tags=["personal-manual-checks"])
REG = re.compile(r"^3[0-9]{10}$")

MANUAL_OWNED = """
SELECT reg_number, job_id, created_at, last_started_at
FROM account_manual_checks
WHERE user_id=%s
ORDER BY last_started_at DESC, reg_number DESC LIMIT 150
"""


def valid_reg(value):
    return isinstance(value, str) and bool(REG.fullmatch(value))


def link_run(owner_id, reg_number, job_id):
    """Associate a verified request with the logged-in owner, never an arbitrary ID."""
    if not valid_reg(reg_number) or not re.fullmatch(r"[0-9a-f]{24}", str(job_id)):
        raise ValueError("A real registry request and EIS job are required")
    with connect() as db:
        db.execute(
            """INSERT INTO account_manual_checks(user_id,reg_number,job_id)
               VALUES (%s,%s,%s)
               ON CONFLICT(user_id,reg_number) DO UPDATE SET
                 job_id=EXCLUDED.job_id,
                 last_started_at=CURRENT_TIMESTAMP""",
            (owner_id, reg_number, job_id)
        )


def _read_only_status(ns, entries):
    """Never infer a completed audit from a finished download/bridge job."""
    from v352_dashboard_truth import load_counts
    if not entries:
        return []
    conn=ns["conn"]()
    try:
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA busy_timeout=1500")
        conn.execute("PRAGMA query_only=ON")
        regs=[r["reg_number"] for r in entries]
        counts=load_counts(conn,regs)
        has_jobs=bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v301_search_jobs'"
        ).fetchone())
        has_purchases=bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchases'"
        ).fetchone())
        purchase_columns=({x[1] for x in conn.execute("PRAGMA table_info(purchases)")}
                          if has_purchases else set())
        available_name=[c for c in ("customer","customer_name") if c in purchase_columns]
        has_inn="customer_inn" in purchase_columns
        has_findings=bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='findings'"
        ).fetchone())
        has_checks=bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checks'"
        ).fetchone())
        has_legal_queue=bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v332_audit_queue'"
        ).fetchone())
        finding_columns={col[1] for col in conn.execute("PRAGMA table_info(findings)")} if has_findings else set()
        check_columns={col[1] for col in conn.execute("PRAGMA table_info(checks)")} if has_checks else set()
        data=[]
        for entry in entries:
            reg=entry["reg_number"]
            job_id=entry["job_id"]
            job=(conn.execute(
                "SELECT status,detail,created_at FROM v301_search_jobs "
                "WHERE id=? AND kind='reg' AND target=? LIMIT 1",
                (job_id,reg)).fetchone() if has_jobs and job_id else None)
            purchase=(conn.execute(
                "SELECT documents_found,documents_checked FROM purchases "
                "WHERE reg_number=? LIMIT 1",(reg,)).fetchone()
                if has_purchases else None)
            customer_name=""
            customer_inn=""
            if purchase:
                cols=", ".join(([available_name[0]] if available_name else [])+
                               (["customer_inn"] if has_inn else []))
                if cols:
                    customer_row=conn.execute(
                        "SELECT "+cols+" FROM purchases WHERE reg_number=? LIMIT 1",
                        (reg,)).fetchone()
                    if available_name:
                        customer_name=str(customer_row[available_name[0]] or "")[:250]
                    if has_inn:
                        customer_inn=str(customer_row["customer_inn"] or "")[:12]
            labels=set()
            if {"title","kind","reg_number"}<=finding_columns:
                for value in conn.execute(
                    "SELECT DISTINCT title FROM findings WHERE reg_number=? "
                    "AND (kind='violation' OR kind='нарушение') LIMIT 40",(reg,)):
                    if value[0]:labels.add(str(value[0]).strip()[:160])
            if {"item","status","reg_number"}<=check_columns:
                for value in conn.execute(
                    "SELECT DISTINCT item FROM checks WHERE reg_number=? "
                    "AND (status='violation' OR status='нарушение') LIMIT 40",(reg,)):
                    if value[0]:labels.add(str(value[0]).strip()[:160])
            legal_review_state="unknown"
            legal_review_documents=0
            if has_legal_queue:
                audit=conn.execute(
                    """SELECT state,documents_read FROM v332_audit_queue
                       WHERE reg=? ORDER BY updated_at DESC LIMIT 1""",(reg,)).fetchone()
                if audit:
                    legal_review_state=str(audit["state"] or "unknown")
                    legal_review_documents=int(audit["documents_read"] or 0)
            started_at=entry["last_started_at"]
            data.append({
                "reg_number":reg,
                "job_id":job_id,
                "started_at":started_at.isoformat() if hasattr(started_at,"isoformat")
                              else str(started_at or ""),
                "job_status":job["status"] if job else "unknown",
                "job_detail":str(job["detail"] or "")[:350] if job else "",
                "documents_found":int(purchase["documents_found"] or 0) if purchase else 0,
                "documents_checked":int(purchase["documents_checked"] or 0) if purchase else 0,
                "violations":int(counts.get(reg,0)),
                "legal_review_state":legal_review_state,
                "legal_review_completed":(legal_review_state=="completed" and
                                          legal_review_documents>0),
                "legal_review_documents":legal_review_documents,
                "card_available":bool(purchase),
                "customer":customer_name,
                "customer_inn":customer_inn,
                "violation_types":sorted(labels)[:40],
            })
        return data
    finally:
        conn.close()


@router.get("")
def list_manual_checks(request:Request, response:Response):
    user=current_account(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        raw=db.execute(MANUAL_OWNED,(user[0],)).fetchall()
    entries=[{"reg_number":r[0], "job_id":r[1],
              "created_at":r[2], "last_started_at":r[3]} for r in raw]
    if not entries:
        return []
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if ns is None:
        raise HTTPException(503,"Загрузчик самостоятельных проверок недоступен")
    try:
        return _read_only_status(ns,entries)
    except (sqlite3.Error,KeyError,TypeError) as exc:
        raise HTTPException(503,"Не удалось получить состояние проверки") from exc


@router.post("/{reg_number}", status_code=202)
async def start_manual_check(reg_number:str, request:Request):
    require_origin(request)
    owner=current_account(request)[0]
    if not valid_reg(reg_number):
        raise HTTPException(422,"Номер закупки 223-ФЗ: 11 цифр, начинается с 3")
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if ns is None:
        raise HTTPException(503,"Серверный загрузчик ЕИС не подключён")
    # Existing pending EIS work is reused by the queue: no artificial
    # check/doc/finding increment and no second job for the same pending reg.
    from v301_search_jobs import _enqueue
    job,created=await asyncio.to_thread(
        _enqueue,ns,"reg",reg_number,1,0,"",True)
    # For a missing PG mapping, the EIS job is still real, never a fake result.
    # A retry reuses the unfinished job and completes the owner association.
    try:
        link_run(owner,reg_number,job["id"])
    except Exception as exc:
        raise HTTPException(503,
            "Задание ЕИС принято, но сохранение в личном кабинете не удалось. "
            "Повторите запуск; ожидающая проверка не дублируется.") from exc
    return {"reg_number":reg_number,"job_id":job["id"],
            "job_status":job["status"],"created":created}


@router.delete("/{reg_number}")
def remove_manual_check(reg_number:str, request:Request):
    require_origin(request)
    owner=current_account(request)[0]
    if not valid_reg(reg_number):
        raise HTTPException(422,"Некорректный номер ЕИС")
    # Deletion only removes the personal link. The EIS request, collected
    # originals, legal evidence and shared procurement remain untouched.
    with connect() as db:
        result=db.execute(
            "DELETE FROM account_manual_checks WHERE user_id=%s AND reg_number=%s",
            (owner,reg_number))
    return {"removed":bool(result.rowcount),"originals_retained":True}
