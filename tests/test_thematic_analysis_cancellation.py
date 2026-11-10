"""A thematic timeout must retain ownership until its real worker stops.

Use the real thematic review and outer timeout/semaphore. Only the source
fixture, legal callbacks and queue bookkeeping are isolated; no live data,
network requests or production threads are touched.
"""
import asyncio
from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import vp_inspection_engine as inspection


class ThematicAnalysisCancellation(unittest.IsolatedAsyncioTestCase):
    async def verify_timeout(self, blocked_stage, *, fail_after_release=False):
        release = threading.Event()
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        calls = []
        physical = {"active": 0, "peak": 0, "starts": 0}
        lock = threading.Lock()
        results = {"analyze": [], "checks": [], "qualify": ([], []),
                   "projection": {"outcomes": {}}, "snapshot": None}

        def callback(stage):
            def run(*args):
                with lock:
                    calls.append(stage + ":start")
                if stage != blocked_stage:
                    return results[stage]
                with lock:
                    physical["active"] += 1
                    physical["starts"] += 1
                    first = physical["starts"] == 1
                    physical["peak"] = max(physical["peak"], physical["active"])
                if first:
                    loop.call_soon_threadsafe(started.set)
                try:
                    if not release.wait(5):
                        raise RuntimeError("Test failed to release its synthetic worker")
                    if fail_after_release and first:
                        raise ValueError("Late synthetic worker failure")
                    return results[stage]
                finally:
                    with lock:
                        physical["active"] -= 1
                        calls.append(stage + ":settled")
            return run

        ns = {"analyze": callback("analyze"), "build_checks": callback("checks"),
              "vp_methodology_qualify": callback("qualify"),
              "_vp_save_snapshot": callback("snapshot")}
        source = {"docs": [{"name": "Документация.docx", "text": "Условия закупки",
                            "status": "checked", "meta": {}}],
                  "unreadable": 0, "source_incomplete": False,
                  "p": {}, "card": {}, "detail": {}}

        async def file_stage(function, *args):
            return ("source", "engine") if function is fast._watermark else "db-token"

        def item(token):
            return {"batch": "test-batch", "reg": "32619990001", "ready": 1,
                    "categories": ["protocol_compliance"], "reservation_token": token}

        tasks = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(general, "existing", return_value=(
                {"customer": "ООО Тест", "reg_number": "32619990001"}, [])))
            stack.enter_context(patch.object(general, "collect_saved_evidence", AsyncMock(return_value=source)))
            stack.enter_context(patch.object(general, "is_official", return_value=True))
            stack.enter_context(patch.object(fast, "_retry_db", AsyncMock(return_value=True)))
            stack.enter_context(patch.object(fast, "_retry_file", side_effect=file_stage))
            stack.enter_context(patch.object(fast, "ITEM_TIMEOUT_SECONDS", .15))
            stack.enter_context(patch.object(inspection, "evaluate", side_effect=callback("projection")))
            stack.enter_context(redirect_stdout(io.StringIO()))
            sem = asyncio.Semaphore(1)
            first_item = item("first")
            tasks.append(asyncio.create_task(fast._one(ns, first_item, sem)))
            try:
                await asyncio.wait_for(started.wait(), 2)
                # Let the real wait_for deadline cancel the inner review.
                await asyncio.sleep(.25)
                self.assertFalse(tasks[0].done(), "Timeout released a live physical worker")
                self.assertTrue(sem.locked(), "Thematic capacity released before worker settled")
                tasks.append(asyncio.create_task(fast._one(ns, item("retry"), sem)))
                await asyncio.sleep(.03)
                self.assertEqual(physical["starts"], 1, "Retry overlapped the old physical stage")
                self.assertEqual(physical["active"], 1)
            finally:
                release.set()
                completed = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
            self.assertEqual(physical["active"], 0)
            self.assertEqual(physical["peak"], 1)
            self.assertEqual(completed[0][1:3], ("retry", []))
            self.assertNotIn("source_version", first_item)
            self.assertNotIn("semantic_version", first_item)
            self.assertNotIn("engine_signature", first_item)
            self.assertEqual(completed[1][1:3], ("completed", []), completed[1])
            self.assertLess(calls.index(blocked_stage + ":settled"),
                            calls.index("analyze:start", calls.index("analyze:start") + 1))

    async def test_timed_out_analyzer_retains_slot_until_physical_completion(self):
        await self.verify_timeout("analyze")

    async def test_timed_out_checklist_retains_slot_until_physical_completion(self):
        await self.verify_timeout("checks")

    async def test_timed_out_qualifier_retains_slot_until_physical_completion(self):
        await self.verify_timeout("qualify")

    async def test_timed_out_projection_retains_slot_until_physical_completion(self):
        await self.verify_timeout("projection")

    async def test_timed_out_report_write_finishes_before_retry_begins(self):
        await self.verify_timeout("snapshot")

    async def test_late_thread_failure_remains_retry_and_releases_capacity(self):
        await self.verify_timeout("analyze", fail_after_release=True)


if __name__ == "__main__":
    unittest.main()
