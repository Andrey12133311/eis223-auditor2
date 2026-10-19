"""A failed supplemental original collection cannot finish a partial audit."""
import asyncio
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v332_all_procurements_audit as audit
from test_general_audit_recovery import DatabaseFixture


class OriginalCollectionRetry(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.real_sleep = asyncio.sleep

        async def fast_start(seconds, *args, **kwargs):
            return await self.real_sleep(0 if seconds == 14 else seconds, *args, **kwargs)

        self.stack.enter_context(patch.object(audit.asyncio, "sleep", side_effect=fast_start))
        self.stack.enter_context(patch.object(audit, "QUEUE_POLL_SECONDS", .02))

    async def asyncTearDown(self):
        campaign = self.ns.get("_v332_campaign_worker")
        if campaign is not None:
            campaign.cancel()
            await asyncio.wait_for(asyncio.gather(campaign, return_exceptions=True), 5)

    async def until(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            await self.real_sleep(.005)
        self.fail("Condition timed out. Captured logs:\n" + self.output.getvalue())

    def with_supplemental_original(self, number=1):
        reg = self.purchase(number)
        # The original and EIS receipt exist, while its persisted document row
        # is not yet eligible for stored_docs. Collection must inspect it too.
        self.document(reg, "Дополнительные условия поставки материалов.", status="downloaded")
        return reg

    async def test_collection_error_keeps_confirmed_finding_and_reports_incomplete_source(self):
        regs = [self.with_supplemental_original(number) for number in (1, 2)]
        audit.initialize(self.ns)
        for reg, error in zip(regs, (asyncio.TimeoutError(), ValueError("temporary collection failure"))):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(audit.claim_next(self.ns), reg)
                with patch.object(audit, "offline_audit", side_effect=error) as collection:
                    state, detail = await audit.inspect_one(self.ns, reg)
                collection.assert_awaited_once_with(self.ns, reg, collect_only=True)
                self.assertEqual(state, "completed")  # The readable subset was analyzed.
                self.assertEqual(detail["documents_found"], 1)
                self.assertEqual(detail["documents_read"], 1)
                self.assertEqual(detail["documents_unreadable"], 0)
                self.assertEqual(detail["verified_original_documents"], 1)
                self.assertEqual(detail["original_received"], 2)
                self.assertEqual(detail["original_expected"], 2)
                self.assertEqual(detail["new_findings"], 1)
                self.assertEqual(self.findings(reg), 1)
                self.assertTrue(detail.get("source_incomplete"), detail)

    async def test_worker_retries_same_originals_after_timeout_without_losing_or_duplicating_finding(self):
        await self.worker_recovery("timeout")

    async def test_worker_retries_unreadable_collected_original_without_counting_it_twice(self):
        await self.worker_recovery("downloaded_no_text")

    async def test_worker_retries_unreadable_collected_archive(self):
        await self.worker_recovery("archive_unreadable")

    async def test_processed_archive_container_does_not_make_source_incomplete(self):
        reg = self.with_supplemental_original()
        audit.initialize(self.ns)
        self.assertEqual(audit.claim_next(self.ns), reg)
        with self.connect() as con:
            row = dict(con.execute("SELECT * FROM docs WHERE reg_number=? ORDER BY id DESC", (reg,)).fetchone())
        documents, unreadable = await audit.stored_docs(self.ns, reg, [row])
        self.assertEqual(unreadable, 0)
        # The collector retains expanded containers as source records; their
        # empty body is expected, unlike an unreadable member/original.
        container = {**documents[0], "status": "archive_processed", "type": "archive", "text": "",
                     "meta": {**documents[0]["meta"], "source": "official_eis_archive"}}
        with patch.object(audit, "offline_audit", return_value={"docs": [container]}):
            state, detail = await audit.inspect_one(self.ns, reg)
        self.assertEqual(state, "completed")
        self.assertFalse(detail["source_incomplete"])
        self.assertEqual(detail["documents_found"], 2)
        self.assertEqual(detail["documents_read"], 1)
        self.assertEqual(detail["documents_unreadable"], 0)
        self.assertEqual(self.findings(reg), 1)

    async def test_old_completed_revision_is_rechecked_once_without_losing_credited_finding(self):
        reg = self.with_supplemental_original()
        with patch.object(audit, "RULE_REVISION", "2026-10-19-v380-v372-readable-evidence-recovery"):
            audit.initialize(self.ns)
            self.assertEqual(audit.claim_next(self.ns), reg)
            with patch.object(audit, "offline_audit", side_effect=asyncio.TimeoutError()):
                state, detail = await audit.inspect_one(self.ns, reg)
            # Reproduce the previous worker's terminal mark after a swallowed
            # timeout. Existing installations have no stored timeout marker.
            audit.mark(self.ns, reg, state,
                       {key: value for key, value in detail.items() if key != "source_incomplete"})
        before = self.row(reg)
        self.assertEqual(before["state"], "completed")
        self.assertEqual(before["error"], None)
        self.assertEqual(before["new_findings"], 1)
        audit.initialize(self.ns)
        self.assertEqual(self.row(reg)["state"], "completed")
        self.assertEqual(audit.refresh_changed(self.ns), 1)
        self.assertEqual(self.row(reg)["state"], "pending")
        self.assertEqual(self.row(reg)["last_source_version"], before["last_source_version"])
        self.assertEqual(audit.progress(self.ns)["new_evidence_based_findings"], 1)
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(audit.claim_next(self.ns), reg)
        with self.connect() as con:
            rows = [dict(row) for row in con.execute("SELECT * FROM docs WHERE reg_number=? ORDER BY id", (reg,))]
        documents, unreadable = await audit.stored_docs(self.ns, reg, rows)
        self.assertEqual(unreadable, 0)
        watermark = audit.review_watermark(self.ns, reg)
        with patch.object(audit, "offline_audit", return_value={"docs": documents}):
            state, detail = await audit.inspect_one(self.ns, reg)
        self.assertFalse(detail["source_incomplete"])
        audit.mark(self.ns, reg, state,
                   {**{key: value for key, value in detail.items() if key != "source_incomplete"}, **watermark})
        self.assertEqual(self.row(reg)["documents_read"], 2)
        self.assertEqual(audit.refresh_changed(self.ns), 0)
        audit.initialize(self.ns)
        self.assertEqual(self.row(reg)["state"], "completed")
        self.assertIsNone(audit.claim_next(self.ns))
        self.assertEqual(audit.progress(self.ns)["new_evidence_based_findings"], 1)
        self.assertEqual(self.findings(reg), 1)

    async def worker_recovery(self, failure):
        reg = self.with_supplemental_original()
        sibling = self.purchase(2)
        collection_available = False
        calls = []

        async def collect(ns, number, *, collect_only=False):
            self.assertEqual(number, reg)
            self.assertTrue(collect_only)
            calls.append(number)
            if not collection_available and failure == "timeout":
                raise asyncio.TimeoutError()
            with self.connect() as con:
                rows = [dict(row) for row in con.execute("SELECT * FROM docs WHERE reg_number=? ORDER BY id", (reg,))]
            documents, unreadable = await audit.stored_docs(ns, reg, rows)
            self.assertEqual(unreadable, 0)
            if not collection_available:
                documents[-1] = {**documents[-1], "status": failure, "text": "",
                                 "error": "Temporary extraction failure"}
            return {"docs": documents}

        with self.connect() as con:
            before = [tuple(row) for row in con.execute("SELECT * FROM docs ORDER BY id")]
        self.stack.enter_context(patch.object(audit, "offline_audit", side_effect=collect))
        audit.install(self.ns)
        await self.ns["app"].startups[0]()
        campaign = self.ns["_v332_campaign_worker"]
        await self.until(lambda: self.row(reg) and self.row(reg)["state"] == "pending"
                         and self.row(reg)["retry_count"] == 1
                         and self.row(sibling)["state"] == "completed"
                         and "partial=True" in self.output.getvalue())
        deferred = self.row(reg)
        self.assertFalse(campaign.done())
        self.assertEqual(audit.runtime_snapshot(self.ns)["live_workers"], 4)
        self.assertEqual(calls, [reg])
        self.assertEqual(deferred["documents_found"], 1 if failure == "timeout" else 2)
        self.assertEqual(deferred["documents_read"], 1)
        self.assertEqual(deferred["documents_unreadable"], 0)
        self.assertEqual(deferred["original_received"], 2)
        self.assertEqual(deferred["original_expected"], 2)
        self.assertEqual(deferred["audit_revision"], "")
        self.assertGreater(deferred["next_retry_at"], time.time())
        self.assertLessEqual(deferred["next_retry_at"], time.time() + 90)
        self.assertEqual(deferred["new_findings"], 1)
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(audit.progress(self.ns)["new_evidence_based_findings"], 2)
        self.assertIn("partial=True", self.output.getvalue())
        self.assertIsNone(audit.claim_next(self.ns))
        self.assertEqual(audit.refresh_changed(self.ns), 0)

        # A fresh process sees the same durable delay and the already credited
        # finding; no changed document/status/content is needed for recovery.
        fresh_ns = {"conn": self.connect, "_verified_set235": lambda: self.regs}
        audit.initialize(fresh_ns)
        self.assertIsNone(audit.claim_next(fresh_ns))
        self.assertEqual(audit.progress(fresh_ns)["new_evidence_based_findings"], 2)
        collection_available = True
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET next_retry_at=0 WHERE reg=?", (reg,))
        audit.audit_signals(self.ns).notify("general")
        await self.until(lambda: self.row(reg)["state"] == "completed")
        completed = self.row(reg)
        self.assertEqual(calls, [reg, reg])
        self.assertEqual(completed["documents_found"], 2)
        self.assertEqual(completed["documents_read"], 2)
        self.assertEqual(completed["documents_unreadable"], 0)
        self.assertEqual(completed["retry_count"], 0)
        self.assertEqual(completed["next_retry_at"], 0)
        self.assertEqual(completed["audit_revision"], audit.RULE_REVISION)
        self.assertEqual(completed["new_findings"], 1)
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(audit.progress(self.ns)["new_evidence_based_findings"], 2)
        self.assertEqual(audit.refresh_changed(self.ns), 0)
        self.assertIsNone(audit.claim_next(self.ns))
        with self.connect() as con:
            self.assertEqual([tuple(row) for row in con.execute("SELECT * FROM docs ORDER BY id")], before)


if __name__ == "__main__":
    unittest.main()
