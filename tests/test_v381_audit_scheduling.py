"""Temporary-fixture regressions for actual audit scheduling and resource caps."""
import asyncio
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v381_audit_scheduling as scheduling
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import v332_all_procurements_audit as general


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_leaves_a_physical_manual_slot(self):
        gate = scheduling.PriorityGate(6, 5)
        held = [await gate.acquire(True) for _ in range(5)]
        background = [asyncio.create_task(gate.acquire(True)) for _ in range(20)]
        await asyncio.sleep(0)
        manual = await asyncio.wait_for(gate.acquire(False), .2)
        self.assertFalse(manual)
        self.assertEqual((gate.active, gate.background_active), (6, 5))
        self.assertFalse(any(task.done() for task in background))
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        gate.release(manual)
        for permit in held:
            gate.release(permit)
        self.assertEqual(gate.active, 0)

    async def test_manual_passes_queued_review_without_starving_background(self):
        gate = scheduling.PriorityGate(1, 1)
        holder = await gate.acquire(True)
        background = asyncio.create_task(gate.acquire(True))
        foreground = [asyncio.create_task(gate.acquire(False)) for _ in range(5)]
        await asyncio.sleep(0)
        gate.release(holder)
        for task in foreground[:3]:
            self.assertFalse(await asyncio.wait_for(task, .2))
            self.assertFalse(background.done())
            gate.release(False)
        self.assertTrue(await asyncio.wait_for(background, .2))
        gate.release(True)
        for task in foreground[3:]:
            self.assertFalse(await asyncio.wait_for(task, .2))
            gate.release(False)
        self.assertEqual(gate.active, 0)

    async def test_queued_background_review_can_be_promoted_without_restart(self):
        gate = scheduling.PriorityGate(1, 1)
        holder = await gate.acquire(True)
        background = asyncio.create_task(gate.acquire(True))
        promoted = asyncio.create_task(gate.acquire(True))
        await asyncio.sleep(0)
        self.assertTrue(gate.promote(promoted))
        gate.release(holder)
        self.assertFalse(await asyncio.wait_for(promoted, .2))
        self.assertFalse(background.done())
        gate.release(False)
        self.assertTrue(await asyncio.wait_for(background, .2))
        gate.release(True)
        self.assertFalse(gate.promote(promoted))

    async def test_cancelled_grant_returns_capacity(self):
        gate = scheduling.PriorityGate(1, 1)
        permit = await gate.acquire(True)
        queued = asyncio.create_task(gate.acquire(False))
        await asyncio.sleep(0)
        gate.release(permit)
        queued.cancel()
        await asyncio.gather(queued, return_exceptions=True)
        self.assertEqual(gate.active, 0)
        gate.release(await asyncio.wait_for(gate.acquire(True), .2))

    async def test_timeout_keeps_physical_thread_permit(self):
        await self._assert_physical_bound(cancel=False)

    async def test_cancellation_keeps_physical_thread_permit(self):
        await self._assert_physical_bound(cancel=True)

    async def _assert_physical_bound(self, cancel):
        gate = scheduling.PriorityGate(1, 1)
        release = threading.Event()
        started = threading.Event()
        second_started = threading.Event()

        def slow():
            started.set()
            release.wait(2)
            return "first"

        with patch.object(scheduling, "_gate", return_value=gate):
            first = asyncio.create_task(scheduling.run_file_io(slow, timeout=.04))
            while not started.is_set():
                await asyncio.sleep(.001)
            if cancel:
                first.cancel()
            with self.assertRaises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
                await first
            second = asyncio.create_task(scheduling.run_file_io(second_started.set))
            try:
                await asyncio.sleep(.015)
                self.assertEqual(gate.active, 1)
                self.assertFalse(second_started.is_set())
            finally:
                release.set()
            await asyncio.wait_for(second, 1)
            self.assertTrue(second_started.is_set())
            await asyncio.sleep(0)
            self.assertEqual(gate.active, 0)

    async def test_rolling_file_window_has_no_slow_first_batch_barrier(self):
        release = asyncio.Event()
        third = asyncio.Event()

        async def work(index):
            if index == 0:
                await release.wait()
            if index == 2:
                third.set()
            return index

        task = asyncio.create_task(scheduling.ordered_map(work, range(4), limit=2))
        try:
            await asyncio.wait_for(third.wait(), .2)
            self.assertFalse(task.done())
        finally:
            release.set()
        self.assertEqual(await task, [0, 1, 2, 3])

    async def test_evidence_wakeup_is_threadsafe_and_not_lost_before_wait(self):
        ns = {}
        signals = scheduling.audit_signals(ns)
        version = signals.version("thematic")
        await asyncio.to_thread(scheduling.notify_evidence, ns)
        await asyncio.wait_for(signals.wait("thematic", version, 30), .2)
        version = signals.version("general")
        task = asyncio.create_task(signals.wait("general", version, 30))
        await asyncio.sleep(0)
        await asyncio.to_thread(scheduling.notify_evidence, ns)
        await asyncio.wait_for(task, .2)

    async def test_general_new_evidence_runs_without_waiting_for_slow_worker(self):
        class App:
            def __init__(self):
                self.startups = []
            def get(self, path):
                return lambda handler:handler
            def on_event(self, event):
                def register(handler):
                    self.startups.append(handler)
                    return handler
                return register

        app = App()
        ns = {"app":app}
        slow_started = asyncio.Event()
        fast_started = asyncio.Event()
        release = asyncio.Event()
        arrivals = threading.Event()
        lock = threading.Lock()
        queue = ["slow"]
        real_sleep = asyncio.sleep

        async def sleep(seconds):
            await real_sleep(0 if seconds == 14 else seconds)

        def claim(ns):
            with lock:
                return queue.pop(0) if queue else None

        def refresh(ns):
            if not arrivals.is_set():
                return 0
            arrivals.clear()
            with lock:
                queue.append("fast")
            return 1

        async def inspect(ns, reg):
            if reg == "slow":
                slow_started.set()
                await release.wait()
            else:
                fast_started.set()
            return "no_readable_documents", {}

        with patch.object(general, "initialize", return_value={}), \
             patch.object(general, "claim_next", side_effect=claim), \
             patch.object(general, "evidence_version", return_value="fixture"), \
             patch.object(general, "inspect_one", side_effect=inspect), \
             patch.object(general, "mark"), \
             patch.object(general, "defer_retry", return_value=90), \
             patch.object(general, "return_running"), \
             patch.object(general, "add_new_verified", return_value=0), \
             patch.object(general, "refresh_changed", side_effect=refresh), \
             patch.object(general.asyncio, "sleep", side_effect=sleep):
            general.install(ns)
            await app.startups[0]()
            try:
                await asyncio.wait_for(slow_started.wait(), .5)
                arrivals.set()
                scheduling.notify_evidence(ns)
                await asyncio.wait_for(fast_started.wait(), .5)
                self.assertFalse(release.is_set())
            finally:
                release.set()
                ns["_v332_campaign_worker"].cancel()
                await asyncio.gather(ns["_v332_campaign_worker"], return_exceptions=True)

    async def test_thematic_completed_result_persists_while_slow_item_runs(self):
        items = [{"reg":reg, "batch":"batch", "ready":1,
                  "doc_id":1, "categories":["dates"]} for reg in ("slow", "fast")]
        release = asyncio.Event()
        persisted = threading.Event()
        saved = []

        async def review(ns, reg, categories):
            if reg == "slow":
                await release.wait()
            return "completed", []

        def persist(ns, results):
            saved.extend(result[0]["reg"] for result in results)
            if "fast" in saved:
                persisted.set()

        with patch.object(thematic, "storage_health", return_value={"blocked":False}), \
             patch.object(thematic, "independent_review", side_effect=review), \
             patch.object(fast, "_prepare", return_value=items), \
             patch.object(fast, "_persist", side_effect=persist), \
             patch.object(fast, "_request_missing_originals", return_value=0):
            task = asyncio.create_task(fast.faster_tick({}))
            try:
                deadline = time.monotonic() + .5
                while not persisted.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(.005)
                self.assertTrue(persisted.is_set(), "Fast completed results were held by the slow audit")
                self.assertEqual(saved, ["fast"])
                self.assertFalse(task.done())
            finally:
                release.set()
            self.assertEqual(await task, 2)
        self.assertEqual(saved, ["fast", "slow"])

    async def test_thematic_cancellation_cleans_up_child_audits(self):
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def review(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        item = {"reg":"slow", "batch":"batch", "ready":1,
                "doc_id":1, "categories":["dates"]}
        with patch.object(thematic, "storage_health", return_value={"blocked":False}), \
             patch.object(thematic, "independent_review", side_effect=review), \
             patch.object(fast, "_prepare", return_value=[item]), \
             patch.object(fast, "_request_missing_originals", return_value=0):
            task = asyncio.create_task(fast.faster_tick({}))
            await asyncio.wait_for(entered.wait(), .5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stopped.is_set())


class ThematicSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audit.sqlite"
        self.ns = {"conn":self.connect}
        with self.connect() as db:
            db.executescript("""CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT);
                CREATE INDEX docs_by_reg ON docs(reg_number,id);""")
        thematic.init(self.ns)
        fast.install(self.ns)
        self.state = patch.dict(thematic.WORKER_STATE, {"index_at":0})
        self.state.start()
        self.addCleanup(self.state.stop)

    def connect(self):
        db = sqlite3.connect(self.path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    def add_batch(self, name):
        with self.connect() as db:
            db.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) VALUES(?,?,?,?,?)",
                       (name, '["dates"]', "2026-10-15", "active", '["ООО"]'))

    def add_sources(self, count):
        with self.connect() as db:
            db.executemany("INSERT INTO purchases VALUES(?, 'ООО Пример')",
                           ((str(32610000000+i),) for i in range(count)))
            db.executemany("INSERT INTO docs VALUES(?,?,?,'checked')",
                           ((i+1,str(32610000000+i),f"/fixtures/{i}.pdf") for i in range(count)))

    def test_manual_priority_requires_explicit_manual_input(self):
        self.assertFalse(scheduling.is_manual_review(self.ns,"32610000000"))
        with self.connect() as db:
            db.execute("CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY)")
            db.execute("INSERT INTO v319_manual_inputs VALUES('32610000000')")
        self.assertTrue(scheduling.is_manual_review(self.ns,"32610000000"))
        self.assertFalse(scheduling.is_manual_review(self.ns,"32610000001"))

    def test_three_campaigns_each_get_service_in_same_cycle(self):
        self.add_sources(30)
        for batch in ("a", "b", "c"):
            self.add_batch(batch)
        selected = fast._prepare(self.ns)
        self.assertEqual(Counter(item["batch"] for item in selected), {"a":12,"b":12,"c":12})
        self.assertEqual([item["batch"] for item in selected[:6]], ["a","b","c"]*2)

    def test_more_than_cycle_size_campaigns_rotate_between_cycles(self):
        self.add_sources(1)
        for index in range(40):
            self.add_batch(f"batch-{index:02}")
        first = fast._prepare(self.ns)
        second = fast._prepare(self.ns)
        served = {item["batch"] for item in first + second}
        self.assertEqual(len(first), 36)
        self.assertEqual(len(second), 36)
        self.assertEqual(len(served), 40)
        self.assertEqual(second[0]["batch"], "batch-36")

    def test_unchanged_completed_rows_cannot_hide_new_evidence_beyond_limit(self):
        self.add_sources(3002)
        self.add_batch("a")
        fast._prepare(self.ns)
        with self.connect() as db:
            db.execute("UPDATE thematic_items SET status='completed',last_ready_count=1,"
                       "last_doc_id=(SELECT MAX(id) FROM docs WHERE reg_number=reg),last_analysis_version=?",
                       (fast.ANALYSIS_VERSION,))
            # These fixtures represent audits already completed on the current
            # inputs, including the same-row evidence snapshot.
            regs = [row[0] for row in db.execute("SELECT reg FROM thematic_items").fetchall()]
            from audit_source_version import source_version
            db.executemany("UPDATE thematic_items SET last_source_version=? WHERE reg=?",
                           [(source_version(db, reg), reg) for reg in regs])
            target = str(32610000000+3001)
            db.execute("INSERT INTO docs VALUES(4000,?, '/fixtures/new.pdf','checked')",(target,))
        selected = fast._prepare(self.ns)
        self.assertEqual([item["reg"] for item in selected], [target])

    def test_stopped_batch_cannot_receive_late_persistence(self):
        self.add_sources(1)
        self.add_batch("a")
        item = fast._prepare(self.ns)[0]
        with self.connect() as db:
            db.execute("UPDATE thematic_batches SET status='stopped'")
        fast._persist(self.ns, [(item,"completed",[],"done")])
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM thematic_items").fetchone()[0], "queued")


if __name__ == "__main__":
    unittest.main()
