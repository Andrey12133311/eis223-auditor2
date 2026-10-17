import unittest
from datetime import datetime, timedelta, timezone
from scripts.account_security_v1 import (
    new_session_token, digest_session_token, session_token_matches,
    normalize_email, can_access_session, owner_access
)


class AccountSecurityTests(unittest.TestCase):
    def test_token_digest(self):
        token = new_session_token()
        self.assertNotEqual(token, digest_session_token(token))
        self.assertTrue(session_token_matches(token, digest_session_token(token)))
        self.assertFalse(session_token_matches(new_session_token(), digest_session_token(token)))

    def test_normalize_email(self):
        self.assertEqual(normalize_email("  User@Example.COM "), "user@example.com")
        with self.assertRaises(ValueError):
            normalize_email("invalid")

    def test_pending_and_revoked_sessions_rejected(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertFalse(can_access_session("pending", future))
        self.assertFalse(can_access_session("approved", future, revoked_at=datetime.now(timezone.utc)))
        self.assertTrue(can_access_session("approved", future))

    def test_ownership(self):
        self.assertTrue(owner_access("a", "a", "approved"))
        self.assertFalse(owner_access("a", "b", "approved"))
        self.assertFalse(owner_access("a", "a", "pending"))


if __name__ == "__main__":
    unittest.main()
