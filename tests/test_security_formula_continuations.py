"""Punctuation must not hide a cap when qualifying bid-security evidence."""
from copy import deepcopy
import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bid_security_requirement_recall import HIGH, evidence, inspect, original
import test_bid_security_requirement_recall as saved_security
import v336_legal_matrix as matrix
from vp_inspection_engine import qualify_outcomes


class SecurityFormulaContinuations(unittest.TestCase):
    def test_kopeck_abbreviation_cannot_hide_parenthetical_amount_modifier(self):
        for suffix in (" (но не более 100000 руб.).", " (предварительная сумма).",
                       "; но не более 100000 рублей.", " но не более 100000 рублей."):
            body = "Обеспечение заявки: 600000 руб. 00 коп." + suffix
            with self.subTest(suffix=suffix):
                findings, checks = inspect(body, 8000000)
                self.assertEqual(findings, [])
                self.assertTrue(any("security_money_amount_unverified" in
                                    item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_semicolon_keeps_money_formula_continuation(self):
        for suffix in ("но не более 100000 рублей", "за вычетом аванса", "или 1% от НМЦД"):
            with self.subTest(suffix=suffix):
                findings, checks = inspect("Обеспечение заявки: 600000 рублей; " + suffix + ".", 8000000)
                self.assertEqual(findings, [])
                self.assertTrue(checks)

    def test_semicolon_keeps_percentage_cap_including_zero_at_low_nmcd(self):
        for price, cap in ((8000000, 100000), (4000000, 0)):
            body = f"Обеспечение заявки: 8% от НМЦД; но не более {cap} рублей."
            with self.subTest(price=price):
                findings, checks = inspect(body, price)
                self.assertEqual(findings, [])
                self.assertTrue(any("security_percent_basis_unverified" in
                                    item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_separate_sentence_and_independent_field_preserve_fixed_requirement(self):
        for requirement in ("600000 руб. 00 коп.", "600000 рублей.", "8% от НМЦД."):
            for suffix in (" Порядок внесения денежных средств установлен ниже.",
                           "\nПорядок предоставления обеспечения заявки | Денежными средствами."):
                with self.subTest(requirement=requirement, suffix=suffix):
                    findings, _ = inspect("Обеспечение заявки: " + requirement + suffix, 8000000)
                    self.assertEqual([item["code"] for item in findings], [HIGH])
        findings, _ = inspect("Обеспечение заявки: 8% от НМЦД; срок внесения установлен ниже.", 8000000)
        self.assertEqual([item["code"] for item in findings], [HIGH])

    def test_capped_peer_after_semicolon_remains_conflicting_evidence(self):
        p, docs = evidence("Обеспечение заявки: 600000 рублей.", 8000000)
        docs.append(original("Обеспечение заявки: 600000 рублей; но не более 100000 рублей.",
                             "Условия обеспечения.docx", 3))
        before = deepcopy((p, docs))
        findings, checks = matrix.security_findings(p, docs)
        self.assertEqual(findings, [])
        self.assertTrue(any("security_conflicting_terms" in
                            item.get("methodology_reason_codes", []) for item in checks))
        self.assertEqual((p, docs), before)

    def test_legacy_confirmed_candidate_rechecks_complete_formula(self):
        for body, reason in (
            ("Обеспечение заявки: 600000 руб. 00 коп. (но не более 100000 руб.).",
             "security_money_amount_unverified"),
            ("Обеспечение заявки: 8% от НМЦД; но не более 100000 рублей.",
             "security_percent_basis_unverified"),
        ):
            p, docs = evidence(body, 8000000)
            candidate = {"code": HIGH, "kind": "violation", "title": "Превышен размер обеспечения",
                         "law": "223-ФЗ, ст. 3.2 ч. 27", "evidence": body,
                         "match_text": matrix.BID_CLAUSE.search(body)[0], "source_position": 0,
                         "document": docs[1]["name"], "source_url": docs[1]["meta"]["source_url"],
                         "security_assessment": {"status": "confirmed"}}
            with self.subTest(body=body):
                findings, checks = qualify_outcomes(p, docs, [candidate])
                self.assertEqual(findings, [])
                self.assertTrue(any(reason in item.get("methodology_reason_codes", []) for item in checks))


class SavedSecurityFormulaContinuations(unittest.TestCase):
    setUp = saved_security.SavedBidSecurityPipeline.setUp
    connect = saved_security.SavedBidSecurityPipeline.connect
    make_xml = saved_security.SavedBidSecurityPipeline.make_xml
    write_archive = saved_security.SavedBidSecurityPipeline.write_archive
    write_original = saved_security.SavedBidSecurityPipeline.write_original
    collect = saved_security.SavedBidSecurityPipeline.collect
    run_original = saved_security.SavedBidSecurityPipeline.run_original

    def test_kopeck_parenthetical_zero_cap_is_review_in_both_saved_audit_paths(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Обеспечение заявки: 100000 руб. 00 коп. (но не более 0 руб.).", 0)

    def test_percentage_semicolon_zero_cap_is_review_in_both_saved_audit_paths(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Обеспечение заявки: 8% от НМЦД; но не более 0 рублей.", 0)

    def test_saved_confirmation_is_reassessed_without_erasing_original_finding(self):
        import payment_finding_reassessment as assessment
        import v332_all_procurements_audit as general
        from legal_audit_revision import engine_signature
        from v352_dashboard_truth import load_counts

        body = ("НМЦД: 4 000 000 рублей.\n"
                "Обеспечение заявки: 100000 руб. 00 коп. (но не более 0 руб.).")
        self.run_original(body, 0)
        reg = saved_security.saved.REG
        doc = self.collect()["docs"][0]
        legacy = {"code": saved_security.LOW, "kind": "violation",
                  "title": "Обеспечение заявки предусмотрено при НМЦД не более 5 млн рублей",
                  "law": "223-ФЗ, ст. 3.2 ч. 27", "evidence": body,
                  "document": doc["name"], "source_url": doc["meta"]["source_url"]}
        with self.connect() as db:
            db.execute("INSERT INTO findings(reg_number,code,kind,title,law,evidence,document,source_url) "
                       "VALUES(?,?,?,?,?,?,?,?)", (reg, *(legacy[key] for key in
                       ("code", "kind", "title", "law", "evidence", "document", "source_url"))))
            event = assessment.prepare_assessment(reg, [legacy], engine_signature=engine_signature(),
                source_version="historical-formula-confirmation", db_token=assessment.db_source_token(db, reg))
            assessment.record_assessment(db, event)
            before = [tuple(row) for row in db.execute("SELECT * FROM findings")]
            self.assertEqual(load_counts(db, [reg]), {reg: 1})
        result = asyncio.run(general.inspect_one(self.ns, reg))
        self.assertEqual((result[0], result[1]["new_findings"]), ("completed", 0))
        with self.connect() as db:
            self.assertEqual(load_counts(db, [reg]), {reg: 0})
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM findings")], before)
            revised = assessment.apply_general_reassessments(db, reg, [legacy])[0]
            self.assertEqual(revised["effective_status"], "requires_review")


if __name__ == "__main__":
    unittest.main()
