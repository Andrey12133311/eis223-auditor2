import asyncio
import unittest
from scripts.account_asgi_gate_v1 import AccountGateMiddleware


class FakeDB:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class AccountAsgiGateTests(unittest.TestCase):
    def test_anonymous_private_api_denied(self):
        called = []
        messages = []

        async def app(scope, receive, send):
            called.append(scope["path"])

        async def send(message):
            messages.append(message)

        gate = AccountGateMiddleware(app, lambda: FakeDB())
        scope = {"type": "http", "path": "/api/thematic/history", "headers": []}
        from unittest.mock import patch
        with patch("scripts.account_asgi_gate_v1.enforce_auth_enabled", return_value=True):
            asyncio.run(gate(scope, None, send))
        self.assertEqual(messages[0]["status"], 401)
        self.assertEqual(called, [])

    def test_public_registration_allowed(self):
        called = []

        async def app(scope, receive, send):
            called.append(scope["path"])

        gate = AccountGateMiddleware(app, lambda: FakeDB())
        scope = {"type": "http", "path": "/auth/register", "headers": []}
        from unittest.mock import patch
        with patch("scripts.account_asgi_gate_v1.enforce_auth_enabled", return_value=True):
            asyncio.run(gate(scope, None, None))
        self.assertEqual(called, ["/auth/register"])

    def test_gate_disabled_keeps_legacy_behavior(self):
        called = []

        async def app(scope, receive, send):
            called.append(scope["path"])

        gate = AccountGateMiddleware(app, lambda: FakeDB())
        scope = {"type": "http", "path": "/api/thematic/history", "headers": []}
        from unittest.mock import patch
        with patch("scripts.account_asgi_gate_v1.enforce_auth_enabled", return_value=False):
            asyncio.run(gate(scope, None, None))
        self.assertEqual(called, ["/api/thematic/history"])


if __name__ == "__main__":
    unittest.main()
