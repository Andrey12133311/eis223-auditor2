"""Counter-evidence must qualify a payment claim without choosing an arbitrary 7."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from payment_terms import assess_payment_term, collect_payment_terms, detect_payment_coverage


P = {"reg": "32610002001", "method": "Открытый конкурс только для субъектов малого и среднего предпринимательства"}
LONG = "Оплата производится в течение 30 рабочих дней со дня подписания сторонами акта приемки."
SHORT = "Оплата производится в течение 7 рабочих дней со дня подписания сторонами акта приемки."


def doc(text, name="Проект договора.docx", *, uid="A", **meta):
    url = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + uid * 32
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "uid": uid * 32, "sha256": hashlib.sha256(text.encode()).hexdigest(), **meta}}


def assess(source, *peers, purchase=None):
    originals = [source, *peers]
    terms = collect_payment_terms(originals)
    term = next(item for item in terms if item["doc"] is source and item["days"] > 7)
    return assess_payment_term(purchase or P, originals, source,
                               term["start"], term["end"], term["days"], terms=terms)


class PaymentTermsTests(unittest.TestCase):
    def test_single_unambiguous_long_term_remains_assessable(self):
        result = assess(doc(LONG))
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["phase"], "tender_terms")
        self.assertEqual(result["source"]["clock"], "acceptance")
        self.assertEqual(result["source"]["days"], 30)

    def test_different_documents_require_source_comparison(self):
        result = assess(doc(LONG), doc(SHORT, "Договор.docx", uid="B"))
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_conflicting_terms", result["reasons"])
        self.assertIn("payment_relation_unresolved", result["reasons"])
        self.assertEqual(result["related_terms"][0]["days"], 7)
        self.assertEqual(result["related_terms"][0]["sha256"], hashlib.sha256(SHORT.encode()).hexdigest())
        json.dumps(result, ensure_ascii=False)

    def test_signed_contract_does_not_erase_project_history(self):
        signed = doc("ДОГОВОР № 24\nДата заключения договора: 06.11.2026\n" + SHORT,
                     "Договор.docx", uid="B")
        result = assess(doc(LONG), signed)
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_cross_phase_terms", result["reasons"])
        self.assertEqual(result["related_terms"][0]["phase"], "executed_contract")
        self.assertNotIn("payment_explicit_replacement", result["reasons"])

    def test_signed_filename_is_not_execution_proof(self):
        result = assess(doc(LONG), doc(SHORT, "Подписанный договор 2026.docx", uid="B"))
        self.assertEqual(result["related_terms"][0]["phase"], "contract_terms")
        self.assertNotIn("payment_cross_phase_terms", result["reasons"])

    def test_different_lots_do_not_cancel_real_violation(self):
        for sources in (
            (doc(LONG, lot_number="1"), doc(SHORT, lot_number="2", uid="B")),
            (doc("Лот № 1\n" + LONG), doc("Лот № 2\n" + SHORT, uid="B")),
            (doc("Лот № 1\n" + LONG + "\nЛот № 2\n" + SHORT),),
        ):
            with self.subTest(sources=sources):
                result = assess(*sources)
                self.assertEqual(result["status"], "confirmed")
                self.assertEqual(result["related_terms"], [])

    def test_different_contracts_and_procurements_are_not_substitutes(self):
        for source, other in (
            (doc(LONG, contract_number="24"), doc(SHORT, contract_number="25", uid="B")),
            (doc("ДОГОВОР № 24\n" + LONG), doc("ДОГОВОР № 25\n" + SHORT, uid="B")),
            (doc(LONG), doc(SHORT, purchase_reg_number="32619999999", uid="B")),
        ):
            with self.subTest(other=other):
                self.assertEqual(assess(source, other)["status"], "confirmed")

    def test_source_itself_cannot_be_from_other_purchase_or_lot(self):
        self.assertEqual(assess(doc(LONG, purchase_reg_number="32619999999"))["status"], "inactive")
        result = assess(doc(LONG, lot_number="2"), purchase={**P, "lot_number": "1"})
        self.assertIn("payment_source_other_lot", result["reasons"])
        self.assertEqual(result["status"], "inactive")

    def test_unverified_and_example_seven_day_terms_cannot_suppress(self):
        for other in (
            doc(SHORT, uid="B", source="uploaded_document"),
            doc("Например, " + SHORT.lower(), uid="B"),
            doc("Пунктом 28 Постановления № 1352 установлено: " + SHORT, uid="B"),
            doc(SHORT, "Образец договора.docx", uid="B"),
            doc(SHORT, uid="B", version_status="superseded"),
        ):
            with self.subTest(other=other):
                self.assertEqual(assess(doc(LONG), other)["status"], "confirmed")

    def test_unrelated_acceptance_delivery_invoice_and_advance_do_not_suppress(self):
        for clause in (
            "Оплата производится в течение 7 рабочих дней с даты счета. После подписания акта приемки готовят отчет.",
            "Срок поставки: 7 рабочих дней со дня подписания акта приемки.",
            "Авансовая оплата производится в течение 7 рабочих дней со дня подписания акта приемки.",
            "Оплата производится в течение 7 рабочих дней, после подписания акта приемки заказчик составляет отчет.",
            "Срок оплаты: 7 (тридцати) рабочих дней со дня подписания акта приемки.",
        ):
            with self.subTest(clause=clause):
                self.assertEqual(assess(doc(LONG), doc(clause, uid="B"))["status"], "confirmed")

    def test_alternative_variants_are_not_arbitrarily_selected(self):
        text = ("Оплата выполненных работ осуществляется в следующем порядке:\n"
                "(редакция 1) " + LONG + "\n(редакция 2) " + SHORT)
        result = assess(doc(text))
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_alternative_unselected", result["reasons"])
        self.assertEqual(result["source"]["variant"], "1")
        self.assertEqual(result["related_terms"][0]["variant"], "2")

    def test_alternative_markers_without_readable_second_term_still_require_review(self):
        result = assess(doc("(редакция 1) " + LONG + "\n(редакция 2) [формула оплаты не распознана]"))
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_alternative_unselected", result["reasons"])

    def test_variant_heading_inside_wide_payment_match_is_not_lost(self):
        source = doc("Оплата выполненных по договору работ осуществляется в следующем порядке:\n"
                     "(редакция 1) Заказчик оплачивает подрядчику стоимость работ в течение 30 рабочих дней "
                     "со дня подписания сторонами акта приемки.\n"
                     "(редакция 2) [не удалось прочитать второе условие]")
        result = assess(source)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["source"]["variant"], "1")
        self.assertIn("payment_alternative_unselected", result["reasons"])

    def test_one_revision_label_and_filename_dates_do_not_establish_replacement(self):
        result = assess(doc("Редакция 1\n" + LONG))
        self.assertEqual(result["status"], "confirmed")
        result = assess(doc(LONG, "Проект договора редакция 1 01.11.2026.docx"),
                        doc(SHORT, "Проект договора редакция 2 06.11.2026.docx", uid="B"))
        self.assertEqual(result["status"], "review")
        self.assertNotIn("payment_explicit_replacement", result["reasons"])

    def test_explicit_selected_variant_is_preserved_as_history(self):
        result = assess(doc("Применяется редакция 2.\n(редакция 1) " + LONG + "\n(редакция 2) " + SHORT))
        self.assertEqual(result["status"], "inactive")
        self.assertIn("payment_alternative_not_selected", result["reasons"])

    def test_unselected_seven_day_variant_cannot_challenge_selected_long_term(self):
        for prefix in ("", "Для субъектов МСП "):
            result = assess(doc("Применяется редакция 1.\n(редакция 1) " + LONG
                                + "\n(редакция 2) " + prefix + SHORT.lower()))
            self.assertEqual(result["status"], "confirmed")
            self.assertEqual(result["related_terms"], [])

    def test_variant_selection_in_another_lot_or_contract_does_not_apply(self):
        for heading1, heading2 in (("Лот № 1", "Лот № 2"), ("ДОГОВОР № 1", "ДОГОВОР № 2")):
            result = assess(doc(heading1 + "\n(редакция 1) " + LONG + "\n" + heading2
                                + "\nПрименяется редакция 2.\n(редакция 2) " + SHORT))
            self.assertEqual(result["status"], "confirmed")
            self.assertNotIn("payment_alternative_not_selected", result["reasons"])

    def test_variant_selection_in_security_section_does_not_apply_to_payment(self):
        text = ("4. Порядок расчетов\n(редакция 1) " + LONG
                + "\n5. Обеспечение исполнения договора\nПрименяется редакция 2.\n"
                "(редакция 1) Обеспечение не требуется.\n"
                "(редакция 2) Обеспечение предоставляется в форме независимой гарантии.")
        result = assess(doc(text))
        self.assertEqual(result["status"], "confirmed")
        self.assertNotIn("payment_alternative_not_selected", result["reasons"])

    def test_prohibited_example_does_not_establish_current_thirty_day_condition(self):
        source = doc("Применение условия «" + LONG[:-1].lower() + "» не допускается.")
        result = assess(source)
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_negated_or_proposed_clause", result["reasons"])

    def test_special_sme_clause_in_same_document_limits_general_term(self):
        result = assess(doc(LONG + "\nЕсли поставщик является субъектом МСП, " + SHORT.lower()))
        self.assertEqual(result["status"], "inactive")
        self.assertIn("payment_specific_sme_clause", result["reasons"])
        self.assertEqual(result["related_terms"][0]["scope"], "sme")

    def test_specific_sme_clause_requires_independent_scope_and_same_document(self):
        text = LONG + "\nЕсли поставщик является субъектом МСП, " + SHORT.lower()
        result = assess(doc(text), purchase={"reg": P["reg"], "method": "Открытый конкурс"})
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_sme_scope_unresolved", result["reasons"])
        result = assess(doc(LONG), doc("Для субъектов МСП " + SHORT.lower(), uid="B"))
        self.assertEqual(result["status"], "review")
        self.assertNotIn("payment_specific_sme_clause", result["reasons"])

    def test_explicit_non_sme_variant_is_not_applied_to_sme_only_purchase(self):
        text = ("Если подрядчик не является субъектом МСП, " + LONG.lower() + "\n"
                "Если подрядчик является субъектом МСП, " + SHORT.lower())
        result = assess(doc(text))
        self.assertEqual(result["status"], "inactive")
        self.assertIn("payment_non_sme_clause", result["reasons"])

    def test_subcontractor_term_does_not_replace_contractor_term(self):
        result = assess(doc(LONG), doc("Для субподрядчика, являющегося субъектом МСП, " + SHORT.lower(), uid="B"))
        self.assertEqual(result["status"], "confirmed")

    def test_negated_inactive_or_proposed_seven_days_cannot_override(self):
        for clause in (
            "Для субъектов МСП оплата не производится в течение 7 рабочих дней со дня подписания акта приемки.",
            "Для субъектов МСП срок оплаты: 7 рабочих дней со дня подписания акта приемки не применяется.",
            "Для субъектов МСП предлагаемая оплата производится в течение 7 рабочих дней со дня подписания акта приемки.",
            "Если будет подписано дополнительное соглашение, для субъектов МСП " + SHORT.lower(),
        ):
            with self.subTest(clause=clause):
                result = assess(doc(LONG + "\n" + clause))
                self.assertEqual(result["status"], "confirmed")
                self.assertNotIn("payment_specific_sme_clause", result["reasons"])

    def test_other_stage_installment_and_contract_do_not_override(self):
        for long_clause, short_clause in (
            ("Окончательный расчет производится в течение 30 рабочих дней со дня подписания акта приемки.",
             "Для субъектов МСП оплата первого этапа производится в течение 7 рабочих дней со дня подписания акта приемки."),
            ("Оплата второго этапа производится в течение 30 рабочих дней со дня подписания акта приемки.",
             "Для субъектов МСП оплата этапа № 1 производится в течение 7 рабочих дней со дня подписания акта приемки."),
            ("Оплата второго платежа производится в течение 30 рабочих дней со дня подписания акта приемки.",
             "Для субъектов МСП оплата первого платежа производится в течение 7 рабочих дней со дня подписания акта приемки."),
            ("ДОГОВОР № 1\n" + LONG, "ДОГОВОР № 2\nДля субъектов МСП " + SHORT.lower()),
        ):
            with self.subTest(long_clause=long_clause):
                result = assess(doc(long_clause + "\n" + short_clause))
                self.assertEqual(result["status"], "confirmed")
                self.assertEqual(result["related_terms"], [])

    def test_uncertain_payment_obligation_and_other_conditions_require_review(self):
        result = assess(doc("Окончательный расчет производится в течение 30 рабочих дней со дня подписания акта приемки.\n"
                            "Для субъектов МСП " + SHORT.lower()))
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_obligation_unresolved", result["reasons"])
        result = assess(doc(LONG + "\nДля субъектов МСП при наличии финансирования " + SHORT.lower()))
        self.assertEqual(result["status"], "review")
        self.assertNotIn("payment_specific_sme_clause", result["reasons"])

    def test_sme_term_with_price_membership_or_other_unproved_premise_is_not_override(self):
        for condition in (
            "Если поставщик является субъектом МСП и цена договора не превышает 100000 рублей, ",
            "Для субъектов МСП, включенных в программу партнерства заказчика, ",
            "Если поставщик является субъектом МСП и выполняет дополнительное требование, ",
        ):
            with self.subTest(condition=condition):
                result = assess(doc(LONG + "\n" + condition + SHORT.lower()), purchase={**P, "nmcd": 45341787.84})
                self.assertEqual(result["status"], "review")
                self.assertNotIn("payment_specific_sme_clause", result["reasons"])

    def test_seven_day_upper_bound_is_counterevidence_but_lower_bound_is_not(self):
        for bound in ("не должен превышать", "не может превышать", "не превышает"):
            result = assess(doc(LONG + "\nДля субъектов МСП срок оплаты " + bound
                                + " 7 рабочих дней со дня подписания акта приемки."))
            self.assertEqual(result["status"], "inactive")
            self.assertIn("payment_specific_sme_clause", result["reasons"])
        result = assess(doc(LONG + "\nДля субъектов МСП оплата производится не ранее 7 рабочих дней "
                            "со дня подписания акта приемки."))
        self.assertEqual(result["status"], "confirmed")

    def test_comma_separated_conditions_keep_own_scope(self):
        text = ("Если поставщик не является субъектом МСП, " + LONG.lower().rstrip(".")
                + ", если поставщик является субъектом МСП, " + SHORT.lower())
        result = assess(doc(text))
        self.assertEqual(result["status"], "inactive")
        self.assertIn("payment_non_sme_clause", result["reasons"])
        self.assertEqual(result["related_terms"][0]["scope"], "sme")

    def test_word_only_and_payer_verb_alternative_terms_are_compared(self):
        for clause in (
            "Заказчик оплачивает Подрядчику стоимость работ в течение 7 рабочих дней со дня подписания сторонами акта приемки.",
            "Оплата производится в течение семи рабочих дней со дня подписания акта приемки.",
            "Оплата производится не более 7 (семи) рабочих дней со дня подписания акта приемки.",
        ):
            with self.subTest(clause=clause):
                result = assess(doc(LONG), doc(clause, uid="B"))
                self.assertEqual(result["status"], "review")
                self.assertEqual(result["related_terms"][0]["days"], 7)

    def test_calendar_and_working_units_are_retained(self):
        result = assess(doc(LONG), doc(SHORT.replace("рабочих", "календарных"), uid="B"))
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["source"]["day_type"], "working")
        self.assertEqual(result["related_terms"][0]["day_type"], "calendar")

    def test_only_explicit_current_same_phase_replacement_can_retire_source(self):
        old = doc(LONG, document_id="old", lot_number="1")
        current = doc(SHORT, uid="B", document_id="new", lot_number="1", version_status="current", replaces_document_id="old")
        result = assess(old, current)
        self.assertEqual(result["status"], "inactive")
        self.assertIn("payment_explicit_replacement", result["reasons"])
        for change in ({"lot_number": "2"}, {"lot_number": ""}, {"version_status": "unspecified"},
                       {"replaces_document_id": "unrelated"}, {"contract_stage": "signed"}):
            with self.subTest(change=change):
                other = deepcopy(current)
                other["meta"].update(change)
                result = assess(old, other)
                self.assertNotIn("payment_explicit_replacement", result["reasons"])

    def test_archive_container_reference_is_not_member_replacement(self):
        old = doc(LONG, source="official_eis_archive_member", archive_member="Договор.docx",
                  archive_sha256="a" * 64)
        current = doc(SHORT, source="official_eis_archive_member", archive_member="Другой договор.docx",
                      archive_sha256="a" * 64, version_status="current", supersedes_uid="A" * 32)
        result = assess(old, current)
        self.assertEqual(result["status"], "review")
        self.assertNotIn("payment_explicit_replacement", result["reasons"])
        current["meta"]["supersedes_sha256"] = old["meta"]["sha256"]
        self.assertIn("payment_explicit_replacement", assess(old, current)["reasons"])

    def test_same_stage_with_different_payment_labels_still_needs_comparison(self):
        source = doc("Окончательный расчет за первый этап производится в течение 30 рабочих дней "
                     "со дня подписания акта приемки.\nДля субъектов МСП оплата первого этапа "
                     "производится в течение 7 рабочих дней со дня подписания акта приемки.")
        result = assess(source)
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_obligation_unresolved", result["reasons"])

    def test_source_superseded_status_is_not_current_violation(self):
        result = assess(doc(LONG, version_status="superseded"))
        self.assertEqual(result["status"], "inactive")
        self.assertEqual(result["source"]["excerpt"], LONG[:-1])
        self.assertIn("payment_historical_source", result["reasons"])

    def test_incomplete_sources_block_unqualified_confirmation(self):
        for purchase in ({**P, "source_incomplete": True}, {**P, "documents_unreadable": 1},
                         {**P, "original_expected": 2, "original_received": 1}):
            result = assess(doc(LONG), purchase=purchase)
            self.assertEqual(result["status"], "review")
            self.assertIn("payment_incomplete_originals", result["reasons"])
        other = doc("", "Заключенный договор.pdf", uid="B")
        other["status"] = "downloaded_no_text"
        result = assess(doc(LONG), other)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["coverage"]["documents"][0]["document"], other["name"])

    def test_processed_archive_and_other_purchase_do_not_create_false_missing_input(self):
        archive = doc("", "Документация.7z", uid="B", source="official_eis_archive")
        archive["status"] = "archive_processed"
        self.assertEqual(assess(doc(LONG), archive)["status"], "confirmed")
        archive["meta"]["archive_warnings"] = ["Документ не прочитан"]
        self.assertEqual(assess(doc(LONG), archive)["status"], "review")
        archive["meta"]["purchase_reg_number"] = "32619999999"
        self.assertEqual(assess(doc(LONG), archive)["status"], "confirmed")

    def test_source_and_archive_member_identity_and_offsets_remain_exact(self):
        source = doc("Преамбула.\n" + LONG, "Пакет.7z / Договор.docx", source="official_eis_archive_member",
                     archive_member="Документация/Договор.docx", archive_sha256="f" * 64)
        original = deepcopy(source)
        result = assess(source)
        self.assertEqual(source, original)
        self.assertEqual(result["source"]["start"], len("Преамбула.\n"))
        self.assertEqual(result["source"]["archive_member"], "Документация/Договор.docx")
        self.assertEqual(result["source"]["archive_sha256"], "f" * 64)
        json.dumps(result)

    def test_invalid_and_unverified_source_never_confirm(self):
        source = doc(LONG)
        result = assess_payment_term(P, [source], source, 5000, 5001, 30)
        self.assertEqual(result["status"], "review")
        source["meta"]["source"] = "uploaded_document"
        result = assess_payment_term(P, [source], source, 0, LONG.index(" со дня"), 30)
        self.assertEqual(result["status"], "review")
        self.assertIn("payment_source_unverified", result["reasons"])

    def test_unknown_completeness_is_not_invented_as_done(self):
        self.assertIsNone(detect_payment_coverage(P, [doc(LONG)])["complete"])
        complete = {**P, "source_incomplete": False, "original_expected": 1, "original_received": 1}
        self.assertIs(detect_payment_coverage(complete, [doc(LONG)])["complete"], True)


if __name__ == "__main__":
    unittest.main()
