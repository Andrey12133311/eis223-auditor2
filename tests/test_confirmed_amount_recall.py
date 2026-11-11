"""Recover explicit established amounts without relaxing legal evidence gates.

All facts are synthetic. Saved-path regressions use actual DOCX bytes, SHA
receipts and SOAP ZIP/XML, then the ordinary and thematic audit pipelines.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_bid_security_requirement_recall as security_saved
import test_sme_payment_clause_recall as payment_saved
import v336_legal_matrix as matrix
import v341_precise_legal as legal
from vp_inspection_engine import qualify_outcomes


class ConfirmedAmountRecall(unittest.TestCase):
    def security(self, body, price=4000000, *, metadata=None, purchase=None):
        p, docs = security_saved.evidence(body, price, **(purchase or {}))
        docs[1]["meta"].update(metadata or {})
        before = deepcopy((p, docs))
        findings, checks = matrix.security_findings(p, docs)
        result = qualify_outcomes(p, docs, findings, checks)
        self.assertEqual((p, docs), before)
        return result

    def payment(self, body, *, metadata=None, purchase=None):
        p = {"reg": "32610002001", "method": payment_saved.EXCLUSIVE,
             "published": "2026-10-01", "submission_end": "2026-10-30",
             "source_incomplete": False, "original_expected": 1, "original_received": 1,
             **(purchase or {})}
        doc = {"name": "Проект договора.docx", "text": body, "status": "checked",
               "url": payment_saved.URL,
               "meta": {"source": "official_eis_original", "source_url": payment_saved.URL,
                        "sha256": hashlib.sha256(body.encode()).hexdigest(), **(metadata or {})}}
        docs = [doc]
        before = deepcopy((p, docs))
        findings, checks = legal.evaluate(p, docs)
        result = qualify_outcomes(p, docs, findings, checks)
        self.assertEqual((p, docs), before)
        return result

    def test_equal_bid_amount_and_percent_keep_their_verified_price_basis(self):
        for body, price, expected in (
            ("Размер обеспечения заявки равен 100 000 руб.", 4000000, security_saved.LOW),
            ("Размер обеспечения заявки равен 8% от НМЦД.", 8000000, security_saved.HIGH),
        ):
            with self.subTest(body=body):
                findings, checks = self.security(body, price)
                self.assertEqual([item["code"] for item in findings], [expected])
                self.assertEqual(checks, [])
                self.assertEqual(findings[0]["source_position"], 0)
                self.assertTrue(findings[0]["match_text"].startswith("Размер обеспечения заявки равен "))
                self.assertEqual(body[:len(findings[0]["match_text"])], findings[0]["match_text"])
                self.assertEqual(findings[0]["security_assessment"]["status"], "confirmed")
                self.assertEqual(findings[0]["source_url"], security_saved.URL + f"{2:032X}")

    def test_equal_bid_formula_caps_and_other_bases_stay_review(self):
        for body, price, reason in (
            ("Размер обеспечения заявки равен 8% от цены участника.", 8000000,
             "security_percent_basis_unverified"),
            ("Размер обеспечения заявки равен 8% от НМЦД; но не более 0 рублей.", 4000000,
             "security_percent_basis_unverified"),
            ("Размер обеспечения заявки равен 8% от НМЦД; но не более 100000 рублей.", 8000000,
             "security_percent_basis_unverified"),
            ("Размер обеспечения заявки равен 600000 руб. (но не более 100000 рублей).", 8000000,
             "security_money_amount_unverified"),
        ):
            with self.subTest(body=body):
                findings, checks = self.security(body, price)
                self.assertEqual(findings, [])
                self.assertTrue(any(reason in item.get("security_assessment", {}).get("reasons", [])
                                    for item in checks))

    def test_equal_bid_requirement_still_needs_current_own_original(self):
        body = "Размер обеспечения заявки равен 100000 руб."
        for metadata, purchase in (
            ({"source": "local_unverified"}, {}),
            ({"superseded": True}, {}),
            ({"cancelled": True}, {}),
            ({"reg_number": "32619999999"}, {}),
            ({"lot_number": "2"}, {"lot_number": "1"}),
        ):
            with self.subTest(metadata=metadata):
                self.assertEqual(self.security(body, metadata=metadata, purchase=purchase)[0], [])
        for text in (
            "Пример: " + body,
            "Если размер обеспечения заявки равен 100000 руб., средства перечисляются на счет.",
            "Размер обеспечения заявки равен\nОбеспечение исполнения договора: 100000 руб.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.security(text)[0], [])

    def test_explicit_payment_upper_bounds_reach_the_existing_qualification(self):
        for body in (
            "Срок оплаты не превышает 15 рабочих дней со дня подписания акта приемки.",
            "Срок оплаты составляет не более 15 рабочих дней со дня подписания акта приемки.",
            "Оплата производится не более 15 рабочих дней со дня подписания акта приемки.",
        ):
            with self.subTest(body=body):
                findings, checks = self.payment(body)
                self.assertEqual([item["code"] for item in findings], [payment_saved.CODE])
                self.assertEqual(checks, [])
                self.assertEqual(findings[0]["payment_assessment"]["status"], "confirmed")
                self.assertEqual(findings[0]["source_url"], payment_saved.URL)
                self.assertIn("15 рабочих дней", findings[0]["match_text"])

    def test_upper_bound_payment_still_needs_own_acceptance_clock_and_obligation(self):
        term = "Срок оплаты не превышает 15 рабочих дней со дня подписания акта приемки."
        for text in (
            term.replace("15", "7"),
            term.replace("рабочих", "календарных"),
            term.replace("подписания акта приемки", "получения счета"),
            "Например, " + term,
            "Следующее условие исключено: " + term,
            "Предлагаемый " + term.lower(),
            "Авансовая оплата: " + term,
            term.replace("15", "15 (семи)"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.payment(text)[0], [])

    def test_upper_bound_payment_keeps_scope_revision_and_coverage_gates(self):
        term = "Срок оплаты не превышает 15 рабочих дней со дня подписания акта приемки."
        for metadata, purchase in (
            ({"source": "uploaded_document"}, {}),
            ({"superseded": True}, {}),
            ({"reg_number": "32619999999"}, {}),
            ({"lot_number": "2"}, {"lot_number": "1"}),
            ({}, {"method": "Открытый конкурс"}),
            ({}, {"source_incomplete": True, "original_received": 0}),
        ):
            with self.subTest(metadata=metadata, purchase=purchase):
                self.assertEqual(self.payment(term, metadata=metadata, purchase=purchase)[0], [])
        findings, checks = self.payment(term + " Оплата производится в течение 7 рабочих дней "
                                      "со дня подписания акта приемки.")
        self.assertEqual(findings, [])
        self.assertTrue(any("payment_conflicting_terms" in item.get("methodology_reason_codes", [])
                            for item in checks))


class EqualBidAmountOriginalPipeline(unittest.TestCase):
    setUp = security_saved.SavedBidSecurityPipeline.setUp
    connect = security_saved.SavedBidSecurityPipeline.connect
    make_xml = security_saved.SavedBidSecurityPipeline.make_xml
    write_archive = security_saved.SavedBidSecurityPipeline.write_archive
    write_original = security_saved.SavedBidSecurityPipeline.write_original
    collect = security_saved.SavedBidSecurityPipeline.collect
    run_original = security_saved.SavedBidSecurityPipeline.run_original

    def test_equality_survives_receipt_ingress_and_both_audits_once(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Размер обеспечения заявки на участие в аукционе равен 100 000 руб.", 1)

    def test_zero_cap_stays_unconfirmed_in_both_saved_audits(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Размер обеспечения заявки на участие в аукционе равен 8% от НМЦД; "
                          "но не более 0 рублей.", 0)


class UpperBoundPaymentOriginalPipeline(unittest.TestCase):
    setUp = payment_saved.SmePaymentOriginalPipeline.setUp
    connect = payment_saved.SmePaymentOriginalPipeline.connect
    make_xml = payment_saved.SmePaymentOriginalPipeline.make_xml
    write_archive = payment_saved.SmePaymentOriginalPipeline.write_archive
    write_original = payment_saved.SmePaymentOriginalPipeline.write_original
    collect = payment_saved.SmePaymentOriginalPipeline.collect
    prepare_contract = payment_saved.SmePaymentOriginalPipeline.prepare_contract
    assert_both_paths = payment_saved.SmePaymentOriginalPipeline.assert_both_paths

    def test_upper_bound_survives_receipt_ingress_and_both_audits_once(self):
        self.prepare_contract("Срок оплаты не превышает 15 рабочих дней со дня "
                              "подписания акта приемки.")
        self.assert_both_paths(1)

    def test_upper_bound_from_invoice_stays_unconfirmed_in_both_saved_audits(self):
        self.prepare_contract("Срок оплаты не превышает 15 рабочих дней со дня получения счета. "
                              "После подписания акта приемки заказчик составляет отчет.")
        self.assert_both_paths(0)


if __name__ == "__main__":
    unittest.main()
