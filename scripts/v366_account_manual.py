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
from pydantic import BaseModel, Field
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v342_identity import organization_display_name

router = APIRouter(prefix="/me/manual-checks", tags=["personal-manual-checks"])
inn_router = APIRouter(prefix="/me/inn-search", tags=["personal-inn-search"])
REG = re.compile(r"^3[0-9]{10}$")
INN = re.compile(r"^(?:[0-9]{10}|[0-9]{12})$")

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


class InnSearchRequest(BaseModel):
    inn: str = Field(strict=True, pattern=r"^(?:[0-9]{10}|[0-9]{12})$")


def _link_inn_search(owner, inn, job_id):
    """Own a real search request without adding candidates to manual history."""
    with connect() as db:
        db.execute(
            """INSERT INTO account_inn_searches(user_id,job_id,inn)
               VALUES (%s,%s,%s)
               ON CONFLICT(user_id,job_id) DO UPDATE SET
                 last_started_at=CURRENT_TIMESTAMP""", (owner,job_id,inn))


def _owned_inn_search(owner, job_id):
    with connect() as db:
        row=db.execute(
            "SELECT inn FROM account_inn_searches WHERE user_id=%s AND job_id=%s",
            (owner,job_id)).fetchone()
    if not row:
        raise HTTPException(404,"Поиск по ИНН не найден")
    return row[0]


def _verified_inn_results(ns, inn):
    from v301_search_jobs import _local_results
    rows=_local_results(ns,"inn",inn)
    # Buyer identity must come from stored procurement facts. An older bridge
    # association or search-page filter alone cannot establish the buyer.
    if not rows:
        return []
    db=ns["conn"]()
    try:
        verified={row[0] for row in db.execute(
            "SELECT reg_number FROM purchases WHERE customer_inn=?",(inn,))}
    finally:
        db.close()
    return [row for row in rows if row["reg_number"] in verified]


def _inn_namespace(request):
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if ns is None:
        raise HTTPException(503,"Серверный загрузчик ЕИС не подключён")
    return ns


@inn_router.post("",status_code=202)
async def start_inn_search(data:InnSearchRequest, request:Request, response:Response):
    require_origin(request)
    owner=current_account(request)[0]
    response.headers.update(no_store_headers())
    # Keep runtime validation explicit for direct calls as well as HTTP input.
    if not INN.fullmatch(data.inn):
        raise HTTPException(422,"ИНН заказчика: 10 или 12 цифр")
    ns=_inn_namespace(request)
    from v301_search_jobs import _enqueue, _bridge
    job,created=await asyncio.to_thread(_enqueue,ns,"inn",data.inn,1,0,"",False)
    try:
        await asyncio.to_thread(_link_inn_search,owner,data.inn,job["id"])
    except Exception as exc:
        raise HTTPException(503,
            "Поиск ЕИС принят, но сохранение в личном кабинете не удалось. "
            "Повторите запуск; ожидающий поиск не дублируется.") from exc
    existing=await asyncio.to_thread(_verified_inn_results,ns,data.inn)
    return {"id":job["id"],"kind":"inn","target":data.inn,"status":job["status"],
            "new_request":created,"local_recheck":False,"existing":existing[:50],
            "bridge":await asyncio.to_thread(_bridge,ns)}


def _inn_search_snapshot(ns, identifier, inn):
    from v301_search_jobs import _db, _bridge
    from eis_server_worker import inn_progress
    db=_db(ns)
    try:
        row=db.execute("SELECT * FROM v301_search_jobs WHERE id=? AND kind='inn' AND target=?",
                       (identifier,inn)).fetchone()
        if not row:
            raise HTTPException(404,"Поиск по ИНН не найден")
        public_fields=("id","kind","target","page","status","priority","created_at",
                       "updated_at","not_before","attempts","detail","found","accepted","parent_id")
        job={name:row[name] for name in public_fields}
    finally:
        db.close()
    existing=_verified_inn_results(ns,inn)
    return {"job":job,"existing":existing[:100],"total_existing":len(existing),
            **(inn_progress(ns,identifier) or {}),"audit":None,"bridge":_bridge(ns)}


@inn_router.get("/{identifier}")
async def get_inn_search(identifier:str, request:Request, response:Response):
    owner=current_account(request)[0]
    response.headers.update(no_store_headers())
    if not re.fullmatch(r"[0-9a-f]{24}",identifier):
        raise HTTPException(404,"Поиск по ИНН не найден")
    inn=await asyncio.to_thread(_owned_inn_search,owner,identifier)
    return await asyncio.to_thread(_inn_search_snapshot,_inn_namespace(request),identifier,inn)


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
        tables={r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        purchase_columns=({x[1] for x in conn.execute("PRAGMA table_info(purchases)")}
                          if "purchases" in tables else set())
        available_name=[c for c in ("customer","customer_name") if c in purchase_columns]
        has_inn="customer_inn" in purchase_columns
        finding_columns={col[1] for col in conn.execute("PRAGMA table_info(findings)")} if "findings" in tables else set()
        check_columns={col[1] for col in conn.execute("PRAGMA table_info(checks)")} if "checks" in tables else set()
        purchases, jobs, audits, labels_by_reg = {}, {}, {}, {}
        # Bound SQLite parameters and read shared facts once per batch, rather
        # than opening up to six queries for each account procurement.
        unique_regs=list(dict.fromkeys(regs))
        for offset in range(0,len(unique_regs),350):
            batch=unique_regs[offset:offset+350]
            marks=",".join("?" for _ in batch)
            if "purchases" in tables:
                cols=["reg_number","documents_found","documents_checked"]+available_name[:1]
                if has_inn:cols.append("customer_inn")
                for row in conn.execute("SELECT "+",".join(cols)+
                        " FROM purchases WHERE reg_number IN ("+marks+")",batch):
                    purchases.setdefault(row["reg_number"],row)
            if "v332_audit_queue" in tables:
                for row in conn.execute(
                    "SELECT reg,state,documents_read FROM ("
                    "SELECT reg,state,documents_read,ROW_NUMBER() OVER ("
                    "PARTITION BY reg ORDER BY updated_at DESC) AS latest "
                    "FROM v332_audit_queue WHERE reg IN ("+marks+")) WHERE latest=1",batch):
                    audits[row["reg"]]=row
            for table,label,state,columns in (
                    ("findings","title","kind",finding_columns),
                    ("checks","item","status",check_columns)):
                if not {label,state,"reg_number"}<=columns:continue
                # Keep the previous per-source cap; deterministic ordering makes
                # the displayed labels stable across repeated refreshes.
                for row in conn.execute(
                    "SELECT reg_number,label FROM (SELECT reg_number,label,"
                    "ROW_NUMBER() OVER (PARTITION BY reg_number ORDER BY label) AS n "
                    "FROM (SELECT DISTINCT reg_number,"+label+" AS label FROM "+table+
                    " WHERE reg_number IN ("+marks+") AND "+state+
                    " IN ('violation','нарушение'))) WHERE n<=40",batch):
                    if row["label"]:
                        labels_by_reg.setdefault(row["reg_number"],set()).add(str(row["label"]).strip()[:160])
        job_ids=list(dict.fromkeys(e["job_id"] for e in entries if e["job_id"]))
        if "v301_search_jobs" in tables:
            for offset in range(0,len(job_ids),350):
                batch=job_ids[offset:offset+350]
                marks=",".join("?" for _ in batch)
                for row in conn.execute(
                    "SELECT id,target,status,detail FROM v301_search_jobs "
                    "WHERE kind='reg' AND id IN ("+marks+")",batch):
                    jobs[(row["id"],row["target"])]=row
        data=[]
        for entry in entries:
            reg=entry["reg_number"]
            job_id=entry["job_id"]
            job=jobs.get((job_id,reg))
            purchase=purchases.get(reg)
            customer_name=str(purchase[available_name[0]] or "")[:250] if purchase and available_name else ""
            customer_inn=str(purchase["customer_inn"] or "")[:12] if purchase and has_inn else ""
            labels=labels_by_reg.get(reg,set())
            audit=audits.get(reg)
            legal_review_state=str(audit["state"] or "unknown") if audit else "unknown"
            legal_review_documents=int(audit["documents_read"] or 0) if audit else 0
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
                "customer":organization_display_name(customer_name),
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
        await asyncio.to_thread(link_run,owner,reg_number,job["id"])
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
