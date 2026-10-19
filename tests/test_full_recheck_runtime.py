"""Requested fresh attempts reuse real local engine/reservations and receipts."""
from __future__ import annotations

import asyncio
import io
import json
import itertools
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from test_general_audit_recovery import DatabaseFixture, LATE_REPLY
import full_recheck_request as ledger
import full_recheck_runtime as runtime
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import v295_eis_originals as originals
import v298_eis_only as incoming
import test_receipt_original_parity as receipt_fixtures
from v295_eis_originals import _try_execution_lock, _release_execution_lock
from v352_dashboard_truth import load_counts


class FullRecheckRuntime(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        state, tick = dict(thematic.WORKER_STATE), thematic.tick
        def restore():
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(state)
            thematic.tick = tick
        self.addCleanup(restore)

    def activate(self):
        self.stack.enter_context(patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.AUTHORIZED_EPOCH}))
        runtime.initialize(self.ns)

    def member(self, reg):
        with self.connect() as c:
            return dict(c.execute("SELECT * FROM full_recheck_members WHERE request_id=? AND reg=?",
                                 (runtime.AUTHORIZED_EPOCH, reg)).fetchone())

    async def ordinary(self, reg=None, *, finish=True):
        if reg is None:
            reg = await general.claim_safely(self.ns)
        self.assertIsNotNone(reg)
        await runtime.attach_general(self.ns, reg)
        snapshot = await general.file_stage(self.ns, "source", general.review_watermark, self.ns, reg)
        await general.db_stage(self.ns, "request_watermark", runtime.note_snapshot, self.ns, reg, snapshot)
        state, detail = await general.inspect_one(self.ns, reg)
        if finish:
            general.mark(self.ns, reg, state, {**{k: v for k, v in detail.items() if k != "source_incomplete"}, **snapshot})
            await runtime.finish_result(self.ns, reg, state, detail)
        return reg, state, detail

    async def test_corrected_engine_catches_up_completed_noncompany_without_new_epoch(self):
        reg = self.purchase()
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        general.initialize(self.ns)
        self.activate()
        self.assertTrue(await runtime.run_one(self.ns, {}))
        with self.connect() as c:
            before = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
        old_attempt = self.member(reg)["attempt_id"]
        self.assertEqual(before["current_completed"], 1)
        with patch.object(runtime, "TARGET_GENERATION", 2), patch.object(general, "engine_signature", return_value="corrected-engine"):
            runtime.initialize(self.ns)
            self.assertEqual(self.member(reg)["reason"], "engine_changed")
            self.assertTrue(await runtime.run_one(self.ns, {}))
            self.assertFalse(await runtime.run_one(self.ns, {}))
            runtime.initialize(self.ns)
        with self.connect() as c:
            after = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
            history = c.execute("SELECT attempt_id FROM full_recheck_receipts WHERE reg=?", (reg,)).fetchall()
        self.assertEqual((after["current_completed"], after["current_pending"], after["status"]), (1, 0, "completed"))
        self.assertEqual(after["membership_digest"], before["membership_digest"])
        self.assertEqual(after["db_snapshot_at"], before["db_snapshot_at"])
        self.assertEqual((after["unique_attempted"], after["total_attempts"]), (1, 2))
        self.assertIn(old_attempt, [row[0] for row in history])
        self.assertEqual(len(history), 2)
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(after["committed_new_findings"], before["committed_new_findings"])
        self.assertIsNone(self.row(reg))

    async def test_equal_generation_other_engine_stops_claims_without_changing_target(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        before = self.member(reg)
        with self.connect() as c:
            target = ledger.engine_target(c, runtime.AUTHORIZED_EPOCH)
        with patch.object(general, "engine_signature", return_value="different-same-generation"):
            runtime.initialize(self.ns)
        self.assertIsNone(runtime.request_id(self.ns))
        self.assertTrue(self.ns["_full_recheck"]["target_conflict"])
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg), before)
        with self.connect() as c:
            self.assertEqual(ledger.engine_target(c, runtime.AUTHORIZED_EPOCH), target)
        runtime.heartbeat(self.ns)

    async def test_runtime_turn_repairs_legacy_completed_status_after_target_scan(self):
        reg = self.purchase()
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        general.initialize(self.ns)
        self.activate()
        claim = runtime._claim(self.ns, reg)
        runtime.note_snapshot(self.ns, claim, runtime._snapshot(self.ns, claim))
        state, detail = await general.inspect_one(self.ns, claim)
        from test_full_recheck_request import LEGACY_FUNCTIONS
        scope = dict(vars(ledger))
        exec(LEGACY_FUNCTIONS, scope)
        with patch.object(runtime, "TARGET_GENERATION", 2), patch.object(general, "engine_signature", return_value="corrected-engine"):
            runtime.initialize(self.ns)
            self.assertEqual(self.member(reg)["status"], "running")
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                scope["finish_member"](c, claim.full_recheck_claim, result_token=detail["result_token"])
            with self.connect() as c:
                before = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
            self.assertEqual(before["legacy_status"], "completed")
            self.assertEqual(before["current_completed"], 0)
            self.assertTrue(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.member(reg)["result_engine_signature"], "corrected-engine")
        self.assertEqual(self.member(reg)["attempt_count"], 2)

    async def test_disabled_restart_retains_target_and_does_not_reopen_completed(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        claimed, _, _ = await self.ordinary()
        await runtime.release(self.ns, claimed)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            ledger.activate_target(c, runtime.AUTHORIZED_EPOCH, generation=2, engine_signature="new-engine")
        before = self.member(reg)
        with patch.dict(os.environ, {runtime.REQUEST_ENV: ""}):
            runtime.initialize(self.ns)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg), before)
        with self.connect() as c:
            summary = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
        self.assertEqual(summary["current_completed"], 0)
        self.assertEqual(summary["stale_completed"], 1)

    async def test_due_retry_and_catchup_cannot_skip_fresh_members_after_long_rotation(self):
        regs = [self.purchase(number) for number in range(1, 17)]
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение'")
        general.initialize(self.ns)
        self.activate()
        from test_full_recheck_request import LEGACY_FUNCTIONS
        legacy = dict(vars(ledger))
        exec(LEGACY_FUNCTIONS, legacy)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE full_recheck_members SET status='retry',reason='unreadable_documents' WHERE reg=?", (regs[0],))
            old = legacy["claim_member"](c, runtime.AUTHORIZED_EPOCH, regs[8])
            ledger.set_review_watermark(c, old, source_version="old-source", engine_signature="old-engine")
            ledger.record_receipt(c, old, source_version="old-source", engine_signature="old-engine",
                                  result_token="old-result", full_verified_read=True)
            legacy["finish_member"](c, old, result_token="old-result")
        async def partial(ns, reg):
            return "no_readable_documents", {"documents_found": 1, "source_incomplete": True,
                                              "original_expected": 1, "original_received": 1}
        start = time.time()
        # Every previously visited retry is due again by the next turn. Fresh
        # rows must still be visited before another rotation starts.
        with patch.object(general, "inspect_one", side_effect=partial):
            for turn in range(16):
                with patch.object(ledger.time, "time", return_value=start + 700 * turn):
                    self.assertTrue(await runtime.run_one(self.ns, {}))
        attempts = [self.member(reg)["attempt_count"] for reg in regs]
        self.assertEqual(attempts, [2 if reg == regs[8] else 1 for reg in regs])
        with self.connect() as c:
            summary = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
            self.assertEqual(c.execute("SELECT result_token FROM full_recheck_receipts WHERE attempt_id=?", (old.attempt_id,)).fetchone()[0], "old-result")
        self.assertEqual(summary["unique_attempted"], 16)
        self.assertEqual(summary["actual_count"], 16)

    async def test_busy_first_candidate_is_skipped_and_cursor_stops_at_visited_row(self):
        regs = [self.purchase(number) for number in range(1, 10)]
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение'")
        general.initialize(self.ns)
        self.activate()
        held = _try_execution_lock(regs[0])
        self.assertIsNotNone(held)
        try:
            self.assertTrue(await runtime.run_one(self.ns, {}))
            self.assertEqual(self.member(regs[0])["attempt_count"], 0)
            self.assertEqual(self.member(regs[1])["attempt_count"], 1)
            self.assertEqual(self.ns["_full_recheck"]["cursor"], regs[1])
            self.assertTrue(await runtime.run_one(self.ns, {}))
            self.assertEqual(self.member(regs[2])["attempt_count"], 1)
        finally:
            _release_execution_lock(held)
        # It stays pending and is reachable after the normal cursor wrap.
        for _ in range(7):
            await runtime.run_one(self.ns, {})
        self.assertEqual(self.member(regs[0])["status"], "completed")
        self.assertTrue(all(self.member(reg)["attempt_count"] == 1 for reg in regs))

    async def test_disabled_default_creates_no_request_schema_or_new_work(self):
        reg = self.purchase()
        general.initialize(self.ns)
        with patch.dict(os.environ, {runtime.REQUEST_ENV: ""}):
            runtime.initialize(self.ns)
        self.assertIsNone(runtime.request_id(self.ns))
        with self.connect() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='full_recheck_requests'").fetchone())
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.row(reg)["state"], "pending")

    async def test_fresh_ordinary_claim_completes_request_once(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        # Requested path leaves the existing pending queue row for its worker.
        before = self.row(reg)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.row(reg), before)
        claimed, state, detail = await self.ordinary()
        await runtime.release(self.ns, claimed)
        self.assertEqual(state, "completed")
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.member(reg)["attempt_count"], 1)
        self.assertEqual(self.findings(reg), 1)
        runtime.initialize(self.ns)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg)["attempt_count"], 1)
        self.assertEqual(general.refresh_changed(self.ns), 0)

    async def test_pre_request_running_task_not_acknowledged_or_reset(self):
        reg = self.purchase()
        general.initialize(self.ns)
        claimed = await general.claim_safely(self.ns)
        before = self.row(reg)
        self.activate()
        await runtime.attach_general(self.ns, claimed)
        self.assertFalse(hasattr(claimed, "full_recheck_claim"))
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.row(reg), before)
        await self.ordinary(claimed)
        self.assertEqual(self.member(reg)["status"], "pending")
        self.assertTrue(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(general.refresh_changed(self.ns), 0)

    async def test_explicit_dashboard_noncompany_is_reviewed_without_general_expansion(self):
        reg = self.purchase()
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        general.initialize(self.ns)
        self.assertIsNone(self.row(reg))
        self.activate()
        self.assertTrue(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.findings(reg), 1)
        self.assertIsNone(self.row(reg))

    async def test_full_receipt_commits_with_results_and_survives_missing_ack(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        claimed, state, detail = await self.ordinary(finish=False)
        self.assertEqual(self.member(reg)["status"], "running")
        self.assertEqual(self.member(reg)["result_token"], detail["result_token"])
        self.assertEqual(self.findings(reg), 1)
        await runtime.release(self.ns, claimed)
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.member(reg)["attempt_count"], 1)

    async def test_receipt_failure_rolls_back_legal_results(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        claimed = await general.claim_safely(self.ns)
        await runtime.attach_general(self.ns, claimed)
        snapshot = general.review_watermark(self.ns, claimed)
        runtime.note_snapshot(self.ns, claimed, snapshot)
        with patch.object(ledger, "record_receipt", side_effect=RuntimeError("receipt injection")):
            with self.assertRaisesRegex(RuntimeError, "receipt injection"):
                await general.inspect_one(self.ns, claimed)
        self.assertEqual(self.findings(reg), 0)
        self.assertFalse(self.member(reg)["result_token"])
        await runtime.release(self.ns, claimed)
        self.assertEqual(self.member(reg)["status"], "retry")

    async def test_missing_originals_classified_without_resetting_pending(self):
        reg = self.purchase(present=False)
        with self.connect() as c:
            c.execute("UPDATE docs SET local_path='' WHERE reg_number=?", (reg,))
        general.initialize(self.ns)
        self.activate()
        before = self.row(reg)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.row(reg), before)
        self.assertEqual(self.member(reg)["status"], "waiting_originals")
        self.assertEqual(self.member(reg)["attempt_count"], 0)

    async def test_source_arrival_during_review_is_not_full_request_completion(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        analyzer = self.ns["analyze"]
        def changing(purchase, docs):
            result = analyzer(purchase, docs)
            self.document(reg, "Вторая редакция " + LATE_REPLY)
            return result
        self.ns["analyze"] = changing
        claimed, state, detail = await self.ordinary()
        await runtime.release(self.ns, claimed)
        self.assertEqual(state, "completed")
        self.assertEqual(self.member(reg)["status"], "retry")
        self.assertEqual(self.member(reg)["reason"], "source_changed")
        self.assertFalse(self.member(reg)["full_verified_read"])
        self.assertEqual(self.findings(reg), 1)

    async def test_completion_reason_preserves_prior_predicate_and_source_read_count(self):
        base = {"last_semantic_version": "source", "last_engine_signature": "engine"}
        snapshots = [base, {**base, "source_error": "unreported private detail"},
                     {**base, "last_semantic_version": ""}, {**base, "last_engine_signature": ""}]
        expected_reads = 0
        with patch.object(runtime, "_snapshot", return_value=base) as scan:
            for verified, readable, unreadable, incomplete, coverage, snapshot in itertools.product(
                    (0, 1), (1, 2), (0, 1), (False, True), ((1, 1), (2, 1), (None, 1)), snapshots):
                expected, received = coverage
                details = {"documents_found": readable + unreadable, "documents_read": readable,
                    "documents_unreadable": unreadable, "verified_original_documents": verified,
                    "original_expected": expected, "original_received": received, "source_incomplete": incomplete}
                # Exact pre-diagnostic predicate, including its priority/order.
                prior = (verified > 0 and verified == int(details.get("documents_read") or 0)
                         and unreadable == 0 and not details.get("source_incomplete")
                         and not snapshot.get("source_error") and expected is not None
                         and received is not None and received >= expected
                         and bool(snapshot.get("last_semantic_version"))
                         and bool(snapshot.get("last_engine_signature")))
                reg = general.AuditReservation("32619000001", "fixture")
                reg.full_recheck_claim = object()
                reg.full_recheck_snapshot = snapshot
                await runtime.prepare_receipt(self.ns, reg, details)
                self.assertEqual(reg.full_recheck_receipt["full_verified_read"], prior)
                self.assertEqual(bool(reg.full_recheck_failure_reason), not prior)
                self.assertIn(reg.full_recheck_failure_reason, ledger.REASONS)
                self.assertNotIn("unreported private detail", reg.full_recheck_failure_reason)
                expected_reads += prior
            self.assertEqual(scan.call_count, expected_reads)

    async def test_completion_reason_priority_and_post_analysis_changes_are_fixed(self):
        snapshot = {"last_semantic_version": "source", "last_engine_signature": "engine"}
        full = {"documents_found": 1, "documents_read": 1, "documents_unreadable": 0,
                "verified_original_documents": 1, "original_expected": 1, "original_received": 1}
        cases = [({"verified_original_documents": 0, "documents_unreadable": 1, "source_incomplete": True}, "no_verified_documents"),
                 ({"documents_read": 2, "documents_unreadable": 1, "source_incomplete": True}, "unverified_readable_documents"),
                 ({"documents_unreadable": 1, "source_incomplete": True}, "unreadable_documents"),
                 ({"source_incomplete": True, "original_expected": None}, "source_incomplete"),
                 ({"original_expected": None}, "unknown_original_coverage"),
                 ({"original_expected": 2}, "missing_originals")]
        for changes, reason in cases:
            reg = general.AuditReservation("32619000001", "fixture")
            reg.full_recheck_claim = object()
            reg.full_recheck_snapshot = snapshot
            with patch.object(runtime, "_snapshot", side_effect=AssertionError("No extra source read")):
                await runtime.prepare_receipt(self.ns, reg, {**full, **changes})
            self.assertFalse(reg.full_recheck_receipt["full_verified_read"])
            self.assertEqual(reg.full_recheck_failure_reason, reason)
        for after, reason in (({**snapshot, "last_semantic_version": "new", "last_engine_signature": "new"}, "source_changed"),
                              ({**snapshot, "last_engine_signature": "new"}, "engine_changed"),
                              ({"source_error": "private source path"}, "source_unavailable"),
                              ({**snapshot, "last_engine_signature": ""}, "engine_unavailable")):
            with patch.object(runtime, "_snapshot", return_value=after) as scan:
                await runtime.prepare_receipt(self.ns, reg, full)
            self.assertEqual(scan.call_count, 1)
            self.assertFalse(reg.full_recheck_receipt["full_verified_read"])
            self.assertEqual(reg.full_recheck_failure_reason, reason)

    async def test_malformed_coverage_keeps_existing_failure_without_new_reads(self):
        full = {"documents_found": 1, "documents_read": 1, "documents_unreadable": 0,
                "verified_original_documents": 1, "original_expected": 1, "original_received": 1}
        for changes, error in (({"verified_original_documents": "invalid"}, ValueError),
                               ({"documents_read": "invalid"}, ValueError),
                               ({"documents_unreadable": "invalid"}, ValueError),
                               ({"original_expected": "invalid"}, TypeError)):
            reg = general.AuditReservation("32619000001", "fixture")
            reg.full_recheck_claim = object()
            reg.full_recheck_snapshot = {"last_semantic_version": "source", "last_engine_signature": "engine"}
            with patch.object(runtime, "_snapshot", side_effect=AssertionError("No extra source read")):
                with self.assertRaises(error):
                    await runtime.prepare_receipt(self.ns, reg, {**full, **changes})
            self.assertFalse(hasattr(reg, "full_recheck_receipt"))

    async def test_unknown_coverage_reason_and_receipt_survive_replay_and_restart(self):
        number = self.purchase()
        general.initialize(self.ns)
        self.activate()
        reg = await general.claim_safely(self.ns)
        await runtime.attach_general(self.ns, reg)
        runtime.note_snapshot(self.ns, reg, general.review_watermark(self.ns, reg))
        detail = {"documents_found": 1, "documents_read": 1, "documents_unreadable": 0,
                  "verified_original_documents": 1, "original_expected": None, "original_received": 1,
                  "result_token": "unknown-coverage-result"}
        await runtime.prepare_receipt(self.ns, reg, detail)
        general.persist(self.ns, reg, [], [], detail["result_token"])
        general.persist(self.ns, reg, [], [], detail["result_token"])
        await runtime.finish_result(self.ns, reg, "completed", detail)
        await runtime.release(self.ns, reg)
        before = self.member(number)
        self.assertEqual((before["status"], before["reason"]), ("retry", "unknown_original_coverage"))
        self.assertEqual(before["attempt_count"], 1)
        self.assertFalse(before["full_verified_read"])
        runtime.initialize(self.ns)
        self.assertEqual(self.member(number), before)

    async def test_mixed_unverified_readable_input_does_not_overstate_completion(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        claimed = await general.claim_safely(self.ns)
        await runtime.attach_general(self.ns, claimed)
        runtime.note_snapshot(self.ns, claimed, general.review_watermark(self.ns, claimed))
        await runtime.prepare_receipt(self.ns, claimed, {"documents_found": 2, "documents_read": 2,
            "documents_unreadable": 0, "verified_original_documents": 1,
            "original_expected": 1, "original_received": 1, "source_incomplete": False})
        self.assertFalse(claimed.full_recheck_receipt["full_verified_read"])
        await runtime.release(self.ns, claimed)

    async def test_disabled_epoch_recovers_expired_lock_safe_claim_without_new_work(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            claim = ledger.claim_member(c, runtime.AUTHORIZED_EPOCH, reg, lease_seconds=1, **runtime._target_args(self.ns))
            c.execute("UPDATE full_recheck_members SET lease_until=? WHERE reg=?", (time.time() - 1, reg))
        held = _try_execution_lock(reg)
        self.assertIsNotNone(held)
        try:
            with patch.dict(os.environ, {runtime.REQUEST_ENV: ""}):
                runtime.initialize(self.ns)
            self.assertFalse(await runtime.run_one(self.ns, {}))
            self.assertEqual(self.member(reg)["status"], "running")
            self.assertIsNone(general.claim_next(self.ns))
        finally:
            _release_execution_lock(held)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg)["status"], "retry")
        self.assertEqual(self.member(reg)["attempt_count"], 1)
        self.assertEqual(general.claim_next(self.ns), reg)

    async def test_full_claim_blocks_ordinary_and_thematic_reservations(self):
        reg = self.purchase()
        general.initialize(self.ns)
        thematic.init(self.ns)
        fast.install(self.ns)
        batch = "f" * 24
        with self.connect() as c:
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) VALUES(?,?,'2026','active',?)",
                      (batch, json.dumps(["dates"]), json.dumps(["ООО"])))
            c.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,'2026')", (batch, reg))
        self.activate()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            claim = ledger.claim_member(c, runtime.AUTHORIZED_EPOCH, reg, **runtime._target_args(self.ns))
        self.assertIsNotNone(claim)
        self.assertIsNone(general.claim_next(self.ns))
        self.assertEqual(fast._prepare(self.ns), [])
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            ledger.defer_member(c, claim, status="retry", reason="interrupted", retry_after=0)
        self.assertTrue(fast._prepare(self.ns))

    async def test_stopped_reserved_thematic_history_does_not_strand_requested_review(self):
        reg = self.purchase()
        general.initialize(self.ns)
        thematic.init(self.ns)
        fast.install(self.ns)
        batch = "b" * 24
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET state='completed' WHERE reg=?", (reg,))
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) VALUES(?,?,'2026','active',?)",
                      (batch, json.dumps(["dates"]), json.dumps(["ООО"])))
            c.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,'2026')", (batch, reg))
        selected = fast._prepare(self.ns)
        self.assertEqual(len(selected), 1)
        self.activate()
        self.assertFalse(await runtime.run_one(self.ns, {}))
        with self.connect() as c:
            c.execute("UPDATE thematic_batches SET status='stopped' WHERE id=?", (batch,))
            before = dict(c.execute("SELECT * FROM thematic_items WHERE batch_id=?", (batch,)).fetchone())
        fast._release_items(self.ns, selected)
        fast.install(self.ns)
        self.assertFalse(fast._start_item(self.ns, selected[0]))
        self.assertTrue(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg)["status"], "completed")
        with self.connect() as c:
            self.assertEqual(dict(c.execute("SELECT * FROM thematic_items WHERE batch_id=?", (batch,)).fetchone()), before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings WHERE batch_id=?", (batch,)).fetchone()[0], 0)

    async def test_independent_thematic_history_and_repeated_general_episode_do_not_inflate_dashboard(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        claimed, _, _ = await self.ordinary()
        await runtime.release(self.ns, claimed)
        thematic.init(self.ns)
        fast.install(self.ns)
        batch = "d" * 24
        with self.connect() as c:
            c.execute("CREATE TABLE manager_decisions(id INTEGER PRIMARY KEY,decision TEXT)")
            c.execute("INSERT INTO manager_decisions VALUES(1,'approved')")
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) VALUES(?,?,'2026','active',?)",
                      (batch, json.dumps(["dates"]), json.dumps(["ООО"])))
            c.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,'2026')", (batch, reg))
        selected = fast._prepare(self.ns)
        self.assertEqual(len(selected), 1)
        result = await fast._one(self.ns, selected[0], asyncio.Semaphore(1))
        fast._persist(self.ns, [result])
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings WHERE batch_id=?", (batch,)).fetchone()[0], 1)
            self.assertEqual(load_counts(c, [reg])[reg], 1)
            self.assertEqual(c.execute("SELECT decision FROM manager_decisions WHERE id=1").fetchone()[0], "approved")
        # Repeating the source through its existing general persistence is
        # deduplicated, while the thematic campaign retains its own history.
        _, detail = await general.inspect_one(self.ns, str(reg))
        self.assertEqual(detail["new_findings"], 0)
        with self.connect() as c:
            self.assertEqual(load_counts(c, [reg])[reg], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings WHERE batch_id=?", (batch,)).fetchone()[0], 1)

    async def test_cancellation_during_analysis_releases_exact_request_lease(self):
        reg = self.purchase()
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        general.initialize(self.ns)
        self.activate()
        started = asyncio.Event()
        async def held(*args):
            started.set()
            await asyncio.Event().wait()
        with patch.object(general, "inspect_one", side_effect=held):
            task = asyncio.create_task(runtime.run_one(self.ns, {}))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.member(reg)["status"], "retry")
        self.assertEqual(self.member(reg)["reason"], "interrupted_attempt")
        lock = _try_execution_lock(reg)
        self.assertIsNotNone(lock)
        _release_execution_lock(lock)

    async def test_release_cancellation_settles_renewal_and_releases_real_lock(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        claimed = await general.claim_safely(self.ns)
        await runtime.attach_general(self.ns, claimed)
        claimed.full_recheck_renew.cancel()
        await asyncio.gather(claimed.full_recheck_renew, return_exceptions=True)
        started, settling, allow_finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def renewal():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settling.set()
                await allow_finish.wait()
        claimed.full_recheck_renew = asyncio.create_task(renewal())
        await started.wait()
        task = asyncio.create_task(runtime.release(self.ns, claimed))
        await settling.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertIsNone(_try_execution_lock(reg))
        allow_finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        self.assertEqual(self.member(reg)["status"], "retry")
        self.assertEqual(self.member(reg)["reason"], "interrupted_attempt")
        lock = _try_execution_lock(reg)
        self.assertIsNotNone(lock)
        _release_execution_lock(lock)
        await general.release_reservation(self.ns, claimed)
        self.assertEqual(self.row(reg)["state"], "pending")

    async def test_disabled_without_recovery_is_connection_free(self):
        self.assertFalse(await runtime.run_one({}, {}))

    async def test_request_recovery_error_does_not_stop_ordinary_worker_turn(self):
        import sqlite3
        with patch.object(runtime, "_expired", side_effect=sqlite3.OperationalError("database is locked")):
            self.assertFalse(await runtime.run_one(self.ns, {}))

    async def test_incomplete_source_does_not_block_partial_legal_persistence(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        claimed = await general.claim_safely(self.ns)
        await runtime.attach_general(self.ns, claimed)
        runtime.note_snapshot(self.ns, claimed, {"source_error": "source catalogue incomplete"})
        state, detail = await general.inspect_one(self.ns, claimed)
        self.assertEqual(self.findings(reg), 1)
        self.assertFalse(self.member(reg)["full_verified_read"])
        await runtime.finish_result(self.ns, claimed, state, detail)
        await runtime.release(self.ns, claimed)
        self.assertEqual(self.member(reg)["status"], "retry")

    def restored_archive(self, number=1, *, members=None, copies=1, payload=None):
        reg = self.purchase(number)
        with self.connect() as c:
            c.execute("DELETE FROM docs WHERE reg_number=?", (reg,))
        self.originals[reg] = []
        if payload is None:
            encoded = io.BytesIO()
            with zipfile.ZipFile(encoded, "w") as archive:
                for name, content in (members or [
                        ("Explanation.docx", receipt_fixtures.docx_bytes(LATE_REPLY))]):
                    archive.writestr(name, content)
            payload = encoded.getvalue()
        paths = [receipt_fixtures.ReceiptOriginalParity.put_original(
            self, reg, payload, f"Original-{index}.zip") for index in range(copies)]
        # Exercise the real crash-recovery insertion. A fabricated collector
        # container alone did not reproduce the persistent false retry.
        self.assertEqual(originals._restore_intake_rows(self.ns, reg), copies)
        _, rows = general.existing(self.ns, reg)
        self.assertEqual([row["status"] for row in rows], ["downloaded_no_text"] * copies)
        return reg, paths

    async def finish_archive_request(self, reg, *, status="completed", unreadable=0,
                                     read=1, expected=1, received=1, incomplete=False,
                                     findings=1):
        general.initialize(self.ns)
        self.activate()
        claimed, state, detail = await self.ordinary()
        self.assertEqual(claimed, reg)
        await runtime.release(self.ns, claimed)
        self.assertEqual(detail["documents_unreadable"], unreadable)
        self.assertEqual(detail["documents_read"], read)
        self.assertEqual(detail["original_expected"], expected)
        self.assertEqual(detail["original_received"], received)
        self.assertEqual(detail["source_incomplete"], incomplete)
        self.assertEqual(self.member(reg)["status"], status)
        self.assertEqual(bool(self.member(reg)["full_verified_read"]), status == "completed")
        self.assertEqual(self.findings(reg), findings)
        return detail

    async def test_actual_intake_restored_archive_completes_without_rewriting_evidence(self):
        reg, paths = self.restored_archive()
        with self.connect() as c:
            rows_before = [tuple(row) for row in c.execute("SELECT * FROM docs WHERE reg_number=?", (reg,))]
        bytes_before = {path: path.read_bytes() for path in [*paths, paths[0].parent / "receipt.json"]}
        _, rows = general.existing(self.ns, reg)
        self.assertEqual(await general.stored_docs(self.ns, reg, rows), ([], 1))
        detail = await self.finish_archive_request(reg)
        self.assertEqual((detail["documents_found"], detail["verified_original_documents"]), (2, 1))
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(self.member(reg)["attempt_count"], 1)
        with self.connect() as c:
            self.assertEqual([tuple(row) for row in c.execute("SELECT * FROM docs WHERE reg_number=?", (reg,))],
                             rows_before)
        self.assertEqual({path: path.read_bytes() for path in bytes_before}, bytes_before)

    async def test_restored_archive_duplicate_receipt_aliases_and_rows_do_not_inflate_findings(self):
        reg, _ = self.restored_archive(copies=2)
        with self.connect() as c:
            c.execute("INSERT INTO docs(reg_number,name,url,doc_type,local_path,status,metadata_json,final_url) "
                      "SELECT reg_number,name,url,doc_type,local_path,status,metadata_json,final_url "
                      "FROM docs WHERE reg_number=? ORDER BY id LIMIT 1", (reg,))
        _, rows = general.existing(self.ns, reg)
        self.assertEqual(await general.stored_docs(self.ns, reg, rows), ([], 3))
        detail = await self.finish_archive_request(reg, expected=2, received=2)
        self.assertEqual((detail["documents_found"], detail["new_findings"]), (2, 1))
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.findings(reg), 1)

    async def test_restored_archive_multiple_readable_members_preserve_original_counts(self):
        reg, _ = self.restored_archive(members=[
            ("Late.docx", receipt_fixtures.docx_bytes(LATE_REPLY)),
            ("Timely.docx", receipt_fixtures.docx_bytes(receipt_fixtures.TIMELY_REPLY))])
        detail = await self.finish_archive_request(reg, read=2)
        self.assertEqual((detail["documents_found"], detail["verified_original_documents"]), (3, 2))

    async def test_collected_archive_parent_status_and_legacy_url_still_require_same_proof(self):
        reg, _ = self.restored_archive()
        collected = await incoming.offline_audit(self.ns, reg, collect_only=True)
        parent = next(doc for doc in collected["docs"] if doc["type"] == "archive")
        for status in ("checked", "downloaded_no_text"):
            with self.subTest(status=status):
                meta = dict(parent["meta"])
                meta.pop("source_url")  # Legacy rows use docs.url.
                with self.connect() as c:
                    c.execute("UPDATE docs SET status=?,metadata_json=? WHERE reg_number=?",
                              (status, json.dumps(meta), reg))
                _, rows = general.existing(self.ns, reg)
                result = await general.collect_saved_evidence(self.ns, reg, rows)
                self.assertEqual((result["unreadable"], result["source_incomplete"]), (0, False))
        await self.finish_archive_request(reg)

    async def test_expanded_archive_does_not_forgive_failed_nonarchive_original(self):
        reg, _ = self.restored_archive()
        receipt_fixtures.ReceiptOriginalParity.put_original(self, reg, b"not a docx", "Broken.docx")
        self.assertEqual(originals._restore_intake_rows(self.ns, reg), 1)
        await self.finish_archive_request(reg, status="retry", unreadable=1,
                                          expected=2, received=2, incomplete=True)

    async def test_failed_stored_archive_member_remains_unreadable_after_parent_expands(self):
        reg, _ = self.restored_archive()
        collected = await incoming.offline_audit(self.ns, reg, collect_only=True)
        member = next(doc for doc in collected["docs"] if doc["status"] == "checked")
        meta = {**member["meta"], "sha256": "0" * 64}
        with self.connect() as c:
            c.execute("INSERT INTO docs(reg_number,name,url,doc_type,local_path,status,metadata_json,final_url) "
                      "VALUES(?,?,?,?,?,'checked',?,?)", (reg, member["name"], member["url"],
                      member["type"], member["local"], json.dumps(meta), member["final_url"]))
        await self.finish_archive_request(reg, status="retry", unreadable=1)

    async def test_restored_archive_partial_extraction_and_path_security_warnings_retry(self):
        for name, content in (("Broken.docx", b"not a docx"),
                              ("../Traversal.docx", receipt_fixtures.docx_bytes(LATE_REPLY))):
            with self.subTest(member=name):
                reg, _ = self.restored_archive(len(self.regs) + 1, members=[
                    ("Late.docx", receipt_fixtures.docx_bytes(LATE_REPLY)), (name, content)])
                _, rows = general.existing(self.ns, reg)
                result = await general.collect_saved_evidence(self.ns, reg, rows)
                self.assertEqual(result["unreadable"], 1)
                self.assertTrue(result["source_incomplete"])
        # The request receipt must retain the partial result, not just the
        # collector's warning. Review both real procurement fixtures.
        general.initialize(self.ns)
        self.activate()
        for reg in self.regs:
            claimed, _, detail = await self.ordinary()
            self.assertEqual(claimed, reg)
            await runtime.release(self.ns, claimed)
            self.assertEqual(self.member(reg)["status"], "retry")
            self.assertEqual(detail["documents_unreadable"], 1)
            self.assertEqual(self.findings(reg), 1)

    async def test_bad_parent_metadata_and_paths_keep_their_unreadable_contribution(self):
        reg, paths = self.restored_archive()
        _, rows = general.existing(self.ns, reg)
        row = rows[0]
        meta = json.loads(row["metadata_json"])
        wrong_path = self.folder / "copy.zip"
        wrong_path.write_bytes(paths[0].read_bytes())
        loop_path = self.folder / "loop.zip"
        loop_path.symlink_to(loop_path)
        outside = tempfile.TemporaryDirectory(prefix="archive-outside-")
        self.addCleanup(outside.cleanup)
        outside_path = Path(outside.name) / "outside.zip"
        outside_path.write_bytes(paths[0].read_bytes())
        variants = [
            {"metadata_json": "not JSON"},
            {"metadata_json": "[]"},
            {"metadata_json": json.dumps({**meta, "sha256": "0" * 64})},
            {"metadata_json": json.dumps({**meta, "sha256": None})},
            {"metadata_json": json.dumps({**meta, "source": "existing_local_unverified"})},
            {"metadata_json": json.dumps({**meta, "uid": "F" * 32})},
            {"metadata_json": json.dumps({**meta, "source_url": row["url"][:-32] + "F" * 32})},
            {"local_path": str(wrong_path)},
            {"local_path": str(self.folder / "missing.zip")},
            {"local_path": str(loop_path)},
            {"local_path": str(outside_path)},
        ]
        for changed in variants:
            with self.subTest(changed=changed):
                result = await general.collect_saved_evidence(self.ns, reg, [{**row, **changed}])
                self.assertEqual(result["unreadable"], 1)
                self.assertFalse(result["source_incomplete"])
        # A valid duplicate must remove only its own count, leaving this stale
        # copy's blocker in the real requested completion gate.
        with self.connect() as c:
            c.execute("INSERT INTO docs(reg_number,name,url,doc_type,local_path,status,metadata_json,final_url) "
                      "SELECT reg_number,name,url,doc_type,?,status,metadata_json,final_url "
                      "FROM docs WHERE reg_number=?", (str(wrong_path), reg))
        await self.finish_archive_request(reg, status="retry", unreadable=1)

    async def test_archive_parent_size_guard_is_not_relaxed_by_successful_expansion(self):
        reg, paths = self.restored_archive()
        _, rows = general.existing(self.ns, reg)
        with patch.object(general, "MAX_BYTES", paths[0].stat().st_size - 1):
            result = await general.collect_saved_evidence(self.ns, reg, rows)
        self.assertEqual(result["unreadable"], 1)
        self.assertFalse(result["source_incomplete"])
        self.assertTrue(any(doc["status"] == "archive_processed" for doc in result["docs"]))

    async def test_malformed_archive_cannot_complete_from_parent_status(self):
        reg, _ = self.restored_archive(payload=b"not a zip archive")
        await self.finish_archive_request(reg, status="retry", unreadable=1, read=0,
                                          incomplete=True, findings=0)

    async def test_tampered_archive_and_receipt_fail_closed_without_losing_other_finding(self):
        cases = []
        for number, kind in enumerate(("bytes", "receipt", "missing"), 1):
            reg, paths = self.restored_archive(number)
            self.document(reg, LATE_REPLY)
            if kind == "bytes":
                payload = paths[0].read_bytes()
                paths[0].write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
            elif kind == "receipt":
                receipt_path = paths[0].parent / "receipt.json"
                value = json.loads(receipt_path.read_text())
                value["sha256"] = "0" * 64
                receipt_path.write_text(json.dumps(value))
            else:
                paths[0].unlink()
            cases.append((reg, kind))
        general.initialize(self.ns)
        self.activate()
        for reg, kind in cases:
            with self.subTest(kind=kind):
                claimed, _, detail = await self.ordinary()
                self.assertEqual(claimed, reg)
                await runtime.release(self.ns, claimed)
                self.assertEqual(detail["documents_unreadable"], 1)
                self.assertEqual(self.member(reg)["status"],
                                 "retry" if kind == "bytes" else "waiting_originals")
                self.assertFalse(self.member(reg)["full_verified_read"])
                self.assertEqual(self.findings(reg), 1)

    async def test_malformed_archive_receipt_still_blocks_snapshot_and_parent_accounting(self):
        reg, paths = self.restored_archive()
        self.document(reg, LATE_REPLY)
        (paths[0].parent / "receipt.json").write_text("invalid receipt")
        _, rows = general.existing(self.ns, reg)
        result = await general.collect_saved_evidence(self.ns, reg, rows)
        self.assertEqual(result["unreadable"], 1)
        general.initialize(self.ns)
        self.activate()
        claimed = await general.claim_safely(self.ns)
        await runtime.attach_general(self.ns, claimed)
        with self.assertRaises(general.SemanticSnapshotError):
            general.review_watermark(self.ns, claimed)
        await runtime.release(self.ns, claimed)
        self.assertEqual(self.member(reg)["status"], "retry")
        self.assertFalse(self.member(reg)["full_verified_read"])

    async def test_stored_only_forged_alias_cannot_reconcile_tampered_archive_parent(self):
        reg, paths = self.restored_archive(copies=2)
        original_bytes = paths[0].read_bytes()
        paths[1].write_bytes(original_bytes[:-1] + bytes([original_bytes[-1] ^ 1]))
        _, rows = general.existing(self.ns, reg)
        alias = json.loads(rows[1]["metadata_json"])
        # A real readable local TXT copy has identical binary SHA and carries
        # an untrusted alias. merge_docs preserves it for source display, but
        # it must never become fresh archive-expansion proof.
        copied = self.folder / "stored-copy.txt"
        copied.write_bytes(original_bytes)
        extract = self.ns["extract_text"]
        self.ns["extract_text"] = lambda path: (Path(path).read_text(errors="replace")
                                               if Path(path).suffix == ".txt" else extract(path))
        meta = {"source": "existing_local_unverified", "sha256": alias["sha256"],
                "identical_sources": [{"url": alias["source_url"], "uid": alias["uid"]}]}
        with self.connect() as c:
            c.execute("INSERT INTO docs(reg_number,name,url,doc_type,local_path,status,metadata_json) "
                      "VALUES(?,?,'','other',?,'checked',?)",
                      (reg, copied.name, str(copied), json.dumps(meta)))
        _, rows = general.existing(self.ns, reg)
        stored, unreadable = await general.stored_docs(self.ns, reg, rows)
        self.assertEqual((len(stored), unreadable), (1, 2))
        source = await general.collect_saved_evidence(self.ns, reg, rows)
        parent = next(doc for doc in source["docs"] if doc["status"] == "archive_processed")
        self.assertTrue(any(item.get("uid") == alias["uid"]
                            for item in parent["meta"]["identical_sources"]))
        self.assertEqual(source["unreadable"], 1)
        await self.finish_archive_request(reg, status="retry", unreadable=1,
                                          expected=2, received=2, incomplete=True)

    async def test_current_expansion_requires_a_readable_member_with_matching_archive_proof(self):
        reg, _ = self.restored_archive()
        _, rows = general.existing(self.ns, reg)
        collected = await incoming.offline_audit(self.ns, reg, collect_only=True)
        parent = next(doc for doc in collected["docs"] if doc["type"] == "archive")
        member = next(doc for doc in collected["docs"] if doc["status"] == "checked")
        variants = [[], [{**member, "text": ""}],
                    [{**member, "meta": {**member["meta"], "archive_sha256": "0" * 64}}],
                    [{**member, "meta": {**member["meta"], "uid": "F" * 32}}],
                    [{**member, "meta": {**member["meta"], "source": "existing_local_unverified"}}]]
        for children in variants:
            with self.subTest(children=children):
                with patch.object(general, "offline_audit", return_value={"docs": [parent, *children]}):
                    result = await general.collect_saved_evidence(self.ns, reg, rows)
                self.assertEqual(result["unreadable"], 1)

    async def test_receipt_original_symlink_outside_root_keeps_path_guard(self):
        reg, paths = self.restored_archive()
        outside = tempfile.TemporaryDirectory(prefix="archive-original-outside-")
        self.addCleanup(outside.cleanup)
        outside_path = Path(outside.name) / paths[0].name
        outside_path.write_bytes(paths[0].read_bytes())
        paths[0].unlink()
        paths[0].symlink_to(outside_path)
        _, rows = general.existing(self.ns, reg)
        source = await general.collect_saved_evidence(self.ns, reg, rows)
        self.assertEqual(source["unreadable"], 1)
        self.assertTrue(any(doc["status"] == "archive_processed" for doc in source["docs"]))

    async def test_failed_archive_collection_never_clears_restored_parent_count(self):
        reg, _ = self.restored_archive()
        self.document(reg, LATE_REPLY)
        _, rows = general.existing(self.ns, reg)
        for error in (asyncio.TimeoutError(), ValueError("collection failed")):
            with self.subTest(error=type(error).__name__):
                with patch.object(general, "offline_audit", side_effect=error):
                    source = await general.collect_saved_evidence(self.ns, reg, rows)
                self.assertEqual(source["unreadable"], 1)
                self.assertTrue(source["source_incomplete"])


if __name__ == "__main__":
    unittest.main()
