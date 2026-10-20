"""Request membership reaches the unchanged downloader, using local fixtures."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sqlite3
import time
import threading
import unittest
import uuid
from unittest.mock import patch

from test_general_audit_recovery import DatabaseFixture
import full_recheck_originals as bridge
import full_recheck_request as ledger
import full_recheck_runtime as runtime
import v301_search_jobs as queue
import eis_server_worker as worker
import v332_all_procurements_audit as general


class OriginalsBridge(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def activate(self, count=1, *, noncompany=False):
        regs = [self.purchase(number, present=False) for number in range(1, count + 1)]
        with self.connect() as c:
            c.execute("DELETE FROM docs")
            c.execute("DROP TABLE v319_manual_inputs")
            if noncompany:
                c.execute("UPDATE purchases SET customer='ГБУ Учреждение'")
        queue._init(self.ns)
        worker.init(self.ns)
        with self.connect() as c:
            c.execute("DELETE FROM v301_search_jobs")
        general.initialize(self.ns)
        self.stack.enter_context(patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.AUTHORIZED_EPOCH}))
        runtime.initialize(self.ns)
        return regs

    def waiting(self, reg, *, reason="waiting_originals", status="waiting_originals"):
        with self.connect() as c:
            c.execute("UPDATE full_recheck_members SET status=?,reason=?,next_retry_at=0 WHERE reg=?", (status, reason, reg))

    def candidate(self, reg):
        with self.connect() as c:
            return dict(c.execute("SELECT reg,status,attempt_id,result_token FROM full_recheck_members WHERE reg=?", (reg,)).fetchone())

    def jobs(self):
        with self.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM v301_search_jobs ORDER BY created_at,id")]

    def member(self, reg):
        with self.connect() as c:
            return dict(c.execute("SELECT * FROM full_recheck_members WHERE reg=?", (reg,)).fetchone())

    def due(self):
        with self.connect() as c:
            c.execute("UPDATE full_recheck_originals SET next_check_at=0")
        self.ns["_full_recheck"]["originals_after"] = 0

    def admit(self, reg, observation=None, **kwargs):
        with self.connect() as c:
            reports = bridge.report_snapshots(c, runtime.AUTHORIZED_EPOCH, reg)
        observation = bridge.probe(reg) if observation is None else observation
        with runtime.transaction(self.ns) as c:
            return bridge.admit(c, runtime.AUTHORIZED_EPOCH, self.candidate(reg),
                                observation, reports=reports,
                                **runtime._target_args(self.ns), **kwargs)

    async def test_runtime_missing_to_existing_queue_then_verified_same_epoch_completion(self):
        reg, = self.activate()
        before = self.member(reg)
        self.assertFalse(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(reg)["status"], "waiting_originals")
        self.due()
        self.assertFalse(await runtime.run_one(self.ns, {}))
        job, = self.jobs()
        self.assertEqual((job["kind"], job["target"], job["parent_id"]), ("reg", reg, bridge.PARENT_ID))
        runtime.initialize(self.ns)  # no new snapshot, download, or reset
        self.due()
        await bridge.pump(self.ns)
        self.assertEqual(self.jobs(), [job])
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT actual_count FROM full_recheck_requests").fetchone()[0], 1)
        # Existing worker can claim this exact ordinary reg task. Fake only
        # transport delivery: restore fixture original bytes, then use engine.
        claimed, = worker.claim(self.ns, str(uuid.uuid4()), limit=1)
        self.assertEqual(claimed["id"], job["id"])
        for path, contents in self.contents.items():
            path.write_bytes(contents)
        with self.connect() as c:
            c.execute("UPDATE full_recheck_members SET next_retry_at=0")
        # General receipt scan sees originals even without a docs row.
        general.refresh_changed(self.ns)
        ordinary = general.claim_next(self.ns)
        self.assertIsNotNone(ordinary)
        await runtime.attach_general(self.ns, ordinary)
        snapshot = await general.file_stage(self.ns, "source", general.review_watermark, self.ns, ordinary)
        runtime.note_snapshot(self.ns, ordinary, snapshot)
        state, detail = await general.inspect_one(self.ns, ordinary)
        general.mark(self.ns, ordinary, state, {**{k: v for k, v in detail.items() if k != "source_incomplete"}, **snapshot})
        await runtime.finish_result(self.ns, ordinary, state, detail)
        await runtime.release(self.ns, ordinary)
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.member(reg)["attempt_count"], before["attempt_count"] + 1)
        self.assertEqual(len(self.jobs()), 1)

    async def test_noncompany_missing_and_missing_no_readable_are_included(self):
        reg, second = self.activate(2, noncompany=True)
        self.waiting(reg)
        self.waiting(second, reason="no_readable_documents", status="retry")
        await bridge.pump(self.ns)
        self.assertEqual({j["target"] for j in self.jobs()}, {reg, second})
        self.assertIsNone(self.row(reg))
        self.assertIsNone(self.row(second))
        self.assertEqual(self.member(reg)["status"], "waiting_originals")

    async def test_unreadable_present_originals_are_not_remotely_retried(self):
        reg, = self.activate()
        for path, contents in self.contents.items(): path.write_bytes(contents)
        self.waiting(reg, reason="no_readable_documents", status="retry")
        await bridge.pump(self.ns)
        self.assertEqual(self.jobs(), [])
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT outcome FROM full_recheck_originals").fetchone()[0], "originals_present")

    async def test_temporary_failures_retry_same_job_with_existing_backoff_then_stop(self):
        reg, = self.activate()
        self.waiting(reg)
        await bridge.pump(self.ns)
        identity = str(uuid.uuid4())
        identifier = self.jobs()[0]["id"]
        for index in range(worker.MAX_ATTEMPTS):
            with self.connect() as c:
                c.execute("UPDATE v301_search_jobs SET not_before=0 WHERE id=?", (identifier,))
            claimed, = worker.claim(self.ns, identity, limit=1)
            result = worker.report(self.ns, identifier, {"worker_id": identity, "lease_token": claimed["lease_token"],
                "reg_number": reg, "status": "error", "error_code": "eis_connection_error"})
            self.due()
            await bridge.pump(self.ns)
            self.assertEqual(len(self.jobs()), 1)
            if index < worker.MAX_ATTEMPTS - 1:
                self.assertEqual(result["status"], "queued")
                self.assertGreater(self.jobs()[0]["not_before"], time.time())
        self.assertEqual(result["status"], "partial")
        runtime.initialize(self.ns)
        self.due()
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), 1)
        self.assertNotEqual(self.member(reg)["status"], "completed")

    async def test_existing_leased_and_metadata_jobs_never_reset_or_duplicate(self):
        first, second = self.activate(2)
        for reg in (first, second): self.waiting(reg)
        queue._enqueue(self.ns, "reg", first, parent_id="server-discovery")
        queue._enqueue(self.ns, "reg", second, parent_id="v341-card-facts")
        worker.claim(self.ns, str(uuid.uuid4()), limit=2)
        before = self.jobs()
        await bridge.pump(self.ns)
        self.assertEqual(self.jobs(), before)
        with self.connect() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT outcome FROM full_recheck_originals")}, {"active", "metadata_pending"})

    async def test_terminal_unsupported_unknown_and_partial_progress_do_not_hot_loop(self):
        first, second, third = self.activate(3)
        for reg in (first, second, third): self.waiting(reg)
        for reg, code in ((first, "unsupported_xml_type"), (second, "unrecognized_error")):
            job, _ = queue._enqueue(self.ns, "reg", reg)
            with self.connect() as c:
                c.execute("UPDATE v301_search_jobs SET status='partial' WHERE id=?", (job["id"],))
                c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (job["id"], "f"*48, "fixture", reg,
                          json.dumps({"error_code": code}), 1))
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), 3)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial' WHERE target=?", (third,))
        # The job's own partial source progress is adopted at termination.
        changed = {"source_version": "a"*64, "missing": True}
        for _ in range(3):
            self.due()
            with patch.object(bridge, "probe", return_value=changed):
                await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), 3)
        self.assertTrue(all(self.member(reg)["status"] == "waiting_originals" for reg in (first, second, third)))

    async def test_old_known_temporary_terminal_gets_one_bounded_request_attempt(self):
        reg, = self.activate()
        self.waiting(reg)
        old, _ = queue._enqueue(self.ns, "reg", reg)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial',updated_at='2026-01-01T00:00:00+00:00' WHERE id=?", (old["id"],))
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (old["id"], "f"*48, "fixture", reg,
                      json.dumps({"error_code": "eis_http_503"}), 1))
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), 2)
        self.due()
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), 2)

    async def test_queue_capacity_turn_budget_and_round_robin_fairness(self):
        regs = self.activate(11)
        for reg in regs: self.waiting(reg)
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), bridge.MAX_NEW_JOBS)
        self.due()
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), bridge.MAX_ACTIVE_JOBS)
        self.due()
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), bridge.MAX_ACTIVE_JOBS)
        # Let existing jobs finish. Previously unvisited tail members have
        # next_check=0, so repeatedly unavailable heads cannot starve them.
        with self.connect() as c: c.execute("UPDATE v301_search_jobs SET status='partial'")
        self.ns["_full_recheck"]["originals_after"] = 0
        await bridge.pump(self.ns)
        self.assertTrue(set(regs[8:10]) <= {j["target"] for j in self.jobs()})

    async def test_manual_reserve_and_atomic_rollback(self):
        reg, = self.activate()
        self.waiting(reg)
        with self.connect() as c:
            c.executemany("INSERT INTO v301_search_jobs(id,kind,target,created_at,updated_at) VALUES(?,'reg',?,'0','0')",
                          [(str(i), str(32618000000 + i)) for i in range(queue.MAX_QUEUE - worker.RESERVED_MANUAL_SLOTS)])
        await bridge.pump(self.ns)
        self.assertEqual(len(self.jobs()), queue.MAX_QUEUE - worker.RESERVED_MANUAL_SLOTS)
        with self.connect() as c: c.execute("DELETE FROM v301_search_jobs")
        self.due()
        with self.connect() as c:
            c.execute("CREATE TRIGGER fixture_fail BEFORE INSERT ON full_recheck_originals BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.admit(reg)
        self.assertEqual(self.jobs(), [])

    async def test_exact_member_and_active_analysis_guards(self):
        reg, = self.activate()
        self.waiting(reg)
        candidate = self.candidate(reg)
        observation = bridge.probe(reg)
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET state='running',reservation_token='owner' WHERE reg=?", (reg,))
        self.assertEqual(self.admit(reg, observation), "changed")
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET state='pending' WHERE reg=?", (reg,))
            c.execute("UPDATE full_recheck_members SET status='running',lease_until=0 WHERE reg=?", (reg,))
        self.assertEqual(self.admit(reg, observation), "changed")
        self.waiting(reg)
        with self.connect() as c: c.execute("UPDATE full_recheck_members SET attempt_id='new-attempt' WHERE reg=?", (reg,))
        with runtime.transaction(self.ns) as c:
            self.assertEqual(bridge.admit(c, runtime.AUTHORIZED_EPOCH, candidate, observation, **runtime._target_args(self.ns)), "changed")
        self.assertEqual(self.jobs(), [])

    async def test_concurrent_restart_admission_is_atomic_and_source_identity_is_stable(self):
        reg, = self.activate()
        self.waiting(reg)
        candidate, observation = self.candidate(reg), bridge.probe(reg)
        def enqueue():
            with runtime.transaction(self.ns) as c:
                return bridge.admit(c, runtime.AUTHORIZED_EPOCH, candidate, observation, **runtime._target_args(self.ns))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: enqueue(), range(6)))
        self.assertEqual(len(self.jobs()), 1)
        for path in self.contents: os.utime(path.parent / "receipt.json", None)
        self.assertEqual(bridge.probe(reg), observation)
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Новое имя' WHERE reg_number=?", (reg,))
        self.assertEqual(bridge.probe(reg), observation)

    async def test_catalogue_failure_and_intake_execution_lock_fail_closed(self):
        from v295_eis_originals import _try_execution_lock, _release_execution_lock
        reg, = self.activate()
        self.waiting(reg)
        handle = _try_execution_lock(reg)
        try:
            await bridge.pump(self.ns)
            self.assertEqual(self.jobs(), [])
        finally:
            _release_execution_lock(handle)
        self.due()
        with patch.object(bridge, "probe", return_value={"error": "catalogue_unavailable"}):
            await bridge.pump(self.ns)
        self.assertEqual(self.jobs(), [])

    async def test_large_cohort_file_scans_are_bounded_and_not_every_tick(self):
        reg, = self.activate()
        self.waiting(reg)
        with self.connect() as c:
            for index in range(2, 3087):
                number = str(32619000000 + index)
                c.execute("INSERT INTO purchases(reg_number,customer) VALUES(?,'ГБУ')", (number,))
                c.execute("INSERT INTO full_recheck_members(request_id,reg,status,reason,updated_at) VALUES(?,?,'waiting_originals','missing_originals',0)",
                          (runtime.AUTHORIZED_EPOCH, number))
        with patch.object(bridge, "probe", return_value={"source_version": "a"*64, "missing": True}) as probe:
            await bridge.pump(self.ns)
            self.assertEqual(probe.call_count, bridge.PAGE_SIZE)
            for _ in range(20): await bridge.pump(self.ns)
            self.assertEqual(probe.call_count, bridge.PAGE_SIZE)
        self.assertEqual(len(self.jobs()), bridge.MAX_NEW_JOBS)

    async def test_admission_has_no_filesystem_or_json_work_under_writer(self):
        from pathlib import Path
        reg, = self.activate()
        self.waiting(reg)
        candidate, observation = self.candidate(reg), bridge.probe(reg)
        with self.connect() as c:
            reports = bridge.report_snapshots(c, runtime.AUTHORIZED_EPOCH, reg)
        with runtime.transaction(self.ns) as c, patch.object(Path, "open", side_effect=AssertionError("file under writer")), \
                patch.object(json, "loads", side_effect=AssertionError("JSON under writer")), \
                patch.object(json, "dumps", side_effect=AssertionError("JSON under writer")), \
                patch.object(bridge, "probe", side_effect=AssertionError("probe under writer")):
            self.assertEqual(bridge.admit(c, runtime.AUTHORIZED_EPOCH, candidate, observation, reports=reports,
                             **runtime._target_args(self.ns)), "enqueued")

    async def test_report_supersession_between_read_and_admission_fails_closed(self):
        reg, = self.activate()
        self.waiting(reg)
        old, _ = queue._enqueue(self.ns, "reg", reg)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial',updated_at='2026-01-01T00:00:00+00:00' WHERE id=?", (old["id"],))
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (old["id"], "f"*48, "fixture", reg,
                      json.dumps({"error_code": "eis_http_503"}), 1))
            reports = bridge.report_snapshots(c, runtime.AUTHORIZED_EPOCH, reg)
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (old["id"], "e"*48, "fixture", reg,
                      json.dumps({"error_code": "unsupported_xml_type"}), 2))
        candidate, observation = self.candidate(reg), bridge.probe(reg)
        with runtime.transaction(self.ns) as c:
            self.assertEqual(bridge.admit(c, runtime.AUTHORIZED_EPOCH, candidate, observation, reports=reports,
                             **runtime._target_args(self.ns)), "terminal")
        self.assertEqual(len(self.jobs()), 1)

    async def test_freshness_a_b_a_and_job_identity_collision_do_not_duplicate(self):
        import hashlib
        reg, second = self.activate(2)
        for number in (reg, second): self.waiting(number)
        a = {"source_version": "a"*64, "missing": True}
        b = {"source_version": "b"*64, "missing": True}
        self.assertEqual(self.admit(reg, a), "enqueued")
        with self.connect() as c: c.execute("UPDATE v301_search_jobs SET status='done'")
        self.due()
        self.assertEqual(self.admit(reg, a), "terminal")
        self.due()
        self.assertEqual(self.admit(reg, b, now=time.time()+bridge.CHECK_SECONDS+1), "enqueued")
        with self.connect() as c: c.execute("UPDATE v301_search_jobs SET status='done'")
        self.due()
        self.assertEqual(self.admit(reg, b), "terminal")
        self.due()
        self.assertEqual(self.admit(reg, a, now=time.time()+bridge.CHECK_SECONDS+1), "terminal")
        self.assertEqual(len(self.jobs()), 2)
        collision = hashlib.sha256((runtime.AUTHORIZED_EPOCH + "\0" + second + "\0" + a["source_version"]).encode()).hexdigest()[:24]
        with self.connect() as c:
            c.execute("INSERT INTO v301_search_jobs(id,kind,target,status,created_at,updated_at) VALUES(?,'inn','7701234567','done','0','0')", (collision,))
        before = self.jobs()
        self.assertEqual(self.admit(second, a), "identity_conflict")
        self.assertEqual(self.jobs(), before)

    async def test_active_thematic_claims_and_late_download_lease_are_preserved(self):
        reg, = self.activate()
        self.waiting(reg)
        with self.connect() as c:
            c.executescript("CREATE TABLE thematic_batches(id TEXT PRIMARY KEY,status TEXT); "
                            "CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT,reservation_token TEXT);")
            c.execute("INSERT INTO thematic_batches VALUES('fixture','active')")
            c.execute("INSERT INTO thematic_items VALUES('fixture',?,'queued','reserved')", (reg,))
        self.assertEqual(self.admit(reg), "changed")
        with self.connect() as c: c.execute("UPDATE thematic_batches SET status='stopped'")
        candidate, observation = self.candidate(reg), bridge.probe(reg)
        job, _ = queue._enqueue(self.ns, "reg", reg)
        worker.claim(self.ns, str(uuid.uuid4()), limit=1)
        before = self.jobs()
        with runtime.transaction(self.ns) as c:
            self.assertEqual(bridge.admit(c, runtime.AUTHORIZED_EPOCH, candidate, observation,
                             **runtime._target_args(self.ns)), "active")
        self.assertEqual(self.jobs(), before)

    async def test_existing_schema_and_legacy_reader_survive_additive_bridge(self):
        reg, = self.activate()
        self.waiting(reg)
        before = self.member(reg)
        with self.connect() as c:
            tables = {r[0]: r[1] for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
            c.execute("BEGIN IMMEDIATE")
            bridge.initialize(c)
            ledger.initialize(c)
            self.assertEqual(tables, {r[0]: r[1] for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")})
            self.assertEqual(ledger.inventory(c, runtime.AUTHORIZED_EPOCH)["actual_count"], 1)
        self.assertEqual(self.member(reg), before)

    async def test_terminal_cooldown_is_durable_and_then_allows_only_one_attempt(self):
        reg, = self.activate()
        self.waiting(reg)
        old, _ = queue._enqueue(self.ns, "reg", reg)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial' WHERE id=?", (old["id"],))
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (old["id"], "f"*48, "fixture", reg,
                      json.dumps({"error_code": "site_http_503"}), 1))
        stamp = time.time()
        self.assertEqual(self.admit(reg, now=stamp), "terminal_cooldown")
        runtime.initialize(self.ns)
        self.assertEqual(self.admit(reg, now=stamp+bridge.CHECK_SECONDS-1), "not_due")
        self.assertEqual(self.admit(reg, now=stamp+bridge.CHECK_SECONDS+1), "enqueued")
        self.assertEqual(len(self.jobs()), 2)

    async def test_terminal_adoption_survives_catalogue_error_and_present_sample(self):
        for diagnostic in ({"error": "catalogue_unavailable"}, {"source_version": "a"*64, "missing": False}):
            with self.subTest(diagnostic=diagnostic):
                if not self.regs: reg, = self.activate()
                else:
                    reg = self.regs[0]
                    with self.connect() as c:
                        c.execute("DELETE FROM full_recheck_originals")
                        c.execute("DELETE FROM v301_search_jobs")
                        c.execute("DELETE FROM eis_server_reports")
                self.waiting(reg)
                self.assertEqual(self.admit(reg, {"source_version": "a"*64, "missing": True}), "enqueued")
                identifier = self.jobs()[0]["id"]
                self.due()
                self.admit(reg, diagnostic)
                with self.connect() as c:
                    self.assertEqual(c.execute("SELECT job_phase FROM full_recheck_originals").fetchone()[0], "active")
                    c.execute("UPDATE v301_search_jobs SET status='partial',updated_at='2026-01-01T00:00:00+00:00'")
                    c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (identifier, "f"*48, "fixture", reg,
                              json.dumps({"error_code": "eis_http_503"}), 1))
                self.due()
                self.assertEqual(self.admit(reg, {"source_version": "b"*64, "missing": True}), "terminal")
                self.assertEqual(len(self.jobs()), 1)

    async def test_unknown_and_future_terminal_time_never_turn_into_short_cooldown_bypass(self):
        first, second = self.activate(2)
        stamp = time.time()
        for reg, updated in ((first, "invalid"), (second, "2099-01-01T00:00:00+00:00")):
            self.waiting(reg)
            job, _ = queue._enqueue(self.ns, "reg", reg)
            with self.connect() as c:
                c.execute("UPDATE v301_search_jobs SET status='partial',updated_at=? WHERE id=?", (updated, job["id"]))
                c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (job["id"], "f"*48, "fixture", reg,
                          json.dumps({"error_code": "eis_http_503"}), 1))
            first_result = self.admit(reg, now=stamp)
            self.assertEqual(first_result, "terminal" if updated == "invalid" else "terminal_cooldown")
            self.assertEqual(self.admit(reg, now=stamp+601), first_result)
            self.assertEqual(self.admit(reg, now=stamp+1202), first_result)
        self.assertEqual(len(self.jobs()), 2)

    async def test_external_newer_terminal_replaces_old_unsupported_binding(self):
        reg, = self.activate()
        self.waiting(reg)
        old, _ = queue._enqueue(self.ns, "reg", reg)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial',updated_at='2026-01-01T00:00:00+00:00' WHERE id=?", (old["id"],))
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (old["id"], "f"*48, "fixture", reg,
                      json.dumps({"error_code": "unsupported_xml_type"}), 1))
        self.assertEqual(self.admit(reg), "terminal")
        newer, _ = queue._enqueue(self.ns, "reg", reg)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial',updated_at='2026-01-01T00:00:00+00:00' WHERE id=?", (newer["id"],))
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (newer["id"], "e"*48, "fixture", reg,
                      json.dumps({"error_code": "eis_http_503"}), 2))
        self.due()
        self.assertEqual(self.admit(reg), "enqueued")
        self.assertEqual(len(self.jobs()), 3)

    async def test_cooldown_eligibility_survives_diagnostic_error(self):
        reg, = self.activate()
        self.waiting(reg)
        job, _ = queue._enqueue(self.ns, "reg", reg)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial'")
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (job["id"], "f"*48, "fixture", reg,
                      json.dumps({"error_code": "eis_http_503"}), 1))
        stamp = time.time()
        self.assertEqual(self.admit(reg, now=stamp), "terminal_cooldown")
        self.due()
        self.admit(reg, {"error": "catalogue_unavailable"}, now=stamp+1)
        self.assertEqual(self.admit(reg, now=stamp+602), "enqueued")

    async def test_execution_locked_heads_do_not_starve_unlocked_tail(self):
        from v295_eis_originals import _try_execution_lock, _release_execution_lock
        regs = self.activate(10)
        for reg in regs: self.waiting(reg)
        locks = [_try_execution_lock(reg) for reg in regs[:bridge.PAGE_SIZE]]
        try:
            await bridge.pump(self.ns)
            self.assertEqual(self.jobs(), [])
            self.ns["_full_recheck"]["originals_after"] = 0
            await bridge.pump(self.ns)
            self.assertEqual({job["target"] for job in self.jobs()}, set(regs[bridge.PAGE_SIZE:]))
        finally:
            for handle in locks: _release_execution_lock(handle)

    async def test_cancelled_slow_probe_and_commit_keep_cancel_even_if_unlock_fails(self):
        import v295_eis_originals as originals
        reg, = self.activate()
        self.waiting(reg)
        release = originals._release_execution_lock
        for phase in ("probe", "admission"):
            for unlock_first in (False, True):
                with self.subTest(phase=phase, unlock_first=unlock_first):
                    with self.connect() as c:
                        c.execute("DELETE FROM full_recheck_originals")
                        c.execute("DELETE FROM v301_search_jobs")
                    self.due()
                    started, allow_finish = threading.Event(), threading.Event()
                    handles = []
                    actual = bridge.probe if phase == "probe" else bridge.admit
                    def held(*args, **kwargs):
                        value = actual(*args, **kwargs)
                        started.set()
                        if not allow_finish.wait(5): raise TimeoutError("fixture barrier")
                        return value
                    def bad_unlock(handle):
                        handles.append(handle)
                        if unlock_first: release(handle)
                        raise OSError("fixture unlock failure")
                    task = None
                    try:
                        with patch.object(bridge, "probe" if phase == "probe" else "admit", side_effect=held), \
                                patch.object(originals, "_release_execution_lock", side_effect=bad_unlock):
                            task = asyncio.create_task(runtime.run_one(self.ns, {}))
                            self.assertTrue(await asyncio.to_thread(started.wait, 5))
                            task.cancel(); await asyncio.sleep(0); task.cancel(); await asyncio.sleep(0)
                            self.assertFalse(task.done())
                            self.assertIsNone(originals._try_execution_lock(reg))
                            self.assertEqual(self.jobs(), [])  # slow admission is not committed yet
                            allow_finish.set()
                            with self.assertRaises(asyncio.CancelledError): await task
                        self.assertEqual(len(self.jobs()), int(phase == "admission"))
                    finally:
                        allow_finish.set()
                        if task and not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                        if not unlock_first:
                            for handle in handles: release(handle)
                    self.assertEqual(runtime._dispatch(self.ns)["active"], 0)
                    runtime.initialize(self.ns)
                    self.due()
                    await bridge.pump(self.ns)
                    self.assertEqual(len(self.jobs()), 1)

    async def test_metadata_interposition_preserves_full_terminal_adoption(self):
        reg, = self.activate()
        self.waiting(reg)
        a, b = ({"source_version": letter*64, "missing": True} for letter in ("a", "b"))
        self.assertEqual(self.admit(reg, a), "enqueued")
        full = self.jobs()[0]["id"]
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial',updated_at='2026-01-01T00:00:00+00:00'")
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (full, "f"*48, "fixture", reg,
                      json.dumps({"error_code": "eis_http_503"}), 1))
        metadata, _ = queue._enqueue(self.ns, "reg", reg, parent_id="v341-card-facts")
        self.due()
        self.assertEqual(self.admit(reg, b), "metadata_pending")
        with self.connect() as c:
            self.assertEqual(tuple(c.execute("SELECT job_id,job_phase,source_version FROM full_recheck_originals").fetchone()),
                             (full, "active", a["source_version"]))
            c.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (metadata["id"],))
        self.due()
        self.assertEqual(self.admit(reg, b), "terminal")
        self.assertEqual(len(self.jobs()), 2)

    async def test_metadata_interposition_preserves_terminal_cooldown_eligibility(self):
        reg, = self.activate()
        self.waiting(reg)
        old, _ = queue._enqueue(self.ns, "reg", reg)
        with self.connect() as c:
            c.execute("UPDATE v301_search_jobs SET status='partial'")
            c.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)", (old["id"], "f"*48, "fixture", reg,
                      json.dumps({"error_code": "eis_http_503"}), 1))
        stamp = time.time()
        self.assertEqual(self.admit(reg, now=stamp), "terminal_cooldown")
        metadata, _ = queue._enqueue(self.ns, "reg", reg, parent_id="v341-card-facts")
        self.due()
        self.assertEqual(self.admit(reg, now=stamp+1), "metadata_pending")
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT job_phase FROM full_recheck_originals").fetchone()[0], "cooldown")
            c.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (metadata["id"],))
        self.assertEqual(self.admit(reg, now=stamp+601), "enqueued")
        self.assertEqual(len(self.jobs()), 3)

    async def test_rolling_peer_busy_deferral_does_not_veto_actual_lock_owner(self):
        reg, = self.activate()
        self.waiting(reg)
        peer = {**self.ns, "_full_recheck": dict(self.ns["_full_recheck"])}
        started, continue_probe = threading.Event(), threading.Event()
        actual_probe = bridge.probe
        def slow_probe(number):
            observation = actual_probe(number)
            started.set()
            if not continue_probe.wait(5): raise TimeoutError("fixture probe")
            return observation
        task = None
        try:
            with patch.object(bridge, "probe", side_effect=slow_probe):
                task = asyncio.create_task(bridge.pump(self.ns))
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                await bridge.pump(peer)
                with self.connect() as c:
                    self.assertEqual(c.execute("SELECT outcome FROM full_recheck_originals").fetchone()[0], "execution_busy")
                continue_probe.set()
                await task
            self.assertEqual(len(self.jobs()), 1)
            self.due()
            peer["_full_recheck"]["originals_after"] = 0
            await asyncio.gather(bridge.pump(self.ns), bridge.pump(peer))
            self.assertEqual(len(self.jobs()), 1)
        finally:
            continue_probe.set()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_disabled_or_conflicting_target_cannot_enqueue(self):
        reg, = self.activate()
        self.waiting(reg)
        self.ns["_full_recheck"].pop("request_id")
        await bridge.pump(self.ns)
        self.assertEqual(self.jobs(), [])
        self.ns["_full_recheck"]["request_id"] = runtime.AUTHORIZED_EPOCH
        self.ns["_full_recheck"]["engine_signature"] = "conflicting-engine"
        await bridge.pump(self.ns)
        self.assertEqual(self.jobs(), [])


if __name__ == "__main__":
    unittest.main()
