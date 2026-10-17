import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from scripts.account_throttle_v1 import bucket_key, consume


class FakeDB:
    def __init__(self):
        self.attempts = 0
        self.started = None

    def execute(self, sql, params):
        if sql.startswith("SELECT pg_advisory"):
            return self
        if sql.startswith("SELECT attempts"):
            self.fetched = (self.attempts, self.started) if self.started else None
        elif sql.startswith("UPDATE account_throttle"):
            self.attempts = params[0]
        elif sql.startswith("INSERT INTO account_throttle"):
            self.attempts = 1
            self.started = params[1]
        return self

    def fetchone(self):
        return self.fetched


class ThrottleTests(unittest.TestCase):
    def test_bucket_is_keyed_and_private(self):
        with patch.dict(os.environ, {"EIS223_RATE_LIMIT_SECRET": "x" * 40}):
            self.assertEqual(bucket_key("login", "a"), bucket_key("login", "a"))
            self.assertNotEqual(bucket_key("login", "a"), bucket_key("login", "b"))

    def test_attempts_are_limited(self):
        with patch.dict(os.environ, {"EIS223_RATE_LIMIT_SECRET": "x" * 40}):
            db = FakeDB()
            self.assertTrue(consume(db, "login", "a", 2, 900))
            self.assertTrue(consume(db, "login", "a", 2, 900))
            self.assertFalse(consume(db, "login", "a", 2, 900))


if __name__ == "__main__":
    unittest.main()
