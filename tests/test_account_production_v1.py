"""Contract tests for opt-in production login and legacy-route separation."""
import asyncio
import os
import unittest
from unittest.mock import patch

from scripts.account_production_v1 import ProductionAccountGate, machine_path, verified_machine


async def fake_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def request(path, account=None, method="GET", token=None):
    messages = []
    headers = []
    if token:
        headers.append((b"x-eis223-upload-token", token.encode()))
    scope = {"type": "http", "path": path, "method": method, "headers": headers}
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    with patch.dict(os.environ, {"EIS223_AUTH_ENFORCE": "true",
                                "EIS223_UPLOAD_TOKEN": "valid-token-for-bridge-worker-12345"}):
        with patch("scripts.account_production_v1.user_session", return_value=account):
            asyncio.run(ProductionAccountGate(fake_app)(scope, receive, send))
    return messages[0]["status"], dict(messages[0].get("headers", []))


class ProductionLoginTests(unittest.TestCase):
    def test_public_pages_and_health(self):
        for path in ("/health", "/healthz", "/account/login",
                     "/account/register", "/auth/register", "/eis223/collector.js"):
            self.assertEqual(request(path)[0], 200)

    def test_anonymous_dashboard_redirects_to_login(self):
        code, headers = request("/")
        self.assertEqual(code, 303)
        self.assertEqual(headers[b"location"], b"/account/login")

    def test_anonymous_private_api_is_denied(self):
        self.assertEqual(request("/me/procurements")[0], 401)

    def test_regular_user_cannot_read_global_eis_data(self):
        self.assertEqual(request("/api/purchases", account=("id", "user", "approved"))[0], 403)
        code, headers = request("/", account=("id", "user", "approved"))
        self.assertEqual(code, 303)
        self.assertEqual(headers[b"location"], b"/account/home")

    def test_regular_user_gets_only_private_routes(self):
        self.assertEqual(request("/me/procurements", account=("id", "user", "approved"))[0], 200)
        self.assertEqual(request("/account/home", account=("id", "user", "approved"))[0], 200)
        self.assertEqual(request("/admin/accounts/pending", account=("id", "user", "approved"))[0], 403)

    def test_admin_can_access_existing_app(self):
        code, headers = request("/api/purchases", account=("id", "admin", "approved"))
        self.assertEqual(code, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")

    def test_eis_upload_token_is_route_limited(self):
        token = "valid-token-for-bridge-worker-12345"
        self.assertTrue(machine_path("/api/v294/eis223/archive"))
        self.assertEqual(request("/api/v294/eis223/archive", method="POST", token=token)[0], 200)
        self.assertEqual(request("/api/purchases", token=token)[0], 401)
        self.assertEqual(request("/api/v294/eis223/archive", method="POST", token="bad")[0], 401)

    def test_machine_preflight_is_allowed_for_existing_cors_guard(self):
        self.assertEqual(request("/api/v294/eis223/archive", method="OPTIONS")[0], 200)


if __name__ == "__main__":
    unittest.main()
