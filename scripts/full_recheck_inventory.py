"""Bounded, read-only, aggregate-only inventory of existing audit populations.

This diagnoses population differences. It never schedules work, reads original
bytes, hashes evidence, calls account services or infers missing registry IDs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import islice
import sqlite3
import time

MAX_PURCHASES = 20000
MAX_THEMATIC_POSITIONS = 100000
MAX_SECONDS = 2.0


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def collect(ns):
    from v332_all_procurements_audit import CAMPAIGN
    from v342_thematic_queue import business_company
    from full_recheck_runtime import AUTHORIZED_REQUESTS, REQUEST_ENV, AUTHORIZED_EPOCH
    import os
    expected = AUTHORIZED_REQUESTS.get(os.environ.get(REQUEST_ENV, "").strip(),
                                       AUTHORIZED_REQUESTS[AUTHORIZED_EPOCH])
    started = time.monotonic()
    result = {"status": "unavailable", "captured_at": timestamp(),
              "basis": "saved_db_and_separately_captured_verified_intake",
              "expected_requested_count": expected}
    c = None
    try:
        getter = ns.get("_verified_set235")
        if not callable(getter):
            return {**result, "reason": "verified_membership_unavailable"}
        # The restored getter's internals are not part of the public checkout.
        # Call it before opening a DB snapshot, never under a writer lock.
        values = getter()
        if values is None or isinstance(values, (str, bytes, dict)):
            return {**result, "reason": "verified_membership_unavailable"}
        raw = list(islice(iter(values), MAX_PURCHASES + 1))
        if len(raw) > MAX_PURCHASES:
            return {**result, "reason": "verified_membership_limit"}
        verified = {str(value) for value in raw}
        result["verified_captured_at"] = timestamp()
        c = ns["conn"]()
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=250")
        c.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + MAX_SECONDS
        c.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        c.execute("BEGIN")
        snapshot_at = timestamp()
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"purchases", "v319_manual_inputs", "v332_audit_queue"} <= tables:
            return {**result, "reason": "inventory_schema_unavailable"}
        rows = c.execute("""SELECT p.reg_number,p.customer,
            EXISTS(SELECT 1 FROM v319_manual_inputs m WHERE m.reg=p.reg_number) manual,
            EXISTS(SELECT 1 FROM v332_audit_queue q WHERE q.campaign=? AND q.reg=p.reg_number) queued
            FROM purchases p ORDER BY p.reg_number LIMIT ?""", (CAMPAIGN, MAX_PURCHASES + 1)).fetchall()
        if len(rows) > MAX_PURCHASES:
            return {**result, "reason": "purchase_inventory_limit"}
        saved = {str(row["reg_number"]) for row in rows}
        dashboard = set()
        general = set()
        counts = {"saved_purchases": len(rows), "manual_saved_purchases": 0,
                  "verified_saved_purchases": 0, "dashboard_purchases": 0,
                  "dashboard_company": 0, "dashboard_other_or_unknown_company": 0,
                  "general_joined_purchases": 0, "general_current_company": 0,
                  "general_other_or_unknown_company": 0,
                  "new_general_membership_eligible": 0}
        for row in rows:
            if time.monotonic() >= deadline:
                return {**result, "reason": "inventory_budget_exhausted"}
            reg = str(row["reg_number"])
            company = business_company(row["customer"])
            manual, queued, live = bool(row["manual"]), bool(row["queued"]), reg in verified
            counts["manual_saved_purchases"] += manual
            counts["verified_saved_purchases"] += live
            if queued:
                general.add(reg)
                counts["general_joined_purchases"] += 1
                counts["general_current_company"] += company
                counts["general_other_or_unknown_company"] += not company
            if (live or queued) and not manual:
                dashboard.add(reg)
                counts["dashboard_purchases"] += 1
                counts["dashboard_company"] += company
                counts["dashboard_other_or_unknown_company"] += not company
            counts["new_general_membership_eligible"] += live and company and not manual and not queued
        counts["general_queue_positions"] = c.execute(
            "SELECT COUNT(*) FROM v332_audit_queue WHERE campaign=?", (CAMPAIGN,)).fetchone()[0]
        counts["general_orphan_positions"] = counts["general_queue_positions"] - len(general)
        counts["dashboard_expected_difference"] = len(dashboard) - expected
        counts["verified_not_saved"] = len(verified - saved)
        if {"thematic_items", "thematic_batches"} <= tables:
            positions = c.execute("""SELECT t.reg FROM thematic_items t
                JOIN thematic_batches b ON b.id=t.batch_id
                WHERE b.status='active' LIMIT ?""", (MAX_THEMATIC_POSITIONS + 1,)).fetchall()
            if len(positions) > MAX_THEMATIC_POSITIONS:
                return {**result, "reason": "thematic_inventory_limit"}
            unique = {str(row[0]) for row in positions}
            counts.update(thematic_active_positions=len(positions),
                          thematic_active_unique_registrations=len(unique),
                          thematic_active_saved_purchases=len(unique & saved),
                          thematic_active_dashboard_overlap=len(unique & dashboard),
                          thematic_active_general_overlap=len(unique & general))
        return {**result, "status": "complete", "db_snapshot_at": snapshot_at,
                "counts": counts, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "note": "Counts describe membership only; original bytes and legal completion are not verified."}
    except sqlite3.OperationalError:
        return {**result, "reason": "inventory_db_busy_or_budget"}
    except Exception:
        return {**result, "reason": "inventory_source_unavailable"}
    finally:
        if c is not None:
            if c.in_transaction:
                c.rollback()
            c.close()
