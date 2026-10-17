"""Behavioral checks for administrator approval and session revocation."""
import unittest
from uuid import uuid4
from scripts.account_repository_v1 import approve_user, reject_user, block_user


class FakeDB:
    def __init__(self, updated=True):
        self.updated = updated
        self.calls = []
    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self
    def fetchone(self):
        return (uuid4(),) if self.updated else None


class AdminTransitions(unittest.TestCase):
    def test_approval_requires_pending_and_records_audit(self):
        db = FakeDB()
        admin, user = uuid4(), uuid4()
        self.assertTrue(approve_user(db, admin, user))
        self.assertIn("status='pending'", db.calls[0][0])
        self.assertEqual(db.calls[0][1], (admin, user))
        self.assertIn("'approve'", db.calls[1][0])
        self.assertEqual(db.calls[1][1], (admin, user))

    def test_repeated_approval_does_not_log_success(self):
        db = FakeDB(updated=False)
        self.assertFalse(approve_user(db, uuid4(), uuid4()))
        self.assertEqual(len(db.calls), 1)

    def test_rejection_only_applies_to_pending(self):
        db = FakeDB()
        self.assertTrue(reject_user(db, uuid4(), uuid4()))
        self.assertIn("status='pending'", db.calls[0][0])
        self.assertIn("'reject'", db.calls[1][0])

    def test_block_revokes_existing_sessions(self):
        db = FakeDB()
        user = uuid4()
        self.assertTrue(block_user(db, uuid4(), user))
        self.assertIn("role='user'", db.calls[0][0])
        self.assertIn("UPDATE account_sessions", db.calls[1][0])
        self.assertEqual(db.calls[1][1], (user,))
        self.assertIn("'block'", db.calls[2][0])

    def test_nonexistent_user_block_has_no_side_effects(self):
        db = FakeDB(updated=False)
        self.assertFalse(block_user(db, uuid4(), uuid4()))
        self.assertEqual(len(db.calls), 1)


if __name__ == '__main__':
    unittest.main()
