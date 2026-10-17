import unittest
from scripts.account_owner_queries_v1 import owned_memo_key, owns_batch


class FakeDB:
    def __init__(self, owner):
        self.owner = owner

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return self

    def fetchone(self):
        if self.params[-1] != self.owner:
            return None
        return ("private/owned.odt",) if "storage_key" in self.sql else (1,)


class TenantAccessTests(unittest.TestCase):
    def test_memo_requires_correct_owner(self):
        db = FakeDB("alice")
        self.assertEqual(owned_memo_key(db, "memo1", "alice"), "private/owned.odt")
        self.assertIsNone(owned_memo_key(db, "memo1", "bob"))

    def test_batch_requires_correct_owner(self):
        db = FakeDB("alice")
        self.assertTrue(owns_batch(db, "batch1", "alice"))
        self.assertFalse(owns_batch(db, "batch1", "bob"))


if __name__ == "__main__":
    unittest.main()
