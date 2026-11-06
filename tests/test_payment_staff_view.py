"""The real staff route must classify exact payment rows before deduplication."""
from pathlib import Path
import re
import sqlite3
import sys
import unittest
from unittest.mock import patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_thematic_finding_submissions as thematic_fixture
import test_v382_finding_submissions as accounts
from scripts import v370_manager_campaigns as campaigns
import payment_finding_reassessment as assessment
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast

from test_payment_finding_reassessment import PAYMENT, ENGINE

BATCH_A, BATCH_B, BATCH_C = thematic_fixture.BATCH_A, thematic_fixture.BATCH_B, thematic_fixture.BATCH_C


class CampaignIdentity(accounts.SQLiteIdentity):
    def execute(self, sql, params=()):
        if "finding_key=ANY(%s)" in sql:
            keys = params[-1]
            sql = sql.replace("finding_key=ANY(%s)", "finding_key IN (" + ",".join("%s" for _ in keys) + ")")
            params = (*params[:-1], *keys)
        return super().execute(sql, params)


class PaymentStaffView(unittest.TestCase):
    def setUp(self):
        self.t = thematic_fixture.ThematicFindingTests()
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.f, self.ns = self.t.f, self.t.ns
        prior_tick, prior_state = thematic.tick, dict(thematic.WORKER_STATE)
        def restore():
            thematic.tick = prior_tick
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(prior_state)
            thematic._HISTORY_COUNT_CACHE.clear()
        self.addCleanup(restore)
        fast.install(self.ns)
        self.f.stack.enter_context(patch.object(assessment, "_engine", return_value=ENGINE))
        self.f.stack.enter_context(patch.object(campaigns, "connect",
            lambda: CampaignIdentity(self.f.identity_path, self.f.calls)))
        with sqlite3.connect(self.f.identity_path) as db:
            db.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value)))
            for table in ("account_manager_campaign_targets", "account_staff_finding_decisions"):
                ddl = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\(.*?\n\);",
                                accounts.schema.SCHEMA, re.S).group()
                db.execute(ddl.replace("TIMESTAMPTZ", "TIMESTAMP").replace(" ~ ", " REGEXP "))
            for index, (batch, owner, manager) in enumerate((
                (BATCH_A, accounts.EMPLOYEE_A, accounts.MANAGER_A),
                (BATCH_B, accounts.EMPLOYEE_B, accounts.MANAGER_B),
            )):
                db.execute("INSERT INTO account_manager_campaign_targets(id,manager_id,user_id,batch_id,title,"
                    "categories,legal_forms,target_violations,status) VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(UUID(int=1000+index)), str(manager), str(owner), batch, "Проверка срока оплаты",
                     '["protocols","sme_payment"]', '["ООО"]', 1, "active"))
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute("DELETE FROM thematic_findings WHERE category='national'")
            db.execute("UPDATE thematic_batches SET categories='[\"protocols\",\"sme_payment\"]'")
            for batch in (BATCH_A, BATCH_B, BATCH_C):
                self.insert_payment(db, batch, PAYMENT)
        self.ns["app"].include_router(campaigns.staff_router)

    @staticmethod
    def insert_payment(db, batch, item):
        db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (batch, accounts.REG_A, *(item[key] for key in
                   ("category", "title", "law", "evidence", "code", "document", "source_url"))))

    def reader(self, user=accounts.EMPLOYEE_A):
        response = self.f.client.get("/me/campaign-goals/violations", headers=self.f.headers(user))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        return response.json()

    def snapshot(self):
        with sqlite3.connect(self.f.evidence_path) as db:
            return db.execute("SELECT rowid,* FROM thematic_findings ORDER BY rowid").fetchall()

    def record(self, findings):
        with sqlite3.connect(self.f.evidence_path) as db:
            event = assessment.prepare_assessment(accounts.REG_A, findings, scope="thematic", batch=BATCH_A,
                engine_signature=ENGINE, source_version="verified-payment-sources",
                db_token=assessment.db_source_token(db, accounts.REG_A))
            assessment.record_assessment(db, event)
            db.execute("UPDATE thematic_items SET status='completed',last_semantic_version=?,last_engine_signature=? "
                       "WHERE batch_id=? AND reg=?", (event["source_version"], ENGINE, BATCH_A, accounts.REG_A))

    def test_legacy_payment_leaves_active_count_but_retains_revised_evidence(self):
        original = self.snapshot()
        result = self.reader()
        self.assertEqual(result["count"], 1)
        self.assertEqual([item["title"] for item in result["findings"]], [thematic_fixture.TITLE])
        self.assertEqual(len(result["reassessed_findings"]), 1)
        revised = result["reassessed_findings"][0]
        self.assertEqual((revised["batch_id"], revised["effective_status"]), (BATCH_A, "requires_review"))
        self.assertEqual(revised["evidence"], PAYMENT["evidence"])
        self.assertTrue(revised["payment_reassessment"]["reason"])
        self.assertEqual(self.snapshot(), original)

    def test_current_exact_source_remains_active_alongside_old_reviewed_source(self):
        # Same quoted condition in another source is a distinct assessment.
        # A different legacy category permits both immutable representations.
        current = {**PAYMENT, "category": "contract", "document": "Заключенный договор.docx"}
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute("UPDATE thematic_batches SET categories='[\"protocols\",\"sme_payment\",\"contract\"]' "
                       "WHERE id=?", (BATCH_A,))
            self.insert_payment(db, BATCH_A, current)
        self.record([current])
        before, foreign_before = self.snapshot(), self.reader(accounts.EMPLOYEE_B)
        result = self.reader()
        self.assertEqual(result["count"], 2)
        active = next(item for item in result["findings"] if item["title"] == PAYMENT["title"])
        self.assertEqual((active["document"], active["batch_id"]), (current["document"], BATCH_A))
        revised = result["reassessed_findings"]
        self.assertEqual(len(revised), 1)
        self.assertEqual((revised[0]["document"], revised[0]["effective_status"]),
                         (PAYMENT["document"], "requires_review"))
        self.assertEqual(self.reader(accounts.EMPLOYEE_B), foreign_before)
        self.assertEqual(self.snapshot(), before)
        # A later completed pass withdrawing the quote changes the view,
        # never deletes the original rows or leaves an active stale payment.
        self.record([])
        result = self.reader()
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["reassessed_findings"])
        self.assertTrue(all(item["effective_status"] == "requires_review" for item in result["reassessed_findings"]))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
