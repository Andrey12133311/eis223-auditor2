"""Opt-in one-request adapter for the existing general worker pool.

No endpoint, extra analysis pool, legal revision bump or global queue reset.
The ledger and aggregate diagnostics are operational, never a shared ownerless
HTTP response. Originals and existing evidence are handled by the same engine.

Operational policy: the explicit correction generation is monotonic and paired
with the final integrated engine hash. Reusing a generation with a different
hash fails closed, including a same-generation rollback. Older builds may keep
saving legal results and legacy completed flags; those flags are historical,
not current-engine coverage. Only this build's enabled turns repair them. A
rollback to a pre-target build pauses target fulfillment but does not erase its
membership, target, successful prior receipts or cumulative credits. Keep the
same epoch and return to the matching build to resume. No import-time work.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import contextmanager

REQUEST_ENV = "EIS223_FULL_RECHECK_REQUEST"
AUTHORIZED_EPOCH = "2026-10-19-all-saved-214328"
LEASE_SECONDS = 300
RENEW_SECONDS = 30
PAGE_SIZE = 8
INVENTORY_SECONDS = 300
# Authorized semantic correction generation, not a deployment or legal quota.
# Future changed engines need an explicitly reviewed higher generation. Equal
# generation/different signature disables request claims; it never retargets.
TARGET_GENERATION = 4
CATCHUP_PAGE_SIZE = 32
# Reuse two of the existing four general workers, leaving two ordinary-only.
# This is an in-process explicit-only budget, not a cross-process lease cap.
EXPLICIT_WORKERS = 2


class RequestOwnershipBusy(RuntimeError):
    """An unfinished requested member needs a fresh, owned ordinary attempt."""


def _dispatch(ns):
    loop = asyncio.get_running_loop()
    state = ns.get("_full_recheck_dispatch")
    if state is None or state["loop"] is not loop:
        state = {"loop": loop, "active": 0, "selector": asyncio.Lock()}
        ns["_full_recheck_dispatch"] = state
    return state


@contextmanager
def transaction(ns):
    c = ns["conn"]()
    try:
        c.execute("PRAGMA busy_timeout=2500")
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def request_id(ns):
    return (ns.get("_full_recheck") or {}).get("request_id")


def initialize(ns):
    from full_recheck_inventory import collect
    state = ns.setdefault("_full_recheck", {})
    state["inventory"] = collect(ns)
    state["inventory_at"] = time.monotonic()
    print("FULL_RECHECK_INVENTORY " + json.dumps(state["inventory"], ensure_ascii=False), flush=True)
    epoch = os.environ.get(REQUEST_ENV, "").strip()
    c = ns["conn"]()
    try:
        state["recovery_request_id"] = (AUTHORIZED_EPOCH if c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='full_recheck_members'").fetchone() else None)
    finally:
        c.close()
    if not epoch:
        state.pop("target_conflict", None)
        state.pop("request_id", None)
        return
    # This release authorizes exactly one user instruction, not a mechanism
    # for inventing recurring full scans or treating deployment SHAs as epochs.
    if epoch != AUTHORIZED_EPOCH:
        raise ValueError("Unrecognized explicit full-recheck request epoch")
    import full_recheck_request as ledger
    with transaction(ns) as c:
        ledger.initialize(c)
        from full_recheck_originals import initialize as initialize_originals
        initialize_originals(c)
    c = ns["conn"]()
    try:
        summary = ledger.create_request(c, epoch, ns.get("_verified_set235"), expected_count=3058)
    finally:
        c.close()
    from v332_all_procurements_audit import engine_signature
    signature = engine_signature()
    try:
        with transaction(ns) as c:
            ledger.activate_target(c, epoch, generation=TARGET_GENERATION, engine_signature=signature)
            ledger.reopen_completed(c, epoch, generation=TARGET_GENERATION,
                                    engine_signature=signature, limit=CATCHUP_PAGE_SIZE)
    except ledger.TargetConflict:
        state.pop("request_id", None)
        state["target_conflict"] = True
        print("FULL_RECHECK_TARGET conflict=True generation=" + str(TARGET_GENERATION), flush=True)
        return
    state["engine_signature"] = signature
    state["target_generation"] = TARGET_GENERATION
    state.pop("target_conflict", None)
    c = ns["conn"]()
    try:
        summary = ledger.inventory(c, epoch)
    finally:
        c.close()
    # Publication to the workers follows the committed complete snapshot only.
    state["request_id"] = epoch
    state["cursor"] = ""
    print("FULL_RECHECK_REQUEST " + json.dumps(summary, ensure_ascii=False), flush=True)


def heartbeat(ns):
    """Private aggregate logs only; never called by the progress GET handler."""
    state = ns.get("_full_recheck")
    if state is None:
        return
    if time.monotonic() - state.get("inventory_at", 0) >= INVENTORY_SECONDS:
        from full_recheck_inventory import collect
        state["inventory"] = collect(ns)
        state["inventory_at"] = time.monotonic()
        print("FULL_RECHECK_INVENTORY " + json.dumps(state["inventory"], ensure_ascii=False), flush=True)
    epoch = request_id(ns) or (AUTHORIZED_EPOCH if state.get("target_conflict") else None)
    if not epoch:
        return
    import full_recheck_request as ledger
    c = ns["conn"]()
    try:
        summary = ledger.inventory(c, epoch)
    finally:
        c.close()
    print("FULL_RECHECK_PROGRESS " + json.dumps(summary, ensure_ascii=False), flush=True)


def available_sql(c, expression):
    """Internal trusted column expression; absence keeps old builds compatible."""
    if expression not in ("q.reg", "t.reg", "v332_audit_queue.reg"):
        raise ValueError("Unexpected reservation expression")
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='full_recheck_members'").fetchone():
        return ""
    # Expiration is not release: an exclusive file lock must prove the old
    # executor is gone before recovery can make this registration claimable.
    return (" AND NOT EXISTS(SELECT 1 FROM full_recheck_members fr "
            "WHERE fr.reg=" + expression + " AND fr.status='running')")


def require_request(c, reg):
    claim = getattr(reg, "full_recheck_claim", None)
    if claim is not None:
        from full_recheck_request import require_claim
        require_claim(c, claim)


def note_snapshot(ns, reg, snapshot):
    claim = getattr(reg, "full_recheck_claim", None)
    if claim is None:
        return
    from full_recheck_request import set_review_watermark
    reg.full_recheck_snapshot = dict(snapshot)
    with transaction(ns) as c:
        set_review_watermark(c, claim,
                            source_version=snapshot.get("last_semantic_version") or "unavailable-source",
                            engine_signature=snapshot.get("last_engine_signature") or "unavailable-engine")


def _receipt_failure(snapshot, details, verified, unreadable):
    """First failed precondition in the existing completion predicate's order.

    Fixed diagnostic codes only. Keep short-circuit evaluation (including
    malformed-input failures) identical to the completion check below.
    """
    if not verified > 0:
        return "no_verified_documents"
    if not verified == int(details.get("documents_read") or 0):
        return "unverified_readable_documents"
    if not unreadable == 0:
        return "unreadable_documents"
    if details.get("source_incomplete"):
        return "source_incomplete"
    if snapshot.get("source_error"):
        return "source_unavailable"
    expected, received = details.get("original_expected"), details.get("original_received")
    if expected is None or received is None:
        return "unknown_original_coverage"
    if not received >= expected:
        return "missing_originals"
    if not snapshot.get("last_semantic_version"):
        return "source_unavailable"
    if not snapshot.get("last_engine_signature"):
        return "engine_unavailable"
    return ""


async def prepare_receipt(ns, reg, details):
    if getattr(reg, "full_recheck_claim", None) is None:
        return
    snapshot = getattr(reg, "full_recheck_snapshot", {})
    verified = int(details.get("verified_original_documents") or 0)
    unreadable = int(details.get("documents_unreadable") or 0)
    expected, received = details.get("original_expected"), details.get("original_received")
    complete = (verified > 0 and verified == int(details.get("documents_read") or 0)
                and unreadable == 0 and not details.get("source_incomplete")
                and not snapshot.get("source_error") and expected is not None
                and received is not None and received >= expected
                and bool(snapshot.get("last_semantic_version"))
                and bool(snapshot.get("last_engine_signature")))
    reason = "" if complete else _receipt_failure(snapshot, details, verified, unreadable)
    if complete:
        from v332_all_procurements_audit import file_stage
        after = await file_stage(ns, "full_request_verify_source", _snapshot, ns, reg)
        complete = (after.get("last_semantic_version") == snapshot["last_semantic_version"]
                    and after.get("last_engine_signature") == snapshot["last_engine_signature"])
        if not complete:
            if not after.get("last_semantic_version"):
                reason = "source_unavailable"
            elif after.get("last_semantic_version") != snapshot["last_semantic_version"]:
                reason = "source_changed"
            elif not after.get("last_engine_signature"):
                reason = "engine_unavailable"
            else:
                reason = "engine_changed"
    reg.full_recheck_failure_reason = reason
    reg.full_recheck_receipt = {"source_version": snapshot.get("last_semantic_version") or "unavailable-source",
                              "engine_signature": snapshot.get("last_engine_signature") or "unavailable-engine",
                              "full_verified_read": bool(complete),
                              "metrics": {key: (details.get(key) if key in ("original_expected", "original_received")
                                                else int(details.get(key) or 0)) for key in
                                          ("documents_found", "documents_read", "documents_unreadable",
                                           "verified_original_documents", "original_expected", "original_received")}}


def record_receipt(c, reg, token, new_findings, new_checks):
    """Called inside the legal result's existing SQLite commit, never afterward."""
    claim = getattr(reg, "full_recheck_claim", None)
    if claim is None:
        return
    from full_recheck_request import record_receipt as save
    context = getattr(reg, "full_recheck_receipt", None)
    if context is None:
        raise RuntimeError("Requested analysis has no coverage receipt context")
    context = {**context, "metrics": {**context["metrics"],
                                      "new_findings": new_findings, "new_checks": new_checks}}
    save(c, claim, result_token=token, **context)
    if getattr(reg, "full_recheck_only", False) and context["full_verified_read"]:
        _mirror_terminal(c, reg, token, context)


def _mirror_terminal(c, reg, token, context):
    """Share a fresh requested result only with the unchanged terminal row."""
    from v332_all_procurements_audit import CAMPAIGN, RULE_REVISION, now
    before = getattr(reg, "full_recheck_general_before", None)
    if not before or before["state"] not in ("completed", "failed", "no_readable_documents"):
        return
    metrics = context["metrics"]
    c.execute("""UPDATE v332_audit_queue SET state='completed',updated_at=?,
        last_source_version=?,last_semantic_version=?,last_engine_signature=?,audit_revision=?,
        documents_found=?,documents_read=?,documents_unreadable=?,verified_original_documents=?,
        original_expected=?,original_received=?,last_result_token=?,retry_count=0,next_retry_at=0,error=NULL
        WHERE campaign=? AND reg=? AND state=? AND updated_at IS ?
          AND reservation_token=? AND last_result_token=? AND next_retry_at=? AND retry_count=?
          AND last_semantic_version=? AND last_engine_signature=?""",
        (now(), context["source_version"], context["source_version"], context["engine_signature"],
         RULE_REVISION, *[metrics[key] for key in ("documents_found", "documents_read", "documents_unreadable",
             "verified_original_documents", "original_expected", "original_received")], token,
         CAMPAIGN, str(reg), before["state"], before["updated_at"], before["reservation_token"],
         before["last_result_token"], before["next_retry_at"], before["retry_count"],
         before["last_semantic_version"], before["last_engine_signature"]))


def _finish(ns, reg, state, detail):
    from full_recheck_request import finish_member, defer_member
    claim = getattr(reg, "full_recheck_claim", None)
    if claim is None:
        return
    context = getattr(reg, "full_recheck_receipt", {})
    with transaction(ns) as c:
        if state == "completed" and context.get("full_verified_read") and detail.get("result_token"):
            finish_member(c, claim, result_token=detail["result_token"])
            return
        expected, received = detail.get("original_expected"), detail.get("original_received")
        missing = expected is not None and received is not None and received < expected
        waiting = missing or (not detail.get("documents_found") and not detail.get("source_incomplete"))
        status = "waiting_originals" if waiting else "retry"
        reason = ("missing_originals" if waiting else
                  getattr(reg, "full_recheck_failure_reason", "") or
                  ("no_readable_documents" if state == "no_readable_documents" else
                   "partial_or_unverified_documents"))
        defer_member(c, claim, status=status, reason=reason, retry_after=600,
                     metrics=context.get("metrics"))


async def finish_result(ns, reg, state, detail):
    if getattr(reg, "full_recheck_claim", None) is None:
        return
    from v332_all_procurements_audit import db_stage
    await db_stage(ns, "full_request_ack", _finish, ns, reg, state, detail)


def _renew(ns, reg):
    from full_recheck_request import renew_lease
    with transaction(ns) as c:
        renew_lease(c, reg.full_recheck_claim, lease_seconds=LEASE_SECONDS)


async def _renew_loop(ns, reg):
    from v332_all_procurements_audit import db_stage
    while True:
        await asyncio.sleep(RENEW_SECONDS)
        await db_stage(ns, "full_request_renew", _renew, ns, reg)


async def _lock(reg):
    from v295_eis_originals import _try_execution_lock
    from v381_audit_scheduling import background_audit, run_file_io
    from v332_all_procurements_audit import settle_task
    with background_audit():
        task = asyncio.create_task(run_file_io(_try_execution_lock, str(reg)))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            lock = await settle_task(task)
            if lock is not None:
                from v295_eis_originals import _release_execution_lock
                _release_execution_lock(lock)
            raise


def _attach(ns, reg):
    from full_recheck_request import claim_member
    with transaction(ns) as c:
        return claim_member(c, request_id(ns), str(reg), general_token=reg.reservation_token,
                            lease_seconds=LEASE_SECONDS, **_target_args(ns))


def _attachable(ns, reg):
    c = ns["conn"]()
    try:
        member = c.execute("SELECT status FROM full_recheck_members WHERE request_id=? AND reg=?",
                           (request_id(ns), str(reg))).fetchone()
    finally:
        c.close()
    # A running member is not permission to analyze without its identity.
    # Cross-process/restarted owners remain fenced until their exact release.
    return bool(member and member[0] not in ("completed", "blocked"))


async def attach_general(ns, reg):
    """Only a new ordinary reservation can carry a user-request attempt."""
    if not request_id(ns) or reg is None or getattr(reg, "full_recheck_claim", None) is not None:
        return
    from v332_all_procurements_audit import thread_call, settle_task
    if not await thread_call(_attachable, ns, reg):
        return
    lock = await _lock(reg)
    if lock is None:
        # A selector can briefly own this flock before its durable claim is
        # rejected by the ordinary reservation. Never turn that handoff race
        # into an uncredited analysis. A concurrently finished member no
        # longer needs attachment and keeps normal ordinary behavior.
        if await thread_call(_attachable, ns, reg):
            raise RequestOwnershipBusy("request_execution_busy")
        return
    try:
        task = asyncio.create_task(thread_call(_attach, ns, reg))
        try:
            claim = await asyncio.shield(task)
        except asyncio.CancelledError:
            claim = await settle_task(task)
            if claim is not None:
                reg.full_recheck_claim = claim
                reg.full_recheck_lock = lock
                lock = None
                await settle_task(asyncio.create_task(release(ns, reg)))
            raise
        if claim is None:
            if await thread_call(_attachable, ns, reg):
                raise RequestOwnershipBusy("request_claim_unavailable")
            return
        reg.full_recheck_claim = claim
        reg.full_recheck_lock = lock
        lock = None
        reg.full_recheck_renew = asyncio.create_task(_renew_loop(ns, reg))
    finally:
        if lock is not None:
            from v295_eis_originals import _release_execution_lock
            _release_execution_lock(lock)


def _release_member(ns, reg):
    """Committed full receipts recover even if cancellation interrupted ack."""
    from full_recheck_request import finish_member, defer_member
    claim = reg.full_recheck_claim
    with transaction(ns) as c:
        row = c.execute("SELECT * FROM full_recheck_members WHERE request_id=? AND reg=?",
                        (claim.request_id, str(claim))).fetchone()
        if row is None or row["status"] != "running" or row["lease_token"] != claim.lease_token:
            return
        if row["full_verified_read"] and row["result_token"]:
            finish_member(c, claim, result_token=row["result_token"])
        else:
            defer_member(c, claim, status="retry", reason="interrupted_attempt", retry_after=90)


async def release(ns, reg):
    if getattr(reg, "full_recheck_claim", None) is None:
        return
    from v332_all_procurements_audit import settle_task, thread_call

    async def cleanup():
        try:
            renewal = getattr(reg, "full_recheck_renew", None)
            if renewal is not None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
        finally:
            # Renewal settlement is itself an await: keep both durable member
            # cleanup and physical lock release unconditional if it fails.
            try:
                await thread_call(_release_member, ns, reg)
            except Exception as exc:
                # An expired lease remains blocking until locked recovery;
                # never release another token or count a failed write as done.
                print("FULL_RECHECK_RELEASE deferred=True type=" + type(exc).__name__, flush=True)
            finally:
                lock = getattr(reg, "full_recheck_lock", None)
                if lock is not None:
                    from v295_eis_originals import _release_execution_lock
                    _release_execution_lock(lock)
                    reg.full_recheck_lock = None

    task = asyncio.create_task(cleanup())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await settle_task(task)
        except Exception as exc:
            # The original shutdown request must still propagate if physical
            # cleanup fails. Preserve visibility without logging source data.
            print("FULL_RECHECK_RELEASE cleanup_failed=True type=" + type(exc).__name__, flush=True)
        raise


def _target_args(ns):
    state = ns.get("_full_recheck") or {}
    return {"engine_signature": state.get("engine_signature"), "generation": state.get("target_generation")}


def _catch_up(ns):
    # Do not gate on legacy request.status: a rolling old worker can finish
    # after the previous scan. Target coverage, not that legacy flag, is final.
    from full_recheck_request import reopen_completed
    with transaction(ns) as c:
        return reopen_completed(c, request_id(ns), limit=CATCHUP_PAGE_SIZE, **_target_args(ns))


def _candidates(ns):
    import full_recheck_request as ledger
    state = ns["_full_recheck"]
    c = ns["conn"]()
    try:
        rows = ledger.select_candidates(c, request_id(ns), limit=PAGE_SIZE,
                                        after_reg=state.get("cursor", ""), **_target_args(ns))
        if not rows and state.get("cursor"):
            state["cursor"] = ""
            rows = ledger.select_candidates(c, request_id(ns), limit=PAGE_SIZE, **_target_args(ns))
        return rows
    finally:
        c.close()


def _claim(ns, number):
    import full_recheck_request as ledger
    from v332_all_procurements_audit import CAMPAIGN, AuditReservation
    from v342_thematic_queue import business_company
    with transaction(ns) as c:
        row = c.execute("SELECT q.*,p.customer FROM v332_audit_queue q JOIN purchases p "
                        "ON p.reg_number=q.reg WHERE q.campaign=? AND q.reg=?",
                        (CAMPAIGN, number)).fetchone()
        # A pending company task receives its request identity at the ordinary
        # fresh claim. Keep its backoff and reservation intact, including when
        # missing originals mean it has not become runnable yet.
        if row and row["state"] == "pending" and business_company(row["customer"]):
            return None
        claim = ledger.claim_member(c, request_id(ns), number, lease_seconds=LEASE_SECONDS, **_target_args(ns))
        if claim is None:
            return None
        reg = AuditReservation(str(claim), claim.lease_token)
        reg.full_recheck_claim = claim
        reg.full_recheck_only = True
        reg.full_recheck_general_before = dict(row) if row else None
        return reg


def _snapshot(ns, reg):
    from v332_all_procurements_audit import receipt_readiness, evidence_version, engine_signature
    _, error = receipt_readiness(reg)
    if error:
        return {"source_error": "original_catalogue_incomplete"}
    return {"last_semantic_version": evidence_version(ns, reg),
            "last_engine_signature": engine_signature()}


def _expired(ns):
    from full_recheck_request import expired_members
    epoch = request_id(ns) or (ns.get("_full_recheck") or {}).get("recovery_request_id")
    if not epoch:
        return []
    c = ns["conn"]()
    try:
        return expired_members(c, epoch, limit=PAGE_SIZE)
    finally:
        c.close()


def _recover(ns, claim):
    from full_recheck_request import recover_expired
    with transaction(ns) as c:
        recover_expired(c, claim)


async def recover(ns):
    from v332_all_procurements_audit import thread_call
    from v295_eis_originals import _release_execution_lock
    for claim in await thread_call(_expired, ns):
        lock = await _lock(claim)
        if lock is None:
            continue
        try:
            await thread_call(_recover, ns, claim)
        finally:
            _release_execution_lock(lock)


def _ordinary_pending(ns, reg):
    from v332_all_procurements_audit import CAMPAIGN
    from v342_thematic_queue import business_company
    c = ns["conn"]()
    try:
        row = c.execute("SELECT q.state,p.customer,EXISTS(SELECT 1 FROM docs d WHERE d.reg_number=q.reg "
                        "AND d.status IN ('checked','downloaded_no_text') AND COALESCE(d.local_path,'')<>'') "
                        "FROM v332_audit_queue q JOIN purchases p ON p.reg_number=q.reg "
                        "WHERE q.campaign=? AND q.reg=?", (CAMPAIGN, reg)).fetchone()
        # The selected page can predate an ordinary claim. Avoid its flock
        # while that owner attaches; attach_general also guards later races.
        return bool(row and (row[0] == "running" or
                    row[0] == "pending" and business_company(row[1]))), bool(row and row[2])
    finally:
        c.close()


def _note_waiting(ns, reg):
    from full_recheck_request import note_waiting
    with transaction(ns) as c:
        note_waiting(c, request_id(ns), reg, retry_after=90)


async def run_one(ns, slot):
    """Use a free explicit slot or immediately return to ordinary work.

    Admission is event-loop atomic before the first await. Keep the permit
    through real claim-thread settlement, receipt cleanup and lock release.
    Ordinary-attached receipts already use ordinary slots and need no extra
    explicit permit. Rolling processes retain their existing per-reg fencing.
    """
    dispatch = _dispatch(ns)
    if dispatch["active"] >= EXPLICIT_WORKERS:
        return False
    dispatch["active"] += 1
    try:
        try:
            return await _run_one(ns, slot)
        except Exception as exc:
            print("FULL_RECHECK_TURN deferred=True type=" + type(exc).__name__, flush=True)
            return False
    finally:
        dispatch["active"] -= 1


async def _claim_next(ns):
    """One cursor owner through a settled claim; never serialize analysis.

    A second worker must not read the same page then rewind this cursor after
    its peer has advanced/wrapped it. All cursor reads/writes and bounded
    preflight work share this selector; thread_call settles on cancellation.
    """
    from v332_all_procurements_audit import thread_call, file_stage, settle_task
    from v295_eis_originals import _release_execution_lock
    async with _dispatch(ns)["selector"]:
        await recover(ns)
        if not request_id(ns):
            return None
        await thread_call(_catch_up, ns)
        from full_recheck_originals import pump as pump_originals
        try:
            await pump_originals(ns)
        except Exception as exc:
            # Ingestion backpressure never blocks local evidence analysis.
            print("FULL_RECHECK_ORIGINALS deferred=True type=" + type(exc).__name__, flush=True)
        for number in await thread_call(_candidates, ns):
            # Advance only a visited row, not the unused remainder of a page.
            ns["_full_recheck"]["cursor"] = number
            ordinary, ready = await thread_call(_ordinary_pending, ns, number)
            if ordinary:
                if not ready:
                    from v332_all_procurements_audit import receipt_readiness
                    originals, error = await file_stage(ns, "full_request_availability", receipt_readiness, number)
                    if not originals and not error:
                        await thread_call(_note_waiting, ns, number)
                continue
            lock = await _lock(number)
            if lock is None:
                continue
            reg = None
            handed_off = False
            cancelled = False
            try:
                claim_task = asyncio.create_task(thread_call(_claim, ns, number))
                try:
                    reg = await asyncio.shield(claim_task)
                except asyncio.CancelledError:
                    reg = await settle_task(claim_task)
                    if reg is not None:
                        reg.full_recheck_lock = lock
                        lock = None
                    raise
                if reg is None:
                    continue
                reg.full_recheck_lock = lock
                lock = None
                reg.full_recheck_renew = asyncio.create_task(_renew_loop(ns, reg))
                handed_off = True
                return reg
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                try:
                    if reg is not None and not handed_off:
                        await settle_task(asyncio.create_task(release(ns, reg)))
                    if lock is not None:
                        _release_execution_lock(lock)
                except Exception as exc:
                    if not cancelled:
                        raise
                    # Cleanup may commit a retry before physical unlock fails.
                    # Preserve shutdown without changing that lock's lifetime
                    # or letting a cleanup error become a normal False turn.
                    print("FULL_RECHECK_CLAIM cleanup_failed=True type=" + type(exc).__name__, flush=True)
        return None


async def _run_one(ns, slot):
    """One explicit-first turn inside the unchanged general worker pool."""
    from v332_all_procurements_audit import file_stage, db_stage, inspect_one
    from v381_audit_scheduling import background_audit
    reg = await _claim_next(ns)
    if reg is None:
        return False
    cancelled = False
    try:
        slot.update(reg=reg, phase="full_request_source")
        snapshot = await file_stage(ns, "full_request_source", _snapshot, ns, reg, worker=slot)
        await db_stage(ns, "full_request_watermark", note_snapshot, ns, reg, snapshot, worker=slot)
        slot["phase"] = "full_request_inspect"
        with background_audit():
            state, detail = await asyncio.wait_for(inspect_one(ns, reg), timeout=600)
        await finish_result(ns, reg, state, detail)
        return True
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as exc:
        print("FULL_RECHECK_ATTEMPT deferred=True type=" + type(exc).__name__, flush=True)
        return True
    finally:
        # release already settles its shielded cleanup before propagating
        # cancellation. An extra settle_task here would consume a cancellation
        # first delivered after successful analysis and let the worker resume.
        try:
            await release(ns, reg)
        except Exception as exc:
            if not cancelled:
                raise
            # A cleanup exception must not replace cancellation already
            # unwinding from source preparation, analysis or persistence.
            print("FULL_RECHECK_RELEASE cleanup_failed=True type=" + type(exc).__name__, flush=True)
        finally:
            slot.update(reg=None, phase="full_request_waiting")
