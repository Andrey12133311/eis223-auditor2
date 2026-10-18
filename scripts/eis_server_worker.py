"""Authenticated VPS intake jobs; shared download queue, fenced durable leases.

This server never contacts EIS. A separate VPS obtains official XML/originals
and uses the existing validated upload endpoints. Worker counters are telemetry,
not proof that documents were received or that a legal audit succeeded.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request
from v295_eis_originals import EXT, allowed_documents, root, saved_document, uid_from_url
from v301_search_jobs import MAX_QUEUE, REG, _db, _enqueue, _utc

LEASE_SECONDS = 900
HEALTH_SECONDS = 120
MAX_ATTEMPTS = 4
MAX_REPORT_BYTES = 12288
FEATURE = "eis-soap-vps-worker"
SUPPORTED_CAPABILITIES = frozenset(("inn_search",))


def init(ns):
    db = _db(ns)
    try:
        db.executescript("""
          CREATE TABLE IF NOT EXISTS eis_server_workers(
            worker_id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
            discovery_status TEXT NOT NULL DEFAULT '', discovery_error TEXT NOT NULL DEFAULT ''
          );
          CREATE TABLE IF NOT EXISTS eis_server_reports(
            job_id TEXT NOT NULL, lease_token TEXT NOT NULL,
            worker_id TEXT NOT NULL, reg_number TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at INTEGER NOT NULL,
            PRIMARY KEY(job_id,lease_token)
          );
          CREATE TABLE IF NOT EXISTS eis_server_backoff(
            job_id TEXT PRIMARY KEY, deferrals INTEGER NOT NULL DEFAULT 0
          );
          CREATE TABLE IF NOT EXISTS eis_server_discovery(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            accepted_at INTEGER NOT NULL DEFAULT 0, source_url TEXT NOT NULL DEFAULT ''
          );
        """)
        columns = {item[1] for item in db.execute("PRAGMA table_info(eis_server_workers)")}
        if "capabilities_json" not in columns:
            db.execute("ALTER TABLE eis_server_workers ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '[]'")
        from eis_inn_search import init_schema
        init_schema(db)
        db.commit()
    finally:
        db.close()


def authenticate(request):
    expected = os.environ.get("EIS223_UPLOAD_TOKEN", "").strip()
    provided = request.headers.get("x-eis223-upload-token", "")
    if len(expected) < 24:
        raise HTTPException(503, "Server upload key is not configured")
    if not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(401, "Unauthorized")
    if request.headers.get("origin"):
        raise HTTPException(403, "Use the server worker without a browser Origin")


def worker_id(value):
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid worker identifier")
    if value != parsed:
        raise HTTPException(422, "Use a canonical worker UUID")
    return parsed


def registry(value):
    if not isinstance(value, str) or not REG.fullmatch(value):
        raise HTTPException(422, "Invalid 223-FZ registry number")
    return value


def safe_text(value, limit=300):
    value = str(value or "")
    secret = os.environ.get("EIS223_UPLOAD_TOKEN", "")
    if secret:
        value = value.replace(secret, "[redacted]")
    value = re.sub(r"https?://\S+", "[source URL omitted]", value)
    return re.sub(r"[\x00-\x1f\x7f]", " ", value)[:limit]


async def body(request):
    pieces = []
    size = 0
    async for piece in request.stream():
        size += len(piece)
        if size > MAX_REPORT_BYTES:
            raise HTTPException(413, "Worker payload too large")
        pieces.append(piece)
    try:
        result = json.loads(b"".join(pieces))
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Invalid JSON")
    if not isinstance(result, dict):
        raise HTTPException(422, "Expected an object")
    return result


def capabilities(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",") if value else []
    if (not isinstance(value, (list, tuple)) or len(value) > 8
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_]{1,40}", item) for item in value)):
        raise HTTPException(422, "Invalid worker capabilities")
    return sorted(SUPPORTED_CAPABILITIES.intersection(value))


def _seen(db, identity, status="idle", detail="", discovery_status=None, discovery_error=None, supported=None):
    db.execute("""INSERT INTO eis_server_workers(worker_id,seen_at,status,detail)
      VALUES(?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET
      seen_at=excluded.seen_at,status=excluded.status,detail=excluded.detail""",
      (identity, int(time.time()), safe_text(status, 40), safe_text(detail)))
    if discovery_status is not None:
        db.execute("UPDATE eis_server_workers SET discovery_status=?,discovery_error=? WHERE worker_id=?",
                   (safe_text(discovery_status, 40), safe_text(discovery_error, 100), identity))
    if supported is not None:
        db.execute("UPDATE eis_server_workers SET capabilities_json=? WHERE worker_id=?",
                   (json.dumps(supported), identity))


def health(ns):
    db = _db(ns)
    try:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='eis_server_workers'").fetchone()
        rows = db.execute("SELECT * FROM eis_server_workers ORDER BY seen_at DESC").fetchall() if exists else []
        row = rows[0] if rows else None
        now = time.time()
        active = [item for item in rows if now-item["seen_at"] <= HEALTH_SECONDS and item["status"] != "stopped"]
        available = set()
        for item in active:
            try:
                declared = json.loads(item["capabilities_json"]) if "capabilities_json" in item.keys() else []
                if isinstance(declared, list):
                    available.update(value for value in declared if isinstance(value, str) and value in SUPPORTED_CAPABILITIES)
            except (ValueError, TypeError):
                pass
        return {"active": bool(active), "last_seen": row["seen_at"] if row else None,
                "status": row["status"] if row else "not_connected",
                "discovery_status": row["discovery_status"] if row else "not_started",
                "discovery_error": row["discovery_error"] if row else "",
                "capabilities": sorted(available), "inn_search_available": "inn_search" in available}
    finally:
        db.close()


def _public_job(row):
    base = {"id": row["id"], "kind": row["kind"], "lease_token": row["lease_token"],
            "lease_until": row["lease_until"], "attempts": row["attempts"]}
    if row["kind"] == "inn":
        return {**base, "target": row["target"], "inn": row["target"], "page": row["page"]}
    return {**base, "reg_number": row["target"], "metadata_only": row["parent_id"] == "v341-card-facts"}


def claim(ns, identity, limit=2, supported=()):
    from eis_inn_search import enqueue_pending_candidates, MAX_PAGES
    supported = capabilities(supported)
    db = _db(ns)
    now = int(time.time())
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""UPDATE v301_search_jobs SET status='queued',not_before=?,
          lease_until=0,lease_owner=CASE WHEN lease_owner<>'' THEN 'server-retry' ELSE '' END,lease_token='',updated_at=?
          WHERE status='leased' AND lease_until<? AND attempts<?""", (now+30, _utc(), now, MAX_ATTEMPTS))
        db.execute("""UPDATE v301_search_jobs SET status='error',
          detail='Истёк лимит повторных попыток загрузки ЕИС',lease_until=0,
          lease_owner='',lease_token='',updated_at=?
          WHERE status='leased' AND lease_until<? AND attempts>=?""", (_utc(), now, MAX_ATTEMPTS))
        enqueue_pending_candidates(db)
        capable = "inn_search" in supported
        # Re-deliver live claims only to a worker that still supports their kind.
        rows = list(db.execute("""SELECT * FROM v301_search_jobs WHERE
          status='leased' AND lease_owner=? AND lease_until>=?
          AND (kind='reg' OR (? AND kind='inn' AND page BETWEEN 1 AND ?))
          ORDER BY priority,created_at LIMIT ?""", (identity, now, capable, MAX_PAGES, limit)))
        inn_claimed = any(item["kind"] == "inn" for item in rows)
        # One public INN search page at a time, including across multiple VPSs.
        inn_busy = db.execute("SELECT 1 FROM v301_search_jobs WHERE kind='inn' AND status='leased' AND lease_until>=? LIMIT 1", (now,)).fetchone()
        if len(rows) < limit:
            # Reserve one available slot for a due INN page. Otherwise page
            # priority 12 would starve behind its own priority-8 candidates.
            # The loop skips a second INN so the remaining slot serves a reg.
            ready = db.execute("""SELECT * FROM v301_search_jobs
              WHERE (kind='reg' OR (? AND kind='inn' AND page BETWEEN 1 AND ?))
              AND status='queued' AND not_before<=?
              ORDER BY CASE WHEN kind='inn' THEN 0 ELSE 1 END,priority,created_at LIMIT ?""",
              (capable and not inn_busy, MAX_PAGES, now, MAX_QUEUE)).fetchall()
            for row in ready:
                if len(rows) >= limit:
                    break
                if row["kind"] == "inn" and inn_claimed:
                    continue
                token = secrets.token_hex(24)
                db.execute("""UPDATE v301_search_jobs SET status='leased',lease_until=?,
                  lease_owner=?,lease_token=?,attempts=attempts+1,updated_at=? WHERE id=?""",
                  (now+LEASE_SECONDS, identity, token, _utc(), row["id"]))
                rows.append(db.execute("SELECT * FROM v301_search_jobs WHERE id=?", (row["id"],)).fetchone())
                inn_claimed = inn_claimed or row["kind"] == "inn"
        _seen(db, identity, "running" if rows else "idle", supported=supported)
        db.commit()
        return [_public_job(row) for row in rows]
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _assert_lease(row, identity, token, reg=None):
    if row is None:
        raise HTTPException(404, "Unknown worker job")
    if (row["status"] != "leased" or row["lease_owner"] != identity
            or not isinstance(token, str) or not token
            or not hmac.compare_digest(row["lease_token"], token)
            or row["lease_until"] < int(time.time())
            or (reg is not None and row["target"] != reg)):
        raise HTTPException(409, "Lease expired or belongs to a different attempt")


def heartbeat(ns, data):
    identity = worker_id(data.get("worker_id"))
    supported = capabilities(data.get("capabilities"))
    db = _db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        lease_until = None
        current = data.get("current_job_id")
        if current:
            row = db.execute("SELECT * FROM v301_search_jobs WHERE id=?", (current,)).fetchone()
            _assert_lease(row, identity, data.get("lease_token"))
            lease_until = int(time.time()) + LEASE_SECONDS
            db.execute("UPDATE v301_search_jobs SET lease_until=?,updated_at=? WHERE id=?",
                       (lease_until, _utc(), current))
        _seen(db, identity, data.get("status") or ("running" if current else "idle"),
              data.get("detail"), data.get("discovery_status"), data.get("discovery_error"), supported)
        db.commit()
        return {"received": True, "lease_until": lease_until}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def evidence(reg, hashes=None):
    """Re-parse SHA-verified archives and verify every referenced original.

    Unsupported URLs, filename extensions, documentFS and unresolved historical
    references explicitly keep the download incomplete. The caller's counts
    cannot substitute for bytes saved by the authenticated intake endpoints.
    """
    from v294_eis_bridge import parse_notices, MAX_UPLOAD
    folder = root()/"eis223_official_archive_inbox"/reg
    records = []
    snapshots = []
    paths = [folder/(value+".json") for value in dict.fromkeys(hashes)] if hashes else sorted(folder.glob("*.json"))[-50:]
    for path in paths:
        try:
            snapshot = path.read_bytes()
            receipt = json.loads(snapshot)
            sha = receipt.get("archive_sha256", "")
            if (receipt.get("source") != "official_eis_soap_archive" or receipt.get("reg_number") != reg
                    or not re.fullmatch(r"[0-9a-f]{64}", sha)):
                continue
            if hashes and sha not in hashes:
                continue
            archive = folder/(sha+".zip")
            if archive.stat().st_size > MAX_UPLOAD:
                continue
            raw = archive.read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                continue
            parsed = parse_notices(raw)
            if not parsed or any(item.get("reg_number") != reg for item in parsed):
                continue
            records.extend(parsed)
            snapshots.append((path, snapshot))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if hashes and len(snapshots) != len(set(hashes)):
        return {"metadata_received": False, "expected": 0, "received": 0,
                "unresolved": 0, "complete": False, "reason": "archive_not_received"}
    known = allowed_documents(reg)
    refs = {}
    unresolved = 0
    all_guids = set()
    previous = set()
    for record in records:
        documents = record.get("documents", [])
        unresolved += len(record.get("unresolved_attachments", []))
        previous.update(record.get("previous_document_guids", []))
        for item in documents:
            uid = uid_from_url(item.get("url", ""))
            name = item.get("name", "")
            ext = Path(name).suffix.lower()
            if (not uid or not name or name != Path(name).name or ext not in EXT
                    or uid not in known or known[uid]["ext"] != ext):
                unresolved += 1
                continue
            refs[uid] = ext
            if item.get("guid"):
                all_guids.add(item["guid"])
    # References to a preceding revision can be resolved by prior persisted
    # receipts only when the corresponding allowed original really exists.
    if previous-all_guids:
        for path in sorted(folder.glob("*.json"))[:250]:
            try:
                record = json.loads(path.read_text())
                if record.get("source") != "official_eis_soap_archive" or record.get("reg_number") != reg:
                    continue
                digest = record.get("archive_sha256", "")
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    continue
                archive = folder/(digest+".zip")
                if archive.stat().st_size > MAX_UPLOAD:
                    continue
                raw = archive.read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    continue
                prior = parse_notices(raw)
                if any(item.get("reg_number") != reg for item in prior):
                    continue
                for notice in prior:
                    for item in notice.get("documents", []):
                        uid = uid_from_url(item.get("url", ""))
                        if (item.get("guid") in previous and uid in known
                                and item.get("name") == known[uid]["name"]):
                            refs[uid] = known[uid]["ext"]
                            all_guids.add(item["guid"])
            except (OSError, ValueError, TypeError):
                continue
    unresolved += len(previous-all_guids)
    received = 0
    for uid, ext in refs.items():
        saved = saved_document(reg, uid)
        if not saved or saved[1].get("extension") != ext:
            continue
        try:
            with saved[0].open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest == saved[1].get("sha256"):
                received += 1
        except OSError:
            continue
    try:
        stable = all(path.read_bytes() == snapshot for path, snapshot in snapshots)
    except OSError:
        stable = False
    return {"metadata_received": bool(records) and stable, "expected": len(refs)+unresolved,
            "received": received, "unresolved": unresolved,
            "complete": bool(records) and bool(refs) and not unresolved and received == len(refs) and stable,
            "reason": "unsupported_or_unresolved_attachment" if unresolved else
                      "no_original_references" if records and not refs else ""}


def report(ns, identifier, data):
    identity = worker_id(data.get("worker_id"))
    reg = registry(data.get("reg_number"))
    token = data.get("lease_token")
    status = data.get("status")
    if status not in ("completed", "partial", "no_data", "error"):
        raise HTTPException(422, "Invalid worker result")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{48}", token):
        raise HTTPException(422, "Invalid lease token")
    hashes = data.get("archive_sha256s") or ([data["archive_sha256"]] if data.get("archive_sha256") else [])
    if not isinstance(hashes, list) or len(hashes) > 25 or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
        raise HTTPException(422, "Invalid archive receipt references")
    retry = data.get("retry_after_seconds") or 0
    if type(retry) is not int or retry < 0 or retry > 86400:
        raise HTTPException(422, "Invalid retry delay")
    db = _db(ns)
    try:
        replay = db.execute("SELECT * FROM eis_server_reports WHERE job_id=? AND lease_token=?",
                            (identifier, token)).fetchone()
        if replay:
            if replay["worker_id"] != identity or replay["reg_number"] != reg:
                raise HTTPException(409, "Report does not match completed lease")
            return {**json.loads(replay["result_json"]), "replayed": True}
        row = db.execute("SELECT * FROM v301_search_jobs WHERE id=?", (identifier,)).fetchone()
        _assert_lease(row, identity, token, reg)
        if row["kind"] != "reg":
            raise HTTPException(409, "Use the INN report endpoint for this job")
        proof = evidence(reg, hashes) if hashes or status in ("completed", "partial") else {
            "metadata_received": False, "expected": 0, "received": 0, "unresolved": 0, "complete": False}
        metadata_only = row["parent_id"] == "v341-card-facts"
        metadata_stored = False
        if metadata_only and proof["metadata_received"]:
            importer = ns.get("_v379_import_soap_metadata")
            if callable(importer):
                try:
                    metadata_stored = importer(reg).get("stored") is True
                except Exception:
                    metadata_stored = False
        db.execute("BEGIN IMMEDIATE")
        # A concurrent report/lease reclaim may have happened during hashing.
        replay = db.execute("SELECT * FROM eis_server_reports WHERE job_id=? AND lease_token=?",
                            (identifier, token)).fetchone()
        if replay:
            db.commit()
            return {**json.loads(replay["result_json"]), "replayed": True}
        row = db.execute("SELECT * FROM v301_search_jobs WHERE id=?", (identifier,)).fetchone()
        _assert_lease(row, identity, token, reg)
        metadata_only = row["parent_id"] == "v341-card-facts"
        complete = (metadata_only and status != "error" and proof["metadata_received"] and metadata_stored) or (
            status in ("completed", "partial") and not metadata_only and proof["complete"])
        attempt = row["attempts"]
        error_code = safe_text(data.get("error_code"), 80)
        real_progress = proof["received"] > row["accepted"]
        if real_progress:
            attempt = 0
            db.execute("DELETE FROM eis_server_backoff WHERE job_id=?", (identifier,))
        transient = (error_code in ("eis_http_403", "eis_http_429", "eis_connection_error",
                                  "site_connection_error", "site_http_429", "site_http_507")
                     or re.fullmatch(r"(?:eis|site)_http_5[0-9]{2}", error_code) is not None)
        if not complete and retry and transient and not proof.get("unresolved"):
            # Refund only a bounded number of explicit infrastructure failures.
            # A caller-supplied retry delay alone must not reset the budget.
            previous = db.execute("SELECT deferrals FROM eis_server_backoff WHERE job_id=?", (identifier,)).fetchone()
            deferrals = previous[0] if previous else 0
            if deferrals < 12:
                attempt = max(0, attempt-1)
                db.execute("INSERT OR REPLACE INTO eis_server_backoff VALUES(?,?)", (identifier, deferrals+1))
        state = "done" if complete else "queued" if attempt < MAX_ATTEMPTS else "partial"
        delay = min(86400, max(60, retry or 900)) if state == "queued" else 0
        if complete:
            detail = ("Официальные XML ЕИС получены; сведения карточки обновлены" if metadata_only else
                      "Все учтённые оригиналы ЕИС сохранены; переданы на проверку на сервере")
        elif proof.get("unresolved"):
            detail = "Оригиналы ЕИС загружены не полностью: есть неподдерживаемые или неразрешённые ссылки"
        elif proof.get("metadata_received"):
            detail = f"XML ЕИС сохранены; оригиналов получено {proof['received']} из {proof['expected']}"
        else:
            detail = "Получение данных ЕИС не подтверждено" + (": "+error_code if error_code else "")
        result = {"received": True, "id": identifier, "reg_number": reg, "status": state,
                  "download_complete": complete, "audit_complete": False,
                  "metadata_received": proof["metadata_received"], "metadata_stored": metadata_stored,
                  "originals_expected": proof["expected"], "originals_received": proof["received"],
                  "unresolved_attachment_count": proof["unresolved"], "detail": detail}
        db.execute("""UPDATE v301_search_jobs SET status=?,detail=?,found=?,accepted=?,
          not_before=?,lease_until=0,lease_owner='server-retry',lease_token='',updated_at=?,attempts=? WHERE id=?""",
          (state, detail, int(proof["metadata_received"]), max(row["accepted"], proof["received"]),
           int(time.time())+delay if delay else 0, _utc(), attempt, identifier))
        db.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)",
                   (identifier, token, identity, reg, json.dumps(result, ensure_ascii=False), int(time.time())))
        _seen(db, identity, "idle", error_code)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def discovered(ns, data):
    identity = worker_id(data.get("worker_id"))
    numbers = data.get("reg_numbers")
    if not isinstance(numbers, list) or len(numbers) > 40:
        raise HTTPException(422, "Expected at most 40 official registry numbers")
    numbers = list(dict.fromkeys(registry(number) for number in numbers))
    source = data.get("source_url")
    try:
        parsed = urlsplit(source)
        valid = (isinstance(source, str) and len(source) <= 2000 and parsed.scheme == "https"
                 and parsed.hostname == "zakupki.gov.ru" and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and parsed.path == "/epz/order/extendedsearch/results.html"
                 and parse_qs(parsed.query).get("fz223") == ["on"])
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise HTTPException(422, "Expected the actual official 223-FZ search URL")
    db = _db(ns)
    created = []
    skipped = []
    now = int(time.time())
    try:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT accepted_at FROM eis_server_discovery WHERE singleton=1").fetchone()
        if previous and now-previous[0] < 300:
            raise HTTPException(429, "Discovery is limited to one cycle per five minutes",
                                headers={"Retry-After": str(300-(now-previous[0]))})
        budget = max(0, MAX_QUEUE-8-db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased','deferred')").fetchone()[0])
        for reg in numbers:
            old = db.execute("""SELECT id,status FROM v301_search_jobs WHERE kind='reg' AND target=?
              AND (status IN ('queued','leased','deferred') OR updated_at>=?) LIMIT 1""",
              (reg, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now-86400)))).fetchone()
            if old or budget <= 0:
                skipped.append(reg)
                continue
            identifier = secrets.token_hex(12)
            db.execute("""INSERT INTO v301_search_jobs(id,kind,target,status,priority,parent_id,created_at,updated_at)
              VALUES(?,'reg',?,'queued',10,'server-discovery',?,?)""", (identifier, reg, _utc(), _utc()))
            created.append(reg)
            budget -= 1
        db.execute("INSERT OR REPLACE INTO eis_server_discovery VALUES(1,?,?)", (now, source))
        _seen(db, identity, "idle", discovery_status="ok", discovery_error="")
        db.commit()
        return {"accepted": True, "created": created, "skipped": skipped, "manual_records_created": 0}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def inn_progress(ns, job_id):
    from eis_inn_search import progress
    return progress(ns, job_id)


def install(ns):
    init(ns)
    app = ns["app"]

    @app.get("/eis223/worker-version")
    async def version():
        vps_only = os.environ.get("EIS223_SOURCE_MODE") == "OFFICIAL_EIS_VPS_ONLY"
        return {"feature": FEATURE, "protocol_version": 1,
                "intake_mode": "vps_only" if vps_only else "server_and_browser",
                "browser_collector_enabled": not vps_only,
                "supported_capabilities": sorted(SUPPORTED_CAPABILITIES)}

    @app.get("/api/eis-worker/jobs/next")
    async def next_jobs(request: Request, worker_id: str, limit: int = 2, capabilities: str = ""):
        authenticate(request)
        identity = globals()["worker_id"](worker_id)
        if not 1 <= limit <= 2:
            raise HTTPException(422, "Claim one or two jobs")
        supported = globals()["capabilities"](capabilities)
        return {"jobs": await asyncio.to_thread(claim, ns, identity, limit, supported), "poll_seconds": 15}

    @app.post("/api/eis-worker/jobs")
    async def enqueue(request: Request):
        authenticate(request)
        data = await body(request)
        reg = registry(data.get("reg_number"))
        if data.get("source") != "worker_bootstrap":
            raise HTTPException(422, "Invalid bootstrap source")
        row, created = await asyncio.to_thread(_enqueue, ns, "reg", reg, 1, 9, "worker-bootstrap", False)
        return {"job": {"id": row["id"], "reg_number": reg, "status": row["status"]}, "created": created}

    @app.post("/api/eis-worker/jobs/{identifier}/report")
    async def receive_report(identifier: str, request: Request):
        authenticate(request)
        if not re.fullmatch(r"[0-9a-f]{24}", identifier):
            raise HTTPException(422, "Invalid job identifier")
        result = await asyncio.to_thread(report, ns, identifier, await body(request))
        if not result.get("replayed") and result["originals_received"] > 0:
            enqueue_review = ns.get("_v295_enqueue_review")
            if callable(enqueue_review):
                try:
                    result["review_status"] = enqueue_review(result["reg_number"]).get("status")
                except Exception:
                    result["review_status"] = "pending_retry"
        return result

    @app.post("/api/eis-worker/inn-jobs/{identifier}/report")
    async def receive_inn_report(identifier: str, request: Request):
        authenticate(request)
        if not re.fullmatch(r"[0-9a-f]{24}", identifier):
            raise HTTPException(422, "Invalid job identifier")
        from eis_inn_search import report as inn_report
        return await asyncio.to_thread(inn_report, ns, identifier, await body(request))

    @app.post("/api/eis-worker/heartbeat")
    async def receive_heartbeat(request: Request):
        authenticate(request)
        return await asyncio.to_thread(heartbeat, ns, await body(request))

    @app.post("/api/eis-worker/discovered")
    async def receive_discovered(request: Request):
        authenticate(request)
        return await asyncio.to_thread(discovered, ns, await body(request))

    @app.get("/api/eis-worker/status")
    async def status(request: Request):
        authenticate(request)
        db = _db(ns)
        try:
            counts = {row[0]: row[1] for row in db.execute("SELECT status,COUNT(*) FROM v301_search_jobs GROUP BY status")}
        finally:
            db.close()
        return {"feature": FEATURE, "protocol_version": 1, "server": health(ns), "queue": counts,
                "supported_capabilities": sorted(SUPPORTED_CAPABILITIES)}

    print("EIS_SERVER_WORKER_READY token_auth=True browser_origin_required=False leased_queue=True", flush=True)
