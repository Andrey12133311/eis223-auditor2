import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
from scripts.account_email_v1 import create_challenge, verify_challenge


class DB:
    def __init__(self):
        self.queries = []
        self.user = uuid4()
        self.token_digest = None
        self.consumed = False

    def execute(self, sql, args):
        self.queries.append((sql, args))
        if "INSERT INTO account_email_challenges" in sql:
            self.token_digest = args[2]
        elif "UPDATE account_email_challenges" in sql:
            self.found = args[0] == self.token_digest and not self.consumed
            if self.found:
                self.consumed = True
        return self

    def fetchone(self):
        return (self.user,) if self.found else None


class EmailVerificationTests(unittest.TestCase):
    def test_token_single_use(self):
        db = DB()
        token = create_challenge(db, db.user)
        self.assertTrue(verify_challenge(db, token))
        self.assertFalse(verify_challenge(db, token))

    def test_invalid_token_rejected(self):
        self.assertFalse(verify_challenge(DB(), "short"))

    def test_no_plaintext_token_in_database(self):
        db = DB()
        token = create_challenge(db, db.user)
        self.assertNotEqual(db.token_digest, token)


if __name__ == "__main__":
    unittest.main()
