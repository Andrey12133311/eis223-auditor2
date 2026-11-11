"""Source-exact contract-condition controls; no finding of actual signature."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from contract_conclusion_terms import (
    CODE, LAW, assess_finding, evaluate, finding_excerpt, install, prepare_assessment,
)

URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
P = {"reg": "32619000221", "method": "Открытый конкурс в электронной форме",
     "published": "2026-10-01", "source_incomplete": False,
     "original_expected": 1, "original_received": 1}


def clause(low=5, high=20, unit=""):
    return (f"Договор заключается не ранее чем через {low} {unit}дней и не позднее чем через "
            f"{high} {unit}дней с даты размещения в ЕИС итогового протокола.")


def original(body=None, name="Документация закупки.docx", uid="A", **metadata):
    body = clause() if body is None else body
    return {"name": name, "text": body, "status": "checked", "url": URL + uid*32,
            "meta": {"source": "official_eis_original", "source_url": URL + uid*32,
                     "sha256": hashlib.sha256(body.encode()).hexdigest(), **metadata}}


class ContractConclusionTerms(unittest.TestCase):
    def run_rule(self, body=None, *, purchase=None, name="Документация закупки.docx", metadata=None):
        p, docs = {**P, **(purchase or {})}, [original(body, name, **(metadata or {}))]
        saved = deepcopy((p, docs))
        result = evaluate(p, docs)
        self.assertEqual((p, docs), saved, "The control must not change its source snapshots")
        return result

    def test_bad_lower_and_upper_are_document_conditions_with_exact_sources(self):
        for body in (clause(), clause(10, 30), clause(5, 30)):
            with self.subTest(body=body):
                findings, checks = self.run_rule(body)
                self.assertEqual(checks, [])
                self.assertEqual(len(findings), 1)
                item = findings[0]
                self.assertEqual((item["code"], item["law"], item["kind"]), (CODE, LAW, "violation"))
                self.assertEqual(item["criterion_keys"], ["contract_signing"])
                self.assertEqual(item["decision_kind"], "contract_condition")
                self.assertEqual(item["legal_fact_type"], "document_requirement")
                self.assertEqual(item["source_position"], 0)
                self.assertIn(item["source_excerpt"], body)
                self.assertEqual(item["source_excerpt"], item["match_text"])
                self.assertEqual(item["source_url"], URL + "A"*32)
                self.assertEqual(item["contract_conclusion_assessment"]["state"], "confirmed")
                self.assertNotIn("administrative_basis", item)

    def test_legal_subranges_do_not_claim_whole_criterion_compliance(self):
        for low, high in ((10, 20), (10, 15), (15, 20)):
            with self.subTest(low=low, high=high):
                self.assertEqual(self.run_rule(clause(low, high)), ([], []))

    def test_current_contract_project_can_prove_only_the_condition(self):
        findings, checks = self.run_rule(name="Проект договора.docx")
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])
        self.assertEqual(findings[0]["decision_kind"], "contract_condition")
        self.assertIn("не устанавливает фактическую дату", findings[0]["note"])

    def test_competitive_method_and_consistent_modern_notice_are_required(self):
        cases = [
            {"method": "Закупка у единственного поставщика"},
            {"method": "Способ закупки не указан"},
            {"purchase_method": "Закупка у единственного поставщика"},
            {"published": None}, {"published": "не установлена"},
            {"published": "2018-10-01"},
            {"published_at": "2026-10-02"},
        ]
        for purchase in cases:
            with self.subTest(purchase=purchase):
                findings, checks = self.run_rule(purchase=purchase)
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)

    def test_source_roles_and_explicit_examples_cannot_prove_current_condition(self):
        for name in ("Образец документации.docx", "Инструкция.docx", "Положение о закупке.pdf",
                     "Протокол.docx", "Договор подписанный.docx", "Проект документации.docx"):
            with self.subTest(name=name):
                self.assertEqual(self.run_rule(name=name), ([], []))
        for before in ("Пример: ", "Цитата из закона: ", "Нельзя использовать формулировку: "):
            with self.subTest(before=before):
                self.assertEqual(self.run_rule(before + clause()), ([], []))

    def test_explicit_old_or_cancelled_revisions_are_review_only(self):
        for metadata in ({"superseded": True}, {"cancelled": True}, {"is_current": False}):
            with self.subTest(metadata=metadata):
                findings, checks = self.run_rule(metadata=metadata)
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)
                self.assertIn("contract_term_source_revision", checks[0]["methodology_reason_codes"])

    def test_foreign_purchase_and_foreign_selected_lot_are_not_borrowed(self):
        for purchase, metadata in (({}, {"reg_number": "32619999999"}),
                                   ({"lot_number": "1"}, {"lot_number": "2"})):
            with self.subTest(metadata=metadata):
                findings, checks = self.run_rule(purchase=purchase, metadata=metadata)
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)
                self.assertIn("contract_term_scope", checks[0]["methodology_reason_codes"])

    def test_exceptions_and_special_conclusion_cases_remain_review(self):
        for qualifier in (
            "В случае необходимости одобрения органом управления заказчика: ",
            "При обжаловании действий заказчика в антимонопольном органе: ",
            "Для несостоявшейся закупки и единственного участника: ",
            "При уклонении победителя и заключении договора со вторым участником: ",
            "За исключением случаев, предусмотренных законодательством: ",
        ):
            with self.subTest(qualifier=qualifier):
                findings, checks = self.run_rule(qualifier + clause())
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)
                self.assertIn("contract_term_exception", checks[0]["methodology_reason_codes"])

    def test_distant_exception_in_same_document_is_not_ignored(self):
        findings, checks = self.run_rule(clause() + "\n\nИные сроки устанавливаются "
                                        "при обжаловании действий заказчика.")
        self.assertEqual(findings, [])
        self.assertIn("contract_term_exception", checks[0]["methodology_reason_codes"])

    def test_conditional_calendar_extension_is_reviewed_without_recalculating_the_period(self):
        for body in (
            "Если двадцатый день срока приходится на воскресенье, " + clause(10, 21),
            "Если двадцатый день срока приходится\nна воскресенье, " + clause(10, 21),
            clause(10, 21) + "\nУказанный срок применяется только при переносе последнего дня срока "
                            "на ближайший рабочий день по статье 193 ГК РФ.",
            clause(10, 21) + "\nУказанный срок применяется только при переносе\nпоследнего дня срока "
                            "на ближайший рабочий день по статье 193 ГК РФ.",
        ):
            with self.subTest(body=body):
                findings, checks = self.run_rule(body)
                self.assertEqual(findings, [])
                self.assertIn("contract_term_calendar", checks[0]["methodology_reason_codes"])

    def test_explicit_replacement_of_this_condition_prevents_confirmation(self):
        for statement in ("Указанное условие заменено на срок от 10 до 20 дней.",
                          "Указанное условие\nзаменено на срок от 10 до 20 дней."):
            findings, checks = self.run_rule(clause(10, 30) + "\n" + statement)
            self.assertEqual(findings, [])
            self.assertIn("contract_term_replaced", checks[0]["methodology_reason_codes"])

    def test_other_own_lot_original_can_explain_calendar_transfer_or_replacement(self):
        statements = (
            ("contract_term_calendar", "Указанный срок заключения договора применяется только при переносе "
             "последнего дня срока на ближайший рабочий день по статье 193 ГК РФ."),
            ("contract_term_replaced", "Указанное условие о сроке заключения договора заменено на срок от 10 до 20 дней."),
        )
        p = {**P, "original_expected": 2, "original_received": 2}
        for reason, body in statements:
            with self.subTest(reason=reason):
                docs = [original(clause(10, 30), name="Проект договора.docx", lot_number="1"),
                        original(body, "Документация закупки.docx", "B", lot_number="1")]
                findings, checks = evaluate(p, docs)
                self.assertEqual(findings, [])
                self.assertIn(reason, checks[0]["methodology_reason_codes"])
                self.assertTrue(any(value["source_url"] == URL+"B"*32 for value in
                                    checks[0]["contract_conclusion_assessment"]["related_sources"]))
                docs[1]["meta"]["lot_number"] = "2"
                self.assertEqual(len(evaluate(p, docs)[0]), 1)
                docs[1]["meta"]["lot_number"] = "1"
                docs[1]["meta"]["superseded"] = True
                self.assertEqual(len(evaluate(p, docs)[0]), 1)

    def test_working_days_and_numeric_spelling_conflict_stay_review(self):
        for body in (clause(unit="рабочих "), clause("5 (десяти)", "20 (двадцати)")):
            with self.subTest(body=body):
                findings, checks = self.run_rule(body)
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)
        self.assertEqual(len(self.run_rule(clause("5 (пяти)", "20 (двадцати)"))[0]), 1)
        self.assertEqual(len(self.run_rule(clause("5 (пяти)", "20 (двадцати)", "календарных "))[0]), 1)

    def test_inverted_ranges_are_uncertain_even_with_bad_bounds(self):
        findings, checks = self.run_rule(clause(30, 5))
        self.assertEqual(findings, [])
        self.assertIn("contract_term_range_invalid", checks[0]["methodology_reason_codes"])

    def test_only_final_eis_protocol_is_the_proved_clock(self):
        for body in (
            clause().replace("в ЕИС ", ""),
            clause().replace("итогового протокола", "протокола рассмотрения заявок"),
            clause().replace("размещения", "подписания"),
            "Победитель подписывает проект договора в течение 5 дней со дня размещения итогового протокола в ЕИС.",
            "Договор заключен 05.10.2026. Итоговый протокол размещен в ЕИС 01.10.2026.",
        ):
            with self.subTest(body=body):
                self.assertEqual(self.run_rule(body), ([], []))

    def test_coverage_and_truncated_text_cannot_hide_competing_terms(self):
        cases = ({"source_incomplete": True}, {"original_received": 0},
                 {"documents_unreadable": 1},
                 {"source_incomplete": None, "original_expected": None, "original_received": None})
        for purchase in cases:
            with self.subTest(purchase=purchase):
                findings, checks = self.run_rule(purchase=purchase)
                self.assertEqual(findings, [])
                self.assertIn("contract_term_coverage", checks[0]["methodology_reason_codes"])
        findings, checks = self.run_rule(clause() + " "*750001)
        self.assertEqual(findings, [])
        self.assertIn("contract_term_collection_limit", checks[0]["methodology_reason_codes"])

    def test_conflicting_active_ranges_are_not_chosen_for_a_finding(self):
        for docs in ([original(clause() + "\n" + clause(10, 20))],
                     [original(), original(clause(10, 20), "Извещение.docx", "B")]):
            with self.subTest(documents=len(docs)):
                findings, checks = evaluate({**P, "original_expected": len(docs),
                                              "original_received": len(docs)}, docs)
                self.assertEqual(findings, [])
                self.assertTrue(checks)
                self.assertIn("contract_term_conflict", checks[0]["methodology_reason_codes"])

    def test_unselected_alternative_is_never_confirmed(self):
        body = "Применяется вариант 1.\nВариант 1\n" + clause(10, 20) + "\nВариант 2\n" + clause()
        self.assertEqual(self.run_rule(body)[0], [])

    def test_distinct_lots_survive_and_identical_copies_do_not_add_findings(self):
        docs = [original(uid="A", lot_number="1"), original(clause(10, 30), uid="B", lot_number="2")]
        p = {**P, "original_expected": 2, "original_received": 2}
        findings, checks = evaluate(p, docs)
        self.assertEqual(len(findings), 2)
        self.assertEqual(checks, [])
        copies = docs + [original(uid="C", lot_number="1")]
        self.assertEqual(len(evaluate({**p, "original_expected": 3, "original_received": 3}, copies)[0]), 2)

    def test_calendar_label_does_not_make_the_same_condition_conflicting(self):
        docs = [original(), original(clause(unit="календарных "), "Извещение.docx", "B")]
        p = {**P, "original_expected": 2, "original_received": 2}
        findings, checks = evaluate(p, docs)
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])

    def test_same_condition_in_different_originals_is_one_episode_but_each_source_reassesses(self):
        docs = [original(), original("Порядок заключения договора.\n" + clause(), "Извещение.docx", "B")]
        p = {**P, "original_expected": 2, "original_received": 2}
        findings, checks = evaluate(p, docs)
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])
        second = {**findings[0], "document": "Извещение.docx", "source_url": URL+"B"*32,
                  "source_position": docs[1]["text"].index("Договор"),
                  "sha256": docs[1]["meta"]["sha256"], "source_sha256": docs[1]["meta"]["sha256"]}
        self.assertEqual(assess_finding(second, prepare_assessment(p, docs))["state"], "confirmed")

    def test_a_separate_right_to_complain_does_not_select_a_contract_timing_exception(self):
        body = ("1. Срок заключения договора\n" + clause() + "\n\n6. Обжалование\n"
                "Участник вправе подать жалобу в антимонопольный орган.")
        self.assertEqual(len(self.run_rule(body)[0]), 1)

    def test_selected_bad_range_is_not_overruled_by_an_inactive_alternative(self):
        body = "Применяется вариант 1.\nВариант 1\n" + clause() + "\nВариант 2\n" + clause(10, 20)
        findings, checks = self.run_rule(body)
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])

    def test_inapplicable_sections_and_explicitly_inactive_conditions_remain_review(self):
        cases = (
            "При проведении неконкурентной закупки:\n" + clause(),
            "Правила заключения договора по закупкам в соответствии с Федеральным законом № 44-ФЗ:\n" + clause(),
            "Утратившие силу условия:\n" + clause(),
            clause() + "\nУказанное условие не применяется к настоящей закупке.",
            "Порядок заключения договоров субподряда:\n" + clause(),
            "Предлагаемая редакция условия:\n" + clause(),
            "Не устанавливается следующее условие:\n" + clause(),
            "Исключенное условие:\n" + clause(),
        )
        for body in cases:
            with self.subTest(body=body):
                findings, checks = self.run_rule(body)
                self.assertEqual(findings, [])
                self.assertTrue(checks)
                self.assertIn("contract_term_context", checks[0]["methodology_reason_codes"])

    def test_partial_and_worded_counterterms_are_not_hidden_by_the_closed_range_grammar(self):
        for condition in (
            "При этом договор не может быть заключен ранее чем через 10 дней с даты размещения в ЕИС итогового протокола.",
            "При этом срок заключения договора составляет не менее десяти и не более двадцати дней с даты размещения итогового протокола в ЕИС.",
            "При этом заключение договора возможно только после истечения десяти дней и до истечения двадцати дней с даты размещения итогового протокола в ЕИС.",
            "При этом договор заключается только в период с одиннадцатого по двадцатый день с даты размещения итогового протокола в ЕИС.",
            "При этом заключение договора возможно только после истечения\nдесяти дней с даты размещения итогового протокола в ЕИС.",
        ):
            with self.subTest(condition=condition):
                findings, checks = self.run_rule(clause() + "\n" + condition)
                self.assertEqual(findings, [])
                self.assertIn("contract_term_other_condition", checks[0]["methodology_reason_codes"])
                related = checks[0]["contract_conclusion_assessment"]["related_sources"]
                self.assertTrue(any(value["excerpt"] and value["excerpt"] in condition for value in related))

    def test_other_current_original_can_limit_the_contract_term_for_the_same_lot(self):
        exception = "Заключение договора по настоящей закупке требует одобрения органом управления заказчика."
        docs = [original(name="Проект договора.docx", lot_number="1"),
                original(exception, "Извещение.docx", "B", lot_number="1")]
        p = {**P, "original_expected": 2, "original_received": 2}
        findings, checks = evaluate(p, docs)
        self.assertEqual(findings, [])
        self.assertIn("contract_term_exception", checks[0]["methodology_reason_codes"])
        related = checks[0]["contract_conclusion_assessment"]["related_sources"]
        self.assertEqual(related[0]["document"], "Извещение.docx")
        self.assertEqual(related[0]["source_url"], URL+"B"*32)
        self.assertIn("одобрения", related[0]["excerpt"])
        docs[1]["meta"]["lot_number"] = "2"
        self.assertEqual(len(evaluate(p, docs)[0]), 1)
        docs[1]["meta"]["lot_number"] = "1"
        docs[1]["meta"]["superseded"] = True
        self.assertEqual(len(evaluate(p, docs)[0]), 1)

    def test_exception_under_a_lot_heading_cannot_move_to_the_preceding_lot(self):
        body = ("Лот № 1\n" + clause() + "\nЛот № 2\n" + clause(10, 20) +
                "\nЗаключение договора требует одобрения органом управления заказчика.")
        findings, checks = self.run_rule(body)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["contract_conclusion_assessment"]["lots"], ["1"])
        self.assertTrue(all(item["contract_conclusion_assessment"]["lots"] == ["2"] for item in checks))

    def test_exhausted_countercondition_budget_does_not_hide_a_later_own_lot_exception(self):
        signal = ("Договор не может быть заключен ранее чем через 10 дней с даты размещения "
                  "итогового протокола в ЕИС.\n")
        body = ("Лот № 2\n" + signal*100 + "\nЛот № 1\n"
                "Заключение договора требует одобрения органом управления заказчика.")
        docs = [original(name="Проект договора.docx", lot_number="1"),
                original(body, "Извещение.docx", "B")]
        p = {**P, "original_expected": 2, "original_received": 2}
        findings, checks = evaluate(p, docs)
        self.assertEqual(findings, [])
        self.assertIn("contract_term_collection_limit", checks[0]["methodology_reason_codes"])

    def test_exhausted_full_range_budget_limits_other_originals_too(self):
        body = "Лот № 2\n" + (clause(10, 20)+"\n")*100 + "\nЛот № 1\n" + clause(10, 20)
        docs = [original(name="Проект договора.docx", lot_number="1"),
                original(body, "Извещение.docx", "B")]
        p = {**P, "original_expected": 2, "original_received": 2}
        findings, checks = evaluate(p, docs)
        self.assertEqual(findings, [])
        self.assertIn("contract_term_collection_limit", checks[0]["methodology_reason_codes"])

    def test_package_budget_keeps_an_uninspected_original_from_becoming_a_confirmation(self):
        docs = [original(name="Проект договора.docx", lot_number="1"),
                original("Договор не может быть заключен ранее 10 дней.", "Извещение.docx", "B", lot_number="2"),
                original("Заключение договора требует одобрения органом управления заказчика.",
                         "Приложение к извещению.docx", "C", lot_number="1")]
        p = {**P, "original_expected": 3, "original_received": 3}
        with patch("contract_conclusion_terms.MAX_PACKAGE_TERMS", 2, create=True):
            findings, checks = evaluate(p, docs)
        self.assertEqual(findings, [])
        self.assertIn("contract_term_collection_limit", checks[0]["methodology_reason_codes"])

    def test_serialized_evidence_is_rebound_only_after_exact_fresh_reproduction(self):
        docs = [original(name="Проект договора.docx")]
        fresh = evaluate(P, docs)[0][0]
        saved = {key: fresh[key] for key in ("code", "document", "source_url", "evidence", "note")}
        self.assertEqual(finding_excerpt(saved), fresh["source_excerpt"])
        result = assess_finding(saved, prepare_assessment(P, docs))
        self.assertEqual(result["state"], "confirmed")
        self.assertEqual(result["source_binding"]["source_position"], 0)
        self.assertEqual(result["source_binding"]["source_excerpt"], fresh["source_excerpt"])
        self.assertEqual(result["source_binding"]["document"], "Проект договора.docx")
        for altered in (
            [original(clause(10, 20), name="Проект договора.docx")],
            [original("Пример: " + clause(), name="Проект договора.docx")],
            [original(name="Проект договора.docx", uid="B")],
            [original(clause()+"\n"+clause(), name="Проект договора.docx")],
        ):
            with self.subTest(body=altered[0]["text"]):
                self.assertEqual(assess_finding(saved, prepare_assessment(P, altered))["state"], "requires_review")

    def test_evidence_fallback_does_not_parse_an_arbitrary_or_conflicting_claim(self):
        fresh = evaluate(P, [original()])[0][0]
        for item in (
            {**fresh, "source_excerpt": "другое условие"},
            {key: value for key, value in {**fresh, "evidence": "Вывод: " + fresh["evidence"]}.items()
             if key not in {"source_excerpt", "match_text", "source_position"}},
        ):
            with self.subTest(item=item):
                self.assertEqual(finding_excerpt(item), "")
                self.assertEqual(assess_finding(item, prepare_assessment(P, [original()]))["state"], "requires_review")

    def test_reassessment_does_not_replace_an_old_hash_or_archive_member(self):
        doc = original(archive_member="nested/conditions.docx")
        fresh = evaluate(P, [doc])[0][0]
        saved = {key: fresh[key] for key in ("code", "document", "source_url", "evidence", "note")}
        prepared = prepare_assessment(P, [doc])
        for fields in ({"sha256": "f"*64}, {"source_sha256": "f"*64},
                       {"archive_member": "other.docx"}, {"archive_member": ""}):
            with self.subTest(fields=fields):
                self.assertEqual(assess_finding({**saved, **fields}, prepared)["state"], "requires_review")
        exact = assess_finding({**saved, "sha256": doc["meta"]["sha256"],
                                "archive_member": "nested/conditions.docx"}, prepared)
        self.assertEqual(exact["state"], "confirmed")
        self.assertEqual(exact["source_binding"]["sha256"], doc["meta"]["sha256"])
        self.assertEqual(exact["source_binding"]["archive_member"], "nested/conditions.docx")

    def test_unverified_original_does_not_supply_a_condition(self):
        doc = original()
        doc["meta"]["source"] = "uploaded_document"
        self.assertEqual(evaluate(P, [doc]), ([], []))

    def test_reassessment_reproduces_the_exact_current_condition(self):
        docs = [original()]
        finding = evaluate(P, docs)[0][0]
        prepared = prepare_assessment(P, docs)
        self.assertEqual(assess_finding(finding, prepared)["state"], "confirmed")
        for changed in ([original(clause(10, 20))], [original(name="Протокол.docx")],
                        [original(metadata="ignored", superseded=True)]):
            with self.subTest(changed=changed[0]["name"]):
                result = assess_finding({**finding, "legal_basis_verified": True}, prepare_assessment(P, changed))
                self.assertEqual(result["state"], "requires_review")
        stale = {**finding, "source_url": URL+"F"*32}
        self.assertEqual(assess_finding(stale, prepared)["state"], "requires_review")

    def test_reassessment_cannot_pick_a_repeated_unnumbered_occurrence(self):
        doc = original(clause() + "\n" + clause())
        findings, checks = evaluate(P, [doc])
        item = {**findings[0]}
        item.pop("source_position")
        self.assertEqual(assess_finding(item, prepare_assessment(P, [doc]))["state"], "requires_review")

    def test_wrapper_is_idempotent_and_keeps_previous_results(self):
        old_finding = {"code": "old", "kind": "risk"}
        ns = {"analyze": lambda p, docs: [old_finding],
              "build_checks": lambda p, detail, card, docs: [{"field_path": "old-check"}]}
        install(ns)
        wrapper = ns["analyze"]
        install(ns)
        self.assertIs(ns["analyze"], wrapper)
        self.assertEqual([item["code"] for item in wrapper(P, [original()])], ["old", CODE])
        self.assertEqual(ns["build_checks"](P, {}, {}, [original()]), [{"field_path": "old-check"}])
        self.assertTrue(any(item.get("field_path") == CODE for item in
                            ns["build_checks"]({**P, "published": None}, {}, {}, [original()])))


if __name__ == "__main__":
    unittest.main()
