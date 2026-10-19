"""Requested fresh attempts reuse real local engine/reservations and receipts."""
from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

from test_general_audit_recovery import DatabaseFixture, LATE_REPLY
import full_recheck_request as ledger
import full_recheck_runtime as runtime
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
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
        self.assertFalse(self.member(reg)["full_verified_read"])
        self.assertEqual(self.findings(reg), 1)

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
            claim = ledger.claim_member(c, runtime.AUTHORIZED_EPOCH, reg, lease_seconds=1)
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
            claim = ledger.claim_member(c, runtime.AUTHORIZED_EPOCH, reg)
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


if __name__ == "__main__":
    unittest.main()
