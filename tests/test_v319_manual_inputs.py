"""Manual history reflects submissions, even when download work is shared."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v257_features as features
import v301_search_jobs as jobs
from v319_manual_inputs import migrate_legacy_inputs

AUTO = "32610000001"
TYPED = "32610000002"
INN_RESULT = "32610000003"
REPAIR = "32610000004"
LEGACY = "32610000005"
INN = "1234567890"


class ManualInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_file = str(Path(self.temp.name) / "audit.sqlite")

        def conn():
            db = sqlite3.connect(self.db_file, timeout=30)
            db.row_factory = sqlite3.Row
            return db

        self.conn = conn
        with conn() as db:
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,
                    title TEXT,source TEXT,customer_inn TEXT,org_form TEXT,price_num REAL,
                    last_seen TEXT,documents_found INT,documents_checked INT,
                    violations INT,risks INT);
                CREATE TABLE findings(reg_number TEXT,kind TEXT,code TEXT,title TEXT);
                CREATE TABLE requests_v233(reg TEXT PRIMARY KEY,lane TEXT,
                    status TEXT,requested_at TEXT);
                CREATE TABLE document_priorities_v233(reg TEXT PRIMARY KEY,
                    lane TEXT,requested_at TEXT);
                CREATE TABLE customer_purchases_v233(inn TEXT,reg TEXT);
                CREATE TABLE v257_manual_registry(reg TEXT PRIMARY KEY,
                    requested_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """)
            # An automatic import has all the old, misleading manual labels.
            db.execute("""INSERT INTO purchases VALUES(?, 'Customer', 'Automatic',
                'manual', ?, 'ООО', 5000, '2026', 2, 2, 0, 0)""", (AUTO, INN))
            db.execute("INSERT INTO document_priorities_v233 VALUES(?,'manual','2026-10-14')", (AUTO,))
            db.execute("INSERT INTO v257_manual_registry(reg) VALUES(?)", (AUTO,))
            db.execute("INSERT INTO requests_v233 VALUES(?,'manual','saved','2026-10-09')", (LEGACY,))
        self.app = FastAPI()
        self.ns = {"app": self.app, "conn": conn, "_verified_set235": lambda: {AUTO}}
        features.install(self.ns)
        jobs.install(self.ns)

    def tearDown(self):
        self.temp.cleanup()

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                     base_url="http://test") as client:
            response = await client.request(method, path, **kwargs)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

    def manual_regs(self):
        result = asyncio.run(self.request("GET", "/api/v257/manual"))
        self.assertEqual(result["total"], len(result["items"]))
        return {item["reg_number"] for item in result["items"]}

    def test_old_source_and_download_labels_do_not_make_import_manual(self):
        self.assertEqual(self.manual_regs(), {LEGACY})
        dashboard = asyncio.run(self.request("GET", "/api/v257/dashboard"))
        self.assertEqual(dashboard["stats"]["purchases"], 1)
        self.assertEqual(dashboard["purchases"][0]["reg_number"], AUTO)
        # Membership repair never removes originals, findings or old records.
        with self.conn() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT reg FROM v257_manual_registry").fetchone()[0], AUTO)

    def test_number_field_is_visible_immediately_without_originals(self):
        submitted = asyncio.run(self.request("POST", "/api/v301/search",
                                            json={"kind": "reg", "value": TYPED}))
        self.assertEqual(self.manual_regs(), {LEGACY, TYPED})
        result = asyncio.run(self.request("GET", "/api/v257/manual"))
        typed = next(item for item in result["items"] if item["reg_number"] == TYPED)
        self.assertEqual(typed["manual_status"], "queued")
        self.assertIsNone(typed["documents_checked"])
        again = asyncio.run(self.request("POST", "/api/v301/search",
                                        json={"kind": "reg", "value": TYPED}))
        self.assertFalse(again["new_request"])
        self.assertEqual(submitted["id"], again["id"])
        self.assertEqual(self.manual_regs(), {LEGACY, TYPED})

    def test_inn_results_and_automatic_jobs_never_enter_manual_history(self):
        inn_job = asyncio.run(self.request("POST", "/api/v301/search",
                                          json={"kind": "inn", "value": INN}))
        jobs._finish(self.ns, inn_job["id"], "done", "Found", 1, 0,
                     regs=[INN_RESULT], more=True)
        jobs._enqueue(self.ns, "reg", AUTO)
        self.assertEqual(self.manual_regs(), {LEGACY})
        # Repeated installation must not backfill service jobs into the list.
        jobs._init(self.ns)
        self.assertEqual(self.manual_regs(), {LEGACY})

    def test_typing_number_already_in_inn_queue_records_submission(self):
        parent, _ = jobs._enqueue(self.ns, "inn", INN, priority=0)
        jobs._finish(self.ns, parent["id"], "done", "Found", 1, 0, regs=[INN_RESULT])
        child = None
        with self.conn() as db:
            child = db.execute("SELECT id FROM v301_search_jobs WHERE target=?",
                               (INN_RESULT,)).fetchone()[0]
        submitted = asyncio.run(self.request("POST", "/api/v301/search",
                                            json={"kind": "reg", "value": INN_RESULT}))
        self.assertFalse(submitted["new_request"])
        self.assertEqual(submitted["id"], child)
        self.assertEqual(self.manual_regs(), {LEGACY, INN_RESULT})
        jobs._finish(self.ns, child, "done", "Originals received", 1, 1)
        result = asyncio.run(self.request("GET", "/api/v257/manual"))
        row = next(item for item in result["items"] if item["reg_number"] == INN_RESULT)
        self.assertEqual(row["manual_status"], "done")

    def test_migration_restores_only_direct_number_searches_and_is_once_only(self):
        # Simulate the queue from the previously deployed version.
        with self.conn() as db:
            db.execute("DELETE FROM v319_manual_migrations WHERE marker='legacy_v301_number_requests'")
            for identifier, reg, priority, parent in (
                    ("a" * 24, TYPED, 0, ""),
                    ("b" * 24, INN_RESULT, 8, "inn-job"),
                    ("c" * 24, REPAIR, 2, "")):
                db.execute("""INSERT INTO v301_search_jobs
                    (id,kind,target,priority,parent_id,created_at,updated_at)
                    VALUES(?,'reg',?,?,?,'2026-10-13','2026-10-13')""",
                    (identifier, reg, priority, parent))
            self.assertEqual(migrate_legacy_inputs(db), 1)
        self.assertEqual(self.manual_regs(), {LEGACY, TYPED})
        with self.conn() as db:
            db.execute("INSERT INTO requests_v233 VALUES(?,'manual','saved','2026-10-14')", (AUTO,))
            self.assertEqual(migrate_legacy_inputs(db), 0)
        self.assertEqual(self.manual_regs(), {LEGACY, TYPED})

    def test_existing_automatic_number_can_be_explicitly_submitted(self):
        asyncio.run(self.request("POST", "/api/v301/search",
                                json={"kind": "reg", "value": AUTO}))
        self.assertEqual(self.manual_regs(), {LEGACY, AUTO})
        dashboard = asyncio.run(self.request("GET", "/api/v257/dashboard"))
        self.assertEqual(dashboard["stats"]["purchases"], 0)


if __name__ == "__main__":
    unittest.main()
