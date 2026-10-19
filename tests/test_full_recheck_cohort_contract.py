"""Document the existing populations; these fixtures do not enable a recheck.

No production source, queue, authorization, legal rule or schema is changed.
The fixture values deliberately differ from any reported live counter.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v257_features as dashboard
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
from thematic_period import contains
import full_recheck_inventory as inventory


class CohortContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cohorts.sqlite"
        self.verified = set()
        self.ns = {"app": FastAPI(), "conn": self.connect,
                   "_verified_set235": lambda: self.verified}
        with self.database() as c:
            c.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,
                    customer_inn TEXT,org_form TEXT,title TEXT,price_num REAL,
                    documents_checked INTEGER DEFAULT 0,documents_found INTEGER DEFAULT 0,
                    violations INTEGER DEFAULT 0,risks INTEGER DEFAULT 0,last_seen TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,local_path TEXT);
                CREATE TABLE findings(reg_number TEXT,kind TEXT,code TEXT,title TEXT,
                    law TEXT,evidence TEXT,document TEXT,source_url TEXT);
                CREATE TABLE checks(reg_number TEXT,status TEXT,field_path TEXT,item TEXT,
                    law TEXT,evidence TEXT,document TEXT,source_url TEXT);
                CREATE TABLE thematic_batches(id TEXT PRIMARY KEY,status TEXT);
                CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT,
                    PRIMARY KEY(batch_id,reg));
            """)
        dashboard.install(self.ns)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def database(self):
        c = self.connect()
        try:
            with c:
                yield c
        finally:
            c.close()

    def purchase(self, index, customer, *, verified=True, manual=False):
        reg = str(32619000100 + index)
        with self.database() as c:
            c.execute("INSERT INTO purchases(reg_number,customer,last_seen) VALUES(?,?,?)",
                      (reg, customer, "2026-10-19"))
            if manual:
                c.execute("INSERT INTO v319_manual_inputs VALUES(?,?,?,?)",
                          (reg, "2026-10-19", "number_field", ""))
        if verified:
            self.verified.add(reg)
        return reg

    def read_dashboard(self):
        async def get():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns["app"]),
                                         base_url="http://test") as client:
                result = await client.get("/api/v257/dashboard")
                self.assertEqual(result.status_code, 200, result.text)
                return result.json()
        return asyncio.run(get())

    def test_dashboard_and_general_company_populations_differ(self):
        company = self.purchase(1, "ООО Компания")
        institution = self.purchase(2, "ГБУ Учреждение")
        unknown = self.purchase(3, "")
        self.purchase(4, "ООО Ручная", manual=True)
        self.purchase(5, "ООО Вне текущего снимка", verified=False)
        self.assertEqual(general.dashboard_regs(self.ns), [company])
        general.initialize(self.ns)
        result = self.read_dashboard()
        self.assertEqual({row["reg_number"] for row in result["purchases"]},
                         {company, institution, unknown})
        self.assertEqual(result["stats"]["purchases"], 3)
        self.assertEqual(general.progress(self.ns)["total_registered"], 1)
        with self.database() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM purchases").fetchone()[0], 5)

    def test_persistent_membership_survives_volatile_intake_shrink(self):
        reg = self.purchase(1, "ООО Компания")
        general.initialize(self.ns)
        self.verified.clear()
        self.assertEqual(general.dashboard_regs(self.ns), [])
        self.assertEqual(self.read_dashboard()["stats"]["purchases"], 1)
        self.assertEqual(general.progress(self.ns)["total_registered"], 1)
        with self.database() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Исправлено' WHERE reg_number=?", (reg,))
            c.execute("INSERT INTO docs VALUES(1,?,'checked','/fixture/does-not-prove-bytes.pdf')", (reg,))
        # The row remains visible and counted, but the current company guard
        # prevents execution. A queue count is not an executable-cohort count.
        self.assertEqual(self.read_dashboard()["stats"]["purchases"], 1)
        self.assertIsNone(general.claim_next(self.ns))

    def test_seed_cap_does_not_cap_future_membership(self):
        for index in range(general.DASHBOARD_MAX + 20):
            self.purchase(index, "ООО Компания")
        self.assertEqual(len(general.dashboard_regs(self.ns)), general.DASHBOARD_MAX)
        self.assertEqual(len(general.dashboard_regs(self.ns, include_new=True)),
                         general.DASHBOARD_MAX + 20)
        general.initialize(self.ns)
        # Initialization seeds 1,253 and additionally calls the bounded indexer.
        self.assertEqual(general.progress(self.ns)["total_registered"],
                         general.DASHBOARD_MAX + general.MAX_RECHECK_ADMISSIONS)

    def test_baseline_purchase_count_is_not_current_inventory(self):
        self.purchase(1, "ООО Первая")
        general.initialize(self.ns)
        self.purchase(2, "ООО Позднее")
        general.add_new_verified(self.ns)
        progress = general.progress(self.ns)
        self.assertEqual(progress["baseline_purchases"], 1)
        self.assertEqual(progress["total_registered"], 2)

    def test_progress_can_include_orphan_and_currently_ineligible_rows(self):
        reg = self.purchase(1, "ООО Компания")
        general.initialize(self.ns)
        with self.database() as c:
            c.execute("UPDATE purchases SET customer='' WHERE reg_number=?", (reg,))
            c.execute("INSERT INTO v332_audit_queue(campaign,reg) VALUES(?,?)",
                      (general.CAMPAIGN, "32619999999"))
        self.assertEqual(general.progress(self.ns)["total_registered"], 2)
        self.assertEqual(self.read_dashboard()["stats"]["purchases"], 1)
        self.assertIsNone(general.claim_next(self.ns))

    def test_thematic_log_counts_positions_not_unique_procurements(self):
        with self.database() as c:
            c.executemany("INSERT INTO thematic_batches VALUES(?,?)",
                          [("first", "active"), ("second", "active"), ("past", "stopped")])
            c.executemany("INSERT INTO thematic_items VALUES(?,?,'completed')",
                          [("first", "32619000101"), ("first", "32619000102"),
                           ("second", "32619000101"), ("second", "32619000103"),
                           ("past", "32619000104")])
            positions, unique = c.execute("""SELECT COUNT(*),COUNT(DISTINCT t.reg)
                FROM thematic_items t JOIN thematic_batches b ON b.id=t.batch_id
                WHERE b.status='active'""").fetchone()
        self.assertEqual((positions, unique), (4, 3))

    def test_company_parser_is_name_based_not_org_form_based(self):
        for name in ("ГБУ Учреждение", "ФГУП Предприятие", "ИП Иванов", "", None,
                     "Заказчик ООО Вторая часть"):
            self.assertFalse(thematic.business_company(name), repr(name))
        for name in ('«ООО Компания»', "Открытое акционерное общество Компания"):
            self.assertTrue(thematic.business_company(name), repr(name))

    def test_missing_publication_only_excluded_from_dated_thematic_scope(self):
        self.assertTrue(contains(None))
        self.assertFalse(contains(None, "2026-10-01", "2026-10-31"))
        self.assertTrue(contains("2026-10-31T23:59:00+03:00", "2026-10-01", "2026-10-31"))

    def test_inventory_matches_dashboard_and_never_logs_row_values(self):
        company = self.purchase(1, "ООО Секретное имя")
        self.purchase(2, "ГБУ Учреждение")
        self.purchase(3, "ООО Ручная", manual=True)
        self.verified.add("32619999999")
        general.initialize(self.ns)
        result = inventory.collect(self.ns)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"]["dashboard_purchases"], self.read_dashboard()["stats"]["purchases"])
        self.assertEqual(result["counts"]["dashboard_other_or_unknown_company"], 1)
        self.assertEqual(result["counts"]["manual_saved_purchases"], 1)
        self.assertEqual(result["counts"]["verified_not_saved"], 1)
        encoded = json.dumps(result, ensure_ascii=False)
        for value in (company, "32619999999", "Секретное", "ГБУ Учреждение"):
            self.assertNotIn(value, encoded)

    def test_inventory_is_read_only_and_budget_failure_is_not_zero(self):
        self.purchase(1, "ООО Компания")
        self.purchase(2, "ООО Компания")
        general.initialize(self.ns)
        def restricted():
            c = self.connect()
            denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                      sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE}
            c.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK)
            return c
        self.assertEqual(inventory.collect({**self.ns, "conn": restricted})["status"], "complete")
        with patch.object(inventory, "MAX_PURCHASES", 1):
            result = inventory.collect(self.ns)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("counts", result)

    def test_inventory_source_errors_are_private_fixed_reason(self):
        def failed():
            raise RuntimeError("private account /data/customer-name 32619999999")
        result = inventory.collect({**self.ns, "_verified_set235": failed})
        self.assertEqual(result["reason"], "inventory_source_unavailable")
        self.assertNotIn("private", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
