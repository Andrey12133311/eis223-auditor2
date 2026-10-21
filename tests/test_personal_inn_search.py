"""Owner-scoped INN HTTP searches use the real queue and verified buyer facts."""
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eis_server_worker as worker
import v301_search_jobs as queue
from scripts import account_api_v1 as accounts
from scripts import account_production_v1 as production
from scripts import v366_account_manual as manual
from scripts.account_session_policy_v1 import SESSION_COOKIE

INN = "7701234567"
OTHER_INN = "7707654321"
REG = "32616233661"
OTHER_REG = "32616233662"
ORIGIN = "https://example.test"


class AccountDatabase:
    """Execute the account SQL against a local fixture, adapting PG parameters."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)

    def execute(self, sql, args=()):
        return self.db.execute(sql.replace("%s", "?"), args)

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        try:
            self.db.rollback() if kind else self.db.commit()
        finally:
            self.db.close()


class TrackedConnection(sqlite3.Connection):
    was_closed = False

    def close(self):
        self.was_closed = True
        return super().close()


class PersonalInnSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.account_path = self.home / "accounts.sqlite"
        self.queue_path = self.home / "eis.sqlite"
        with closing(sqlite3.connect(self.account_path)) as db:
            db.executescript("""
              CREATE TABLE account_inn_searches(
                user_id TEXT NOT NULL,job_id TEXT NOT NULL,inn TEXT NOT NULL,
                last_started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id,job_id));
              CREATE TABLE account_manual_checks(user_id TEXT,reg_number TEXT,job_id TEXT);
            """)
        for replacement in (
            patch.dict(os.environ, {"EIS223_PUBLIC_ORIGIN": ORIGIN,
                                    "EIS223_AUTH_ENFORCE": "true",
                                    "EIS223_SOURCE_MODE": "OFFICIAL_EIS_VPS_ONLY"}),
            patch.object(accounts, "connect", side_effect=self.account_connect),
            patch.object(manual, "connect", side_effect=self.account_connect),
            patch.object(accounts, "resolve_session", side_effect=lambda db, token:
                         (token, "user", "approved") if token in ("alice", "bob") else None),
            patch.object(production, "user_session", side_effect=self.gate_session),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        app = FastAPI()
        self.ns = {"app": app, "conn": lambda: sqlite3.connect(self.queue_path)}
        queue.install(self.ns)
        worker.install(self.ns)
        app.include_router(manual.inn_router)
        app.add_middleware(production.ProductionAccountGate)
        with closing(self.ns["conn"]()) as db:
            db.execute("DELETE FROM v301_search_jobs")
            db.execute("""CREATE TABLE purchases(
              reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,customer_inn TEXT,
              price TEXT,price_num REAL,violations INTEGER,documents_checked INTEGER,
              documents_found INTEGER,last_seen TEXT)""")
            db.commit()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def account_connect(self):
        return AccountDatabase(self.account_path)

    @staticmethod
    def gate_session(headers, connection):
        cookies = SimpleCookie()
        cookies.load(headers.get(b"cookie", b"").decode())
        token = cookies[SESSION_COOKIE].value if SESSION_COOKIE in cookies else ""
        return (token, "user", "approved") if token in ("alice", "bob") else None

    def headers(self, owner="alice", origin=ORIGIN):
        headers = {"cookie": SESSION_COOKIE + "=" + owner} if owner else {}
        if origin is not None:
            headers["origin"] = origin
        return headers

    def start(self, inn=INN, owner="alice", origin=ORIGIN):
        return self.client.post("/me/inn-search", json={"inn": inn},
                                headers=self.headers(owner, origin))

    def snapshot(self, identifier, owner="alice"):
        return self.client.get("/me/inn-search/" + identifier,
                               headers=self.headers(owner, None))

    def count(self, table, account=False):
        path = self.account_path if account else self.queue_path
        with closing(sqlite3.connect(path)) as db:
            return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def purchase(self, reg, inn):
        with closing(self.ns["conn"]()) as db:
            db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (reg, "Закупка", "Заказчик", inn, "100", 100, 0, 0, 0, "2026-10-19"))
            db.commit()

    def test_recent_searches_are_owner_scoped_and_read_only(self):
        empty = self.client.get("/me/inn-search", headers=self.headers("alice", None))
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json(), {"searches": []})
        self.assertIn("no-store", empty.headers["cache-control"])
        self.assertEqual(self.client.get("/me/inn-search").status_code, 401)
        first = self.start(INN, owner="alice").json()["id"]
        other = self.start(OTHER_INN, owner="bob").json()["id"]
        seen = self.client.get("/me/inn-search", headers=self.headers("alice", None))
        self.assertEqual(seen.json(), {"searches": [{"id": first, "inn": INN}]})
        self.assertNotIn(other, seen.text)
        self.assertEqual(
            self.client.get("/me/inn-search", headers=self.headers("bob", None)).json(),
            {"searches": [{"id": other, "inn": OTHER_INN}]}
        )
        self.assertEqual(self.count("v301_search_jobs"), 2)

    def test_requires_session_for_both_routes(self):
        self.assertEqual(self.start(owner="").status_code, 401)
        self.assertEqual(self.snapshot("a" * 24, owner="").status_code, 401)
        self.assertEqual(self.count("v301_search_jobs"), 0)
        self.assertEqual(self.count("account_inn_searches", account=True), 0)

    def test_rejects_missing_or_foreign_origin_before_queueing(self):
        for origin in (None, "https://other.test", ORIGIN + ".evil.test"):
            with self.subTest(origin=origin):
                self.assertEqual(self.start(origin=origin).status_code, 403)
        self.assertEqual(self.count("v301_search_jobs"), 0)

    def test_accepts_ten_and_twelve_digits_without_manual_membership(self):
        for inn in (INN, "123456789012"):
            response = self.start(inn)
            self.assertEqual(response.status_code, 202, response.text)
            body = response.json()
            self.assertEqual((body["kind"], body["target"], body["status"]), ("inn", inn, "queued"))
            self.assertTrue(body["new_request"])
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertFalse(body["bridge"]["browser_collector_enabled"])
        self.assertEqual(self.count("v301_search_jobs"), 2)
        self.assertEqual(self.count("account_inn_searches", account=True), 2)
        self.assertEqual(self.count("account_manual_checks", account=True), 0)
        self.assertEqual(self.count("v319_manual_inputs"), 0)
        self.assertEqual(self.count("v301_inn_purchases"), 0)

    def test_invalid_input_never_queues(self):
        for inn in (None, 7701234567, True, "", "123456789", "12345678901", "1234567890123",
                    "770123456x", " " + INN, INN + "\n", [INN], "７７０１２３４５６７"):
            with self.subTest(inn=inn):
                self.assertEqual(self.start(inn).status_code, 422)
        self.assertEqual(self.count("v301_search_jobs"), 0)

    def test_same_search_is_shared_but_access_requires_each_owners_submission(self):
        first = self.start().json()
        identifier = first["id"]
        self.assertEqual(self.snapshot(identifier, "bob").status_code, 404)
        repeat = self.start().json()
        second_owner = self.start(owner="bob").json()
        self.assertEqual(first["id"], repeat["id"])
        self.assertEqual(first["id"], second_owner["id"])
        self.assertFalse(repeat["new_request"])
        self.assertFalse(second_owner["new_request"])
        for owner in ("alice", "bob"):
            response = self.snapshot(identifier, owner)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(self.count("v301_search_jobs"), 1)
        self.assertEqual(self.count("account_inn_searches", account=True), 2)
        self.assertEqual(self.snapshot("f" * 24).status_code, 404)
        self.assertEqual(self.snapshot("invalid-id").status_code, 404)

    def test_owned_link_cannot_resolve_another_target_or_registry_job(self):
        identifier = self.start().json()["id"]
        with closing(self.ns["conn"]()) as db:
            db.execute("UPDATE v301_search_jobs SET target=? WHERE id=?", (OTHER_INN, identifier))
            db.commit()
        self.assertEqual(self.snapshot(identifier).status_code, 404)
        with closing(self.ns["conn"]()) as db:
            db.execute("UPDATE v301_search_jobs SET target=?,kind='reg' WHERE id=?", (INN, identifier))
            db.commit()
        self.assertEqual(self.snapshot(identifier).status_code, 404)

    def test_only_exact_buyer_is_shown_even_with_legacy_association(self):
        self.purchase(REG, INN)
        self.purchase(OTHER_REG, OTHER_INN)
        with closing(self.ns["conn"]()) as db:
            db.execute("INSERT INTO v301_inn_purchases VALUES(?,?,?)", (INN, OTHER_REG, "2026-10-19"))
            db.commit()
        # The account endpoint enforces buyer identity even if another runtime
        # still has the legacy association-inclusive local results enabled.
        with patch.dict(os.environ, {"EIS223_SOURCE_MODE": "legacy"}):
            response = self.start()
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual([r["reg_number"] for r in response.json()["existing"]], [REG])
            result = self.snapshot(response.json()["id"]).json()
            self.assertEqual([r["reg_number"] for r in result["existing"]], [REG])
            self.assertEqual(result["total_existing"], 1)

    def test_leases_are_never_returned_in_create_or_poll_responses(self):
        identifier = self.start().json()["id"]
        with closing(self.ns["conn"]()) as db:
            db.execute("UPDATE v301_search_jobs SET status='leased',lease_owner=?,lease_token=?,lease_until=9999999999 WHERE id=?",
                       ("private-worker-owner", "secret-lease-token", identifier))
            db.commit()
        for response in (self.start(), self.snapshot(identifier)):
            self.assertIn(response.status_code, (200, 202))
            for secret in ("lease_owner", "lease_token", "lease_until", "private-worker-owner", "secret-lease-token"):
                self.assertNotIn(secret, response.text)

    def test_discovery_done_still_exposes_pending_buyer_verification(self):
        identifier = self.start().json()["id"]
        child, _ = queue._enqueue(self.ns, "reg", REG, 1, 8, "inn-search:" + identifier, False)
        with closing(self.ns["conn"]()) as db:
            db.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (identifier,))
            db.execute("INSERT INTO eis_inn_candidates VALUES(?,?,?,?,?,?,?,?)",
                       (identifier, INN, REG, identifier, 1, "https://zakupki.gov.ru/", "2026-10-19", child["id"]))
            db.commit()
        result = self.snapshot(identifier).json()
        self.assertEqual(result["job"]["status"], "done")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["verification_pending"], 1)
        self.assertEqual(result["existing"], [])

    def test_owner_save_retry_reuses_accepted_queue_job(self):
        with patch.object(manual, "_link_inn_search", side_effect=RuntimeError("fixture database unavailable")):
            response = self.start()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.count("v301_search_jobs"), 1)
        self.assertEqual(self.count("account_inn_searches", account=True), 0)
        retry = self.start()
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertFalse(retry.json()["new_request"])
        self.assertEqual(self.count("v301_search_jobs"), 1)
        self.assertEqual(self.count("account_inn_searches", account=True), 1)

    def test_uninitialized_eis_service_returns_clear_unavailable_status(self):
        self.ns["app"].state.eis223_manual_ns = None
        response = self.start()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.count("v301_search_jobs"), 0)

    def test_lookup_closes_every_sqlite_connection(self):
        self.purchase(REG, INN)
        opened = []

        def tracked_connect():
            connection = sqlite3.connect(self.queue_path, factory=TrackedConnection)
            opened.append(connection)
            return connection

        result = manual._verified_inn_results({"conn": tracked_connect}, INN)
        self.assertEqual([row["reg_number"] for row in result], [REG])
        self.assertTrue(opened)
        self.assertTrue(all(connection.was_closed for connection in opened))


if __name__ == "__main__":
    unittest.main()
