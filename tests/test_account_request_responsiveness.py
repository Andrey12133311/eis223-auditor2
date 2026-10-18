"""Slow identity/ownership I/O must not stop unrelated ASGI requests."""
import asyncio
from contextlib import nullcontext
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import account_api_v1 as accounts
from scripts import account_production_v1 as production
from scripts.account_session_policy_v1 import SESSION_COOKIE
import v342_thematic_queue as thematic


class ResponsiveRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EIS223_AUTH_ENFORCE": "true",
            "EIS223_ACCOUNTS_MOUNT": "true",
            "EIS223_PUBLIC_ORIGIN": "https://app.test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.account = ("owner-a", "user", "approved")
        self.app = FastAPI()

        @self.app.get("/health")
        async def health():
            return {"ok": True}

        @self.app.get("/me/identity")
        async def identity(request: Request):
            first = accounts.current_account(request)
            self.assertEqual(first, accounts.current_account(request))
            return {"owner": first[0], "role": first[1]}

        @self.app.get("/me/administrator")
        async def administrator(request: Request):
            return {"owner": accounts.current_account(request, admin=True)[0]}

        self.app.add_middleware(production.ProductionAccountGate,
                                connection=lambda: nullcontext(object()))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
            base_url="https://app.test", cookies={SESSION_COOKIE: "s" * 48})
        self.addAsyncCleanup(self.client.aclose)

    async def assert_health_while_waiting(self, request, entered, release):
        pending = asyncio.create_task(request)
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
            self.assertTrue(entered.is_set(), "Slow operation was not reached")
            self.assertFalse(pending.done(), "Slow operation blocked the event loop")
            health = await asyncio.wait_for(self.client.get("/health"), .8)
            self.assertEqual(health.status_code, 200)
            self.assertFalse(pending.done(), "Health waited for the slow operation")
        finally:
            release.set()
        return await asyncio.wait_for(pending, 3)

    async def test_slow_session_lookup_does_not_block_health(self):
        entered, release = threading.Event(), threading.Event()

        def slow_lookup(*_):
            entered.set()
            release.wait(3)
            return self.account

        with patch.object(production, "resolve_session", side_effect=slow_lookup), \
             patch.object(accounts, "connect", side_effect=AssertionError("Repeated identity lookup")):
            result = await self.assert_health_while_waiting(
                self.client.get("/me/identity"), entered, release)
        self.assertEqual(result.json(), {"owner": "owner-a", "role": "user"})

    async def test_identity_reused_only_in_same_request_and_revocation_rechecked(self):
        with patch.object(production, "resolve_session", side_effect=lambda *_: self.account) as lookup, \
             patch.object(accounts, "connect", side_effect=AssertionError("Repeated identity lookup")):
            self.assertEqual((await self.client.get("/me/identity")).status_code, 200)
            self.assertEqual(lookup.call_count, 1)
            self.account = ("owner-a", "admin", "approved")
            self.assertEqual((await self.client.get("/me/administrator")).status_code, 200)
            self.account = ("owner-a", "user", "approved")
            self.assertEqual((await self.client.get("/me/administrator")).status_code, 403)
            self.account = None
            self.assertEqual((await self.client.get("/me/identity")).status_code, 401)
            self.assertEqual(lookup.call_count, 4)

    async def test_missing_cookie_and_database_failure_fail_closed(self):
        self.client.cookies.clear()
        with patch.object(production, "resolve_session") as lookup:
            self.assertEqual((await self.client.get("/me/identity", headers={
                "x-eis223-account-id": "owner-a", "x-eis223-account-role": "admin"})).status_code, 401)
            lookup.assert_not_called()
        self.client.cookies.set(SESSION_COOKIE, "s" * 48)
        with patch.object(production, "resolve_session", side_effect=RuntimeError("DB offline")):
            self.assertEqual((await self.client.get("/me/identity")).status_code, 503)
            self.assertEqual((await self.client.get("/health")).status_code, 200)

    def test_untrusted_scope_identity_cannot_replace_database_authorization(self):
        request = Request({"type": "http", "headers": [],
            "eis223_account_id": "owner-a", "eis223_account_role": "admin",
            "_eis223_verified_account": (object(), ("owner-a", "admin", "approved"))})
        with patch.object(accounts, "connect", return_value=nullcontext(object())), \
             patch.object(accounts, "resolve_session", return_value=None) as lookup:
            with self.assertRaises(HTTPException) as result:
                accounts.current_account(request, admin=True)
            self.assertEqual(result.exception.status_code, 401)
            lookup.assert_called_once()

    def install_thematic(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = str(Path(folder.name) / "thematic.db")
        ns = {"app": self.app, "conn": lambda: sqlite3.connect(path)}
        thematic.install(ns)
        return ns

    async def test_slow_thematic_owner_lookup_does_not_block_health(self):
        self.install_thematic()
        entered, release = threading.Event(), threading.Event()

        def owned_ids(request):
            self.assertEqual(request.scope["eis223_account_id"], "owner-a")
            entered.set()
            release.wait(3)
            return ["a" * 24]

        with patch.object(production, "resolve_session", return_value=self.account), \
             patch.object(thematic.ownership, "linked_ids", side_effect=owned_ids), \
             patch.object(thematic, "thematic_history_records", return_value={"batches": []}) as read:
            response = await self.assert_health_while_waiting(
                self.client.get("/api/thematic/history"), entered, release)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(read.call_args.args[1], ["a" * 24])

    async def test_thematic_start_keeps_owner_link_and_sqlite_off_event_loop(self):
        ns = self.install_thematic()
        entered, release = threading.Event(), threading.Event()
        linked = []

        def link(request, batch):
            linked.append((request.scope["eis223_account_id"], batch))
            entered.set()
            release.wait(3)

        with patch.object(production, "resolve_session", return_value=self.account), \
             patch.object(thematic.ownership, "link_started", side_effect=link):
            response = await self.assert_health_while_waiting(self.client.post(
                "/api/thematic/start", headers={"Origin": "https://app.test"},
                json={"categories": ["docs"], "legal_forms": ["ООО"]}), entered, release)
        self.assertEqual(response.status_code, 200)
        batch = response.json()["id"]
        self.assertEqual(linked, [("owner-a", batch)])
        db = ns["conn"]()
        try:
            self.assertEqual(db.execute("SELECT status FROM thematic_batches WHERE id=?",
                                       (batch,)).fetchone()[0], "active")
        finally:
            db.close()

    async def test_themed_status_still_checks_owner_before_reading_evidence(self):
        self.install_thematic()

        def owned(request, batch):
            if request.scope["eis223_account_id"] != "owner-a" or batch != "a" * 24:
                raise HTTPException(404, "Not owned")

        with patch.object(production, "resolve_session", side_effect=lambda *_: self.account), \
             patch.object(thematic.ownership, "require_owned", side_effect=owned), \
             patch.object(thematic, "overview", return_value={"id": "a" * 24}) as read:
            self.assertEqual((await self.client.get("/api/thematic/" + "a" * 24)).status_code, 200)
            self.account = ("owner-b", "user", "approved")
            self.assertEqual((await self.client.get("/api/thematic/" + "a" * 24)).status_code, 404)
            self.assertEqual(read.call_count, 1)

    async def test_concurrent_thematic_starts_preserve_campaign_limit(self):
        ns = self.install_thematic()
        with patch.object(production, "resolve_session", return_value=self.account), \
             patch.object(thematic.ownership, "link_started") as link:
            responses = await asyncio.gather(*(self.client.post(
                "/api/thematic/start", headers={"Origin": "https://app.test"},
                json={"categories": ["docs"], "legal_forms": ["ООО"]})
                for _ in range(5)))
            self.assertEqual(sorted(r.status_code for r in responses), [200, 200, 200, 429, 429])
            self.assertEqual(link.call_count, 3)
        db = ns["conn"]()
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0], 3)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
