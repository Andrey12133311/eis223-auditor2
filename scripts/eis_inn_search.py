"""Fenced VPS INN search pages; search results are candidates, never buyer facts."""
from __future__ import annotations

import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from v301_search_jobs import INN, REG, MAX_QUEUE, _db, _utc

MAX_PAGES = 10


def init_schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS eis_inn_candidates(
        root_job_id TEXT NOT NULL, inn TEXT NOT NULL, reg_number TEXT NOT NULL,
        source_job_id TEXT NOT NULL, page INTEGER NOT NULL,
        source_url TEXT NOT NULL, discovered_at TEXT NOT NULL,
        reg_job_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(root_job_id,reg_number)
      );
      CREATE INDEX IF NOT EXISTS eis_inn_candidates_pending
        ON eis_inn_candidates(reg_job_id,discovered_at);
      CREATE TABLE IF NOT EXISTS eis_server_inn_reports(
        job_id TEXT NOT NULL, lease_token TEXT NOT NULL, worker_id TEXT NOT NULL,
        inn TEXT NOT NULL, page INTEGER NOT NULL, root_job_id TEXT NOT NULL,
        result_json TEXT NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(job_id,lease_token)
      );
      CREATE INDEX IF NOT EXISTS eis_server_inn_reports_recent ON eis_server_inn_reports(created_at DESC);
    """)


def root_job(db, row):
    parent = row["parent_id"]
    if parent and re.fullmatch(r"[0-9a-f]{24}", parent):
        existing = db.execute("SELECT * FROM v301_search_jobs WHERE id=? AND kind='inn' AND target=?",
                              (parent, row["target"])).fetchone()
        if existing:
            return parent
    return row["id"]


def enqueue_pending_candidates(db, limit=40, max_new=None):
    """Attach candidates to shared downloads inside the caller's write lock.

    A full queue never drops search results. Unscheduled candidates remain in
    this table and are picked up when a later worker poll frees capacity.
    """
    active = db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased','deferred')").fetchone()[0]
    budget = max(0, MAX_QUEUE-8-active)
    if max_new is not None:
        budget = min(budget, max_new)
    created = 0
    for candidate in db.execute("SELECT * FROM eis_inn_candidates WHERE reg_job_id='' ORDER BY discovered_at,root_job_id,reg_number LIMIT ?",
                                (limit,)).fetchall():
        reg = candidate["reg_number"]
        existing = db.execute("""SELECT id FROM v301_search_jobs WHERE kind='reg' AND target=?
          AND status IN ('queued','leased','deferred') ORDER BY priority,created_at LIMIT 1""", (reg,)).fetchone()
        if existing:
            identifier = existing[0]
        elif budget:
            identifier = secrets.token_hex(12)
            db.execute("""INSERT INTO v301_search_jobs(id,kind,target,page,status,priority,parent_id,created_at,updated_at)
              VALUES(?,'reg',?,1,'queued',8,?,?,?)""",
              (identifier, reg, "inn-search:"+candidate["root_job_id"], _utc(), _utc()))
            created += 1
            budget -= 1
        else:
            continue
        db.execute("UPDATE eis_inn_candidates SET reg_job_id=? WHERE root_job_id=? AND reg_number=?",
                   (identifier, candidate["root_job_id"], reg))
    return created


def official_search_source(source, inn, page):
    try:
        parsed = urlsplit(source)
        query = parse_qs(parsed.query, keep_blank_values=True)
        return (isinstance(source, str) and len(source) <= 2000
                and parsed.scheme == "https" and parsed.hostname == "zakupki.gov.ru"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.fragment and not any(ord(c) < 33 for c in source)
                and parsed.path == "/epz/order/extendedsearch/results.html"
                and query.get("fz223") == ["on"] and query.get("customerInn") == [inn]
                and query.get("pageNumber") == [str(page)])
    except (ValueError, TypeError, AttributeError):
        return False


def report(ns, identifier, data):
    from eis_server_worker import (worker_id, _assert_lease, _seen, safe_text, MAX_ATTEMPTS,
                                   drain_candidates, _log_telemetry, _report_telemetry, _telemetry_error)

    identity = worker_id(data.get("worker_id"))
    inn, page = data.get("inn"), data.get("page")
    token, status = data.get("lease_token"), data.get("status")
    numbers, more = data.get("reg_numbers", []), data.get("has_more", False)
    source = data.get("source_url") or ""
    retry = data.get("retry_after_seconds") or 0
    if data.get("kind") != "inn" or not isinstance(inn, str) or not INN.fullmatch(inn):
        raise HTTPException(422, "Invalid INN search result")
    if type(page) is not int or not 1 <= page <= MAX_PAGES:
        raise HTTPException(422, "Invalid INN search page")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{48}", token):
        raise HTTPException(422, "Invalid lease token")
    if (status not in ("completed", "no_data", "error") or type(more) is not bool
            or not isinstance(numbers, list) or len(numbers) > 20
            or any(not isinstance(reg, str) or not REG.fullmatch(reg) for reg in numbers)):
        raise HTTPException(422, "Invalid INN page result")
    numbers = list(dict.fromkeys(numbers))
    if (status == "completed" and not numbers) or (status != "completed" and (numbers or more)):
        raise HTTPException(422, "Empty, failed and completed search pages are distinct")
    if (status != "error" or source) and not official_search_source(source, inn, page):
        raise HTTPException(422, "Expected the actual official INN/page search URL")
    if type(retry) is not int or not 0 <= retry <= 86400:
        raise HTTPException(422, "Invalid retry delay")
    db = _db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        replay = db.execute("SELECT * FROM eis_server_inn_reports WHERE job_id=? AND lease_token=?",
                            (identifier, token)).fetchone()
        if replay:
            if replay["worker_id"] != identity or replay["inn"] != inn or replay["page"] != page:
                raise HTTPException(409, "Report does not match completed lease")
            db.commit()
            return {**json.loads(replay["result_json"]), "replayed": True}
        row = db.execute("SELECT * FROM v301_search_jobs WHERE id=?", (identifier,)).fetchone()
        _assert_lease(row, identity, token, inn)
        if row["kind"] != "inn" or row["page"] != page:
            raise HTTPException(409, "Report does not match the claimed INN page")
        root = root_job(db, row)
        next_id = None
        queued = 0
        attempts = row["attempts"]
        error_code = safe_text(data.get("error_code"), 80)
        if status != "error":
            for reg in numbers:
                db.execute("""INSERT OR IGNORE INTO eis_inn_candidates
                  (root_job_id,inn,reg_number,source_job_id,page,source_url,discovered_at)
                  VALUES(?,?,?,?,?,?,?)""", (root, inn, reg, identifier, page, source, _utc()))
            state = "done"
            detail = ("В официальной выдаче ЕИС закупки не найдены" if status == "no_data" else
                      "Номера из выдачи ЕИС сохранены; ИНН заказчика будет подтверждён по XML")
        else:
            transient = (error_code in ("eis_http_403", "eis_http_429", "eis_connection_error", "eis_search_cooldown",
                                       "site_connection_error", "site_http_429", "site_http_507")
                         or re.fullmatch(r"(?:eis|site)_http_5[0-9]{2}", error_code) is not None)
            if error_code == "eis_search_cooldown":
                # A reserved search interval means no EIS request occurred.
                # Scheduling cannot exhaust the real network-attempt budget.
                attempts = max(0, attempts-1)
            elif transient and retry:
                old = db.execute("SELECT deferrals FROM eis_server_backoff WHERE job_id=?", (identifier,)).fetchone()
                deferrals = old[0] if old else 0
                if deferrals < 12:
                    attempts = max(0, attempts-1)
                    db.execute("INSERT OR REPLACE INTO eis_server_backoff VALUES(?,?)", (identifier, deferrals+1))
            state = "queued" if attempts < MAX_ATTEMPTS else "error"
            detail = "Поиск ЕИС по ИНН не выполнен" + (": "+error_code if error_code else "")
        if more and page >= MAX_PAGES:
            detail += "; достигнут предел 10 страниц, выдача обработана не полностью"
        delay = 0
        if state == "queued":
            # A shared search slot is scheduling, not a remote EIS failure.
            # Honour the remaining slot exactly; real failures keep backoff.
            delay = max(1, retry or 60) if error_code == "eis_search_cooldown" else max(60, retry or 900)
        db.execute("""UPDATE v301_search_jobs SET status=?,detail=?,found=?,accepted=0,attempts=?,
          not_before=?,lease_until=0,lease_owner='server-retry',lease_token='',updated_at=? WHERE id=?""",
          (state, detail, len(numbers), attempts, int(time.time())+delay if delay else 0, _utc(), identifier))
        if status != "error":
            # Reserve the next page first; candidate download backlog is durable
            # and can wait for subsequent claims when queue space is exhausted.
            if more and page < MAX_PAGES:
                prior = db.execute("""SELECT id FROM v301_search_jobs WHERE kind='inn' AND target=?
                  AND page=? AND (id=? OR parent_id=?) LIMIT 1""", (inn, page+1, root, root)).fetchone()
                if prior:
                    next_id = prior[0]
                else:
                    active = db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased','deferred')").fetchone()[0]
                    if active < MAX_QUEUE:
                        next_id = secrets.token_hex(12)
                        db.execute("""INSERT INTO v301_search_jobs
                          (id,kind,target,page,status,priority,created_at,updated_at,not_before,parent_id)
                          VALUES(?,'inn',?,?,'queued',12,?,?,?,?)""",
                          (next_id, inn, page+1, _utc(), _utc(), int(time.time())+60, root))
            drain_candidates(db)
            queued = db.execute("SELECT COUNT(*) FROM eis_inn_candidates WHERE source_job_id=? AND reg_job_id<>''", (identifier,)).fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM eis_inn_candidates WHERE root_job_id=? AND reg_job_id=''", (root,)).fetchone()[0]
        result = {"received": True, "id": identifier, "kind": "inn", "inn": inn, "page": page,
                  "status": state, "page_complete": status != "error", "search_complete": status != "error" and not more, "audit_complete": False,
                  "candidate_count": len(numbers), "candidates_queued": queued,
                  "pending_candidates": pending, "next_page_job_id": next_id,
                  "pagination_limited": bool(more and page >= MAX_PAGES),
                  "detail": detail, "buyer_inn_verified_by_search": False,
                  "requested_status": status, "error_code": _telemetry_error(error_code)}
        db.execute("INSERT INTO eis_server_inn_reports VALUES(?,?,?,?,?,?,?,?)",
                   (identifier, token, identity, inn, page, root, json.dumps(result, ensure_ascii=False), int(time.time())))
        _seen(db, identity, "idle", error_code)
        db.commit()
        _log_telemetry("report", **_report_telemetry(result, "inn"))
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def progress(ns, identifier):
    """Count verification separately from search; unresolved work is not endless pending."""
    db = _db(ns)
    try:
        row = db.execute("SELECT * FROM v301_search_jobs WHERE id=? AND kind='inn'", (identifier,)).fetchone()
        if row is None:
            return None
        root = root_job(db, row)
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='eis_inn_candidates'").fetchone()
        if not exists:
            return {"candidate_count": 0, "verification_pending": 0}
        has_purchases = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchases'").fetchone()
        buyer = "p.customer_inn" if has_purchases else "NULL"
        join = "LEFT JOIN purchases p ON p.reg_number=c.reg_number" if has_purchases else ""
        candidates = db.execute(f"""SELECT c.reg_job_id,j.status AS download_status,{buyer} AS buyer_inn
          FROM eis_inn_candidates c LEFT JOIN v301_search_jobs j ON j.id=c.reg_job_id {join}
          WHERE c.root_job_id=?""", (root,)).fetchall()
        result = {"candidate_count": len(candidates), "verified_matches": 0, "excluded_candidates": 0,
                  "verification_pending": 0, "unresolved_candidates": 0, "queued_downloads": 0,
                  "search_pages_pending": 0, "search_pages_completed": 0, "search_pages_failed": 0,
                  "pagination_limited": False}
        active = {"queued", "leased", "deferred"}
        for candidate in candidates:
            waiting = not candidate["reg_job_id"] or candidate["download_status"] in active
            if waiting:
                result["queued_downloads"] += 1
            known_inn = str(candidate["buyer_inn"] or "").strip()
            if known_inn == row["target"]:
                result["verified_matches"] += 1
            elif known_inn:
                result["excluded_candidates"] += 1
            elif waiting:
                result["verification_pending"] += 1
            else:
                result["unresolved_candidates"] += 1
        for page in db.execute("SELECT status FROM v301_search_jobs WHERE kind='inn' AND (id=? OR parent_id=?)", (root, root)):
            result["search_pages_pending" if page[0] in active else "search_pages_completed" if page[0] == "done" else "search_pages_failed"] += 1
        for item in db.execute("SELECT result_json FROM eis_server_inn_reports WHERE root_job_id=? AND page=?", (root, MAX_PAGES)):
            if json.loads(item[0]).get("pagination_limited"):
                result["pagination_limited"] = True
        return result
    finally:
        db.close()
