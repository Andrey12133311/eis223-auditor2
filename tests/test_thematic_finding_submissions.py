"""Real owner routes, thematic provenance, manager decisions and printed fields."""
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_v382_finding_submissions as fixture
from scripts import account_schema_v1 as schema
from scripts import v367_private_features as features
from scripts import v384_thematic_findings as source
from scripts.account_production_v1 import ProductionAccountGate
import v342_thematic_queue as thematic

BATCH_A, BATCH_B, BATCH_C = "a" * 24, "b" * 24, "c" * 24
TITLE = "Нарушен срок публикации тематического протокола"


class ThematicFindingTests(unittest.TestCase):
    def setUp(self):
        # Reuse the SQL-backed identity adapter without running inherited tests.
        self.f = fixture.FindingSubmissionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.clear_manual_checks()
        self.f.stack.enter_context(patch.dict("os.environ", {"EIS223_ACCOUNTS_MOUNT": "true"}))
        self.f.stack.enter_context(patch.object(features, "connect", self.f.connect))
        self.f.stack.enter_context(patch.object(features, "_memo_defaults", return_value={
            "recipient": "Образец получателя", "executor": "Образец исполнителя", "phone": "Тел.: 000"}))
        with sqlite3.connect(self.f.identity_path) as db:
            for table in ("account_thematic_batches", "account_memo_requisites"):
                ddl = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\(.*?\n\);", schema.SCHEMA, re.S).group()
                db.execute(ddl.replace("TIMESTAMPTZ", "TIMESTAMP"))
            db.executemany("INSERT INTO account_thematic_batches VALUES(?,?)", [
                (str(fixture.EMPLOYEE_A), BATCH_A), (str(fixture.EMPLOYEE_B), BATCH_B),
                (str(fixture.EMPLOYEE_A), BATCH_C)])
        app = FastAPI()
        self.ns = {"conn": lambda: sqlite3.connect(self.f.evidence_path), "app": app}
        app.state.eis223_manual_ns = self.ns
        thematic.install(self.ns)
        app.include_router(fixture.submissions.router)
        app.include_router(fixture.evidence.router)
        app.include_router(features.router)
        self.f.client.close()
        self.f.client = TestClient(ProductionAccountGate(app, connection=self.f.connect))
        self.addCleanup(self.f.client.close)
        with sqlite3.connect(self.f.evidence_path) as db:
            for batch in (BATCH_A, BATCH_B, BATCH_C):
                db.execute("INSERT INTO thematic_batches(id,categories,created_at) VALUES(?,?,?)",
                           (batch, '["protocols"]', "2026-10-19"))
                db.execute("INSERT INTO thematic_items(batch_id,reg,status,updated_at) VALUES(?,?,?,?)",
                           (batch, fixture.REG_A, "completed", "2026-10-19"))
                db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,document,source_url) "
                           "VALUES(?,?,?,?,?,?,?,?)", (batch, fixture.REG_A, "protocols", TITLE,
                           "223-ФЗ, ч. 12 ст. 4", "Протокол подписан 01.09.2026 и размещён 25.09.2026.",
                           "thematic-protocol.pdf", "https://zakupki.gov.ru/thematic/protocol.pdf"))
            db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,document) "
                       "VALUES(?,?,?,?,?,?,?)", (BATCH_A, fixture.REG_A, "national", "Исключённая категория",
                       "223-ФЗ, ч. 12 ст. 4", "Иное доказательство", "unselected.pdf"))

    def get(self, batch=BATCH_A, user=fixture.EMPLOYEE_A, reg=fixture.REG_A):
        return self.f.client.get(f"/me/finding-submissions/{reg}?batch={batch}", headers=self.f.headers(user))

    def submit(self, key, batch=BATCH_A, user=fixture.EMPLOYEE_A):
        return self.f.client.post(f"/me/finding-submissions/{fixture.REG_A}/{key}/submit?batch={batch}",
                                  headers=self.f.headers(user))

    def key(self, batch=BATCH_A):
        response = self.get(batch)
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["findings"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], TITLE)
        return items[0]["finding_key"]

    def test_owner_category_and_membership_are_exact(self):
        key = self.key()
        self.assertEqual(self.get(BATCH_B).status_code, 404)
        self.assertEqual(self.submit(key, BATCH_B).status_code, 404)
        self.assertEqual(self.get(reg=fixture.REG_B).status_code, 404)
        general = self.f.key()
        self.assertNotEqual(general, key)
        self.assertEqual(self.submit(general).status_code, 409)
        excluded = source.canonical_item(fixture.REG_A, {"title": "Исключённая категория",
            "law": "223-ФЗ, ч. 12 ст. 4", "evidence": "Иное доказательство", "document": "unselected.pdf"})
        self.assertEqual(self.submit(excluded["finding_key"]).status_code, 409)
        self.f.assert_no_state()

    def test_thematic_only_evidence_reaches_real_manager_decision_without_private_check(self):
        key = self.key()
        self.assertEqual(self.submit(key).status_code, 200)
        again = self.submit(key).json()
        self.assertTrue(again["already_submitted"])
        self.assertEqual(len(self.f.queue()), 1)
        item = self.f.manager_finding()
        self.assertEqual((item["finding_key"], item["source_batch"]), (key, BATCH_A))
        self.assertEqual(item["document"], "thematic-protocol.pdf")
        approved = self.f.decision(key=key, version=1)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.get().json()["findings"][0]["submission"]["status"], "approved")
        self.assertEqual(self.f.rows("account_manual_checks"), [])
        self.assertEqual(self.f.evidence_rows(), self.f.original_evidence)

    def test_repeat_from_second_owned_batch_preserves_first_provenance_and_approval(self):
        key = self.key()
        self.assertEqual(self.key(BATCH_C), key)
        self.submit(key)
        self.assertEqual(self.f.decision(key=key, version=1).status_code, 200)
        response = self.submit(key, BATCH_C)
        self.assertEqual(response.json(), {"status": "approved", "already_submitted": True})
        with sqlite3.connect(self.f.identity_path) as db:
            self.assertEqual(db.execute("SELECT source_batch,status,version FROM account_finding_submissions").fetchall(),
                             [(BATCH_A, "approved", 1)])

    def test_changed_or_deleted_thematic_source_never_falls_back_to_general_evidence(self):
        key = self.key()
        self.submit(key)
        with sqlite3.connect(self.f.evidence_path) as db:
            row = db.execute("SELECT title,law,evidence,document,source_url FROM thematic_findings "
                             "WHERE batch_id=? AND category='protocols'", (BATCH_A,)).fetchone()
            db.execute("INSERT INTO findings(reg_number,kind,title,law,evidence,document,source_url) VALUES(?,?,?,?,?,?,?)",
                       (fixture.REG_A, "violation", *row))
            db.execute("DELETE FROM thematic_findings WHERE batch_id=?", (BATCH_A,))
        response = self.f.decision(key=key, version=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.f.rows("account_manager_finding_reviews"), [])

    def test_team_reassignment_revokes_former_manager_access(self):
        key = self.key()
        self.submit(key)
        with sqlite3.connect(self.f.identity_path) as db:
            db.execute("UPDATE account_manager_teams SET manager_id=? WHERE user_id=?",
                       (str(fixture.MANAGER_B), str(fixture.EMPLOYEE_A)))
        response = self.f.decision(key=key, version=1)
        self.assertEqual(response.status_code, 404, response.text)

    def test_card_controls_and_print_use_exact_source_and_saved_owner_requisites(self):
        key = self.key()
        headers = self.f.headers(fixture.EMPLOYEE_A)
        card = self.f.client.get(f"/thematic/card/{BATCH_A}/{fixture.REG_A}", headers=headers)
        self.assertEqual(card.status_code, 200, card.text)
        self.assertIn('data-review-batch="' + BATCH_A + '"', card.text)
        self.assertIn('data-finding-review="' + key + '"', card.text)
        self.assertIn("Номер закупки ЕИС: " + fixture.REG_A, card.text)
        self.assertIn("Направить на согласование руководителю", card.text)
        self.assertIn("Реквизиты служебной записки", card.text)
        self.assertNotIn("Исключённая категория", card.text)
        values = {"recipient": "Руководителю Иванову", "executor": "Петров П.П.", "phone": "Тел.: +7 123 456-78-90"}
        saved = self.f.client.put("/me/memo-requisites/" + fixture.REG_A, json=values, headers=headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        printed = self.f.client.get(f"/thematic/card/{BATCH_A}/{fixture.REG_A}?print=1", headers=headers)
        self.assertEqual(printed.status_code, 200, printed.text)
        for value in values.values(): self.assertIn(value, printed.text)
        self.assertIn("memo-executor", printed.text)
        self.assertIn("color:#000", printed.text)
        self.assertEqual(printed.text.count("Тел.:"), 1)
        self.assertNotIn("Исключённая категория", printed.text)
        self.assertNotIn("fixture-protocol.pdf", printed.text)

    def test_long_distinct_proofs_keep_exact_card_keys_and_both_submissions(self):
        prefix = "Обстоятельства подтверждены сохранёнными оригиналами. " * 100
        proofs = [prefix + "Протокол подписан 01.09.2026 и размещён 25.09.2026.",
                  prefix + "Протокол подписан 02.10.2026 и размещён 26.10.2026."]
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute("UPDATE thematic_findings SET evidence=? WHERE batch_id=? AND category='protocols'",
                       (proofs[0], BATCH_A))
            db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,document,source_url) "
                "VALUES(?,?,?,?,?,?,?,?)", (BATCH_A, fixture.REG_A, "protocols", TITLE,
                "223-ФЗ, ч. 12 ст. 4", proofs[1], "second-protocol.pdf",
                "https://zakupki.gov.ru/thematic/second-protocol.pdf"))
        items = self.get().json()["findings"]
        self.assertEqual(len(items), 2)
        self.assertEqual({x["evidence"] for x in items}, set(proofs))
        card = self.f.client.get(f"/thematic/card/{BATCH_A}/{fixture.REG_A}",
                                 headers=self.f.headers(fixture.EMPLOYEE_A))
        self.assertEqual(set(re.findall(r'data-finding-review="([0-9a-f]{64})"', card.text)),
                         {x["finding_key"] for x in items})
        for item in items:
            self.assertEqual(self.submit(item["finding_key"]).status_code, 200)
        self.assertEqual(len(self.f.queue()), 2)

    def test_migration_adds_source_to_existing_submission_table(self):
        from scripts.account_migrations_v1 import initialize_schema

        class RecordingDB:
            def __init__(self): self.statements = []
            def execute(self, sql): self.statements.append(sql); return self
            def fetchone(self): return ("CHECK ('manager','assignment')",)

        db = RecordingDB()
        initialize_schema(db)
        alterations = [sql for sql in db.statements if sql.startswith(
            "ALTER TABLE account_finding_submissions ADD COLUMN IF NOT EXISTS")]
        self.assertEqual(alterations, ["ALTER TABLE account_finding_submissions ADD COLUMN IF NOT EXISTS "
            "source_batch TEXT NOT NULL DEFAULT ''"])


if __name__ == "__main__":
    unittest.main()
