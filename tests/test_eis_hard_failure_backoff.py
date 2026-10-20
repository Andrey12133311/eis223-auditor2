"""Failed intake retries yield capacity without deleting or completing jobs."""
import time
import unittest
from unittest.mock import patch
import eis_server_worker as worker
import test_eis_server_worker as fixtures


class HardFailureBackoff(unittest.TestCase):
    setUp = fixtures.ServerWorkerTests.setUp
    review = fixtures.ServerWorkerTests.review
    enqueue = fixtures.ServerWorkerTests.enqueue
    claim = fixtures.ServerWorkerTests.claim
    report = fixtures.ServerWorkerTests.report
    row = fixtures.ServerWorkerTests.row
    make_ready = fixtures.ServerWorkerTests.make_ready
    archive = fixtures.ServerWorkerTests.archive
    original = fixtures.ServerWorkerTests.original

    def test_repeated_invalid_archive_has_bounded_exponential_delay_without_refund(self):
        self.enqueue()
        for attempt in range(1, 5):
            job = self.claim()
            stamp = int(time.time())
            with patch.object(worker.time, "time", return_value=stamp):
                result = self.report(job, status="error", error_code="site_http_422", retry_after_seconds=300)
            self.assertEqual(result.status_code, 200)
            row = self.row(job["id"])
            self.assertEqual(row["attempts"], attempt)
            self.assertFalse(result.json()["download_complete"])
            self.assertFalse(result.json()["audit_complete"])
            self.assertEqual(result.json()["originals_received"], 0)
            self.assertEqual(row["status"], "queued" if attempt < 4 else "partial")
            self.assertEqual(row["not_before"], stamp + 900 * 2 ** (attempt - 1) if attempt < 4 else 0)
            self.make_ready(job["id"])

    def test_oversized_response_delay_preserves_long_retry_after(self):
        self.enqueue(); job = self.claim(); stamp = int(time.time())
        with patch.object(worker.time, "time", return_value=stamp):
            result = self.report(job, status="partial", error_code="response_size_limit", retry_after_seconds=86400)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.row(job["id"])["not_before"], stamp + 86400)

    def test_other_registry_is_claimed_while_failed_one_is_deferred(self):
        self.enqueue(); job = self.claim()
        self.report(job, status="error", error_code="site_http_422", retry_after_seconds=60)
        other = "32616478351"
        self.enqueue(other)
        self.assertEqual(self.claim()["reg_number"], other)
        self.assertEqual(self.row(job["id"])["status"], "queued")

    def test_report_replay_cannot_extend_or_duplicate_the_deferral(self):
        self.enqueue(); job = self.claim()
        self.report(job, status="error", error_code="site_http_422", retry_after_seconds=300)
        before = self.row(job["id"])
        repeated = self.report(job, status="error", error_code="site_http_422", retry_after_seconds=86400)
        self.assertTrue(repeated.json()["replayed"])
        self.assertEqual(self.row(job["id"]), before)

    def test_real_progress_uses_normal_retry_and_keeps_actual_original(self):
        sha = self.archive('<documentFS><guid>later</guid><contentUid>opaque</contentUid></documentFS>')
        path = self.original(); original = path.read_bytes()
        self.enqueue(); job = self.claim(); stamp = int(time.time())
        with patch.object(worker.time, "time", return_value=stamp):
            response = self.report(job, status="partial", error_code="response_size_limit",
                                   retry_after_seconds=300, archive_sha256=sha)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row(job["id"])["attempts"], 0)
        self.assertEqual(self.row(job["id"])["not_before"], stamp + 300)
        self.assertEqual(response.json()["originals_received"], 1)
        self.assertFalse(response.json()["download_complete"])
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
