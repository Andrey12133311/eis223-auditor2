"""Security requirements retain table boundaries and actual formula amounts."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bid_security_requirement_recall import evidence, inspect, original, HIGH
import test_bid_security_requirement_recall as saved_security
import v336_legal_matrix as matrix
from vp_inspection_engine import qualify_outcomes


class SecurityFieldBoundaries(unittest.TestCase):
    def test_nmcd_percentage_ends_at_explicit_next_table_field(self):
        for next_field in (
            "Порядок предоставления обеспечения заявки: денежными средствами.",
            "Срок предоставления обеспечения заявки: до окончания подачи заявок.",
            "Порядок подачи заявок | в электронной форме.",
            "17 | Адрес электронной площадки | https://example.invalid/",
        ):
            body = "Обеспечение заявки: 8% от НМЦД\n" + next_field
            with self.subTest(next_field=next_field):
                findings, _ = inspect(body, 8000000)
                self.assertEqual([item["code"] for item in findings], [HIGH])
                basis = findings[0]["security_assessment"]["percent_basis"]
                self.assertEqual(basis["excerpt"].strip(), "от НМЦД")
                self.assertEqual(body[basis["start"]:basis["end"]], basis["excerpt"])

    def test_field_boundary_requires_exact_label_and_structural_separator(self):
        for suffix in (
            "\nно не более 100000 рублей.",
            "\nза вычетом аванса.",
            "\nПорядок предоставления обеспечения заявки изменяет размер до 100000 рублей.",
            " Порядок предоставления обеспечения заявки: 100000 рублей.",
            "\nСрок предоставления обеспечения заявки или размер обеспечения: 100000 рублей.",
        ):
            with self.subTest(suffix=suffix):
                findings, checks = inspect("Обеспечение заявки: 8% от НМЦД" + suffix, 8000000)
                self.assertEqual(findings, [])
                self.assertTrue(checks)

    def test_cap_before_next_field_is_not_lost(self):
        body = ("Обеспечение заявки: 8% от НМЦД, но не более 100000 рублей\n"
                "Срок предоставления обеспечения заявки | до окончания подачи заявок.")
        findings, checks = inspect(body, 8000000)
        self.assertEqual(findings, [])
        self.assertTrue(checks)

    def test_actual_docx_table_has_same_legal_result(self):
        from docx import Document
        from v314_eis_metadata import document_text
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Документация.docx"
            doc = Document()
            table = doc.add_table(rows=3, cols=3)
            for row, values in zip(table.rows, (
                ("14", "Размер обеспечения заявки", "8% от НМЦД"),
                ("15", "Порядок предоставления обеспечения заявки", "Денежными средствами"),
                ("16", "Срок предоставления обеспечения заявки", "До окончания подачи заявок"),
            )):
                for cell, value in zip(row.cells, values):
                    cell.text = value
            doc.save(path)
            body = document_text(path, lambda _path: "")
            findings, _ = inspect(body, 8000000)
            self.assertEqual([item["code"] for item in findings], [HIGH])
            self.assertEqual(findings[0]["match_text"], "Размер обеспечения заявки | 8%")


class SecurityMonetaryFormula(unittest.TestCase):
    def test_capped_or_modified_money_is_not_a_fixed_amount(self):
        for suffix in (
            ", но не более 100000 рублей.",
            " или 1% от НМЦД.",
            " за вычетом аванса.",
            " (предварительная сумма).",
            ", уменьшенные на сумму ранее внесенного обеспечения.",
        ):
            with self.subTest(suffix=suffix):
                findings, checks = inspect("Обеспечение заявки: 600000 рублей" + suffix, 8000000)
                self.assertEqual(findings, [])
                self.assertTrue(any("security_money_amount_unverified" in
                                    item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_zero_cap_does_not_prove_positive_security_at_low_nmcd(self):
        findings, checks = inspect("Обеспечение заявки: 100000 рублей, но не более 0 рублей.")
        self.assertEqual(findings, [])
        self.assertTrue(checks)

    def test_unresolved_money_peer_is_not_silently_ignored(self):
        p, docs = evidence("Обеспечение заявки: 600000 рублей.", 8000000)
        docs.append(original("Обеспечение заявки: 600000 рублей, но не более 100000 рублей.",
                             "Условия обеспечения.docx", 3))
        before = deepcopy((p, docs))
        findings, checks = matrix.security_findings(p, docs)
        self.assertEqual(findings, [])
        self.assertTrue(any("security_conflicting_terms" in
                            item.get("security_assessment", {}).get("reasons", []) for item in checks))
        self.assertEqual((p, docs), before)

    def test_money_formula_legacy_candidate_is_reassessed(self):
        body = "Обеспечение заявки: 600000 рублей, но не более 100000 рублей."
        p, docs = evidence(body, 8000000)
        candidate = {"code": HIGH, "kind": "violation", "title": "Превышен размер обеспечения",
                     "law": "223-ФЗ, ст. 3.2 ч. 27", "evidence": body,
                     "match_text": "Обеспечение заявки: 600000 рублей", "source_position": 0,
                     "document": docs[1]["name"], "source_url": docs[1]["meta"]["source_url"],
                     "security_assessment": {"status": "confirmed"}}
        findings, checks = qualify_outcomes(p, docs, [candidate])
        self.assertEqual(findings, [])
        self.assertEqual(len(checks), 1)
        self.assertIn("security_money_amount_unverified", checks[0]["methodology_reason_codes"])

    def test_fixed_money_keeps_confirmation_and_ignores_next_distinct_field(self):
        for body in ("Обеспечение заявки: 600000 рублей.",
                     "Обеспечение заявки: 600000 руб.,\nПорядок подачи заявок | электронный.",
                     "Обеспечение заявки: 600000 рублей\nПорядок предоставления обеспечения заявки | по реквизитам."):
            with self.subTest(body=body):
                self.assertEqual([item["code"] for item in inspect(body, 8000000)[0]], [HIGH])

    def test_explicit_zero_kopecks_preserve_exact_whole_ruble_amount(self):
        for value in ("600000 рублей 00 копеек", "600000,00 рублей 00 копеек",
                      "600000 (шестьсот тысяч) рублей 00 копеек", "600000 руб. 00 коп."):
            with self.subTest(value=value):
                findings, _ = inspect("Обеспечение заявки: " + value + ".", 8000000)
                self.assertEqual([item["code"] for item in findings], [HIGH])

    def test_kopecks_are_not_discarded_or_used_to_hide_amount_modifiers(self):
        for value in ("600000 рублей 01 копейка", "600000,01 рублей 00 копеек",
                      "600000 рублей 00 копеек, но не более 100000 рублей",
                      "600000 руб. 00 коп., но не более 100000 рублей"):
            with self.subTest(value=value):
                findings, checks = inspect("Обеспечение заявки: " + value + ".", 8000000)
                self.assertEqual(findings, [])
                self.assertTrue(checks)

    def test_small_ruble_operand_with_kopecks_still_produces_review(self):
        findings, checks = inspect("Обеспечение заявки: 160000 рублей 01 копейка.", 8000000)
        self.assertEqual(findings, [])
        self.assertTrue(checks)


class SavedSecurityFormulaPipeline(unittest.TestCase):
    setUp = saved_security.SavedBidSecurityPipeline.setUp
    connect = saved_security.SavedBidSecurityPipeline.connect
    make_xml = saved_security.SavedBidSecurityPipeline.make_xml
    write_archive = saved_security.SavedBidSecurityPipeline.write_archive
    write_original = saved_security.SavedBidSecurityPipeline.write_original
    collect = saved_security.SavedBidSecurityPipeline.collect
    run_original = saved_security.SavedBidSecurityPipeline.run_original

    def test_separate_field_preserves_general_and_thematic_confirmed_count(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Обеспечение заявки на участие в аукционе: 1% от НМЦД\n"
                          "Порядок предоставления обеспечения заявки | Денежными средствами.", 1)

    def test_money_cap_does_not_enter_general_or_thematic_confirmed_count(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Обеспечение заявки: 100000 рублей, но не более 0 рублей.", 0)

    def test_exact_zero_kopecks_survive_both_audit_paths(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Обеспечение заявки на участие в аукционе: 100000 рублей 00 копеек.", 1)


if __name__ == "__main__":
    unittest.main()
