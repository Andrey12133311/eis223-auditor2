"""VPS intake authentication, shared queue fencing and actual-byte completion."""
import concurrent.futures
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import eis_server_worker as worker
import v301_search_jobs as queue
from v294_eis_bridge import parse_notices
from scripts.account_production_v1 import ProductionAccountGate

KEY = "test-machine-secret-with-more-than-24-characters"
REG = "32616233661"
UID = "A"*32
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+UID


class ServerWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {"EIS223_DATA_DIR": str(self.home),
                                     "EIS223_UPLOAD_TOKEN": KEY, "EIS223_AUTH_ENFORCE": "true"})
        env.start()
        self.addCleanup(env.stop)
        session = patch("scripts.account_production_v1.user_session", return_value=None)
        self.session = session.start()
        self.addCleanup(session.stop)
        self.identity = str(uuid.uuid4())
        self.other = str(uuid.uuid4())
        self.reviews = []
        self.ns = {"conn": lambda: sqlite3.connect(self.home/"queue.sqlite", timeout=5),
                   "app": FastAPI(), "_v295_enqueue_review": self.review}
        queue.install(self.ns)
        worker.install(self.ns)
        with self.ns["conn"]() as db:
            db.execute("DELETE FROM v301_search_jobs")
        self.ns["app"].add_middleware(ProductionAccountGate)
        self.client = TestClient(self.ns["app"])
        self.addCleanup(self.client.close)
        self.headers = {"x-eis223-upload-token": KEY}

    def review(self, reg):
        self.reviews.append(reg)
        return {"status": "queued"}

    def enqueue(self, reg=REG):
        response = self.client.post("/api/eis-worker/jobs", headers=self.headers,
                                    json={"reg_number": reg, "source": "worker_bootstrap"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def claim(self, identity=None):
        response = self.client.get("/api/eis-worker/jobs/next", headers=self.headers,
                                   params={"worker_id": identity or self.identity, "limit": 1})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["jobs"][0]

    def report(self, job, identity=None, **changes):
        data = {"worker_id": identity or self.identity, "lease_token": job["lease_token"],
                "reg_number": job["reg_number"], "status": "completed"}
        data.update(changes)
        return self.client.post("/api/eis-worker/jobs/"+job["id"]+"/report",
                                json=data, headers=self.headers)

    def row(self, identifier):
        db = queue._db(self.ns)
        try:
            return dict(db.execute("SELECT * FROM v301_search_jobs WHERE id=?", (identifier,)).fetchone())
        finally:
            db.close()

    def make_ready(self, identifier):
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET not_before=0 WHERE id=?", (identifier,))

    def archive(self, extra="", document=True):
        attachment = ("<document><guid>original-guid</guid><fileName>original.pdf</fileName><url>"+
                      URL+"</url></document>") if document else ""
        xml = (f'<purchaseNoticeKESMBO xmlns="http://zakupki.gov.ru/223fz/purchase/1">'
               f'<body><item><purchaseNoticeKESMBOData><registrationNumber>{REG}</registrationNumber>'
               f'<version>1</version><attachments>{attachment}{extra}</attachments>'
               '</purchaseNoticeKESMBOData></item></body></purchaseNoticeKESMBO>').encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("purchaseNoticeKESMBO_"+REG+"_fixture.xml", xml)
        raw = buffer.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        folder = self.home/"eis223_official_archive_inbox"/REG
        folder.mkdir(parents=True, exist_ok=True)
        (folder/(sha+".zip")).write_bytes(raw)
        (folder/(sha+".json")).write_text(json.dumps({
            "source": "official_eis_soap_archive", "reg_number": REG,
            "archive_sha256": sha, "notices": parse_notices(raw)}))
        return sha

    def original(self):
        raw = b"%PDF-1.7\nactual original bytes\n%%EOF"
        digest = hashlib.sha256(raw).hexdigest()
        folder = self.home/"eis223_official_originals"/REG/UID
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/(digest+".pdf")
        path.write_bytes(raw)
        (folder/"receipt.json").write_text(json.dumps({
            "reg_number": REG, "uid": UID, "sha256": digest, "bytes": len(raw),
            "extension": ".pdf", "source_url": URL}))
        return path

    def test_machine_routes_require_token_without_account_session_or_origin(self):
        for path, method in (("/api/eis-worker/status", "GET"),
                             ("/api/eis-worker/jobs", "POST"),
                             ("/api/eis-worker/heartbeat", "POST"),
                             ("/api/eis-worker/discovered", "POST")):
            for token in ("", "bad-token"):
                response = self.client.request(method, path, headers={"x-eis223-upload-token": token}, json={})
                self.assertEqual(response.status_code, 401)
        self.session.reset_mock()
        response = self.client.get("/api/eis-worker/status", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.session.assert_not_called()
        response = self.client.get("/api/eis-worker/status", headers={**self.headers, "Origin": "https://zakupki.gov.ru"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/eis223/worker-version").json()["feature"], worker.FEATURE)
        for path in ("/api/eis-worker/status/extra", "/api/eis-worker/jobs/anything/report", "/me/profile"):
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 401)

    def test_bootstrap_deduplicates_without_creating_manual_submissions(self):
        one = self.enqueue()
        two = self.enqueue()
        self.assertTrue(one["created"])
        self.assertFalse(two["created"])
        self.assertEqual(one["job"]["id"], two["job"]["id"])
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)
        explicit, created = queue._enqueue(self.ns, "reg", REG, 1, 0, "", True)
        self.assertFalse(created)
        self.assertEqual(explicit["id"], one["job"]["id"])
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 1)

    def test_atomic_claims_are_disjoint_and_lost_response_is_recoverable(self):
        self.enqueue()
        self.enqueue("32610000001")
        queue._enqueue(self.ns, "inn", "1234567890")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda identity: worker.claim(self.ns, identity, 1),
                                 [self.identity, self.other]))
        self.assertNotEqual(a[0]["id"], b[0]["id"])
        self.assertEqual(worker.claim(self.ns, self.identity, 1), a)
        self.assertEqual(self.row(a[0]["id"])["attempts"], 1)
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT status FROM v301_search_jobs WHERE kind='inn'").fetchone()[0], "queued")

    def test_wrong_owner_old_lease_and_browser_reports_cannot_finish_server_job(self):
        self.enqueue()
        old = self.claim()
        self.assertEqual(self.report(old, identity=self.other).status_code, 409)
        browser = self.client.post("/api/v301/bridge/report/"+old["id"], json={"status": "done"},
            headers={**self.headers, "origin": "https://zakupki.gov.ru", "x-eis223-collector-version": "378"})
        self.assertEqual(browser.status_code, 409)
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET lease_until=0 WHERE id=?", (old["id"],))
        self.assertEqual(worker.claim(self.ns, self.other, 1), [])
        self.make_ready(old["id"])
        new = self.claim(self.other)
        self.assertNotEqual(old["lease_token"], new["lease_token"])
        self.assertEqual(self.report(old).status_code, 409)
        self.assertEqual(self.row(new["id"])["lease_owner"], self.other)

    def test_late_browser_report_cannot_finish_server_retry_between_leases(self):
        self.enqueue()
        job = self.claim()
        response = self.report(job, status="partial")
        self.assertEqual(response.json()["status"], "queued")
        browser = self.client.post("/api/v301/bridge/report/"+job["id"], json={"status": "done"},
            headers={**self.headers, "origin": "https://zakupki.gov.ru", "x-eis223-collector-version": "378"})
        self.assertEqual(browser.status_code, 409)
        self.make_ready(job["id"])
        # A genuine browser claim may take over the available retry.
        claimed = queue._claim(self.ns, 378)
        self.assertEqual(claimed["id"], job["id"])
        self.assertEqual(claimed["lease_owner"], "")

    def test_heartbeat_extends_only_owned_live_lease_and_distinguishes_health(self):
        self.enqueue()
        job = self.claim()
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET lease_until=? WHERE id=?", (int(time.time())+2, job["id"]))
        data = {"worker_id": self.identity, "current_job_id": job["id"], "lease_token": job["lease_token"],
                "discovery_status": "blocked", "discovery_error": "eis_http_403"}
        response = self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()["lease_until"], time.time()+850)
        response = self.client.post("/api/eis-worker/heartbeat", headers=self.headers,
                                    json={**data, "worker_id": self.other})
        self.assertEqual(response.status_code, 409)
        bridge = queue._bridge(self.ns)
        self.assertTrue(bridge["active"])
        self.assertTrue(bridge["server_active"])
        self.assertFalse(bridge["browser_active"])
        self.assertEqual(bridge["discovery_status"], "blocked")

    def test_claimed_counts_do_not_complete_metadata_only_transfer(self):
        sha = self.archive()
        self.enqueue()
        job = self.claim()
        response = self.report(job, archive_sha256=sha, archives_uploaded=99, originals_uploaded=99,
                               originals_failed=0)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "queued")
        self.assertFalse(result["download_complete"])
        self.assertEqual((result["originals_expected"], result["originals_received"]), (1, 0))
        self.assertEqual(self.reviews, [])

    def test_saved_bytes_finish_download_not_audit_and_report_replay_is_idempotent(self):
        sha = self.archive()
        self.original()
        self.enqueue()
        job = self.claim()
        response = self.report(job, archive_sha256=sha)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["download_complete"])
        self.assertFalse(result["audit_complete"])
        self.assertEqual(self.reviews, [REG])
        replay = self.report(job, status="error", retry_after_seconds=1200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["status"], "done")
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(self.reviews, [REG])

    def test_same_size_corruption_and_missing_selected_archive_cannot_complete(self):
        sha = self.archive()
        original = self.original()
        self.assertTrue(worker.evidence(REG, [sha])["complete"])
        raw = original.read_bytes()
        original.write_bytes(b"X"+raw[1:])
        self.assertFalse(worker.evidence(REG, [sha])["complete"])
        self.assertEqual(worker.evidence(REG, ["0"*64])["reason"], "archive_not_received")
        archive = self.home/"eis223_official_archive_inbox"/REG/(sha+".zip")
        raw = archive.read_bytes()
        archive.write_bytes(b"X"+raw[1:])
        self.assertFalse(worker.evidence(REG, [sha])["metadata_received"])

    def test_unsupported_fs_url_and_prior_guid_keep_completion_incomplete(self):
        self.original()
        variants = [
            '<documentFS><guid>fs-guid</guid><contentUid>opaque</contentUid></documentFS>',
            '<document><fileName>other.pdf</fileName><url>https://zakupki.gov.ru/unsupported?id=1</url></document>',
            '<prevDocument><guid>unknown-prior</guid></prevDocument>',
        ]
        for extra in variants:
            with self.subTest(extra=extra):
                sha = self.archive(extra)
                proof = worker.evidence(REG, [sha])
                self.assertFalse(proof["complete"])
                self.assertGreater(proof["unresolved"], 0)

    def test_metadata_only_job_waits_for_real_card_write(self):
        sha = self.archive(document=False)
        self.enqueue()
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET parent_id='v341-card-facts' WHERE target=?", (REG,))
        self.ns["_v379_import_soap_metadata"] = lambda reg: {"stored": False}
        job = self.claim()
        response = self.report(job, status="no_data", archive_sha256=sha)
        self.assertEqual(response.json()["status"], "queued")
        self.assertFalse(response.json()["metadata_stored"])
        self.make_ready(job["id"])
        self.ns["_v379_import_soap_metadata"] = lambda reg: {"stored": True}
        job = self.claim()
        response = self.report(job, status="no_data", archive_sha256=sha)
        self.assertEqual(response.json()["status"], "done")
        self.assertTrue(response.json()["metadata_stored"])
        self.assertFalse(response.json()["audit_complete"])

    def test_retry_after_alone_does_not_refund_missing_original_attempts(self):
        self.archive()
        self.enqueue()
        for attempt in range(1, 5):
            job = self.claim()
            result = self.report(job, status="partial", retry_after_seconds=1200,
                                 error_code="unsupported_attachment_links").json()
            self.assertEqual(self.row(job["id"])["attempts"], attempt)
            self.assertEqual(result["status"], "queued" if attempt < 4 else "partial")
            self.make_ready(job["id"])

    def test_transient_deferrals_are_bounded_and_refunded_once(self):
        self.enqueue()
        job = self.claim()
        response = self.report(job, status="error", retry_after_seconds=1200, error_code="eis_http_429")
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(self.row(job["id"])["attempts"], 0)
        self.report(job, status="error", retry_after_seconds=1200, error_code="eis_http_429")
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT deferrals FROM eis_server_backoff WHERE job_id=?", (job["id"],)).fetchone()[0], 1)
            db.execute("UPDATE eis_server_backoff SET deferrals=12 WHERE job_id=?", (job["id"],))
        self.make_ready(job["id"])
        job = self.claim()
        self.report(job, status="error", retry_after_seconds=1200, error_code="eis_http_429")
        self.assertEqual(self.row(job["id"])["attempts"], 1)

    def test_long_remote_retry_is_not_shortened_to_one_hour(self):
        self.enqueue()
        job = self.claim()
        now = int(time.time())
        with patch.object(worker.time, 'time', return_value=now):
            response = self.report(job, status="error", retry_after_seconds=86400, error_code="eis_http_429")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row(job["id"])["not_before"], now + 86400)

    def test_discovery_is_bounded_rate_limited_and_not_manual(self):
        self.enqueue()
        body = {"worker_id": self.identity, "reg_numbers": [REG, "32610000001", "32610000001"],
                "source_url": "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?fz223=on"}
        response = self.client.post("/api/eis-worker/discovered", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["created"], ["32610000001"])
        self.assertEqual(response.json()["manual_records_created"], 0)
        replay = self.client.post("/api/eis-worker/discovered", headers=self.headers, json=body)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        limited = self.client.post("/api/eis-worker/discovered", headers=self.headers,
                                   json={**body, "reg_numbers": ["32610000002"]})
        self.assertEqual(limited.status_code, 429)
        self.assertLessEqual(int(limited.headers["Retry-After"]), 30)
        self.assertEqual(limited.headers["X-EIS223-Retry-Scope"], "discovery")
        bad = {**body, "source_url": "https://example.com/epz/order/extendedsearch/results.html?fz223=on"}
        self.assertEqual(self.client.post("/api/eis-worker/discovered", headers=self.headers, json=bad).status_code, 422)
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)

    def discover(self, numbers, page=1):
        return self.client.post("/api/eis-worker/discovered", headers=self.headers, json={
            "worker_id": self.identity, "reg_numbers": numbers,
            "source_url": "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?fz223=on&pageNumber="+str(page)})

    def allow_discovery(self):
        with self.ns["conn"]() as db:
            db.execute("UPDATE eis_server_discovery SET accepted_at=0")

    def test_discovery_accepts_100_and_empty_pages_after_30_seconds(self):
        numbers = [str(32610000000+i) for i in range(100)]
        now = int(time.time())
        with patch.object(worker.time, "time", return_value=now):
            response = self.discover(numbers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["created"]), 100)
        with patch.object(worker.time, "time", return_value=now+30):
            empty = self.discover([], page=2)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["created"], [])
        self.assertEqual(self.discover(numbers+[REG]).status_code, 422)

    def test_discovery_full_queue_is_durable_across_restart_and_parallel_drain(self):
        numbers = ["32610000001", "32610000002", "32610000003"]
        self.enqueue("32610000009")
        with patch.object(worker, "MAX_QUEUE", 9), patch("eis_inn_search.MAX_QUEUE", 9):
            result = self.discover(numbers).json()
            self.assertEqual(result["pending"], numbers)
            self.assertEqual(result["created"], [])
            worker.init(self.ns)  # Reinitialising cannot discard accepted pages.
            self.allow_discovery()
            repeat = self.discover(numbers, page=2).json()
            self.assertEqual(repeat["pending_candidates"], 3)
            with self.ns["conn"]() as db:
                db.execute("UPDATE v301_search_jobs SET status='done'")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                claims = list(pool.map(lambda identity: worker.claim(self.ns, identity, 1), [self.identity, self.other]))
            self.assertEqual(sum(len(jobs) for jobs in claims), 1)
            with self.ns["conn"]() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM eis_server_discovery_candidates WHERE reg_job_id=''").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased','deferred')").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)
        # The eight reserved places remain available for explicit user work.
        with patch.object(queue, "MAX_QUEUE", 9):
            for suffix in range(8):
                queue._enqueue(self.ns, "reg", str(32620000000+suffix), 1, 0, "", True)
            with self.assertRaises(worker.HTTPException) as caught:
                queue._enqueue(self.ns, "reg", "32620000009", 1, 0, "", True)
            self.assertEqual(caught.exception.status_code, 429)

    def test_discovery_saturation_rejects_whole_page_and_recovers_after_claim(self):
        first, second = ["32610000001", "32610000002"], ["32610000003", "32610000004"]
        with patch.object(worker, "MAX_QUEUE", 8), patch("eis_inn_search.MAX_QUEUE", 8), patch.object(worker, "MAX_DISCOVERY_PENDING", 2):
            self.assertEqual(self.discover(first).status_code, 200)
            self.allow_discovery()
            result = self.discover(second, page=2)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.headers["Retry-After"], "60")
            self.assertEqual(result.headers["X-EIS223-Retry-Scope"], "discovery")
            with self.ns["conn"]() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM eis_server_discovery_candidates").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT accepted_at FROM eis_server_discovery").fetchone()[0], 0)
        worker.claim(self.ns, self.identity, 1)
        accepted = self.discover(second, page=2)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["created"], second)

    def test_discovery_daily_dedup_reuses_manual_and_inn_work_and_refreshes_later(self):
        manual = queue._enqueue(self.ns, "reg", REG, 1, 0, "", True)[0]
        self.assertEqual(self.discover([REG]).json()["skipped"], [REG])
        self.allow_discovery()
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (manual["id"],))
        self.assertEqual(self.discover([REG], page=2).json()["skipped"], [REG])
        self.allow_discovery()
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET updated_at='2000-01-01T00:00:00' WHERE id=?", (manual["id"],))
        self.assertEqual(self.discover([REG], page=3).json()["created"], [REG])
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE target=?", (REG,)).fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 1)

    def test_general_and_inn_backlogs_alternate_single_free_slot(self):
        with self.ns["conn"]() as db:
            db.execute("INSERT INTO eis_server_discovery_candidates VALUES(?,?,?,'')", (REG, int(time.time()), "official"))
            db.execute("INSERT INTO eis_inn_candidates VALUES(?,?,?,?,?,?,?,'')",
                       ("a"*24, "7701234567", "32610000001", "a"*24, 1, "official", queue._utc()))
        with patch.object(worker, "MAX_QUEUE", 9), patch("eis_inn_search.MAX_QUEUE", 9):
            first = worker.claim(self.ns, self.identity, 1)[0]
            self.assertEqual(first["reg_number"], REG)
            self.assertEqual(self.row(first["id"])["priority"], 8)
            with self.ns["conn"]() as db:
                db.execute("UPDATE v301_search_jobs SET status='done'")
                db.execute("INSERT INTO eis_server_discovery_candidates VALUES(?,?,?,'')", ("32610000002", int(time.time()), "official"))
            second = worker.claim(self.ns, self.identity, 1)[0]
            self.assertEqual(second["reg_number"], "32610000001")
            self.assertEqual(self.row(second["id"])["priority"], 8)

    def test_pending_recent_jobs_do_not_starve_new_candidates(self):
        old = self.enqueue()["job"]["id"]
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (old,))
            db.execute("INSERT INTO eis_server_discovery_candidates VALUES(?,?,?,'')", (REG, 1, "official"))
            db.execute("INSERT INTO eis_server_discovery_candidates VALUES(?,?,?,'')", ("32610000001", 2, "official"))
        db = queue._db(self.ns)
        try:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(worker.enqueue_discovery_candidates(db, limit=1), [])
            self.assertEqual(worker.enqueue_discovery_candidates(db, limit=1), ["32610000001"])
            db.commit()
        finally:
            db.close()

    def test_legacy_general_work_keeps_fifo_ahead_of_new_automatic_candidates(self):
        old = queue._enqueue(self.ns, "reg", REG, 1, 10, "server-discovery", False)[0]
        other = queue._enqueue(self.ns, "reg", "32610000009", 1, 10, "worker-bootstrap", False)[0]
        with self.ns["conn"]() as db:
            db.execute("UPDATE v301_search_jobs SET created_at='2000-01-01T00:00:00' WHERE id=?", (old["id"],))
            db.execute("INSERT INTO eis_server_discovery_candidates VALUES(?,?,?,'')", ("32610000001", int(time.time()), "official"))
        worker.init(self.ns)
        self.assertEqual(self.row(old["id"])["priority"], 8)
        self.assertEqual(self.row(other["id"])["priority"], 10)
        self.assertEqual(worker.claim(self.ns, self.identity, 1)[0]["id"], old["id"])

    def test_heartbeat_telemetry_is_safe_quiet_and_old_worker_compatible(self):
        data = {"worker_id": self.identity, "version": "1.3.0", "status": "idle",
                "discovery_status": "blocked", "discovery_error": "eis_http_403"}
        with patch.object(worker, "_log_telemetry") as logged:
            for _ in range(2):
                self.assertEqual(self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data).status_code, 200)
            self.assertEqual(logged.call_count, 1)
            data.update(version="1.4.0", search_backoff_seconds=100, queue_backoff_seconds=0,
                        search_interval_seconds=60, discovery_page=2, discovery_pending=True,
                        discovery_status="pending_delivery")
            self.assertEqual(self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data).status_code, 200)
            data["search_backoff_seconds"] = 70
            self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
            self.assertEqual(logged.call_count, 2)  # Countdown alone is not a state change.
            self.assertEqual(worker.telemetry_snapshot(self.ns)["workers"][0]["discovery_status"], "pending_delivery")
            data["search_backoff_seconds"] = 999999
            self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
            self.assertEqual(worker.telemetry_snapshot(self.ns)["workers"][0]["search_backoff_seconds"], 86400)
            self.assertEqual(logged.call_count, 2)
            data.update(discovery_error=KEY, version=KEY, detail=KEY, discovery_status=KEY)
            self.client.post("/api/eis-worker/heartbeat", headers=self.headers, json=data)
            self.assertNotIn(KEY, str(logged.call_args_list))
        snapshot = worker.telemetry_snapshot(self.ns)
        self.assertNotIn(KEY, json.dumps(snapshot))
        self.assertEqual(snapshot["workers"][0]["discovery_error"], "unrecognized_error")

    def test_schema_upgrade_retains_legacy_worker_and_discovery_state(self):
        with self.ns["conn"]() as db:
            db.executescript("""DROP TABLE eis_server_workers; DROP TABLE eis_server_discovery;
              CREATE TABLE eis_server_workers(worker_id TEXT PRIMARY KEY,seen_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT '',detail TEXT NOT NULL DEFAULT '',
                discovery_status TEXT NOT NULL DEFAULT '',discovery_error TEXT NOT NULL DEFAULT '');
              CREATE TABLE eis_server_discovery(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                accepted_at INTEGER NOT NULL DEFAULT 0,source_url TEXT NOT NULL DEFAULT '');""")
            db.execute("INSERT INTO eis_server_workers VALUES(?,?,?,?,?,?)",
                       (self.identity, int(time.time()), "idle", "", "blocked", "eis_http_429"))
            db.execute("INSERT INTO eis_server_discovery VALUES(1,123,'official')")
        worker.init(self.ns)
        worker.init(self.ns)
        self.assertEqual(worker.health(self.ns)["discovery_error"], "eis_http_429")
        self.assertEqual(worker.telemetry_snapshot(self.ns)["workers"][0]["discovery_error"], "eis_http_429")
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT accepted_at,source_url,request_hash,drain_turn FROM eis_server_discovery").fetchone(),
                             (123, "official", "", 0))

    def test_report_telemetry_distinguishes_ack_from_received_bytes_and_reads_old_receipts(self):
        self.enqueue()
        job = self.claim()
        with patch.object(worker, "_log_telemetry") as logged:
            result = self.report(job, status="no_data", error_code="site_http_422")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["download_complete"])
        self.assertEqual(result.json()["requested_status"], "no_data")
        self.assertEqual(logged.call_args.kwargs["originals_received"], 0)
        with self.ns["conn"]() as db:
            legacy = {"status": "queued", "detail": "Получение данных ЕИС не подтверждено: site_http_422",
                      "download_complete": False, "metadata_received": False, "originals_received": 0}
            db.execute("UPDATE eis_server_reports SET result_json=?", (json.dumps(legacy),))
            for suffix in range(25):
                db.execute("INSERT INTO eis_server_reports VALUES(?,?,?,?,?,?)",
                           (str(suffix), "token", self.identity, REG, json.dumps(legacy), int(time.time())+suffix))
        snapshot = worker.telemetry_snapshot(self.ns)
        self.assertEqual(len(snapshot["recent_reports"]), 20)
        self.assertTrue(all(item["error_code"] == "site_http_422" for item in snapshot["recent_reports"]))
        self.assertTrue(all(item["download_complete"] is False for item in snapshot["recent_reports"]))


if __name__ == "__main__":
    unittest.main()
