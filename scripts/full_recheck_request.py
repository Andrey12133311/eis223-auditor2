"""Explicit, frozen full-dashboard recheck requests; no ordinary queue resets.

This module owns only additive, request-scoped operational SQLite tables. Request IDs are supplied by an
explicit operation/configuration, never inferred from an engine revision or a
deployment. All runtime writes use the caller's transaction and never commit.
Only ``create_request`` owns a write transaction and requires an idle connection.
``inventory`` uses the caller's transaction or a short deferred read snapshot.

The restored dashboard getter is materialized once *before* taking a writer
lock. Its capture timestamp and the subsequent atomic database snapshot time
are recorded separately; they are deliberately not claimed to be simultaneous.
A failed/budget-exceeded capture creates no request or partial membership.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from datetime import datetime

CAMPAIGN = "2026-10-15-main-1253-identity-and-legal-v342"
MAX_SNAPSHOT_ROWS = 20000
MAX_SNAPSHOT_SECONDS = 10.0
MAX_CANDIDATES = 128
STATES = ("pending", "running", "completed", "waiting_originals", "retry", "blocked")
METRICS = ("documents_found", "documents_read", "documents_unreadable", "verified_original_documents",
           "original_expected", "original_received", "new_findings", "new_checks")
NULLABLE_METRICS = frozenset(("original_expected", "original_received"))
REASONS = frozenset(("", "noncanonical_registration", "missing_purchase", "waiting_originals",
    "incomplete_read", "source_changed", "source_unavailable", "engine_changed", "engine_unavailable",
    "lease_expired", "interrupted", "sqlite_busy", "analysis_error", "unsupported_original", "blocked",
    "missing_originals", "partial_or_unverified_documents", "interrupted_attempt",
    "no_readable_documents", "no_verified_documents", "unverified_readable_documents",
    "unreadable_documents", "source_incomplete", "unknown_original_coverage"))
_REG = re.compile(r"3[0-9]{10}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_.:/+\-]{1,256}\Z")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,128}\Z")


class SnapshotError(RuntimeError):
    """The complete population could not be captured within its fixed budget."""


class StaleClaim(LookupError):
    """The exact attempt no longer has permission to persist results."""


class TargetConflict(RuntimeError):
    """A release cannot silently replace an equal or newer semantic target."""


class Claim(str):
    """A registry string carrying a request/attempt/lease fencing identity."""
    def __new__(cls, reg, request_id, attempt_id, lease_token, general_token=""):
        value = super().__new__(cls, reg)
        value.request_id = request_id
        value.attempt_id = attempt_id
        value.lease_token = lease_token
        value.general_token = general_token
        return value


def initialize(c):
    """Idempotently add operational tables/indexes; never rebuild or commit."""
    c.execute("""CREATE TABLE IF NOT EXISTS full_recheck_requests(
        request_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
        source_captured_at REAL NOT NULL, db_snapshot_at REAL NOT NULL,
        expected_count INTEGER NOT NULL, actual_count INTEGER NOT NULL, manual_excluded_count INTEGER NOT NULL, verified_not_saved_count INTEGER NOT NULL,
        membership_digest TEXT NOT NULL, campaign TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('active','completed')))
    """)
    c.execute("""CREATE TABLE IF NOT EXISTS full_recheck_members(
        request_id TEXT NOT NULL, reg TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','running','completed','waiting_originals','retry','blocked')),
        reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL,
        next_retry_at REAL NOT NULL DEFAULT 0,
        lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
        attempt_id TEXT NOT NULL DEFAULT '', general_token TEXT NOT NULL DEFAULT '',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        source_version TEXT NOT NULL DEFAULT '', engine_signature TEXT NOT NULL DEFAULT '',
        receipt_attempt_id TEXT NOT NULL DEFAULT '', result_token TEXT NOT NULL DEFAULT '',
        result_source_version TEXT NOT NULL DEFAULT '', result_engine_signature TEXT NOT NULL DEFAULT '',
        full_verified_read INTEGER NOT NULL DEFAULT 0 CHECK(full_verified_read IN (0,1)),
        result_committed_at REAL,
        documents_found INTEGER NOT NULL DEFAULT 0, documents_read INTEGER NOT NULL DEFAULT 0,
        documents_unreadable INTEGER NOT NULL DEFAULT 0, original_expected INTEGER, original_received INTEGER,
        verified_original_documents INTEGER NOT NULL DEFAULT 0,
        new_findings INTEGER NOT NULL DEFAULT 0, new_checks INTEGER NOT NULL DEFAULT 0,
        total_new_findings INTEGER NOT NULL DEFAULT 0, total_new_checks INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(request_id,reg),
        FOREIGN KEY(request_id) REFERENCES full_recheck_requests(request_id))
    """)
    c.execute("CREATE INDEX IF NOT EXISTS full_recheck_due ON full_recheck_members(request_id,status,next_retry_at,reg)")
    c.execute("CREATE INDEX IF NOT EXISTS full_recheck_reserved ON full_recheck_members(reg,status)")
    c.execute("""CREATE TABLE IF NOT EXISTS full_recheck_targets(
        request_id TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation>0),
        engine_signature TEXT NOT NULL, activated_at REAL NOT NULL,
        FOREIGN KEY(request_id) REFERENCES full_recheck_requests(request_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS full_recheck_receipts(
        request_id TEXT NOT NULL, reg TEXT NOT NULL, attempt_id TEXT NOT NULL,
        lease_token TEXT NOT NULL, general_token TEXT NOT NULL,
        result_token TEXT NOT NULL, source_version TEXT NOT NULL, engine_signature TEXT NOT NULL,
        full_verified_read INTEGER NOT NULL, result_committed_at REAL NOT NULL,
        metrics_json TEXT NOT NULL, PRIMARY KEY(request_id,reg,attempt_id))""")


def engine_target(c, request_id):
    if not _exists(c, "full_recheck_targets"):
        return None
    return _dict_one(c.execute("SELECT * FROM full_recheck_targets WHERE request_id=?", (_request_id(request_id),)))


def _target_accepts(c, request_id, engine_signature, generation):
    target = engine_target(c, request_id)
    return target is None or (target["engine_signature"] == engine_signature and target["generation"] == generation)


def activate_target(c, request_id, *, generation, engine_signature, now=None):
    """CAS an explicitly authorized semantic correction, never a deployment ID.

    Generation is monotonic by policy; hashes have no ordering. Equal generation
    plus a different signature fails closed. An older process cannot downgrade.
    Only the enabled, already frozen request is retargeted; membership is never
    reread. A legacy completed flag cannot suppress this authorized catch-up.
    """
    _transaction(c)
    _request_id(request_id)
    _token(engine_signature)
    if isinstance(generation, bool) or not isinstance(generation, int) or not 1 <= generation <= 1000000:
        raise ValueError("A bounded positive semantic correction generation is required")
    if not c.execute("SELECT 1 FROM full_recheck_requests WHERE request_id=?", (request_id,)).fetchone():
        raise TargetConflict("The frozen request does not exist")
    target = engine_target(c, request_id)
    if target and (generation < target["generation"] or
                   (generation == target["generation"] and engine_signature != target["engine_signature"])):
        raise TargetConflict("The durable engine target is newer or incompatible")
    if target and generation == target["generation"]:
        return False
    c.execute("""INSERT INTO full_recheck_targets VALUES(?,?,?,?)
        ON CONFLICT(request_id) DO UPDATE SET generation=excluded.generation,
        engine_signature=excluded.engine_signature,activated_at=excluded.activated_at
        WHERE excluded.generation>full_recheck_targets.generation""",
        (request_id, generation, engine_signature, _time(now)))
    c.execute("UPDATE full_recheck_requests SET status='active' WHERE request_id=?", (request_id,))
    return True


def _archive_receipt(c, row):
    """Immutable first receipt per attempt; no source text or legal findings."""
    if not _exists(c, "full_recheck_receipts") or not row["result_token"] or not row["receipt_attempt_id"] or row["result_committed_at"] is None:
        return
    c.execute("""INSERT OR IGNORE INTO full_recheck_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (row["request_id"], row["reg"], row["receipt_attempt_id"], row["lease_token"], row["general_token"],
         row["result_token"], row["result_source_version"], row["result_engine_signature"],
         row["full_verified_read"], row["result_committed_at"],
         json.dumps({key: row[key] for key in METRICS}, sort_keys=True, separators=(",", ":"))))


def reopen_completed(c, request_id, *, engine_signature, generation, limit=32, now=None):
    """Bounded completed-only repair; leave every pending/running lease alone.

    Run on every enabled turn, even if old rolling code sets legacy status to
    completed. Copy successful history atomically before reopening, so an old
    claim implementation cannot erase it. No new request, quota or rule epoch.
    """
    _transaction(c)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("Catch-up page limit is out of bounds")
    if not _target_accepts(c, request_id, engine_signature, generation) or engine_target(c, request_id) is None:
        return 0
    cursor = c.execute("""SELECT * FROM full_recheck_members WHERE request_id=? AND status='completed'
        AND result_engine_signature<>? ORDER BY reg LIMIT ?""", (request_id, engine_signature, limit))
    rows = [dict(zip((col[0] for col in cursor.description), row)) for row in cursor.fetchall()]
    stamp = _time(now)
    for row in rows:
        _archive_receipt(c, row)
        c.execute("""UPDATE full_recheck_members SET status='retry',reason='engine_changed',
            updated_at=?,next_retry_at=0 WHERE request_id=? AND reg=? AND status='completed'
            AND result_engine_signature<>?""", (stamp, request_id, row["reg"], engine_signature))
    if rows:
        c.execute("UPDATE full_recheck_requests SET status='active' WHERE request_id=?", (request_id,))
    return len(rows)


def _time(value=None):
    value = time.time() if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("A finite timestamp is required")
    return float(value)


def _request_id(value):
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ValueError("An explicit bounded request ID is required")
    return value


def _token(value):
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError("A bounded opaque token is required")
    return value


def _transaction(c):
    if not c.in_transaction:
        raise ValueError("Caller must open a SQLite write transaction")


def _exists(c, table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _columns(c, table, required):
    actual = {row[1] for row in c.execute("PRAGMA table_info(" + table + ")")}
    if not set(required) <= actual:
        raise SnapshotError("Required source schema unavailable: " + table)
    return actual


def _dict_one(cursor):
    row = cursor.fetchone()
    return dict(zip((col[0] for col in cursor.description), row)) if row is not None else None


def inventory(c, request_id):
    """Privacy-safe aggregate: no registry values, titles, source text or owners."""
    # Separate autocommit SELECTs can straddle a receipt completion, making
    # current_completed exceed the earlier completed count. A deferred read
    # snapshot keeps every aggregate consistent without reserving a writer.
    # Some callers already own a write/read transaction: never finish theirs.
    owns_snapshot = not c.in_transaction
    if owns_snapshot:
        c.execute("BEGIN")
    try:
        return _inventory(c, request_id)
    finally:
        if owns_snapshot:
            c.rollback()


def _inventory(c, request_id):
    request = _dict_one(c.execute("SELECT * FROM full_recheck_requests WHERE request_id=?", (_request_id(request_id),)))
    if request is None:
        return None
    counts = {state: 0 for state in STATES}
    counts.update(dict(c.execute("SELECT status,COUNT(*) FROM full_recheck_members WHERE request_id=? GROUP BY status", (request_id,))))
    reasons = dict(c.execute("SELECT reason,COUNT(*) FROM full_recheck_members WHERE request_id=? AND reason<>'' GROUP BY reason", (request_id,)))
    sums = c.execute("SELECT " + ",".join("COALESCE(SUM(" + key + "),0)" for key in METRICS)
        + " FROM full_recheck_members WHERE request_id=?", (request_id,)).fetchone()
    unknown = {key: c.execute("SELECT COUNT(*) FROM full_recheck_members WHERE request_id=? AND " + key + " IS NULL", (request_id,)).fetchone()[0]
        for key in sorted(NULLABLE_METRICS)}
    # Extend the existing request-scoped scan; attempts are durable claims,
    # not successful reads, and retries do not increase unique membership.
    totals = c.execute("SELECT COALESCE(SUM(total_new_findings),0),COALESCE(SUM(total_new_checks),0),"
        "COUNT(CASE WHEN attempt_count>0 THEN 1 END),COALESCE(SUM(attempt_count),0) "
        "FROM full_recheck_members WHERE request_id=?", (request_id,)).fetchone()
    request.update(states=counts, reasons=reasons, metrics=dict(zip(METRICS, sums)), metrics_unknown=unknown,
        metrics_basis="latest_recorded_attempt_per_member", committed_new_findings=totals[0], committed_new_checks=totals[1],
        unique_attempted=totals[2], total_attempts=totals[3],
        expected_matches_actual=request["expected_count"] == request["actual_count"])
    target = engine_target(c, request_id)
    if target:
        current = c.execute("""SELECT COUNT(*) FROM full_recheck_members WHERE request_id=?
            AND status='completed' AND full_verified_read=1 AND result_committed_at IS NOT NULL
            AND receipt_attempt_id=attempt_id AND result_token<>''
            AND source_version<>'' AND source_version=result_source_version
            AND engine_signature=result_engine_signature AND result_engine_signature=?""",
            (request_id, target["engine_signature"])).fetchone()[0]
        request.update(legacy_status=request["status"], target_generation=target["generation"],
            target_engine_signature=target["engine_signature"], target_activated_at=target["activated_at"],
            current_completed=current, current_pending=request["actual_count"]-current,
            stale_completed=counts["completed"]-current,
            coverage_basis="full_committed_receipt_for_target_engine",
            status="completed" if current == request["actual_count"] else "active")
    return request


def create_request(c, request_id, verified_getter, *, expected_count=3058, campaign=CAMPAIGN,
                   max_rows=MAX_SNAPSHOT_ROWS, max_seconds=5.0):
    """Freeze (getter ∪ current-campaign queue) ∩ saved purchases − manual inputs.

    ``max_rows`` bounds total materialized source rows (duplicates included).
    The trusted getter must return/iterate promptly; an elapsed deadline aborts
    after return and is never mistaken for a partial success. SQL execution is
    additionally interrupted by a progress handler. Use a dedicated connection.
    A repeated request ID returns its original membership without re-enumerating.
    """
    _request_id(request_id)
    if c.in_transaction:
        raise ValueError("Request creation requires an idle dedicated connection")
    initialize(c)
    existing = inventory(c, request_id)
    if existing is not None:
        return existing
    if not callable(verified_getter):
        raise SnapshotError("Dashboard membership getter unavailable")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError("Expected count must be a nonnegative integer")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= MAX_SNAPSHOT_ROWS:
        raise ValueError("Snapshot row budget is out of bounds")
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not 0 < max_seconds <= MAX_SNAPSHOT_SECONDS:
        raise ValueError("Snapshot time budget is out of bounds")
    _token(campaign)
    created = _time()
    deadline = time.monotonic() + max_seconds
    scanned = 0

    def check():
        if time.monotonic() >= deadline:
            raise SnapshotError("Complete membership capture exceeded its time budget")

    def consume(values):
        nonlocal scanned
        result = set()
        for value in values:
            check()
            scanned += 1
            if scanned > max_rows:
                raise SnapshotError("Complete membership capture exceeded its row budget")
            value = str(value)
            if len(value) > 256:
                raise SnapshotError("Membership value exceeds its bounded representation")
            result.add(value)
        check()
        return result

    # An opaque restored getter can open its own database connection. Never
    # invoke it under a writer lock, and do not imply an atomic cross-getter read.
    try:
        raw = verified_getter()
        if raw is None or isinstance(raw, (str, bytes, dict)):
            raise SnapshotError("Dashboard getter did not return a complete collection")
        verified = consume(raw)
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError("Dashboard membership capture failed") from exc
    source_captured = _time()
    previous_timeout = c.execute("PRAGMA busy_timeout").fetchone()[0]
    c.execute("PRAGMA busy_timeout=" + str(max(1, int((deadline - time.monotonic()) * 1000))))
    c.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        check()
        c.execute("BEGIN IMMEDIATE")
        # Concurrent creation of the same explicit ID preserves the first epoch.
        existing = inventory(c, request_id)
        if existing is not None:
            c.rollback()
            return existing
        _columns(c, "purchases", ("reg_number",))
        _columns(c, "v332_audit_queue", ("campaign", "reg"))
        _columns(c, "v319_manual_inputs", ("reg",))
        snapshot_at = _time()
        queue = consume(row[0] for row in c.execute("""SELECT q.reg FROM v332_audit_queue q
            JOIN purchases p ON p.reg_number=q.reg WHERE q.campaign=? ORDER BY q.reg LIMIT ?""",
            (campaign, max_rows + 1)))
        manual = consume(row[0] for row in c.execute("SELECT reg FROM v319_manual_inputs WHERE reg IS NOT NULL LIMIT ?", (max_rows + 1,)))
        union = sorted(verified | queue)
        # The actual dashboard enumerates saved purchases, not source-only IDs.
        present = set()
        for offset in range(0, len(union), 400):
            chunk = union[offset:offset + 400]
            present.update(consume(row[0] for row in c.execute("SELECT reg_number FROM purchases WHERE reg_number IN ("
                + ",".join("?" for _ in chunk) + ")", chunk)))
        members = sorted(present - manual)
        digest = hashlib.sha256(json.dumps(members, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()
        c.execute("""INSERT INTO full_recheck_requests(request_id,created_at,source_captured_at,db_snapshot_at,
            expected_count,actual_count,manual_excluded_count,verified_not_saved_count,membership_digest,campaign,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (request_id, created, source_captured, snapshot_at, expected_count, len(members), len(present & manual), len(verified - present), digest,
             campaign, "active" if members else "completed"))
        rows = []
        for reg in members:
            reason = "noncanonical_registration" if not _REG.fullmatch(reg) else ""
            rows.append((request_id, reg, "blocked" if reason else "pending", reason, snapshot_at))
        c.executemany("INSERT INTO full_recheck_members(request_id,reg,status,reason,updated_at) VALUES(?,?,?,?,?)", rows)
        check()
        result = inventory(c, request_id)
        check()
        c.commit()
        return result
    except BaseException as exc:
        if c.in_transaction:
            c.rollback()
        if isinstance(exc, sqlite3.OperationalError) and time.monotonic() >= deadline:
            raise SnapshotError("Complete membership capture exceeded its SQL/time budget") from exc
        raise
    finally:
        c.set_progress_handler(None, 0)
        c.execute("PRAGMA busy_timeout=" + str(previous_timeout))


def has_active_claim(c, reg, *, now=None):
    """An expired running lease still blocks until execution-lock-safe recovery."""
    if not _exists(c, "full_recheck_members"):
        return False
    return c.execute("SELECT 1 FROM full_recheck_members WHERE reg=? AND status='running' LIMIT 1", (str(reg),)).fetchone() is not None


def _ordinary_blockers(c):
    """Fixed SQL fragments; tolerate absent engines, fail closed on bad schema."""
    clauses = []
    if _exists(c, "v332_audit_queue"):
        _columns(c, "v332_audit_queue", ("reg", "state"))
        clauses.append("NOT EXISTS(SELECT 1 FROM v332_audit_queue q WHERE q.reg=m.reg AND q.state='running')")
    if _exists(c, "thematic_items"):
        columns = _columns(c, "thematic_items", ("batch_id", "reg", "status"))
        _columns(c, "thematic_batches", ("id", "status"))
        reserved = " OR (t.status='queued' AND COALESCE(t.reservation_token,'')<>'')" if "reservation_token" in columns else ""
        # Stopping/deleting a campaign preserves its historical reservation.
        # Its existing start/persist fence forbids further writes, so only a
        # still-active campaign can block this independent requested review.
        clauses.append("NOT EXISTS(SELECT 1 FROM thematic_items t JOIN thematic_batches b ON b.id=t.batch_id "
                       "WHERE b.status='active' AND t.reg=m.reg AND (t.status='running'" + reserved + "))")
    return clauses


def select_candidates(c, request_id, *, limit=8, now=None, after_reg="", engine_signature=None, generation=None):
    """Read a bounded eligible page without reserving or resetting any queue."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("Candidate limit is out of bounds")
    if not _target_accepts(c, request_id, engine_signature, generation):
        return []
    clauses = _ordinary_blockers(c)
    clauses.append("NOT EXISTS(SELECT 1 FROM full_recheck_members active WHERE active.reg=m.reg AND active.status='running')")
    sql = """SELECT m.reg FROM full_recheck_members m JOIN full_recheck_requests r ON r.request_id=m.request_id
        WHERE m.request_id=? AND (r.status='active' OR EXISTS(SELECT 1 FROM full_recheck_targets t WHERE t.request_id=r.request_id)) AND m.status IN ('pending','retry','waiting_originals')
        AND m.next_retry_at<=? AND m.reg>? AND """ + " AND ".join(clauses) + " ORDER BY m.reg LIMIT ?"
    return [row[0] for row in c.execute(sql, (_request_id(request_id), _time(now), after_reg, limit))]


def _lease_seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 3600:
        raise ValueError("Lease duration must be between 1 and 3600 seconds")
    return float(value)


def _claim_where(claim):
    if not isinstance(claim, Claim):
        raise ValueError("An exact full-recheck Claim is required")
    return (claim.request_id, str(claim), claim.attempt_id, claim.lease_token)


def _updated_epoch(value):
    try:
        if isinstance(value, (int, float)):
            return _time(value)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def claim_member(c, request_id, reg, *, now=None, lease_seconds=300, general_token=None, engine_signature=None, generation=None):
    """CAS one member. A supplied normal claim must be fresh for this request.

    The caller must hold its per-reg execution lock plus a write transaction.
    No pre-request or unidentifiable normal work can acknowledge this request.
    """
    _transaction(c)
    stamp, duration = _time(now), _lease_seconds(lease_seconds)
    _request_id(request_id)
    reg = str(reg)
    if not _target_accepts(c, request_id, engine_signature, generation):
        return None
    if not _REG.fullmatch(reg) or has_active_claim(c, reg):
        return None
    request = c.execute("SELECT db_snapshot_at,campaign FROM full_recheck_requests r WHERE request_id=? AND (status='active' OR EXISTS(SELECT 1 FROM full_recheck_targets t WHERE t.request_id=r.request_id))", (request_id,)).fetchone()
    if request is None:
        return None
    if not c.execute("SELECT 1 FROM purchases WHERE reg_number=?", (reg,)).fetchone():
        c.execute("UPDATE full_recheck_members SET status='blocked',reason='missing_purchase',updated_at=? "
            "WHERE request_id=? AND reg=? AND status IN ('pending','retry','waiting_originals')", (stamp, request_id, reg))
        return None
    # Active thematic claims block, including reserved queued work.
    clauses = _ordinary_blockers(c)
    if _exists(c, "thematic_items"):
        theme_clause = next(clause for clause in clauses if "thematic_items" in clause)
        if not c.execute("SELECT 1 FROM (SELECT ? AS reg) m WHERE " + theme_clause, (reg,)).fetchone():
            return None
    if _exists(c, "v332_audit_queue"):
        columns = {row[1] for row in c.execute("PRAGMA table_info(v332_audit_queue)")}
        if general_token is None:
            if c.execute("SELECT 1 FROM v332_audit_queue WHERE reg=? AND state='running' LIMIT 1", (reg,)).fetchone():
                return None
        else:
            _token(general_token)
            if not {"campaign", "updated_at", "reservation_token"} <= columns:
                return None
            active = c.execute("SELECT campaign,reservation_token,updated_at FROM v332_audit_queue WHERE reg=? AND state='running'", (reg,)).fetchall()
            if len(active) != 1 or active[0][0] != request[1] or active[0][1] != general_token:
                return None
            updated = _updated_epoch(active[0][2])
            if updated is None or updated < request[0]:
                return None
    elif general_token is not None:
        return None
    prior = _dict_one(c.execute("SELECT * FROM full_recheck_members WHERE request_id=? AND reg=?", (request_id, reg)))
    if prior:
        _archive_receipt(c, prior)
    attempt, lease = uuid.uuid4().hex, uuid.uuid4().hex
    changed = c.execute("""UPDATE full_recheck_members SET status='running',reason='',updated_at=?,
        next_retry_at=0,lease_token=?,lease_until=?,attempt_id=?,general_token=?,attempt_count=attempt_count+1,
        source_version='',engine_signature='',receipt_attempt_id='',result_token='',result_source_version='',
        result_engine_signature='',full_verified_read=0,result_committed_at=NULL
        WHERE request_id=? AND reg=? AND status IN ('pending','retry','waiting_originals') AND (?<>'' OR next_retry_at<=?)""",
        (stamp, lease, stamp + duration, attempt, general_token or "", request_id, reg, general_token or "", stamp)).rowcount
    return Claim(reg, request_id, attempt, lease, general_token or "") if changed == 1 else None


def require_claim(c, claim, *, now=None):
    """Fence persistence by exact running attempt and an unexpired deadline."""
    identity = _claim_where(claim)
    row = _dict_one(c.execute("""SELECT * FROM full_recheck_members WHERE request_id=? AND reg=?
        AND attempt_id=? AND lease_token=? AND status='running' AND lease_until>?""", (*identity, _time(now))))
    if row is None:
        raise StaleClaim("Full-recheck lease is expired or superseded")
    return row


def renew_lease(c, claim, *, now=None, lease_seconds=300):
    _transaction(c)
    stamp, duration = _time(now), _lease_seconds(lease_seconds)
    require_claim(c, claim, now=stamp)
    c.execute("UPDATE full_recheck_members SET lease_until=MAX(lease_until,?),updated_at=? "
        "WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=? AND status='running'",
        (stamp + duration, stamp, *_claim_where(claim)))


def set_review_watermark(c, claim, *, source_version, engine_signature, now=None):
    """Capture separate source/engine identities before reading/analysis."""
    _transaction(c)
    source_version, engine_signature = _token(source_version), _token(engine_signature)
    row = require_claim(c, claim, now=now)
    if row["source_version"] and (row["source_version"] != source_version or row["engine_signature"] != engine_signature):
        raise StaleClaim("The attempt's review watermark is already frozen")
    c.execute("UPDATE full_recheck_members SET source_version=?,engine_signature=? WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=?",
        (source_version, engine_signature, *_claim_where(claim)))


def _metrics(values):
    values = {} if values is None else values
    if not isinstance(values, dict) or set(values) - set(METRICS):
        raise ValueError("Only fixed aggregate metrics may be stored")
    result = {key: values.get(key, None if key in NULLABLE_METRICS else 0) for key in METRICS}
    if any((isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1000000000)
           for key, v in result.items() if not (v is None and key in NULLABLE_METRICS)):
        raise ValueError("Metrics must be bounded nonnegative integers")
    return result


def record_receipt(c, claim, *, source_version, engine_signature, result_token, full_verified_read, metrics=None, now=None):
    """Write a durable receipt *inside the legal-results transaction*.

    The caller establishes authenticated full coverage and unchanged source and
    engine before setting ``full_verified_read``. This function never treats
    document presence or an old ordinary completion as verified reading.
    """
    _transaction(c)
    source_version, engine_signature, result_token = _token(source_version), _token(engine_signature), _token(result_token)
    if not isinstance(full_verified_read, bool):
        raise ValueError("Full verified reading must be an explicit boolean")
    values, stamp = _metrics(metrics), _time(now)
    row = require_claim(c, claim, now=stamp)
    if (row["source_version"], row["engine_signature"]) != (source_version, engine_signature):
        raise StaleClaim("Result does not match the attempt's captured source/engine")
    if row["result_token"]:
        expected = (claim.attempt_id, result_token, source_version, engine_signature, int(full_verified_read))
        actual = tuple(row[key] for key in ("receipt_attempt_id", "result_token", "result_source_version", "result_engine_signature", "full_verified_read"))
        if actual != expected or any(row[key] != values[key] for key in METRICS):
            raise StaleClaim("A different result receipt is already committed for this attempt")
        return
    c.execute("""UPDATE full_recheck_members SET receipt_attempt_id=?,result_token=?,result_source_version=?,
        result_engine_signature=?,full_verified_read=?,result_committed_at=?,"""
        + ",".join(key + "=?" for key in METRICS)
        + ",total_new_findings=total_new_findings+?,total_new_checks=total_new_checks+?"
        + " WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=? AND status='running'",
        (claim.attempt_id, result_token, source_version, engine_signature, int(full_verified_read), stamp,
         *values.values(), values["new_findings"], values["new_checks"], *_claim_where(claim)))
    _archive_receipt(c, require_claim(c, claim, now=stamp))


def _refresh_request(c, request_id):
    mismatch = (" OR EXISTS(SELECT 1 FROM full_recheck_targets t WHERE t.request_id=m.request_id "
                "AND t.engine_signature<>m.result_engine_signature)" if _exists(c, "full_recheck_targets") else "")
    c.execute("UPDATE full_recheck_requests SET status='completed' WHERE request_id=? AND status='active' "
        "AND NOT EXISTS(SELECT 1 FROM full_recheck_members m WHERE request_id=? AND (status<>'completed'"
        + mismatch + "))", (request_id, request_id))


def finish_member(c, claim, *, result_token):
    """Acknowledge only the exact fully verified committed attempt receipt.

    Lease expiry after the result commit cannot erase that receipt. This write
    remains safe after expiry because a superseding attempt changes its token.
    """
    _transaction(c)
    identity = _claim_where(claim)
    _token(result_token)
    row = _dict_one(c.execute("""SELECT * FROM full_recheck_members WHERE request_id=? AND reg=?
        AND attempt_id=? AND lease_token=? AND status IN ('running','completed')""", identity))
    target = engine_target(c, claim.request_id)
    if row and _full_receipt(row, claim) and row["result_token"] == result_token and target and row["result_engine_signature"] != target["engine_signature"]:
        _archive_receipt(c, row)
        c.execute("""UPDATE full_recheck_members SET status='retry',reason='engine_changed',
            next_retry_at=0,lease_until=0 WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=?""", identity)
        c.execute("UPDATE full_recheck_requests SET status='active' WHERE request_id=?", (claim.request_id,))
        return "retry"
    changed = c.execute("""UPDATE full_recheck_members SET status='completed',reason='',lease_until=0,
        next_retry_at=0 WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=?
        AND status IN ('running','completed') AND receipt_attempt_id=attempt_id AND result_token=?
        AND full_verified_read=1 AND result_committed_at IS NOT NULL
        AND source_version<>'' AND source_version=result_source_version
        AND engine_signature<>'' AND engine_signature=result_engine_signature""", (*identity, result_token)).rowcount
    if changed != 1:
        raise StaleClaim("No matching fully verified result receipt to acknowledge")
    _refresh_request(c, claim.request_id)
    return "completed"


def defer_member(c, claim, *, status, reason, retry_after=600, metrics=None, now=None):
    """Preserve partial receipts; waiting/retry/blocked never means completed."""
    _transaction(c)
    if status not in ("waiting_originals", "retry", "blocked") or reason not in REASONS or not reason:
        raise ValueError("A fixed deferred status and reason are required")
    if isinstance(retry_after, bool) or not isinstance(retry_after, (int, float)) or not 0 <= retry_after <= 86400:
        raise ValueError("Retry delay must be bounded")
    stamp = _time(now)
    row = require_claim(c, claim, now=stamp)
    if row["full_verified_read"] and row["receipt_attempt_id"] == claim.attempt_id and row["result_token"]:
        raise StaleClaim("A fully verified result receipt must be completed, not deferred")
    # Once legal results committed, their receipt metrics are authoritative.
    # A status callback may carry only pre-persist coverage counts; it must not
    # erase committed new-findings/checks totals by supplying a partial dict.
    if metrics is not None:
        _metrics(metrics)
    values = ({key: row[key] for key in METRICS} if row["result_token"] or metrics is None
              else _metrics(metrics))
    c.execute("UPDATE full_recheck_members SET status=?,reason=?,updated_at=?,next_retry_at=?,lease_until=0,"
        + ",".join(key + "=?" for key in METRICS)
        + " WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=? AND status='running'",
        (status, reason, stamp, stamp + retry_after, *values.values(), *_claim_where(claim)))



def note_waiting(c, request_id, reg, *, reason="waiting_originals", retry_after=90, now=None):
    """Classify missing originals after an outside-transaction bounded probe.

    Never changes ordinary queue state. A fresh ordinary claim may subsequently
    share this request despite this request-specific missing-original cooldown.
    """
    _transaction(c)
    _request_id(request_id)
    if reason not in ("waiting_originals", "missing_originals"):
        raise ValueError("A fixed missing-originals reason is required")
    if isinstance(retry_after, bool) or not isinstance(retry_after, (int, float)) or not 0 <= retry_after <= 86400:
        raise ValueError("Retry delay must be bounded")
    stamp = _time(now)
    if has_active_claim(c, reg):
        return False
    return c.execute("""UPDATE full_recheck_members SET status='waiting_originals',reason=?,updated_at=?,next_retry_at=?
        WHERE request_id=? AND reg=? AND status IN ('pending','retry','waiting_originals')
        AND EXISTS(SELECT 1 FROM full_recheck_requests r WHERE r.request_id=full_recheck_members.request_id AND r.status='active')""",
        (reason, stamp, stamp + retry_after, request_id, str(reg))).rowcount == 1


def expired_members(c, request_id, *, now=None, limit=64):
    """Read bounded recovery candidates; do not release any executing worker."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("Recovery page limit is out of bounds")
    return [Claim(row[0], request_id, row[1], row[2], row[3]) for row in c.execute("""SELECT reg,attempt_id,lease_token,general_token
        FROM full_recheck_members WHERE request_id=? AND status='running' AND lease_until<=? ORDER BY reg LIMIT ?""",
        (_request_id(request_id), _time(now), limit))]


def _full_receipt(row, claim):
    return (row["full_verified_read"] == 1 and row["receipt_attempt_id"] == claim.attempt_id
        and bool(row["result_token"]) and row["result_committed_at"] is not None
        and bool(row["source_version"]) and row["source_version"] == row["result_source_version"]
        and bool(row["engine_signature"]) and row["engine_signature"] == row["result_engine_signature"])


def recover_expired(c, claim, *, now=None):
    """Recover one exact lease only while caller holds its exclusive execution lock.

    No global recovery is provided: expiry alone does not prove a worker stopped.
    A durable fully verified receipt is finalized without rerunning the audit;
    incomplete/uncommitted attempts become retry, never successful completion.
    """
    _transaction(c)
    stamp = _time(now)
    row = _dict_one(c.execute("""SELECT * FROM full_recheck_members WHERE request_id=? AND reg=? AND attempt_id=?
        AND lease_token=? AND status='running' AND lease_until<=?""", (*_claim_where(claim), stamp)))
    if row is None:
        return None
    complete = _full_receipt(row, claim)
    if complete:
        return finish_member(c, claim, result_token=row["result_token"])
    c.execute("""UPDATE full_recheck_members SET status='retry',reason='lease_expired',updated_at=?,
        next_retry_at=?,lease_until=0 WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=? AND status='running'""",
        (stamp, stamp, *_claim_where(claim)))
    return "retry"
