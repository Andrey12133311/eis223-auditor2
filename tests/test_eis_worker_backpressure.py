"""Real held-writer proofs for the authenticated VPS worker protocol."""
import asyncio
import concurrent.futures
import contextlib
import io
import json
import sqlite3
import time
import unittest
from unittest.mock import patch

from starlette.requests import ClientDisconnect

from tests import test_eis_server_worker as fixtures

worker, queue = fixtures.worker, fixtures.queue
KEY, REG = fixtures.KEY, fixtures.REG
INN = "7701234567"


class WorkerBackpressureTests(unittest.TestCase):
    setUp = fixtures.ServerWorkerTests.setUp
    review = fixtures.ServerWorkerTests.review
    enqueue = fixtures.ServerWorkerTests.enqueue
    claim = fixtures.ServerWorkerTests.claim
    row = fixtures.ServerWorkerTests.row

    def snapshot(self):
        with self.ns["conn"]() as db:
            return list(db.iterdump())

    def busy_then_retry(self, method, path, *, json=None, params=None):
        before = self.snapshot()
        connector = self.ns["conn"]
        # Only the fixture uses a short deadline; production timeouts stay intact.
        self.ns["conn"] = lambda: sqlite3.connect(self.home/"queue.sqlite", timeout=.02)
        blocker = self.ns["conn"]()
        output = io.StringIO()
        try:
            blocker.execute("BEGIN IMMEDIATE")
            unauthorized = self.client.request(method, path, json=json, params=params)
            self.assertEqual(unauthorized.status_code, 401)
            with contextlib.redirect_stdout(output):
                busy = self.client.request(method, path, headers=self.headers, json=json, params=params)
            self.assertEqual(busy.status_code, 503, busy.text)
            self.assertEqual(busy.headers["Retry-After"], "300")
            self.assertNotIn("X-EIS223-Retry-Scope", busy.headers)
            self.assertEqual(set(busy.json()), {"detail"})
            self.assertNotIn("Traceback", output.getvalue())
            for private in (KEY, self.identity, REG, INN):
                self.assertNotIn(private, busy.text + output.getvalue())
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.reviews, [])
        finally:
            blocker.rollback()
            blocker.close()
            self.ns["conn"] = connector
        response = self.client.request(method, path, headers=self.headers, json=json, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_busy_heartbeat_does_not_renew_or_ack_until_writer_released(self):
        self.enqueue()
        job = self.claim()
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET lease_until=? WHERE id=?", (int(time.time())+600, job["id"]))
        before = self.row(job["id"])
        data = {"worker_id": self.identity, "current_job_id": job["id"], "lease_token": job["lease_token"]}
        response = self.busy_then_retry("POST", "/api/eis-worker/heartbeat", json=data)
        self.assertTrue(response.json()["received"])
        after = self.row(job["id"])
        self.assertGreater(after["lease_until"], before["lease_until"])
        self.assertEqual(after["lease_until"], response.json()["lease_until"])
        for key in ("status", "lease_token", "lease_owner", "attempts", "found", "accepted"):
            self.assertEqual(after[key], before[key])
        for altered in ({"worker_id": self.other}, {"lease_token": "a"*48}):
            rejected = self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json={**data, **altered})
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(self.row(job["id"]), after)
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET lease_until=0 WHERE id=?", (job["id"],))
        expired = self.snapshot()
        rejected = self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.snapshot(), expired)

    def test_busy_claim_does_not_spend_attempt_and_redelivers_same_lease(self):
        created = self.enqueue()["job"]
        params = {"worker_id": self.identity, "limit": 1}
        response = self.busy_then_retry("GET", "/api/eis-worker/jobs/next", params=params)
        job = response.json()["jobs"][0]
        self.assertEqual(job["id"], created["id"])
        self.assertEqual(job["attempts"], 1)
        replay = self.client.get("/api/eis-worker/jobs/next", headers=self.headers, params=params)
        self.assertEqual(replay.json()["jobs"], [job])

    def test_busy_bootstrap_does_not_create_then_retry_deduplicates(self):
        data = {"reg_number": REG, "source": "worker_bootstrap"}
        response = self.busy_then_retry("POST", "/api/eis-worker/jobs", json=data)
        self.assertTrue(response.json()["created"])
        replay = self.client.post("/api/eis-worker/jobs", headers=self.headers, json=data)
        self.assertFalse(replay.json()["created"])
        self.assertEqual(response.json()["job"], replay.json()["job"])
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)

    def test_busy_inn_report_keeps_lease_and_candidates_until_committed_once(self):
        queue._enqueue(self.ns, "inn", INN, 1, 0, "", False)
        claimed = self.client.get("/api/eis-worker/jobs/next", headers=self.headers,
                                  params={"worker_id": self.identity, "limit": 1, "capabilities": "inn_search"})
        job = claimed.json()["jobs"][0]
        data = {"kind": "inn", "worker_id": self.identity, "lease_token": job["lease_token"],
                "inn": INN, "page": 1, "status": "completed", "reg_numbers": [REG], "has_more": False,
                "source_url": "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?fz223=on&customerInn="+INN+"&pageNumber=1"}
        path = "/api/eis-worker/inn-jobs/"+job["id"]+"/report"
        response = self.busy_then_retry("POST", path, json=data)
        self.assertTrue(response.json()["received"])
        self.assertTrue(response.json()["search_complete"])
        replay = self.client.post(path, headers=self.headers, json=data)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual({k: v for k, v in replay.json().items() if k != "replayed"}, response.json())
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM eis_server_inn_reports").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM eis_inn_candidates").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE kind='reg'").fetchone()[0], 1)

    def test_busy_discovery_does_not_ack_page_then_same_payload_replays_once(self):
        data = {"worker_id": self.identity, "reg_numbers": [REG],
                "source_url": "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?fz223=on"}
        response = self.busy_then_retry("POST", "/api/eis-worker/discovered", json=data)
        self.assertTrue(response.json()["accepted"])
        replay = self.client.post("/api/eis-worker/discovered", headers=self.headers, json=data)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual({k: v for k, v in replay.json().items() if k != "replayed"}, response.json())
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM eis_server_discovery_candidates").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs").fetchone()[0], 1)

    def test_busy_heartbeat_cannot_resurrect_lease_that_expires_during_pause(self):
        self.enqueue()
        job = self.claim()
        now = int(time.time())
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET lease_until=? WHERE id=?", (now+30, job["id"]))
        before = self.snapshot()
        data = {"worker_id": self.identity, "current_job_id": job["id"], "lease_token": job["lease_token"]}
        self.ns["conn"] = lambda: sqlite3.connect(self.home/"queue.sqlite", timeout=.02)
        blocker = self.ns["conn"]()
        try:
            blocker.execute("BEGIN IMMEDIATE")
            with patch.object(worker.time, "time", return_value=now):
                busy = self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
            self.assertEqual(busy.status_code, 503)
            self.assertEqual(self.snapshot(), before)
        finally:
            blocker.rollback()
            blocker.close()
        with patch.object(worker.time, "time", return_value=now+301):
            expired = self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
        self.assertEqual(expired.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_all_worker_writes_translate_only_expected_sqlite_contention(self):
        import eis_inn_search
        routes = (
            (worker, "claim", "GET", "/api/eis-worker/jobs/next", {"worker_id": self.identity}, None),
            (worker, "_enqueue", "POST", "/api/eis-worker/jobs", None,
             {"reg_number": REG, "source": "worker_bootstrap"}),
            (worker, "report", "POST", "/api/eis-worker/jobs/"+"a"*24+"/report", None, {}),
            (eis_inn_search, "report", "POST", "/api/eis-worker/inn-jobs/"+"a"*24+"/report", None, {}),
            (worker, "heartbeat", "POST", "/api/eis-worker/heartbeat", None, {}),
            (worker, "discovered", "POST", "/api/eis-worker/discovered", None, {}),
        )
        for module, function, method, path, params, data in routes:
            for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
                         sqlite3.SQLITE_BUSY | 256, sqlite3.SQLITE_LOCKED | 256):
                with self.subTest(function=function, code=code):
                    error = sqlite3.OperationalError("private diagnostic " + KEY)
                    error.sqlite_errorcode = code
                    with patch.object(module, function, side_effect=error) as call, \
                            contextlib.redirect_stdout(io.StringIO()) as output:
                        response = self.client.request(method, path, headers=self.headers, params=params, json=data)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.headers["Retry-After"], "300")
                    call.assert_called_once()  # no hidden server retries
                    self.assertNotIn(KEY, response.text + output.getvalue())
                    self.assertNotIn("private diagnostic", response.text + output.getvalue())
            for error in (sqlite3.OperationalError("no such table: fixture"),
                          sqlite3.OperationalError("disk I/O error"), RuntimeError("database is locked")):
                with self.subTest(function=function, error=str(error)), patch.object(module, function, side_effect=error):
                    with self.assertRaises(type(error)):
                        self.client.request(method, path, headers=self.headers, params=params, json=data)

    def test_busy_log_labels_and_rate_are_bounded_even_with_concurrent_failures(self):
        with patch.dict(worker._database_busy_logged_at, {}, clear=True), \
                patch.object(worker.time, "monotonic", return_value=1000) as clock, \
                patch.object(worker, "_log_telemetry") as log:
            labels = tuple(worker._DATABASE_BUSY_DETAILS)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(worker._log_database_busy, labels*40))
            self.assertEqual(log.call_count, len(labels))
            self.assertEqual(set(worker._database_busy_logged_at), set(labels))
            clock.return_value = 1299
            for label in labels:
                worker._log_database_busy(label)
            self.assertEqual(log.call_count, len(labels))
            clock.return_value = 1300
            for label in labels:
                worker._log_database_busy(label)
            self.assertEqual(log.call_count, len(labels)*2)
            for call in log.call_args_list:
                self.assertIn(call.args[0], {label+"_busy" for label in labels})
                self.assertEqual(call.kwargs, {"retry_after_seconds": 300})

    def test_closed_log_stream_does_not_replace_busy_response(self):
        error = sqlite3.OperationalError("database is locked")
        for logger_error in (BrokenPipeError(), ValueError("I/O operation on closed file")):
            with self.subTest(error=type(logger_error).__name__), \
                    patch.dict(worker._database_busy_logged_at, {}, clear=True), \
                    patch.object(worker, "_log_telemetry", side_effect=logger_error), \
                    patch.object(worker, "heartbeat", side_effect=error):
                response = self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json={})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["Retry-After"], "300")

    def test_heartbeat_nonbusy_database_and_unexpected_errors_remain_visible(self):
        for error in (sqlite3.OperationalError("no such table: fixture"), RuntimeError("fixture defect")):
            with self.subTest(error=type(error).__name__), patch.object(worker, "heartbeat", side_effect=error):
                with self.assertRaises(type(error)):
                    self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json={"worker_id": self.identity})

    def asgi_post(self, path, events, token=KEY):
        messages, reads = [], []
        pending = list(events)
        async def receive():
            reads.append(True)
            event = pending.pop(0)
            if isinstance(event, BaseException):
                raise event
            return event
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"x-eis223-upload-token", token.encode())],
                 "server": ("testserver", 80), "client": ("fixture", 1234)}
        asyncio.run(self.ns["app"](scope, receive, send))
        start = next(message for message in messages if message["type"] == "http.response.start")
        content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return start, content, len(reads)

    def test_partial_worker_bodies_disconnect_quietly_before_any_database_call(self):
        self.enqueue()
        job = self.claim()
        before = self.snapshot()
        routes = ("/api/eis-worker/jobs", "/api/eis-worker/heartbeat", "/api/eis-worker/discovered",
                  "/api/eis-worker/jobs/"+job["id"]+"/report",
                  "/api/eis-worker/inn-jobs/"+job["id"]+"/report")
        events = [{"type": "http.request", "body": b'{"worker_id":', "more_body": True},
                  {"type": "http.request", "body": b'"partial', "more_body": True},
                  {"type": "http.disconnect"}]
        for path in routes:
            with self.subTest(path=path):
                with patch.object(worker, "_database_call") as transaction, \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    start, content, reads = self.asgi_post(path, events)
                self.assertEqual(start["status"], 400)
                self.assertEqual(json.loads(content), {"detail": "Incomplete worker request body"})
                self.assertEqual(reads, 3)
                self.assertNotIn(b"retry-after", dict(start["headers"]))
                transaction.assert_not_called()
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(self.snapshot(), before)
        data = {"worker_id": self.identity, "current_job_id": job["id"], "lease_token": job["lease_token"]}
        retry = self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json()["received"])
        self.assertEqual(self.row(job["id"])["lease_token"], job["lease_token"])
        self.assertEqual(self.row(job["id"])["attempts"], job["attempts"])

    def test_body_auth_validation_and_size_limits_survive_disconnect_handling(self):
        path = "/api/eis-worker/heartbeat"
        before = self.snapshot()
        cases = (
            ("wrong-token", [{"type": "http.disconnect"}], 401, 0),
            (KEY, [{"type": "http.request", "body": b'{"incomplete":', "more_body": False}], 422, 1),
            (KEY, [{"type": "http.request", "body": b'[]', "more_body": False}], 422, 1),
            (KEY, [{"type": "http.request", "body": b'x'*worker.MAX_REPORT_BYTES, "more_body": True},
                   {"type": "http.request", "body": b'x', "more_body": True},
                   {"type": "http.disconnect"}], 413, 2),
        )
        for token, events, expected, count in cases:
            with self.subTest(expected=expected), patch.object(worker, "_database_call") as transaction:
                start, content, reads = self.asgi_post(path, events, token=token)
            self.assertEqual(start["status"], expected)
            self.assertEqual(reads, count)
            self.assertNotIn(b"retry-after", dict(start["headers"]))
            transaction.assert_not_called()
            self.assertEqual(self.snapshot(), before)

    def test_unexpected_stream_errors_and_cancellation_still_propagate(self):
        for error in (RuntimeError("fixture stream error"), asyncio.CancelledError(),
                      sqlite3.OperationalError("database is locked")):
            events = [{"type": "http.request", "body": b'{', "more_body": True}, error]
            with self.subTest(error=type(error).__name__), patch.object(worker, "_database_call") as transaction:
                with self.assertRaises(type(error)):
                    self.asgi_post("/api/eis-worker/heartbeat", events)
            transaction.assert_not_called()

    def test_disconnect_outside_body_stream_is_not_hidden(self):
        with patch.object(worker, "heartbeat", side_effect=ClientDisconnect):
            with self.assertRaises(ClientDisconnect):
                self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json={"worker_id": self.identity})


if __name__ == "__main__":
    unittest.main()
