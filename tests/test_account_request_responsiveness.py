"""Slow identity/ownership I/O must not stop unrelated ASGI requests."""
import asyncio
from contextlib import nullcontext
import os
from pathlib import Path
import re
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
from scripts import account_private_api_v1 as private
from scripts import account_production_v1 as production
from scripts.account_schema_v1 import SCHEMA
from scripts.account_security_v1 import digest_session_token
from scripts.account_session_policy_v1 import SESSION_COOKIE
import v342_thematic_queue as thematic


class SQLiteIdentity:
    """Real session/ownership SQL; translate only PostgreSQL placeholders/ANY/regex."""
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.create_function("regexp", 2,
            lambda pattern, value: bool(re.search(pattern, value or "")))

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self.connection.__exit__(*exc)
        finally:
            self.connection.close()

    def execute(self, sql, params=()):
        if re.search(r"=\s*ANY\(%s\)", sql):
            ids = params[-1]
            sql = re.sub(r"=\s*ANY\(%s\)", " IN (" + ",".join("?" for _ in ids) + ")", sql)
            params = (*params[:-1], *ids)
        sql = re.sub(r"\bbatch_id\s*~", "batch_id REGEXP", sql).replace("%s", "?")
        return self.connection.execute(sql, params)


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

        self.gate_connection = lambda: nullcontext(object())
        self.app.add_middleware(production.ProductionAccountGate,
                                connection=lambda: self.gate_connection())
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
        self.app.include_router(private.router)
        self.identity_path = str(Path(folder.name) / "identity.db")
        with sqlite3.connect(self.identity_path) as db:
            for table in ("account_users", "account_sessions", "account_thematic_batches"):
                ddl = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\(.*?\n\);", SCHEMA, re.S)
                self.assertIsNotNone(ddl)
                db.execute(ddl.group().replace("TIMESTAMPTZ", "TIMESTAMP"))
            for owner, token in (("owner-a", "s" * 48), ("owner-b", "t" * 48)):
                db.execute("INSERT INTO account_users(id,email,password_hash,role,status) VALUES(?,?,?,'user','approved')",
                           (owner, owner + "@example.test", "fixture-only-unused"))
                db.execute("INSERT INTO account_sessions(id,user_id,token_digest,expires_at) "
                           "VALUES(?,?,?,datetime('now','+1 day'))",
                           ("session-" + owner, owner, digest_session_token(token)))
        self.gate_connection = self.identity_connect
        for module in (accounts, private):
            patcher = patch.object(module, "connect", self.identity_connect)
            patcher.start()
            self.addCleanup(patcher.stop)
        return ns

    def identity_connect(self):
        return SQLiteIdentity(self.identity_path)

    def seed_batch(self, ns, batch, owner=None, status="active"):
        with ns["conn"]() as db:
            db.execute("INSERT INTO thematic_batches(id,categories,created_at,status) VALUES(?,?,?,?)",
                       (batch, '["docs"]', "2020-01-01", status))
        if owner is not None:
            with self.identity_connect() as db:
                db.execute("INSERT INTO account_thematic_batches(user_id,batch_id) VALUES(%s,%s)", (owner, batch))

    def links(self):
        with self.identity_connect() as db:
            return db.execute("SELECT user_id,batch_id FROM account_thematic_batches ORDER BY user_id,batch_id").fetchall()

    def start(self, owner="owner-a"):
        token = {"owner-a": "s" * 48, "owner-b": "t" * 48}[owner]
        return self.client.post("/api/thematic/start",
            headers={"Origin": "https://app.test", "Cookie": SESSION_COOKIE + "=" + token},
            json={"categories": ["docs"], "legal_forms": ["ООО"]})

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
        responses = await asyncio.gather(*(self.start() for _ in range(5)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 200, 200, 429, 429])
        created = {r.json()["id"] for r in responses if r.status_code == 200}
        self.assertEqual(set(self.links()), {("owner-a", batch) for batch in created})
        db = ns["conn"]()
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0], 3)
        finally:
            db.close()

    async def test_empty_owner_history_can_start_despite_three_foreign_active_batches(self):
        ns = self.install_thematic()
        for suffix in "abc":
            self.seed_batch(ns, suffix * 24, "owner-b")
        original_links = set(self.links())
        history = await self.client.get("/api/thematic/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json(), {"batches": []})
        response = await self.start()
        self.assertEqual(response.status_code, 200, response.text)
        batch = response.json()["id"]
        self.assertEqual(set(self.links()), original_links | {("owner-a", batch)})
        history = await self.client.get("/api/thematic/history")
        self.assertEqual([row["id"] for row in history.json()["batches"]], [batch])
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0], 4)

    async def test_three_owned_active_batches_block_until_one_is_stopped(self):
        ns = self.install_thematic()
        for suffix in "abc":
            self.seed_batch(ns, suffix * 24, "owner-a")
        before = self.links()
        self.assertEqual((await self.start()).status_code, 429)
        self.assertEqual(self.links(), before)
        stopped = await self.client.post("/api/thematic/" + "a" * 24 + "/stop",
                                         headers={"Origin": "https://app.test"})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        response = await self.start()
        self.assertEqual(response.status_code, 200, response.text)
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT status FROM thematic_batches WHERE id=?", ("a" * 24,)).fetchone()[0], "stopped")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0], 3)
        self.assertEqual(len(self.links()), 4)

    async def test_unassigned_history_and_inactive_owned_batches_do_not_use_capacity(self):
        ns = self.install_thematic()
        for suffix in "abc":
            self.seed_batch(ns, suffix * 24)
        self.seed_batch(ns, "d" * 24, "owner-a", "stopped")
        self.seed_batch(ns, "e" * 24, "owner-a", "completed")
        before = set(self.links())
        response = await self.start()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(self.links()), before | {("owner-a", response.json()["id"])})

    async def test_concurrent_owners_each_keep_their_own_remaining_slot(self):
        ns = self.install_thematic()
        for owner, suffixes in (("owner-a", "ab"), ("owner-b", "cd")):
            for suffix in suffixes:
                self.seed_batch(ns, suffix * 24, owner)
        owners = ["owner-a", "owner-b", "owner-a", "owner-b"]
        responses = await asyncio.gather(*(self.start(owner) for owner in owners))
        for owner in ("owner-a", "owner-b"):
            self.assertEqual(sorted(r.status_code for who, r in zip(owners, responses) if who == owner), [200, 429])
            self.assertEqual(sum(who == owner for who, _ in self.links()), 3)
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0], 6)

    async def test_waiting_start_rechecks_capacity_after_preceding_commit(self):
        ns = self.install_thematic()
        for suffix in "ab":
            self.seed_batch(ns, suffix * 24, "owner-a")
        db = ns["conn"]()
        try:
            path = db.execute("PRAGMA database_list").fetchone()[2]
        finally:
            db.close()
        linked, waiting, release = threading.Event(), threading.Event(), threading.Event()
        attempts, mutex = [], threading.Lock()

        class ObservedConnection(sqlite3.Connection):
            def execute(self, sql, params=()):
                if sql == "BEGIN IMMEDIATE":
                    with mutex:
                        attempts.append(threading.get_ident())
                        if len(attempts) == 2:
                            waiting.set()
                return super().execute(sql, params)

        ns["conn"] = lambda: sqlite3.connect(path, factory=ObservedConnection)
        save_link = thematic.ownership.link_started

        def hold_link(request, batch):
            linked.set()
            if not release.wait(5):
                raise AssertionError("Test did not release the first thematic start")
            return save_link(request, batch)

        with patch.object(thematic.ownership, "link_started", side_effect=hold_link) as link:
            requests = [asyncio.create_task(self.start())]
            try:
                self.assertTrue(await asyncio.to_thread(linked.wait, 3))
                requests.append(asyncio.create_task(self.start()))
                self.assertTrue(await asyncio.to_thread(waiting.wait, 3))
                self.assertFalse(requests[1].done(), "Second start did not wait for the SQLite writer")
            finally:
                release.set()
                responses = await asyncio.wait_for(asyncio.gather(*requests), 5)
            self.assertEqual([response.status_code for response in responses], [200, 429])
            self.assertEqual(link.call_count, 1)
        self.assertEqual(len(self.links()), 3)
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0], 3)

    async def test_owner_database_failure_never_falls_back_to_global_capacity(self):
        ns = self.install_thematic()
        for active in (0, 3):
            with self.subTest(active=active):
                if active:
                    for suffix in "abc":
                        self.seed_batch(ns, suffix * 24, "owner-b")
                before = self.links()
                # Gate has its own working connection: only the ownership DB boundary fails.
                with patch.object(accounts, "connect", side_effect=RuntimeError("Ownership DB unavailable")) as lookup:
                    response = await self.start()
                self.assertEqual(response.status_code, 503, response.text)
                self.assertGreater(lookup.call_count, 0)
                self.assertEqual(self.links(), before)
                with ns["conn"]() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0], active)

    async def test_legacy_unlink_cannot_hide_active_batch_or_release_capacity(self):
        ns = self.install_thematic()
        batches = {suffix * 24 for suffix in "abc"}
        for batch in batches:
            self.seed_batch(ns, batch, "owner-a")
        before = self.links()
        target = "a" * 24
        legacy_path = "/me/thematic-batches/" + target
        wrong_origin = await self.client.delete(legacy_path, headers={"Origin": "https://foreign.test"})
        self.assertEqual(wrong_origin.status_code, 403)
        response = await self.client.delete(legacy_path, headers={"Origin": "https://app.test"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.links(), before)
        history = await self.client.get("/api/thematic/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual({row["id"] for row in history.json()["batches"]}, batches)
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0], 3)
        self.assertEqual((await self.start()).status_code, 429)

        # The supported deletion removes both the real queue and its owner link.
        deleted = await self.client.delete("/api/thematic/" + target, headers={"Origin": "https://app.test"})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(set(self.links()), {("owner-a", batch) for batch in batches - {target}})
        with ns["conn"]() as db:
            self.assertIsNone(db.execute("SELECT id FROM thematic_batches WHERE id=?", (target,)).fetchone())
        history = await self.client.get("/api/thematic/history")
        self.assertEqual({row["id"] for row in history.json()["batches"]}, batches - {target})
        replacement = await self.start()
        self.assertEqual(replacement.status_code, 200, replacement.text)
        self.assertEqual(len(self.links()), 3)

    async def test_legacy_save_cannot_claim_another_owners_real_batch(self):
        ns = self.install_thematic()
        batch = "a" * 24
        self.seed_batch(ns, batch, "owner-b")
        before = self.links()
        path = "/me/thematic-batches/" + batch
        wrong_origin = await self.client.post(path, headers={"Origin": "https://foreign.test"})
        self.assertEqual(wrong_origin.status_code, 403)
        response = await self.client.post(path, headers={"Origin": "https://app.test"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.links(), before)
        history = await self.client.get("/api/thematic/history")
        self.assertEqual(history.json(), {"batches": []})
        self.assertEqual((await self.client.get("/api/thematic/" + batch)).status_code, 404)
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT status FROM thematic_batches WHERE id=?", (batch,)).fetchone()[0], "active")

    async def test_legacy_bookmarks_and_personal_placeholders_remain_owner_scoped(self):
        ns = self.install_thematic()
        headers = {"Origin": "https://app.test"}
        legacy = "legacy-fixture-check"
        saved = await self.client.post("/me/thematic-batches/" + legacy, headers=headers)
        self.assertEqual(saved.status_code, 201, saved.text)
        denied = await self.client.delete("/me/thematic-batches/" + legacy,
            headers={**headers, "Cookie": SESSION_COOKIE + "=" + "t" * 48})
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(self.links(), [("owner-a", legacy)])
        created = await self.client.post("/me/thematic-batches", headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        personal = created.json()["batch_id"]
        self.assertTrue(personal.startswith("personal-"))
        self.assertFalse(created.json()["execution_started"])
        self.assertEqual((await self.client.post("/me/thematic-batches/" + personal, headers=headers)).status_code, 409)
        details = await self.client.get("/me/thematic-batches/" + personal)
        self.assertEqual(details.status_code, 200, details.text)
        self.assertFalse(details.json()["execution_started"])
        for batch in (legacy, personal):
            removed = await self.client.delete("/me/thematic-batches/" + batch, headers=headers)
            self.assertEqual(removed.status_code, 200, removed.text)
        self.client.cookies.clear()
        self.assertEqual((await self.client.post("/me/thematic-batches/" + legacy, headers=headers)).status_code, 401)
        self.assertEqual(self.links(), [])
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0], 0)

    async def test_start_still_requires_session_and_same_origin(self):
        ns = self.install_thematic()
        payload = {"categories": ["docs"], "legal_forms": ["ООО"]}
        response = await self.client.post("/api/thematic/start", json=payload,
                                          headers={"Origin": "https://foreign.test"})
        self.assertEqual(response.status_code, 403)
        self.client.cookies.clear()
        response = await self.client.post("/api/thematic/start", json=payload,
            headers={"Origin": "https://app.test", "x-eis223-account-id": "owner-a"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.links(), [])
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
