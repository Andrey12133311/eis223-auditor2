"""Bounded request-owned admission into the existing authenticated EIS queue.

Only scheduling metadata lives here. No remote I/O, evidence validation bypass,
queue resets, legal completion, or changes to the downloader's retry policy.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time

PAGE_SIZE = 8
MAX_NEW_JOBS = 2
MAX_ACTIVE_JOBS = 4
POLL_SECONDS = 30
CHECK_SECONDS = 600
BACKPRESSURE_SECONDS = 60
# One automatic queue place may wait for the next due local probe. The lease
# is never extended by drainers, restarts or repeated pressure observations.
ADMISSION_SECONDS = BACKPRESSURE_SECONDS + POLL_SECONDS
PARENT_ID = "full-recheck-originals"
GAP_REASONS = ("missing_originals", "no_readable_documents", "no_verified_documents",
               "unverified_readable_documents", "partial_or_unverified_documents",
               "source_incomplete", "unknown_original_coverage")
ACTIVE = ("queued", "leased", "deferred")


def initialize(c):
    """Additive operational tables; callers own the short transaction."""
    c.execute("""CREATE TABLE IF NOT EXISTS full_recheck_originals(
        request_id TEXT NOT NULL, reg TEXT NOT NULL,
        source_version TEXT NOT NULL DEFAULT '', job_id TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL DEFAULT '', job_phase TEXT NOT NULL DEFAULT '',
        next_check_at REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL,
        PRIMARY KEY(request_id,reg),
        FOREIGN KEY(request_id,reg) REFERENCES full_recheck_members(request_id,reg))""")
    c.execute("CREATE INDEX IF NOT EXISTS full_recheck_originals_due "
              "ON full_recheck_originals(request_id,next_check_at,reg)")
    c.execute("""CREATE TABLE IF NOT EXISTS full_recheck_originals_turn(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        request_id TEXT NOT NULL, reg TEXT NOT NULL DEFAULT '',
        engine_signature TEXT NOT NULL, generation INTEGER NOT NULL,
        hold_until REAL NOT NULL DEFAULT 0,
        yield_automatic INTEGER NOT NULL DEFAULT 0)""")


def _automatic_pending(c):
    """Only existing durable discovery relations; never source polling."""
    import full_recheck_request as ledger
    return any(ledger._exists(c, table) and c.execute(
        "SELECT 1 FROM " + table + " WHERE reg_job_id='' LIMIT 1").fetchone()
        for table in ("eis_server_discovery_candidates", "eis_inn_candidates"))


def _release_turn(c):
    c.execute("UPDATE full_recheck_originals_turn SET reg='',hold_until=0,yield_automatic=1 WHERE singleton=1")


def _turn(c, request, target, stamp):
    row = c.execute("SELECT * FROM full_recheck_originals_turn WHERE singleton=1").fetchone()
    if row and (row["request_id"], row["engine_signature"], row["generation"]) != (
            request, target["engine_signature"], target["generation"]):
        return None
    if row and row["hold_until"] and row["hold_until"] <= stamp:
        _release_turn(c)
        row = c.execute("SELECT * FROM full_recheck_originals_turn WHERE singleton=1").fetchone()
    return row


def _save_turn(c, request, target, *, reg="", until=0, yielding=False):
    c.execute("""INSERT INTO full_recheck_originals_turn
        (singleton,request_id,reg,engine_signature,generation,hold_until,yield_automatic)
        VALUES(1,?,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET
        request_id=excluded.request_id,reg=excluded.reg,engine_signature=excluded.engine_signature,
        generation=excluded.generation,hold_until=excluded.hold_until,
        yield_automatic=excluded.yield_automatic""",
        (request, reg, target["engine_signature"], target["generation"], until, int(yielding)))


def discovery_reservation(c, ns, *, now=None):
    """Honor one live request turn without probing files under a writer.

    A reservation is only a capacity opportunity. The bridge still obtains its
    execution lock, reprobes receipts and runs every original admission fence.
    A stopped, disabled, changed-target, ineligible or expired waiter yields.
    """
    import full_recheck_request as ledger
    import full_recheck_runtime as runtime
    if not ledger._exists(c, "full_recheck_originals_turn"):
        return 0
    row = c.execute("SELECT * FROM full_recheck_originals_turn WHERE singleton=1").fetchone()
    if not row or not row["hold_until"]:
        return 0
    stamp = time.time() if now is None else now
    request, target = runtime.request_id(ns), runtime._target_args(ns)
    valid = (row["hold_until"] > stamp and _automatic_pending(c) and request == row["request_id"]
             and target == {"engine_signature": row["engine_signature"], "generation": row["generation"]}
             and ledger._target_accepts(c, request, **target))
    if valid:
        where, args = _eligible(c, request, reg=row["reg"])
        valid = bool(c.execute("SELECT 1 FROM full_recheck_members m WHERE " + where, args).fetchone()
                     and c.execute("""SELECT 1 FROM full_recheck_originals
                         WHERE request_id=? AND reg=? AND outcome IN ('backpressure','execution_busy')
                         AND job_id=''""",
                         (request, row["reg"])).fetchone()
                     and not c.execute("""SELECT 1 FROM v301_search_jobs WHERE kind='reg' AND target=?
                         AND status IN ('queued','leased','deferred') LIMIT 1""", (row["reg"],)).fetchone()
                     and c.execute("""SELECT COUNT(*) FROM v301_search_jobs WHERE parent_id=?
                         AND status IN ('queued','leased','deferred')""", (PARENT_ID,)).fetchone()[0] < MAX_ACTIVE_JOBS)
    if not valid:
        _release_turn(c)
    return int(valid)


def discovery_admitted(c, created):
    """A real automatic admission repays the turn; duplicate bindings do not."""
    import full_recheck_request as ledger
    if ledger._exists(c, "full_recheck_originals_turn") and (created or not _automatic_pending(c)):
        c.execute("UPDATE full_recheck_originals_turn SET yield_automatic=0 WHERE singleton=1 AND yield_automatic<>0")


def _eligible(c, request, *, reg=None):
    import full_recheck_request as ledger
    clauses = ledger._ordinary_blockers(c)
    clauses.append("NOT EXISTS(SELECT 1 FROM full_recheck_members a WHERE a.reg=m.reg AND a.status='running')")
    clauses.append("EXISTS(SELECT 1 FROM purchases p WHERE p.reg_number=m.reg)")
    clauses.append("(m.status='waiting_originals' OR (m.status='retry' AND m.reason IN (" +
                   ",".join("?" for _ in GAP_REASONS) + ")))")
    values = [request, *GAP_REASONS]
    if reg is not None:
        clauses.append("m.reg=?")
        values.append(reg)
    return "m.request_id=? AND " + " AND ".join(clauses), values


def candidates(c, request, *, now=None, **target):
    """Fresh waiting members precede already-observed gaps, with durable delay."""
    import full_recheck_request as ledger
    if not ledger._target_accepts(c, request, **target):
        return []
    if not ledger._exists(c, "v301_search_jobs"):
        return []
    where, args = _eligible(c, request)
    stamp = time.time() if now is None else now
    return [dict(row) for row in c.execute("""SELECT m.reg,m.status,m.attempt_id,m.result_token
        FROM full_recheck_members m LEFT JOIN full_recheck_originals b
          ON b.request_id=m.request_id AND b.reg=m.reg WHERE """ + where + """
        AND COALESCE(b.next_check_at,0)<=?
        ORDER BY COALESCE(b.next_check_at,0),m.reg LIMIT ?""", (*args, stamp, PAGE_SIZE))]


def probe(reg):
    """Bounded local receipt identity, never a verified-read/completion claim.

    Exclude file mtimes, job/report timestamps, engine and deployment identity.
    The existing intake fingerprint excludes receipt bookkeeping/history too.
    A wholly present but unreadable/unverified set needs local analysis repair,
    not a blind remote retry. Catalogue limits fail closed without downloads.
    """
    from audit_source_version import original_sources
    sources = original_sources(reg)
    if any(item.get("scan_error") for item in sources):
        return {"error": "catalogue_unavailable"}
    versions = [item["legal_source_version"] for item in sources if item.get("legal_source_version")]
    if len(versions) != 1:
        return {"error": "catalogue_unavailable"}
    originals = [item for item in sources if item.get("uid")]
    return {"source_version": versions[0],
            "missing": not originals or any(not item.get("available") for item in originals)}


def report_snapshots(c, request, reg):
    """Read/classify at most two bounded persisted reports before any writer."""
    import full_recheck_request as ledger
    if not ledger._exists(c, "eis_server_reports"):
        return {}
    ids = [row[0] for row in c.execute("""SELECT id FROM v301_search_jobs
        WHERE kind='reg' AND target=? AND parent_id<>'v341-card-facts'
        ORDER BY created_at DESC,id DESC LIMIT 1""", (reg,))]
    prior = c.execute("SELECT job_id FROM full_recheck_originals WHERE request_id=? AND reg=?", (request, reg)).fetchone()
    if prior and prior[0]:
        ids.append(prior[0])
    snapshots = {}
    for identifier in set(ids):
        raw = _report_json(c, identifier)
        try:
            result = json.loads(raw) if raw and len(raw) <= 12288 else {}
        except (ValueError, TypeError, RecursionError):
            result = {}
        code = result.get("error_code", "") if isinstance(result, dict) else ""
        transient = isinstance(result, dict) and not result.get("unsupported_xml_count") and not result.get("unresolved_attachment_count") and (
            code in ("eis_http_403", "eis_http_429", "eis_connection_error", "site_connection_error", "site_http_429", "site_http_507") or
            isinstance(code, str) and re.fullmatch(r"(?:eis|site)_http_5[0-9]{2}", code) is not None)
        snapshots[identifier] = (raw, bool(transient))
    return snapshots


def _report_json(c, identifier):
    row = c.execute("SELECT substr(result_json,1,12289) FROM eis_server_reports WHERE job_id=? "
                    "ORDER BY created_at DESC,rowid DESC LIMIT 1", (identifier,)).fetchone()
    return row[0] if row else None


def _transient(c, job, reports):
    # Recheck the exact report under the writer; no JSON/file I/O here.
    previous = (reports or {}).get(job["id"])
    return bool(previous and previous[1] and _report_json(c, job["id"]) == previous[0])


def admit(c, request, candidate, observation, *, now=None, allow_create=True, reports=None, **target):
    """Bind/create atomically under caller's writer lock, with no file work.

    A persistent binding consumes one source-specific opportunity. Terminal
    results never become another job on the next tick. Adopt the source left
    by our in-flight job before considering any later independent changes.
    """
    import full_recheck_request as ledger
    from v301_search_jobs import MAX_QUEUE, REG, _utc
    from eis_server_worker import RESERVED_MANUAL_SLOTS
    ledger._transaction(c)
    stamp = time.time() if now is None else now
    reg = candidate["reg"]
    if not REG.fullmatch(reg) or not ledger._target_accepts(c, request, **target):
        return "ineligible"
    where, args = _eligible(c, request, reg=reg)
    member = c.execute("SELECT m.* FROM full_recheck_members m WHERE " + where, args).fetchone()
    if member is None or any(member[key] != candidate[key] for key in ("status", "attempt_id", "result_token")):
        return "changed"
    prior = c.execute("SELECT * FROM full_recheck_originals WHERE request_id=? AND reg=?", (request, reg)).fetchone()
    if prior and prior["next_check_at"] > stamp:
        # A peer may have marked this registry execution-busy while this
        # already-selected caller held its physical lock and probed. That
        # peer's fairness deferral must not veto the actual lock owner's work.
        if prior["outcome"] != "execution_busy" or observation.get("error"):
            return "not_due"

    source = observation.get("source_version", "")
    if not observation.get("error") and (not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{64}", source)):
        raise ValueError("A complete receipt-source fingerprint is required")
    def save(outcome, job_id="", delay=CHECK_SECONDS, *, fingerprint=source, phase=None):
        if phase is None:
            phase = prior["job_phase"] if prior and prior["job_id"] == job_id else ""
        c.execute("""INSERT INTO full_recheck_originals VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(request_id,reg) DO UPDATE SET source_version=excluded.source_version,
            job_id=excluded.job_id,outcome=excluded.outcome,job_phase=excluded.job_phase,
            next_check_at=excluded.next_check_at,
            updated_at=excluded.updated_at""",
            (request, reg, fingerprint, job_id, outcome, phase, stamp + delay, stamp))
        # A losing peer cannot revoke the actual lock owner's opportunity.
        # Busy observations neither create nor extend a hold; an abandoned
        # physical owner still yields at the original fixed expiry.
        if outcome not in ("backpressure", "execution_busy"):
            c.execute("""UPDATE full_recheck_originals_turn SET reg='',hold_until=0,yield_automatic=1
                WHERE singleton=1 AND request_id=? AND reg=? AND hold_until>0""", (request, reg))
        return outcome

    if observation.get("error"):
        code = "execution_busy" if observation["error"] == "execution_busy" else "catalogue_unavailable"
        return save(code, prior["job_id"] if prior else "",
                    BACKPRESSURE_SECONDS if code == "execution_busy" else CHECK_SECONDS,
                    fingerprint=prior["source_version"] if prior else "")
    if not observation.get("missing"):
        return save("originals_present", prior["job_id"] if prior else "")

    active = c.execute("""SELECT * FROM v301_search_jobs WHERE kind='reg' AND target=?
        AND status IN ('queued','leased','deferred') ORDER BY created_at,id LIMIT 1""", (reg,)).fetchone()
    if active:
        if active["parent_id"] == "v341-card-facts":
            # Metadata-only activity is a blocker, never a replacement for a
            # full-download budget awaiting terminal adoption or cooldown.
            return save("metadata_pending", prior["job_id"] if prior else "", BACKPRESSURE_SECONDS,
                        fingerprint=prior["source_version"] if prior else source)
        return save("active", active["id"], BACKPRESSURE_SECONDS, phase="active")

    latest = c.execute("""SELECT * FROM v301_search_jobs WHERE kind='reg' AND target=?
        AND parent_id<>'v341-card-facts' ORDER BY created_at DESC,id DESC LIMIT 1""", (reg,)).fetchone()
    # A newer externally requested job can replace a previously blocked result,
    # including when it was started and finished entirely between bridge polls.
    bound = latest if latest and prior and latest["id"] == prior["job_id"] else None
    if bound:
        if prior["job_phase"] == "active":
            return save("terminal", bound["id"], phase="terminal")
        if prior["source_version"] == source and prior["job_phase"] != "cooldown":
            return save("terminal", bound["id"], phase="terminal")
    if latest:
        # Unsupported/unknown terminal results cannot manufacture a new budget
        # merely through unrelated receipt updates or a changed engine.
        if latest["status"] != "done" and not _transient(c, latest, reports):
            return save("terminal", latest["id"], phase="terminal")
        updated = ledger._updated_epoch(latest["updated_at"])
        if updated is None:
            return save("terminal", latest["id"], phase="terminal")
        if stamp < updated + CHECK_SECONDS:
            return save("terminal_cooldown", latest["id"], CHECK_SECONDS, phase="cooldown")

    identity = hashlib.sha256((request + "\0" + reg + "\0" + source).encode()).hexdigest()[:24]
    old = c.execute("SELECT id,kind,target,parent_id FROM v301_search_jobs WHERE id=?", (identity,)).fetchone()
    if old:
        if (old["kind"], old["target"], old["parent_id"]) != ("reg", reg, PARENT_ID):
            return save("identity_conflict")
        return save("terminal", old["id"])
    total = c.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased','deferred')").fetchone()[0]
    owned = c.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE parent_id=? AND status IN ('queued','leased','deferred')", (PARENT_ID,)).fetchone()[0]
    if not allow_create or owned >= MAX_ACTIVE_JOBS:
        return save("backpressure", delay=BACKPRESSURE_SECONDS)
    turn = _turn(c, request, target, stamp)
    if turn and turn["yield_automatic"] and _automatic_pending(c):
        return save("backpressure", delay=BACKPRESSURE_SECONDS)
    if total >= MAX_QUEUE - RESERVED_MANUAL_SLOTS:
        result = save("backpressure", delay=BACKPRESSURE_SECONDS)
        if not _automatic_pending(c):
            if turn and turn["hold_until"]:
                _release_turn(c)
        elif not turn or not turn["hold_until"]:
            _save_turn(c, request, target, reg=reg, until=stamp + ADMISSION_SECONDS)
        return result
    c.execute("""INSERT INTO v301_search_jobs(id,kind,target,status,priority,parent_id,created_at,updated_at)
        VALUES(?,'reg',?,'queued',8,?,?,?)""", (identity, reg, PARENT_ID, _utc(), _utc()))
    save("active", identity, BACKPRESSURE_SECONDS, phase="active")
    _save_turn(c, request, target, yielding=_automatic_pending(c))
    return "enqueued"


async def pump(ns):
    """At most one small local scan per interval; no remote polling loop."""
    import full_recheck_runtime as runtime
    from v332_all_procurements_audit import thread_call, file_stage
    state = ns.get("_full_recheck") or {}
    request = runtime.request_id(ns)
    if not request or time.monotonic() < state.get("originals_after", 0):
        return
    state["originals_after"] = time.monotonic() + POLL_SECONDS
    target = runtime._target_args(ns)
    def read():
        c = ns["conn"]()
        try:
            return candidates(c, request, **target)
        finally:
            c.close()
    from v295_eis_originals import _release_execution_lock
    created = 0
    changes = {}
    for candidate in await thread_call(read):
        lock = await runtime._lock(candidate["reg"])
        if lock is None:
            def postpone():
                with runtime.transaction(ns) as c:
                    if runtime.request_id(ns) != request or runtime._target_args(ns) != target:
                        return False
                    before = c.execute("SELECT outcome FROM full_recheck_originals WHERE request_id=? AND reg=?",
                                       (request, candidate["reg"])).fetchone()
                    result = admit(c, request, candidate, {"error": "execution_busy"}, **target)
                    return result == "execution_busy" and (not before or before[0] != result)
            if await thread_call(postpone):
                changes["deferred"] = changes.get("deferred", 0) + 1
            continue
        cancelled = False
        try:
            observation = await file_stage(ns, "full_request_originals_probe", probe, candidate["reg"])
            def reports_read():
                c = ns["conn"]()
                try:
                    return report_snapshots(c, request, candidate["reg"])
                finally:
                    c.close()
            reports = await thread_call(reports_read)
            def write():
                with runtime.transaction(ns) as c:
                    if runtime.request_id(ns) != request or runtime._target_args(ns) != target:
                        return "disabled", False
                    before = c.execute("SELECT outcome,job_id FROM full_recheck_originals WHERE request_id=? AND reg=?",
                                       (request, candidate["reg"])).fetchone()
                    result = admit(c, request, candidate, observation, reports=reports,
                                   allow_create=created < MAX_NEW_JOBS, **target)
                    after = c.execute("SELECT outcome,job_id FROM full_recheck_originals WHERE request_id=? AND reg=?",
                                      (request, candidate["reg"])).fetchone()
                    return result, tuple(before or ()) != tuple(after or ())
            result, changed = await thread_call(write)
            created += int(result == "enqueued")
            if changed:
                label = {"active": "reused", "terminal": "terminal_blocked", "backpressure": "capacity",
                         "metadata_pending": "deferred", "catalogue_unavailable": "deferred",
                         "terminal_cooldown": "deferred", "originals_present": "originals_present",
                         "enqueued": "enqueued"}.get(result, "deferred")
                changes[label] = changes.get(label, 0) + 1
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            try:
                _release_execution_lock(lock)
            except Exception as exc:
                if not cancelled:
                    raise
                # Preserve cancellation after settled probe/admission even if
                # physical cleanup itself fails. Never resume normal claims.
                print("FULL_RECHECK_ORIGINALS cleanup_failed=True type=" + type(exc).__name__, flush=True)
    if changes:
        print("FULL_RECHECK_ORIGINALS " + json.dumps(changes, sort_keys=True), flush=True)
