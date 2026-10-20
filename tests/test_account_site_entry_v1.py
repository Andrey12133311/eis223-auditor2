"""Integration checks for a single site session from entry form to logout."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from scripts.account_api_v1 import router as auth
from scripts.account_page_routes_v1 import router as pages
from scripts.account_production_v1 import ProductionAccountGate
from scripts.account_sessions_v1 import create_session
from scripts.account_session_policy_v1 import SESSION_COOKIE
from scripts.account_security_v1 import digest_session_token
from test_account_session_lifecycle_v1 import FakeResult, SessionDB


class IdentityDB(SessionDB):
    def __init__(self):
        super().__init__()
        self.uid = uuid4()
        self.users[self.uid] = ("user", "approved")

    def execute(self, sql, params):
        if "SELECT id,password_hash,status" in sql:
            return FakeResult((self.uid, "encoded-password", self.users[self.uid][1]))
        if "SELECT u.id,u.role,u.status" in sql:
            record = self.tokens.get(params[0])
            if record and record[1] <= datetime.now(timezone.utc):
                return FakeResult(None)
        return super().execute(sql, params)


class SiteEntryTests(unittest.TestCase):
    def setUp(self):
        self.db = IdentityDB()
        self.downstream_calls = []

        @contextmanager
        def connection():
            yield self.db

        app = FastAPI()
        app.include_router(auth)
        app.include_router(pages)

        @app.get("/", response_class=HTMLResponse)
        def shared_home():
            self.downstream_calls.append("/")
            return '<html><body><main>Saved procurement evidence</main></body></html>'

        @app.get("/api/dashboard")
        def dashboard():
            self.downstream_calls.append("/api/dashboard")
            return {"procurements": 1253}

        app.add_middleware(ProductionAccountGate, connection=connection)
        self.env = patch.dict(os.environ, {
            "EIS223_AUTH_ENFORCE": "true", "EIS223_PUBLIC_ORIGIN": "https://site.example",
        })
        self.env.start()
        self.connect = patch("scripts.account_api_v1.connect", connection)
        self.connect.start()
        self.throttle = patch("scripts.account_api_v1.request_allowed", return_value=True)
        self.throttle.start()
        self.password = patch("scripts.account_api_v1.verify_password", return_value=True)
        self.password.start()
        self.client = TestClient(app, base_url="https://site.example")
        self.addCleanup(self.env.stop)
        self.addCleanup(self.connect.stop)
        self.addCleanup(self.throttle.stop)
        self.addCleanup(self.password.stop)
        self.addCleanup(self.client.close)

    def login(self):
        return self.client.post("/auth/login", headers={"Origin": "https://site.example"},
                                json={"email": "user@example.com", "password": "test-password-1234"})

    def test_guest_sees_only_forms_and_cannot_fetch_audit_data(self):
        response = self.client.get("/")
        self.assertEqual(response.url.path, "/account/login")
        self.assertIn('role="dialog"', response.text)
        self.assertIn('class="auth"', response.text)
        self.assertIn("select('login',false)", response.text)
        self.assertNotIn("Saved procurement evidence", response.text)
        self.assertNotIn('data-eis223-account-nav', response.text)
        registration = self.client.get("/account/register")
        self.assertEqual(registration.status_code, 200)
        self.assertIn('id="registration-form"', registration.text)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        self.assertEqual(self.client.get("/account/home", follow_redirects=False).status_code, 303)
        self.assertEqual(self.downstream_calls, [])

    def test_one_cookie_opens_site_and_cabinet_without_another_login(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookies = response.headers.get_list("set-cookie")
        cookie = next(value for value in cookies if value.startswith(SESSION_COOKIE + "="))
        self.assertNotIn("max-age", cookie.lower())
        self.assertNotIn("expires=", cookie.lower())
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("Path=/", cookie)
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Saved procurement evidence", home.text)
        self.assertIn("Личный кабинет", home.text)
        self.assertIn('data-eis223-account-nav="1"', home.text)
        self.assertEqual(home.headers["cache-control"], "no-store")
        self.assertEqual(int(home.headers["content-length"]), len(home.content))
        for path in ("/account/home", "/account/thematic", "/account/memos", "/auth/me", "/api/dashboard"):
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 200, path)
        for path in ("/account/login", "/account/register"):
            entry = self.client.get(path, follow_redirects=False)
            self.assertEqual(entry.status_code, 200, path)
            self.assertNotIn("location", entry.headers)
            self.assertIn('role="dialog"', entry.text)
            self.assertNotIn("Saved procurement evidence", entry.text)
        # Visiting an explicit entry form does not revoke the working session.
        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)

    def test_logout_revokes_cookie_and_old_session(self):
        self.login()
        old_token = self.client.cookies.get(SESSION_COOKIE)
        self.assertIsInstance(old_token, str)
        self.assertGreaterEqual(len(old_token), 40)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)
        self.assertEqual(self.client.post("/auth/logout", headers={"Origin": "https://site.example"}).status_code, 200)
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)
        self.assertIsNone(self.client.cookies.get(SESSION_COOKIE))
        self.assertTrue(self.db.tokens[digest_session_token(old_token)][2])
        self.assertEqual(self.client.get("/api/dashboard", headers={"Cookie": SESSION_COOKIE + "=" + old_token}).status_code, 401)

    def test_expired_and_blocked_sessions_cannot_open_program(self):
        for status, hours in (("approved", -1), ("blocked", 1), ("pending", 1)):
            self.db.users[self.db.uid] = ("user", status)
            token, _ = create_session(self.db, self.db.uid, hours=hours)
            response = self.client.get("/", headers={"Cookie": SESSION_COOKIE + "=" + token}, follow_redirects=False)
            self.assertEqual(response.status_code, 303, (status, hours))

    def test_legacy_persistent_cookie_does_not_authenticate(self):
        token, _ = create_session(self.db, self.db.uid)
        self.assertEqual(self.client.get("/api/dashboard", headers={
            "Cookie": "eis223_session=" + token}).status_code, 401)
        self.assertEqual(self.client.get("/api/dashboard", headers={
            "Cookie": SESSION_COOKIE + "=" + token}).status_code, 200)

    def test_identity_outage_never_exposes_dashboard(self):
        with patch("scripts.account_production_v1.user_session", side_effect=RuntimeError("offline")):
            self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 503)
            self.assertEqual(self.client.get("/api/dashboard").status_code, 503)
            self.assertEqual(self.client.get("/account/login").status_code, 200)
        self.assertEqual(self.downstream_calls, [])

    def test_streamed_html_gets_single_navigation_and_correct_length(self):
        async def streamed(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/html"), (b"content-length", b"1")]})
            for chunk, more in ((b'<html><bo', True), (b'dy><main>Shared</main>', True), (b'</body></html>', False)):
                await send({"type": "http.response.body", "body": chunk, "more_body": more})
        messages = []
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            messages.append(message)
        with patch("scripts.account_production_v1.user_session", return_value=("id", "user", "approved")):
            asyncio.run(ProductionAccountGate(streamed)({"type": "http", "path": "/", "method": "GET", "headers": []}, receive, send))
        result = messages[-1]["body"]
        self.assertEqual(result.count(b'data-eis223-account-nav="1"'), 1)
        self.assertIn(b'/me/summary', result)
        self.assertEqual(int(dict(messages[0]["headers"])[b"content-length"]), len(result))


if __name__ == "__main__":
    unittest.main()
