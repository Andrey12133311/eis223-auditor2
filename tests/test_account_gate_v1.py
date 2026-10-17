import unittest
from scripts.account_gate_v1 import (
    is_public_path, should_deny_anonymous, origin_matches
)


class AccountGateTests(unittest.TestCase):
    def test_registration_pages_are_public(self):
        self.assertTrue(is_public_path("/account/register"))
        self.assertTrue(is_public_path("/auth/login"))

    def test_sensitive_routes_are_private(self):
        for path in (
            "/api/thematic/history", "/api/v301/search",
            "/thematic/card/abc/32616412317", "/api/documents/download",
            "/account/admin", "/me/procurements",
        ):
            self.assertFalse(is_public_path(path))
            self.assertTrue(should_deny_anonymous(path, False, enabled=True))
            self.assertFalse(should_deny_anonymous(path, True, enabled=True))

    def test_gate_off_does_not_change_legacy_behavior(self):
        self.assertFalse(should_deny_anonymous("/api/thematic/history", False, enabled=False))

    def test_origin_must_match_https(self):
        self.assertTrue(origin_matches("https://example.org", "https://example.org"))
        self.assertFalse(origin_matches("http://example.org", "https://example.org"))
        self.assertFalse(origin_matches("https://evil.example.org", "https://example.org"))


if __name__ == "__main__":
    unittest.main()
