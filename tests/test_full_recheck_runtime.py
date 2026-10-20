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

    async def test_explicit_all_saved_includes_off_dashboard_and_manual_without_reset(self):
        regs = [self.purchase(i) for i in range(1, 4)]
        general.initialize(self.ns)
        with self.connect() as c:
            c.execute("INSERT INTO v319_manual_inputs(reg) VALUES(?)", (regs[1],))
            c.execute("DELETE FROM v332_audit_queue WHERE reg=?", (regs[2],))
            original = [tuple(row) for row in c.execute("SELECT * FROM purchases ORDER BY reg_number")]
        with patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.ALL_SAVED_EPOCH}):
            runtime.initialize(self.ns)
        with self.connect() as c:
            summary = ledger.inventory(c, runtime.ALL_SAVED_EPOCH)
            self.assertEqual(summary["actual_count"], 3)
            self.assertEqual(summary["expected_count"], 3)
            self.assertEqual(summary["manual_excluded_count"], 0)
            self.assertEqual(summary["current_completed"], 0)
            self.assertEqual([tuple(row) for row in c.execute("SELECT * FROM purchases ORDER BY reg_number")], original)
            self.assertEqual(c.execute("SELECT reg FROM v319_manual_inputs").fetchone()[0], regs[1])
        self.assertEqual(runtime.request_id(self.ns), runtime.ALL_SAVED_EPOCH)

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

    async def _assert_generation_transition(self, previous, current):
        reg = self.purchase()
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        general.initialize(self.ns)
        with patch.object(runtime, "TARGET_GENERATION", previous), patch.object(general, "engine_signature", return_value=f"previous-generation-{previous}-engine"):
            self.activate()
            self.assertTrue(await runtime.run_one(self.ns, {}))
            with self.connect() as c:
                before = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
            old_attempt = self.member(reg)["attempt_id"]
        self.assertEqual(runtime.TARGET_GENERATION, current)
        runtime.initialize(self.ns)
        self.assertEqual(self.member(reg)["reason"], "engine_changed")
        self.assertTrue(await runtime.run_one(self.ns, {}))
        runtime.initialize(self.ns)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        with self.connect() as c:
            after = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
            receipts = c.execute("SELECT attempt_id FROM full_recheck_receipts WHERE reg=?", (reg,)).fetchall()
        self.assertEqual((after["target_generation"], after["current_completed"], after["current_pending"]), (current, 1, 0))
        self.assertEqual(after["target_engine_signature"], general.engine_signature())
        self.assertEqual(after["membership_digest"], before["membership_digest"])
        self.assertEqual(after["db_snapshot_at"], before["db_snapshot_at"])
        self.assertEqual((after["unique_attempted"], after["total_attempts"]), (1, 2))
        self.assertEqual(len(receipts), 2)
        self.assertIn(old_attempt, [row[0] for row in receipts])
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(after["committed_new_findings"], before["committed_new_findings"])

    async def test_generation_three_reconciles_existing_generation_two_receipt_once(self):
        with patch.object(runtime, "TARGET_GENERATION", 3):
            await self._assert_generation_transition(2, 3)

    async def test_generation_four_reconciles_generation_three_price_receipt_once(self):
        with patch.object(runtime, "TARGET_GENERATION", 4):
            await self._assert_generation_transition(3, 4)

    async def test_generation_five_reconciles_generation_four_date_receipt_once(self):
        with patch.object(runtime, "TARGET_GENERATION", 5):
            await self._assert_generation_transition(4, 5)

    async def test_generation_six_reconciles_generation_five_local_event_receipt_once(self):
        with patch.object(runtime, "TARGET_GENERATION", 6):
            await self._assert_generation_transition(5, 6)

    async def test_generation_seven_reconciles_generation_six_intake_receipt_once(self):
        with patch.object(runtime, "TARGET_GENERATION", 7):
            await self._assert_generation_transition(6, 7)

    async def test_generation_eight_reconciles_documented_explanation_identity_once(self):
        with patch.object(runtime, "TARGET_GENERATION", 8):
            await self._assert_generation_transition(7, 8)

    async def test_generation_nine_reconciles_legal_fact_and_label_receipt_once(self):
        with patch.object(runtime, "TARGET_GENERATION", 9):
            await self._assert_generation_transition(8, 9)

    async def test_generation_ten_reads_documented_explanation_attachments(self):
        with patch.object(runtime, "TARGET_GENERATION", 10):
            await self._assert_generation_transition(9, 10)

    async def test_generation_eleven_preserves_concurrent_source_guard(self):
        with patch.object(runtime, "TARGET_GENERATION", 11):
            await self._assert_generation_transition(10, 11)

    async def test_generation_twelve_verifies_clarification_publication_once(self):
        self.assertEqual(runtime.TARGET_GENERATION, 12)
        await self._assert_generation_transition(11, 12)

    async def _assert_pending_and_active_generation_transition(self, previous, current):
        regs = [self.purchase(number) for number in range(1, 4)]
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение'")
        general.initialize(self.ns)
        with patch.object(runtime, "TARGET_GENERATION", previous), patch.object(
                general, "engine_signature", return_value=f"previous-generation-{previous}-engine"):
            self.activate()
            self.assertTrue(await runtime.run_one(self.ns, {}))
            self.assertEqual(self.member(regs[0])["status"], "completed")
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                old_claim = ledger.claim_member(c, runtime.AUTHORIZED_EPOCH, regs[1],
                    engine_signature=general.engine_signature(), generation=previous)
                before = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
            active, pending = self.member(regs[1]), self.member(regs[2])
        self.assertIsNotNone(old_claim)
        self.assertEqual(runtime.TARGET_GENERATION, current)
        runtime.initialize(self.ns)
        self.assertEqual(self.member(regs[1]), active)
        self.assertEqual(self.member(regs[2]), pending)
        self.assertEqual(self.member(regs[0])["reason"], "engine_changed")
        reopened = self.member(regs[0])
        runtime.initialize(self.ns)
        self.assertEqual(self.member(regs[0]), reopened)
        self.assertEqual(self.member(regs[1]), active)
        self.assertEqual(self.member(regs[2]), pending)
        with self.connect() as c:
            after = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
            receipts = c.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0]
        self.assertEqual(after["target_generation"], current)
        self.assertEqual(after["target_engine_signature"], general.engine_signature())
        self.assertEqual(after["membership_digest"], before["membership_digest"])
        self.assertEqual(after["db_snapshot_at"], before["db_snapshot_at"])
        self.assertEqual(after["total_attempts"], before["total_attempts"])
        self.assertEqual((after["current_completed"], receipts), (0, 1))
        self.assertEqual(self.findings(regs[0]), 1)

    async def test_generation_five_keeps_pending_member_and_active_generation_four_lease(self):
        with patch.object(runtime, "TARGET_GENERATION", 5):
            await self._assert_pending_and_active_generation_transition(4, 5)

    async def test_generation_six_keeps_pending_member_and_active_generation_five_lease(self):
        with patch.object(runtime, "TARGET_GENERATION", 6):
            await self._assert_pending_and_active_generation_transition(5, 6)

    async def test_generation_seven_keeps_pending_member_and_active_generation_six_lease(self):
        with patch.object(runtime, "TARGET_GENERATION", 7):
            await self._assert_pending_and_active_generation_transition(6, 7)

    async def test_generation_eight_keeps_pending_member_and_active_generation_seven_lease(self):
        with patch.object(runtime, "TARGET_GENERATION", 8):
            await self._assert_pending_and_active_generation_transition(7, 8)

    async def test_generation_nine_preserves_existing_membership_receipts_and_active_lease(self):
        with patch.object(runtime, "TARGET_GENERATION", 9):
            await self._assert_pending_and_active_generation_transition(8, 9)

    async def test_generation_ten_preserves_old_pending_and_active_leases(self):
        with patch.object(runtime, "TARGET_GENERATION", 10):
            await self._assert_pending_and_active_generation_transition(9, 10)

    async def test_generation_eleven_preserves_generation_ten_membership_and_live_lease(self):
        with patch.object(runtime, "TARGET_GENERATION", 11):
            await self._assert_pending_and_active_generation_transition(10, 11)

    async def test_generation_twelve_keeps_pending_and_live_generation_eleven(self):
        self.assertEqual(runtime.TARGET_GENERATION, 12)
        await self._assert_pending_and_active_generation_transition(11, 12)

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
        with patch.object(runtime, "TARGET_GENERATION", runtime.TARGET_GENERATION + 1), patch.object(general, "engine_signature", return_value="corrected-engine"):
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
        with patch.object(runtime, "TARGET_GENERATION", runtime.TARGET_GENERATION + 1), patch.object(general, "engine_signature", return_value="corrected-engine"):
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
            ledger.activate_target(c, runtime.AUTHORIZED_EPOCH, generation=runtime.TARGET_GENERATION + 1, engine_signature="new-engine")
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
        with self.assertRaisesRegex(runtime.RequestOwnershipBusy, "request_claim_unavailable"):
            await runtime.attach_general(self.ns, claimed)
        self.assertFalse(hasattr(claimed, "full_recheck_claim"))
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.row(reg), before)
        self.assertEqual(self.member(reg)["attempt_count"], 0)
        self.assertEqual(self.findings(reg), 0)
        self.assertEqual(general.defer_retry(self.ns, claimed, {}, "full_request_ownership_busy",
                                            general.BUSY_RETRY_BASE_SECONDS), 60)
        with patch.object(general.time, "time", return_value=self.row(reg)["next_retry_at"] + 1):
            fresh = await general.claim_safely(self.ns)
        self.assertNotEqual(fresh.reservation_token, claimed.reservation_token)
        await self.ordinary(fresh)
        await runtime.release(self.ns, fresh)
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual((self.member(reg)["attempt_count"], self.member(reg)["total_new_findings"]), (1, 1))
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

    async def test_first_cancellation_during_successful_turn_cleanup_propagates(self):
        await self.cleanup_cancellation_case(repeated=False)

    async def test_repeated_cancellation_during_successful_turn_cleanup_propagates(self):
        await self.cleanup_cancellation_case(repeated=True)

    async def test_cancellation_survives_reported_cleanup_error(self):
        await self.cleanup_cancellation_case(repeated=True, cleanup_error=True)

    async def test_prior_analysis_cancellation_survives_cleanup_error(self):
        await self.cleanup_cancellation_case(repeated=False, cleanup_error=True, prior_cancel=True)

    async def cleanup_cancellation_case(self, *, repeated, cleanup_error=False, prior_cancel=False):
        import threading
        reg = self.purchase()
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        general.initialize(self.ns)
        self.activate()
        entered, allow_cleanup = threading.Event(), threading.Event()
        real_release = runtime._release_member
        calls = []
        def held_release(ns, claimed):
            calls.append(claimed)
            entered.set()
            if not allow_cleanup.wait(5):
                raise AssertionError("cleanup barrier not released")
            return real_release(ns, claimed)
        def unlock_then_error(handle):
            _release_execution_lock(handle)
            raise OSError("private fixture detail must not be logged")
        slot = {}
        analysis_started = asyncio.Event()
        async def held_analysis(*args):
            analysis_started.set()
            await asyncio.Event().wait()
        if cleanup_error:
            self.stack.enter_context(patch.object(originals, "_release_execution_lock", side_effect=unlock_then_error))
        if prior_cancel:
            self.stack.enter_context(patch.object(general, "inspect_one", side_effect=held_analysis))
        with patch.object(runtime, "_release_member", side_effect=held_release):
            task = asyncio.create_task(runtime.run_one(self.ns, slot))
            try:
                if prior_cancel:
                    await asyncio.wait_for(analysis_started.wait(), 3)
                    task.cancel()
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                # The actual analysis has already committed one durable receipt;
                # cancellation must neither undo it nor abandon its file lock.
                before = self.member(reg)
                self.assertEqual(before["status"], "running" if prior_cancel else "completed")
                self.assertEqual(self.findings(reg), 0 if prior_cancel else 1)
                if not prior_cancel:
                    task.cancel()
                    await asyncio.sleep(0)
                if repeated:
                    task.cancel()
                    await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertIsNone(_try_execution_lock(reg))
                self.assertEqual(runtime._dispatch(self.ns)["active"], 1)
                allow_cleanup.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            finally:
                allow_cleanup.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        if cleanup_error:
            self.assertIn("FULL_RECHECK_RELEASE cleanup_failed=True type=OSError", self.output.getvalue())
            self.assertNotIn("private fixture detail", self.output.getvalue())
        self.assertEqual(len(calls), 1)
        if prior_cancel:
            self.assertEqual(self.member(reg)["status"], "retry")
            self.assertEqual(self.member(reg)["reason"], "interrupted_attempt")
            self.assertEqual(self.member(reg)["attempt_count"], 1)
        else:
            self.assertEqual(self.member(reg), before)
        self.assertEqual(self.findings(reg), 0 if prior_cancel else 1)
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0], 0 if prior_cancel else 1)
        self.assertEqual(runtime._dispatch(self.ns)["active"], 0)
        self.assertEqual(slot["phase"], "full_request_waiting")
        self.assertIsNone(slot["reg"])
        physical = _try_execution_lock(reg)
        self.assertIsNotNone(physical)
        _release_execution_lock(physical)
        self.assertFalse(await runtime.run_one(self.ns, {}))

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

    async def test_stale_page_rechecks_ordinary_owner_before_taking_flock(self):
        import threading
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        ready, advance = threading.Event(), threading.Event()
        locked, unlock = asyncio.Event(), asyncio.Event()
        candidates, lock = runtime._candidates, runtime._lock
        explicit = None
        def page(ns):
            result = candidates(ns)
            ready.set()
            if not advance.wait(5): raise TimeoutError('candidate barrier')
            return result
        async def tracked_lock(number):
            result = await lock(number)
            if asyncio.current_task() is explicit and result is not None:
                locked.set()
                try:
                    await unlock.wait()
                except BaseException:
                    _release_execution_lock(result)
                    raise
            return result
        async def selector_stopped_or_locked():
            while not explicit.done() and not locked.is_set():
                await asyncio.sleep(0)
        with patch.object(runtime, '_candidates', side_effect=page), patch.object(runtime, '_lock', side_effect=tracked_lock):
            explicit = asyncio.create_task(runtime.run_one(self.ns, {}))
            try:
                self.assertTrue(await asyncio.to_thread(ready.wait, 5))
                ordinary = await general.claim_safely(self.ns)
                advance.set()
                await asyncio.wait_for(selector_stopped_or_locked(), 5)
                await runtime.attach_general(self.ns, ordinary)
                self.assertIsNotNone(getattr(ordinary, 'full_recheck_claim', None))
                self.assertFalse(locked.is_set())
                self.assertFalse(await explicit)
            finally:
                advance.set(); unlock.set()
                if not explicit.done(): explicit.cancel()
                await asyncio.gather(explicit, return_exceptions=True)
        # A second attachment call is idempotent, including its renewal task.
        renewal = ordinary.full_recheck_renew
        _, state, detail = await self.ordinary(ordinary)
        self.assertIs(ordinary.full_recheck_renew, renewal)
        await runtime.release(self.ns, ordinary)
        self.assertEqual((state, detail['new_findings']), ('completed', 1))
        self.assertEqual((self.member(reg)['attempt_count'], self.member(reg)['total_new_findings']), (1, 1))
        self.assertEqual(self.findings(reg), 1)

    async def test_post_read_ordinary_claim_defers_without_unowned_analysis(self):
        import threading
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET state='completed' WHERE reg=?", (reg,))
        checked, proceed = threading.Event(), threading.Event()
        locked, unlock = asyncio.Event(), asyncio.Event()
        pending, lock = runtime._ordinary_pending, runtime._lock
        explicit = None
        def stale_check(ns, number):
            result = pending(ns, number)
            self.assertFalse(result[0])
            checked.set()
            if not proceed.wait(5): raise TimeoutError('ordinary-state barrier')
            return result
        async def held_lock(number):
            result = await lock(number)
            if asyncio.current_task() is explicit and result is not None:
                locked.set()
                try:
                    await unlock.wait()
                except BaseException:
                    _release_execution_lock(result)
                    raise
            return result
        with patch.object(runtime, '_ordinary_pending', side_effect=stale_check), patch.object(runtime, '_lock', side_effect=held_lock):
            explicit = asyncio.create_task(runtime.run_one(self.ns, {}))
            try:
                self.assertTrue(await asyncio.to_thread(checked.wait, 5))
                with self.connect() as c:
                    c.execute("UPDATE v332_audit_queue SET state='pending' WHERE reg=?", (reg,))
                ordinary = await general.claim_safely(self.ns)
                proceed.set()
                await asyncio.wait_for(locked.wait(), 5)
                with self.assertRaisesRegex(runtime.RequestOwnershipBusy, 'request_execution_busy'):
                    await runtime.attach_general(self.ns, ordinary)
                self.assertEqual(self.findings(reg), 0)
                self.assertEqual(self.member(reg)['attempt_count'], 0)
                unlock.set()
                self.assertFalse(await asyncio.wait_for(explicit, 5))
            finally:
                proceed.set(); unlock.set()
                if not explicit.done(): explicit.cancel()
                await asyncio.gather(explicit, return_exceptions=True)
        delay = general.defer_retry(self.ns, ordinary, {}, 'full_request_ownership_busy', general.BUSY_RETRY_BASE_SECONDS)
        self.assertEqual(delay, 60)
        self.assertIsNone(general.claim_next(self.ns))
        runtime.initialize(self.ns)
        with patch.object(general.time, 'time', return_value=self.row(reg)['next_retry_at'] + 1):
            fresh = await general.claim_safely(self.ns)
        self.assertNotEqual(fresh.reservation_token, ordinary.reservation_token)
        before = self.row(reg)
        # A late old-owner deferral must not overwrite the fresh reservation.
        general.defer_retry(self.ns, ordinary, {}, 'stale-owner', general.BUSY_RETRY_BASE_SECONDS)
        self.assertEqual(self.row(reg), before)
        _, state, detail = await self.ordinary(fresh)
        await runtime.release(self.ns, fresh)
        self.assertEqual((state, detail['new_findings'], self.findings(reg)), ('completed', 1, 1))
        self.assertEqual((self.member(reg)['attempt_count'], self.member(reg)['total_new_findings']), (1, 1))
        with self.assertRaisesRegex(LookupError, 'reservation superseded'):
            general.persist(self.ns, ordinary, [], [], 'stale-ordinary-token')

    async def test_active_foreign_member_never_becomes_unattached_ordinary_work(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        ordinary = await general.claim_safely(self.ns)
        held = _try_execution_lock(reg)
        self.assertIsNotNone(held)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            foreign = ledger.claim_member(c, runtime.AUTHORIZED_EPOCH, reg,
                general_token=ordinary.reservation_token, **runtime._target_args(self.ns))
        self.assertIsNotNone(foreign)
        before = self.member(reg)
        try:
            with self.assertRaisesRegex(runtime.RequestOwnershipBusy, 'request_execution_busy'):
                await runtime.attach_general(self.ns, ordinary)
        finally:
            _release_execution_lock(held)
        # A durable active lease without a live local flock is still fenced.
        with self.assertRaisesRegex(runtime.RequestOwnershipBusy, 'request_claim_unavailable'):
            await runtime.attach_general(self.ns, ordinary)
        runtime.initialize(self.ns)
        self.assertEqual(self.member(reg), before)
        self.assertEqual(self.findings(reg), 0)
        self.assertIsNone(getattr(ordinary, 'full_recheck_claim', None))
        available = _try_execution_lock(reg)
        self.assertIsNotNone(available)
        _release_execution_lock(available)

    async def test_attachment_rechecks_completed_blocked_and_absent_members(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        owned, _, _ = await self.ordinary()
        await runtime.release(self.ns, owned)
        completed = self.member(reg)
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET state='pending' WHERE reg=?", (reg,))
        ordinary = await general.claim_safely(self.ns)
        attachable = runtime._attachable
        for terminal in ('completed', 'blocked', 'absent'):
            with self.subTest(terminal=terminal):
                with self.connect() as c:
                    if terminal == 'absent': c.execute('DELETE FROM full_recheck_members WHERE reg=?', (reg,))
                    else: c.execute('UPDATE full_recheck_members SET status=? WHERE reg=?', (terminal, reg))
                calls = 0
                def stale_read(ns, number):
                    nonlocal calls
                    calls += 1
                    return True if calls == 1 else attachable(ns, number)
                # Lost flock path and claim-None path both revalidate status.
                for busy in (True, False):
                    calls = 0
                    held = _try_execution_lock(reg) if busy else None
                    try:
                        with patch.object(runtime, '_attachable', side_effect=stale_read):
                            await runtime.attach_general(self.ns, ordinary)
                        self.assertEqual(calls, 2)
                        self.assertIsNone(getattr(ordinary, 'full_recheck_claim', None))
                    finally:
                        _release_execution_lock(held)
                if terminal == 'completed':
                    self.assertEqual(self.member(reg), completed)
        self.assertEqual(self.findings(reg), 1)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM full_recheck_receipts').fetchone()[0], 1)
        await general.release_reservation(self.ns, ordinary)

    async def test_cancelled_attachment_settles_exact_claim_before_restart(self):
        import threading
        reg = self.purchase()
        general.initialize(self.ns)
        self.activate()
        ordinary = await general.claim_safely(self.ns)
        committed, finish = threading.Event(), threading.Event()
        attach = runtime._attach
        def delayed(ns, number):
            result = attach(ns, number)
            committed.set()
            if not finish.wait(5): raise TimeoutError('attach commit barrier')
            return result
        with patch.object(runtime, '_attach', side_effect=delayed):
            task = asyncio.create_task(runtime.attach_general(self.ns, ordinary))
            try:
                self.assertTrue(await asyncio.to_thread(committed.wait, 5))
                before = self.member(reg)
                task.cancel(); await asyncio.sleep(0); task.cancel(); await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertIsNone(_try_execution_lock(reg))
                self.assertEqual(self.member(reg)['lease_token'], before['lease_token'])
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            finally:
                finish.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual((self.member(reg)['status'], self.member(reg)['reason']), ('retry', 'interrupted_attempt'))
        self.assertEqual(self.findings(reg), 0)
        await general.release_reservation(self.ns, ordinary)
        runtime.initialize(self.ns)
        fresh, _, _ = await self.ordinary()
        await runtime.release(self.ns, fresh)
        self.assertEqual((self.member(reg)['attempt_count'], self.member(reg)['total_new_findings']), (2, 1))
        self.assertEqual(self.findings(reg), 1)
        with self.assertRaises(ledger.StaleClaim):
            general.persist(self.ns, ordinary, [], [], 'cancelled-old-claim')
        # An old attached object's attribute is not a fresh lease or a new
        # lock. Repeated attachment keeps it fenced before the analyzer.
        from unittest.mock import Mock
        analyzer = self.ns['analyze']
        watched = Mock(wraps=analyzer)
        self.ns['analyze'] = watched
        try:
            await runtime.attach_general(self.ns, ordinary)
            self.assertIsNone(ordinary.full_recheck_lock)
            with self.assertRaises(ledger.StaleClaim):
                await general.inspect_one(self.ns, ordinary)
            watched.assert_not_called()
        finally:
            self.ns['analyze'] = analyzer

    async def test_foreign_process_flock_defers_one_worker_and_preserves_other_progress(self):
        import subprocess
        import sys
        from contextlib import ExitStack
        regs = [self.purchase(i) for i in (1, 2)]
        base = general.initialize(self.ns)
        self.activate()
        with self.connect() as c:
            c.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) VALUES(?,'old','violation','Historical fact','old evidence')", (regs[0],))
            c.execute("UPDATE v332_audit_queue SET new_findings=5,new_checks=7,last_result_token='historical',last_counted_token='historical' WHERE reg=?", (regs[0],))
            old_findings = [tuple(r) for r in c.execute('SELECT * FROM findings')]
        before = self.member(regs[0])
        code = ('import sys; from v295_eis_originals import _try_execution_lock, _release_execution_lock; '
                'lock=_try_execution_lock(sys.argv[1]); assert lock is not None; '
                'print("locked",flush=True); sys.stdin.readline(); _release_execution_lock(lock)')
        child = subprocess.Popen([sys.executable, '-c', code, regs[0]], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, 'PYTHONPATH': str(Path(general.__file__).parent)})
        campaign = None
        calls = []
        original = self.ns['analyze']
        def analyze(p, docs):
            calls.append(str(p['reg']))
            return original(p, docs)
        self.ns['analyze'] = analyze
        class HostileOwnershipBusy(runtime.RequestOwnershipBusy):
            def __str__(self):
                return 'PRIVATE_OWNER_DIAGNOSTIC_SENTINEL /tmp/private/customer'
        real_sleep = asyncio.sleep
        async def start_now(seconds, *args, **kwargs):
            return await real_sleep(0 if seconds == 14 else seconds, *args, **kwargs)
        async def until(predicate):
            while not predicate(): await real_sleep(.005)
        try:
            self.assertEqual(await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 5), 'locked\n')
            with ExitStack() as stack:
                stack.enter_context(patch.object(runtime, 'RequestOwnershipBusy', HostileOwnershipBusy))
                stack.enter_context(patch.object(general, 'initialize', return_value=base))
                stack.enter_context(patch.object(runtime, 'initialize', return_value=None))
                stack.enter_context(patch.object(runtime, 'run_one', return_value=False))
                stack.enter_context(patch.object(general, 'refresh_changed', return_value=0))
                stack.enter_context(patch.object(general, 'add_new_verified', return_value=0))
                stack.enter_context(patch.object(general.asyncio, 'sleep', side_effect=start_now))
                general.install(self.ns)
                await self.ns['app'].startups[0]()
                campaign = self.ns['_v332_campaign_worker']
                await asyncio.wait_for(until(lambda: self.row(regs[0])['retry_count'] == 1
                    and self.member(regs[1])['status'] == 'completed'
                    and all(s['phase'] == 'waiting_for_documents' and s['reg'] is None
                            for s in self.ns['_v332_runtime']['workers'].values())), 10)
                deferred = self.row(regs[0])
                self.assertEqual((deferred['state'], deferred['new_findings'], deferred['new_checks'],
                    deferred['last_result_token'], deferred['last_counted_token']), ('pending', 5, 7, 'historical', 'historical'))
                self.assertGreater(deferred['next_retry_at'], time.time() + 50)
                self.assertIn('full_request_ownership_busy', deferred['error'])
                self.assertEqual(self.member(regs[0]), before)
                self.assertEqual(calls, [regs[1]])
                with self.connect() as c:
                    self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM findings WHERE reg_number=?', (regs[0],))], old_findings)
                self.assertIsNone(_try_execution_lock(regs[0]))
                self.assertIn('V332 REQUEST_ATTACH_DEFERRED', self.output.getvalue())
                self.assertNotIn('V332 SQLITE_RETRY', self.output.getvalue())
                self.assertNotIn('PRIVATE_OWNER_DIAGNOSTIC_SENTINEL', self.output.getvalue())
                self.assertEqual(deferred['error'], 'full_request_ownership_busy')
                campaign.cancel()
                await asyncio.wait_for(asyncio.gather(campaign, return_exceptions=True), 5)
                campaign = None
        finally:
            if campaign is not None:
                campaign.cancel()
                await asyncio.wait_for(asyncio.gather(campaign, return_exceptions=True), 5)
            if child.poll() is None:
                child.stdin.write('\n'); child.stdin.flush()
            await asyncio.wait_for(asyncio.to_thread(child.communicate), 5)
        self.assertEqual(child.returncode, 0)
        runtime.initialize(self.ns)
        with patch.object(general.time, 'time', return_value=self.row(regs[0])['next_retry_at'] + 1):
            fresh = await general.claim_safely(self.ns)
        self.assertEqual(str(fresh), regs[0])
        await self.ordinary(fresh)
        await runtime.release(self.ns, fresh)
        self.assertEqual(calls, [regs[1], regs[0]])
        self.assertEqual([self.member(reg)['total_new_findings'] for reg in regs], [1, 1])
        self.assertEqual([self.member(reg)['attempt_count'] for reg in regs], [1, 1])
        self.assertEqual(self.row(regs[0])['new_findings'], 6)
        for reg in regs:
            lock = _try_execution_lock(reg)
            self.assertIsNotNone(lock)
            _release_execution_lock(lock)

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
