"""Contract tests for isolated personal thematic worker primitives."""
import unittest
from scripts.account_thematic_worker_v1 import claim_next, complete_job, requeue_stale


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.row


class FakeDB:
    def __init__(self, row):
        self.row = row
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        return FakeCursor(self.row)


class ThematicWorkerTests(unittest.TestCase):
    def test_claim_is_atomic_and_skips_locked(self):
        db = FakeDB(("owner", "personal-queue", "32615969056", 1))
        result = claim_next(db)
        self.assertEqual(result[3], 1)
        self.assertIn("SKIP LOCKED", db.sql)
        self.assertIn("u.status='approved'", db.sql)
        self.assertIn("RETURNING j.user_id,j.batch_id,j.reg_number,j.attempts", db.sql)

    def test_empty_queue(self):
        self.assertIsNone(claim_next(FakeDB(None)))

    def test_completion_fences_attempt_and_owner(self):
        db = FakeDB(("completed",))
        self.assertTrue(complete_job(db, "owner", "batch", "32615969056", 2, True))
        self.assertEqual(db.params, ("completed", "owner", "batch", "32615969056", 2))
        self.assertIn("attempts=%s AND status='running'", db.sql)

    def test_stale_completion_is_rejected(self):
        self.assertFalse(complete_job(FakeDB(None), "owner", "batch", "32615969056", 1, False))

    def test_stale_jobs_requeued_with_retry_limit(self):
        db = FakeDB([("owner", "batch", "32615969056", "queued")])
        rows = requeue_stale(db, max_attempts=3, stale_minutes=30)
        self.assertEqual(rows[0][3], "queued")
        self.assertEqual(db.params, (3, 3, 30))
        self.assertIn("attempts >= %s", db.sql)
        self.assertIn("j.status='running'", db.sql)

    def test_invalid_stale_policy(self):
        with self.assertRaises(ValueError):
            requeue_stale(FakeDB([]), stale_minutes=0)

    def test_invalid_attempt(self):
        with self.assertRaises(ValueError):
            complete_job(FakeDB(None), "owner", "batch", "32615969056", 0, True)


if __name__ == "__main__":
    unittest.main()
