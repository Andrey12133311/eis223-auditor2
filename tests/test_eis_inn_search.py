"""INN pages are VPS capabilities and candidate downloads, never buyer facts."""
import importlib
import time
import unittest
from unittest.mock import patch

fixtures = importlib.import_module("tests.test_eis_server_worker")
worker = fixtures.worker
queue = fixtures.queue
REG = fixtures.REG
INN = "7701234567"
OTHER_INN = "7707654321"


class InnSearchTests(unittest.TestCase):
    setUp = fixtures.ServerWorkerTests.setUp
    review = fixtures.ServerWorkerTests.review
    enqueue = fixtures.ServerWorkerTests.enqueue
    row = fixtures.ServerWorkerTests.row
    make_ready = fixtures.ServerWorkerTests.make_ready

    def inn_job(self, inn=INN, page=1, parent=""):
        return queue._enqueue(self.ns, "inn", inn, page, 0, parent, False)[0]

    def claim(self, capable=True, identity=None, limit=2):
        params = {"worker_id": identity or self.identity, "limit": limit}
        if capable:
            params["capabilities"] = "inn_search"
        response = self.client.get("/api/eis-worker/jobs/next", headers=self.headers, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["jobs"]

    def report(self, job, **updates):
        inn = job.get("inn", job.get("target"))
        data = {"kind": "inn", "worker_id": self.identity, "lease_token": job["lease_token"],
                "inn": inn, "page": job["page"], "status": "completed", "reg_numbers": [REG],
                "has_more": False, "source_url": self.source(inn, job["page"])}
        data.update(updates)
        return self.client.post("/api/eis-worker/inn-jobs/"+job["id"]+"/report", headers=self.headers, json=data)

    @staticmethod
    def source(inn=INN, page=1):
        return ("https://zakupki.gov.ru/epz/order/extendedsearch/results.html?fz223=on&customerInn="+
                inn+"&pageNumber="+str(page))

    def test_old_workers_receive_only_registry_jobs(self):
        requested = self.inn_job()
        self.enqueue()
        jobs = self.claim(capable=False)
        self.assertEqual([job["kind"] for job in jobs], ["reg"])
        self.assertEqual(self.row(requested["id"])["attempts"], 0)
        self.assertFalse(worker.health(self.ns)["inn_search_available"])
        new_jobs = self.claim()
        inn = [job for job in new_jobs if job["kind"] == "inn"][0]
        self.assertEqual((inn["target"], inn["inn"], inn["page"]), (INN, INN, 1))
        self.assertNotIn("reg_number", inn)
        # A rolled-back old worker is never handed its former INN claim.
        self.assertTrue(all(job["kind"] == "reg" for job in self.claim(capable=False)))

    def test_claims_limit_two_jobs_and_one_inn_globally(self):
        self.inn_job()
        self.inn_job(OTHER_INN)
        self.enqueue()
        self.enqueue("32610000001")
        first = self.claim()
        self.assertEqual(len(first), 2)
        self.assertEqual(sum(job["kind"] == "inn" for job in first), 1)
        second = self.claim(identity=self.other)
        self.assertTrue(second)
        self.assertTrue(all(job["kind"] == "reg" for job in second))

    def test_due_pagination_gets_one_slot_despite_registry_backlog(self):
        pagination = queue._enqueue(self.ns, "inn", INN, 2, 12, "", False)[0]
        for suffix in range(20):
            queue._enqueue(self.ns, "reg", "3261000"+str(suffix).zfill(4), 1, 8, "candidate", False)
        jobs = self.claim()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(sum(job["kind"] == "inn" for job in jobs), 1)
        self.assertEqual(sum(job["kind"] == "reg" for job in jobs), 1)
        self.assertIn(pagination["id"], [job["id"] for job in jobs])

    def test_long_search_retry_keeps_remote_delay(self):
        self.inn_job()
        job = self.claim()[0]
        now = int(time.time())
        with patch.object(worker.time, 'time', return_value=now):
            result = self.report(job, status="error", reg_numbers=[], source_url="",
                                 error_code="eis_http_429", retry_after_seconds=86400)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.row(job["id"])["not_before"], now + 86400)

    def test_search_cooldown_never_exhausts_real_attempt_budget(self):
        self.inn_job()
        job = self.claim()[0]
        with self.ns["conn"]() as db:
            db.execute("INSERT INTO eis_server_backoff VALUES(?,12)", (job["id"],))
        for _ in range(15):
            result = self.report(job, status="error", reg_numbers=[], source_url="",
                                 error_code="eis_search_cooldown", retry_after_seconds=30)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["status"], "queued")
            self.assertFalse(result.json()["search_complete"])
            self.assertEqual(self.row(job["id"])["attempts"], 0)
            self.make_ready(job["id"])
            job = self.claim()[0]

    def test_health_capability_uses_any_fresh_worker(self):
        for identity, capable in ((self.identity, True), (self.other, False)):
            response = self.client.post("/api/eis-worker/heartbeat", headers=self.headers,
                json={"worker_id": identity, "capabilities": ["inn_search"] if capable else []})
            self.assertEqual(response.status_code, 200)
        self.assertTrue(worker.health(self.ns)["inn_search_available"])
        with self.ns["conn"]() as db:
            db.execute("UPDATE eis_server_workers SET seen_at=? WHERE worker_id=?",
                       (int(time.time())-121, self.identity))
        health = worker.health(self.ns)
        self.assertTrue(health["active"])
        self.assertFalse(health["inn_search_available"])
        self.assertIn("inn_search", self.client.get("/eis223/worker-version").json()["supported_capabilities"])

    def test_inn_report_auth_owner_page_and_replay_fencing(self):
        self.inn_job()
        job = self.claim()[0]
        path = "/api/eis-worker/inn-jobs/"+job["id"]+"/report"
        self.assertEqual(self.client.post(path, json={}).status_code, 401)
        self.assertEqual(self.report(job, worker_id=self.other).status_code, 409)
        self.assertEqual(self.report(job, page=2, source_url=self.source(page=2)).status_code, 409)
        self.assertEqual(self.report(job, inn=OTHER_INN, source_url=self.source(OTHER_INN)).status_code, 409)
        result = self.report(job)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "done")
        replay = self.report(job, status="error", reg_numbers=[], source_url="", error_code="eis_http_403")
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["status"], "done")
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM eis_inn_candidates").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE kind='reg'").fetchone()[0], 1)

    def test_expired_inn_report_cannot_overwrite_reclaimed_attempt(self):
        self.inn_job()
        old = self.claim()[0]
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET lease_until=0 WHERE id=?", (old["id"],))
        self.assertEqual(self.claim(identity=self.other), [])
        self.make_ready(old["id"])
        new = self.claim(identity=self.other)[0]
        self.assertNotEqual(old["lease_token"], new["lease_token"])
        self.assertEqual(self.report(old).status_code, 409)
        self.assertEqual(self.row(new["id"])["lease_owner"], self.other)

    def test_candidates_never_write_inn_mapping_or_manual_membership(self):
        self.inn_job()
        job = self.claim()[0]
        self.enqueue()  # Reuse an existing original-download job.
        result = self.report(job, reg_numbers=[REG, REG, "32610000001"]).json()
        self.assertEqual(result["candidate_count"], 2)
        self.assertFalse(result["buyer_inn_verified_by_search"])
        self.assertFalse(result["audit_complete"])
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_inn_purchases").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE kind='reg'").fetchone()[0], 2)
        progress = worker.inn_progress(self.ns, job["id"])
        self.assertEqual(progress["candidate_count"], 2)
        self.assertEqual(progress["verification_pending"], 2)
        self.assertEqual(progress["verified_matches"], 0)

    def test_verification_progress_excludes_wrong_buyer_and_stops_terminal_pending(self):
        self.inn_job()
        job = self.claim()[0]
        numbers = [REG, "32610000001", "32610000002"]
        self.report(job, reg_numbers=numbers)
        with self.ns["conn"]() as db:
            db.execute("CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer_inn TEXT)")
            db.execute("INSERT INTO purchases VALUES(?,?)", (REG, INN))
            db.execute("INSERT INTO purchases VALUES(?,?)", (numbers[1], OTHER_INN))
            db.execute("UPDATE v301_search_jobs SET status='partial' WHERE target=?", (numbers[2],))
        progress = worker.inn_progress(self.ns, job["id"])
        self.assertEqual(progress["verified_matches"], 1)
        self.assertEqual(progress["excluded_candidates"], 1)
        self.assertEqual(progress["verification_pending"], 0)
        self.assertEqual(progress["unresolved_candidates"], 1)

    def test_pagination_delays_next_page_and_deduplicates_only_within_current_search(self):
        self.inn_job()
        one = self.claim()[0]
        result = self.report(one, has_more=True).json()
        next_id = result["next_page_job_id"]
        self.assertIsNotNone(next_id)
        self.assertTrue(result["page_complete"])
        self.assertFalse(result["search_complete"])
        page_two = self.row(next_id)
        self.assertEqual((page_two["parent_id"], page_two["page"]), (one["id"], 2))
        self.assertGreaterEqual(page_two["not_before"], time.time()+58)
        self.assertLessEqual(page_two["not_before"], time.time()+60)
        replay = self.report(one, has_more=True).json()
        self.assertEqual(replay["next_page_job_id"], next_id)
        self.make_ready(next_id)
        second = [job for job in self.claim() if job["kind"] == "inn"][0]
        self.report(second, status="no_data", reg_numbers=[])
        fresh = self.inn_job()
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET status='done' WHERE kind='reg'")
        third = [job for job in self.claim() if job["kind"] == "inn"][0]
        self.assertEqual(third["id"], fresh["id"])
        later = self.report(third, has_more=True).json()
        self.assertNotEqual(later["next_page_job_id"], next_id)

    def test_last_page_is_limited_not_exhaustive(self):
        self.inn_job(page=10)
        job = self.claim()[0]
        result = self.report(job, has_more=True).json()
        self.assertTrue(result["pagination_limited"])
        self.assertIsNone(result["next_page_job_id"])
        self.assertIn("не полностью", result["detail"])
        self.assertTrue(worker.inn_progress(self.ns, job["id"])["pagination_limited"])

    def test_no_data_is_successful_empty_search_and_errors_are_not_empty_results(self):
        self.inn_job()
        job = self.claim()[0]
        self.assertEqual(self.report(job, status="completed", reg_numbers=[]).status_code, 422)
        self.assertEqual(self.report(job, status="no_data", reg_numbers=[], has_more=True).status_code, 422)
        result = self.report(job, status="no_data", reg_numbers=[]).json()
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["search_complete"])
        self.inn_job()
        job = [job for job in self.claim() if job["kind"] == "inn"][0]
        result = self.report(job, status="error", reg_numbers=[], source_url="", error_code="search_unrecognized").json()
        self.assertEqual(result["status"], "queued")
        self.assertFalse(result["search_complete"])
        self.assertEqual(self.row(job["id"])["attempts"], 1)

    def test_source_and_collection_validation_fail_closed(self):
        self.inn_job()
        job = self.claim()[0]
        for source in (self.source(OTHER_INN), self.source(page=2), self.source()+"&customerInn="+INN,
                       self.source().replace("zakupki.gov.ru", "example.com")):
            self.assertEqual(self.report(job, source_url=source).status_code, 422)
        self.assertEqual(self.report(job, reg_numbers=[REG]*21).status_code, 422)
        self.assertEqual(self.report(job, reg_numbers=["12345678901"]).status_code, 422)
        self.assertEqual(self.row(job["id"])["status"], "leased")

    def test_queue_capacity_keeps_candidates_for_later_download(self):
        self.inn_job()
        job = self.claim()[0]
        # Capacity is bounded by active queue records; the candidate relation
        # remains durable even when its original-download job cannot yet fit.
        with patch("eis_inn_search.MAX_QUEUE", 9):
            self.enqueue("32610000009")
            result = self.report(job).json()
            self.assertEqual(result["pending_candidates"], 1)
            with self.ns["conn"]() as db:
                self.assertEqual(db.execute("SELECT reg_job_id FROM eis_inn_candidates").fetchone()[0], "")
                db.execute("UPDATE v301_search_jobs SET status='done' WHERE kind='reg'")
            later = self.claim(capable=False)
            self.assertEqual([item["reg_number"] for item in later], [REG])

    def test_cooldown_uses_remaining_slot_and_real_errors_keep_backoff(self):
        self.inn_job()
        now = int(time.time())
        for error, retry, expected, attempts in (("eis_search_cooldown", 7, 7, 0),
                                                ("eis_search_cooldown", 0, 60, 0),
                                                ("eis_connection_error", 0, 900, 1),
                                                ("eis_http_429", 1200, 1200, 1)):
            job = [job for job in self.claim() if job["kind"] == "inn"][0]
            with patch.object(worker.time, "time", return_value=now):
                response = self.report(job, status="error", reg_numbers=[], source_url="",
                                       error_code=error, retry_after_seconds=retry)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.row(job["id"])["not_before"], now+expected)
            self.assertEqual(self.row(job["id"])["attempts"], attempts)
            self.make_ready(job["id"])

    def test_inn_report_telemetry_distinguishes_empty_search_from_failed_page(self):
        self.inn_job()
        job = self.claim()[0]
        with patch.object(worker, "_log_telemetry") as logged:
            result = self.report(job, status="no_data", reg_numbers=[])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(logged.call_args.kwargs["requested_status"], "no_data")
        self.assertTrue(logged.call_args.kwargs["search_complete"])
        self.assertEqual(logged.call_args.kwargs["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
