"""Session lifecycle tests without external PostgreSQL infrastructure."""
import unittest
from unittest.mock import patch
from uuid import uuid4
from scripts.account_sessions_v1 import create_session, resolve_session, revoke_session


class FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


class SessionDB:
    def __init__(self):
        self.tokens = {}
        self.users = {}

    def execute(self, sql, params):
        if "INSERT INTO account_sessions" in sql:
            self.tokens[params[2]] = [params[1], params[3], False]
            return FakeResult(None)
        if "SELECT u.id,u.role,u.status" in sql:
            record = self.tokens.get(params[0])
            if not record or record[2]:
                return FakeResult(None)
            uid = record[0]
            user = self.users.get(uid)
            return FakeResult((uid, user[0], user[1]) if user and user[1] == "approved" else None)
        if "UPDATE account_sessions SET revoked_at" in sql:
            record = self.tokens.get(params[0])
            if record:
                record[2] = True
            return FakeResult(None)
        raise AssertionError(sql)


class SessionLifecycleTests(unittest.TestCase):
    def test_approved_session_and_revocation(self):
        db = SessionDB()
        uid = uuid4()
        db.users[uid] = ("user", "approved")
        token, _ = create_session(db, uid)
        self.assertEqual(resolve_session(db, token)[0], uid)
        revoke_session(db, token)
        self.assertIsNone(resolve_session(db, token))

    def test_pending_user_cannot_resolve_session(self):
        db = SessionDB()
        uid = uuid4()
        db.users[uid] = ("user", "pending")
        token, _ = create_session(db, uid)
        self.assertIsNone(resolve_session(db, token))


if __name__ == "__main__":
    unittest.main()
