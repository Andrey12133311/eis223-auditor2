"""The account case uses the current payment verdict without losing old proof."""
from contextlib import contextmanager
import unittest
from unittest.mock import patch

import payment_finding_reassessment as reassessment
from scripts.v368_manager_evidence import _legal_items
from scripts.v369_case_report_selftest import REG, fixture
from scripts.v369_evidence_case import _case_snapshot


ENGINE = "payment-case-view-test-engine"
PAYMENT = {
    "kind": "violation", "code": "1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS",
    "title": "Установлен срок оплаты свыше семи рабочих дней",
    "law": "Постановление Правительства РФ № 1352, Положение, п. 28",
    "evidence": "Оплата производится в течение 30 рабочих дней со дня подписания акта приемки.",
    "document": "Документация.7z / проект-договора.docx",
    "source_url": "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "1" * 32,
    "law_url": "https://www.consultant.ru/document/cons_doc_LAW_172109/6d4f89e8690d09fef93b1c64f4541294c780d052/",
}


class PaymentCaseViewTests(unittest.TestCase):
    def setUp(self):
        directory, self.ns = fixture()
        self.addCleanup(directory.cleanup)
        engine = patch.object(reassessment, "_engine", return_value=ENGINE)
        engine.start()
        self.addCleanup(engine.stop)
        with self.connection() as db:
            db.execute(
                "INSERT INTO findings(reg_number,kind,title,law,evidence,document,source_url,code,law_url) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (REG, *(PAYMENT[key] for key in
                        ("kind", "title", "law", "evidence", "document", "source_url", "code", "law_url"))),
            )

    @contextmanager
    def connection(self):
        db = self.ns["conn"]()
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, findings):
        with self.connection() as db:
            event = reassessment.prepare_assessment(
                REG, findings, engine_signature=ENGINE,
                source_version="verified-originals-30-and-7",
                db_token=reassessment.db_source_token(db, REG),
            )
            reassessment.record_assessment(db, event)

    def originals(self):
        with self.connection() as db:
            return {table: [tuple(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")]
                    for table in ("findings", "checks", "docs")}

    def assert_preserved(self, entry, canonical):
        for key in ("finding_key", "title", "law", "evidence", "document", "source_url",
                    "warnings", "source_representations", "effective_status", "payment_reassessment"):
            self.assertEqual(entry[key], canonical[key], key)
        self.assertEqual(entry["consultant_references"], canonical["references"][:4])
        self.assertEqual(entry["supporting_sources"], canonical["supporting_sources"])

    def test_legacy_payment_is_review_with_its_sources_and_stable_key(self):
        before = self.originals()
        canonical = next(item for item in _legal_items(self.ns, REG)
                         if item.get("payment_reassessment"))
        report = _case_snapshot(self.ns, REG)
        self.assertEqual(report["findings_count"], 1)  # Unrelated change-publication event.
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual(report["original_findings_records"], 3)
        self.assertEqual(report["duplicates_grouped"], 1)  # Only the actual duplicate.
        self.assertEqual(len(report["review_items"]), 1)
        current = report["review_items"][0]
        self.assertEqual(current["effective_status"], "requires_review")
        self.assertTrue(current["warnings"])
        self.assertTrue(current["payment_reassessment"]["stale"])
        self.assert_preserved(current, canonical)
        self.assertEqual(len(report["comparisons"]), 1)
        self.assertEqual(_case_snapshot(self.ns, REG), report)
        self.assertEqual(self.originals(), before)

    def test_reconfirmed_then_revised_payment_moves_without_changing_evidence(self):
        self.record([PAYMENT])
        before = self.originals()
        confirmed = _case_snapshot(self.ns, REG)
        self.assertEqual(confirmed["findings_count"], 2)
        self.assertEqual(confirmed["review_items"], [])
        original = next(item for item in confirmed["findings"]
                        if item.get("payment_reassessment"))
        self.assertEqual(original["effective_status"], "confirmed")
        self.record([])
        report = _case_snapshot(self.ns, REG)
        self.assertEqual(report["findings_count"], 1)
        self.assertEqual(report["original_findings_records"], 3)
        self.assertEqual(report["duplicates_grouped"], 1)
        self.assertEqual(len(report["review_items"]), 1)
        revised = report["review_items"][0]
        for key in ("finding_key", "title", "law", "evidence", "document", "source_url"):
            self.assertEqual(revised[key], original[key], key)
        self.assertEqual(revised["effective_status"], "requires_review")
        self.assertFalse(revised["payment_reassessment"]["stale"])
        self.assertIn("не подтверждён", revised["payment_reassessment"]["reason"])
        canonical = next(item for item in _legal_items(self.ns, REG)
                         if item.get("payment_reassessment"))
        self.assert_preserved(revised, canonical)
        self.assertEqual(self.originals(), before)

    def test_revised_duplicate_payment_counts_as_one_review_and_one_real_duplicate(self):
        with self.connection() as db:
            db.execute(
                "INSERT INTO checks(reg_number,status,item,law,evidence,document,source_url,field_path,law_url) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (REG, "violation", *(PAYMENT[key] for key in
                     ("title", "law", "evidence", "document", "source_url", "code", "law_url"))),
            )
        self.record([])
        before = self.originals()
        report = _case_snapshot(self.ns, REG)
        self.assertEqual(report["findings_count"], 1)
        self.assertEqual(report["original_findings_records"], 4)
        self.assertEqual(report["duplicates_grouped"], 2)
        self.assertEqual(len(report["review_items"]), 1)
        self.assertEqual(report["review_items"][0]["source_representations"], 2)
        canonical = next(item for item in _legal_items(self.ns, REG)
                         if item.get("payment_reassessment"))
        self.assert_preserved(report["review_items"][0], canonical)
        self.assertEqual(self.originals(), before)


if __name__ == "__main__":
    unittest.main()
