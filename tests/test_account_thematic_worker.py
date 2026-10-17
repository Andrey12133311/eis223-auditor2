"""Contract tests for isolated personal thematic worker primitives."""
import unittest
from scripts.account_thematic_worker_v1 import claim_next, complete_job, requeue_stale, process_one, save_findings


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


    def test_process_one_requires_trusted_callback(self):
        with self.assertRaises(TypeError):
            process_one(lambda: None, None)

    def test_process_one_runs_and_records_completion(self):
        class Transaction:
            def __init__(self, row):
                self.row = row
                self.statements = []
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def execute(self, sql, params=None):
                self.statements.append((sql, params))
                return FakeCursor(self.row)
        transactions = iter([
            Transaction(("owner", "personal-batch", "32615969056", 1)),
            Transaction(("completed",)),
        ])
        seen = []
        result = process_one(lambda: next(transactions),
                             lambda *args: seen.append(args) or True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, [("owner", "personal-batch", "32615969056")])

    def test_process_one_records_callback_failure(self):
        class Transaction:
            def __init__(self, row):
                self.row = row
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def execute(self, sql, params=None):
                if params is not None:
                    self.params = params
                return FakeCursor(self.row)
        first = Transaction(("owner", "batch", "32615969056", 1))
        second = Transaction(("failed",))
        transactions = iter([first, second])
        def broken(*args):
            raise RuntimeError("EIS unavailable")
        result = process_one(lambda: next(transactions), broken)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(second.params[0], "failed")


    def test_findings_require_evidence_and_current_attempt(self):
        class DB:
            def __init__(self):
                self.statements = []
            def execute(self, sql, params=None):
                self.statements.append((sql, params))
                return FakeCursor((1,))
        db = DB()
        findings = [{"finding_key": "deadline-1", "legal_basis": "223-FZ art 4",
                     "description": "Documented discrepancy",
                     "evidence_source": "EIS document identifier"}]
        self.assertTrue(save_findings(db, "owner", "batch", "32615969056", 1, findings))
        self.assertIn("FOR UPDATE", db.statements[0][0])
        self.assertIn("ON CONFLICT", db.statements[1][0])
        with self.assertRaises(ValueError):
            save_findings(db, "owner", "batch", "32615969056", 1,
                          [{"finding_key": "missing", "legal_basis": "223-FZ",
                            "description": "No evidence", "evidence_source": ""}])

    def test_stale_worker_cannot_save_findings(self):
        db = FakeDB(None)
        finding = [{"finding_key": "k", "legal_basis": "law",
                    "description": "fact", "evidence_source": "document"}]
        self.assertFalse(save_findings(db, "owner", "batch", "32615969056", 2, finding))

    def test_invalid_attempt(self):
        with self.assertRaises(ValueError):
            complete_job(FakeDB(None), "owner", "batch", "32615969056", 0, True)


if __name__ == "__main__":
    unittest.main()
