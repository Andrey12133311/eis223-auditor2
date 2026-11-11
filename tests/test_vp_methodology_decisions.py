"""Regression cases for the shared verdict qualification, without network/I/O."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from vp_inspection_engine import (criterion_keys, decision_qualification, evaluate,
                                  install, norm_matches_criterion, qualify_outcomes,
                                  qualify_record)
from tests.test_vp_inspection_engine import URL, doc, finding, purchase, row


def test_precise_citation_accepts_abbreviated_full_reversed_lists_and_ranges():
    cases = [
        ("223-ФЗ, ст. 3.2 ч. 27", "bid_security"),
        ("Федеральный закон № 223-ФЗ, статья 3.2, часть 27", "bid_security"),
        ("часть 27 статьи 3.2 Закона № 223-ФЗ", "bid_security"),
        ("223-ФЗ, ст. 3.2 ч. 25–27", "bid_security"),
        ("ч. 3, 4 ст. 3.2 Закона № 223-ФЗ", "clarifications"),
        ("п. 2, 3 ч. 6.1 ст. 3 Закона № 223-ФЗ", "trademark_equivalent"),
        ("ч. 5.3, 5.4 ст. 3 Закона № 223-ФЗ", "payment_223"),
        ("223-ФЗ, ст. 3 ч. 8; ПП РФ № 1352, п. 23", "sme_bid_security"),
        ("Постановление Правительства РФ № 1352, Положение, п. 28", "sme_payment"),
        ("Положение № 1352, п. 14(3)", "sme_payment"),
        ("пункты 18, 19 ПП РФ № 1352", "sme_criteria"),
        ("ПП РФ № 1875, приложение № 1", "national_1875"),
    ]
    for law, key in cases:
        assert norm_matches_criterion({"law": law}, key), (law, key)


def test_bare_wrong_act_and_wrong_subprovision_never_verify_a_norm():
    cases = [
        ("223-ФЗ", "bid_security"),
        ("223-ФЗ, ст. 3.2", "bid_security"),
        ("ГК РФ, ст. 450", "bid_security"),
        ("ГК РФ, ст. 3.2 ч. 27; 223-ФЗ", "bid_security"),
        ("ГК РФ, ст. 3.2 ч. 27, 223-ФЗ", "bid_security"),
        ("ПП РФ № 1352", "sme_payment"),
        ("ПП РФ № 1352; ГК РФ п. 23", "sme_bid_security"),
        ("ПП РФ № 1352, п. 23.1", "sme_bid_security"),
        ("ПП РФ № 1352, п. 28.1", "sme_payment"),
        ("договор № 1875, п. 7", "national_1875"),
        ("ПП РФ № 1875", "national_1875"),
        ("223-ФЗ, ст. 3.2 ч. 27 не применяется", "bid_security"),
    ]
    for law, key in cases:
        assert not norm_matches_criterion({"law": law}, key), (law, key)
    # Another act may be cited in a separate clause without contaminating
    # a correctly bound procurement-law provision.
    assert norm_matches_criterion({"law": "223-ФЗ, ст. 3.2 ч. 27; ГК РФ, ст. 450"}, "bid_security")


def test_exact_registered_rule_needs_its_own_provision():
    assert not norm_matches_criterion(finding(law="223-ФЗ, ст. 3.2 ч. 25"), "bid_security")
    item = finding(code="223-3.4-19.2-SME-EVALUATION-DOCS-REJECTION", law="223-ФЗ, ст. 3.4 ч. 19.1")
    assert not norm_matches_criterion(item, "sme_application_requirements")
    item["law"] = "223-ФЗ, ст. 3.4 ч. 19.2"
    assert norm_matches_criterion(item, "sme_application_requirements")


def test_inconsistent_law_remains_an_explicit_review_with_original_material():
    item = finding(law="ГК РФ, ст. 450", note="Проверить правовую квалификацию")
    target = row(evaluate(purchase(), [doc()], [item]), "bid_security")
    assert target["status"] == "unknown" and not target["has_violation"]
    assert target["legal_basis_verified"] is False
    review = target["unresolved"][0]
    assert "legal_reference_unverified" in review["methodology_reason_codes"]
    for field in ("title", "law", "evidence", "document", "note", "code"):
        assert review[field] == item[field], field
    assert review["methodology_reasons"] and target["methodology_reasons"]
    assert all(value["comment"] == "" for value in evaluate(purchase(), [doc()], [item])["rows"])


def test_conflicting_status_classes_cannot_confirm_violation_or_compliance():
    pairs = [("violation", "check"), ("violation", "ok"), ("risk", "violation"),
             ("review", "compliant"), ("check", "violation"), ("ok", "violation")]
    for kind, status in pairs:
        item = finding(kind=kind, status=status, criterion_complete=True)
        decision = decision_qualification(item, "bid_security")
        assert decision["status"] == "unknown", (kind, status)
        assert "reported_status_conflict" in decision["methodology_reason_codes"]
        target = row(evaluate(purchase(), [doc()], [item]), "bid_security")
        assert not target["has_violation"] and not target["no_violation"]
    # This positive status is internally consistent, but the completeness flag
    # cannot turn a narrow amount rule into a whole-criterion validator.
    positive = finding(kind="check", status="compliant", criterion_complete=True)
    decision = decision_qualification(positive, "bid_security")
    assert "reported_status_conflict" not in decision["methodology_reason_codes"]
    assert "criterion_completion_unverified" in decision["methodology_reason_codes"]
    target = row(evaluate(purchase(), [doc()], [], [positive]), "bid_security")
    assert target["status"] == "unknown" and not target["no_violation"]


def test_registered_rule_wins_over_wrong_criterion_tag_without_losing_conflict():
    item = finding(criterion_keys=["payment_223"])
    assert criterion_keys(item) == ("bid_security",)
    first = qualify_record(item)
    assert first["criterion_keys"] == ["bid_security"]
    assert first["reported_criterion_keys"] == ["payment_223"]
    assert "criterion_rule_conflict" in first["methodology_reason_codes"]
    assert qualify_record(first) == first
    result = evaluate(purchase(), [doc()], [first])
    assert row(result, "bid_security")["status"] == "unknown"
    assert not row(result, "payment_223")["results"]
    # The analyzer tagging wrapper must not erase a conflict before the common stage.
    ns = {"analyze": lambda p, docs: [item], "build_checks": lambda *args: []}
    install(ns)
    tagged = ns["analyze"](purchase(), [doc()])[0]
    assert "criterion_rule_conflict" in qualify_record(tagged)["methodology_reason_codes"]


def test_generic_review_and_unregistered_rule_cannot_be_promoted_by_fields():
    cases = [finding(code="v337-bid_security_clause"),
             finding(code="v372-protocol-criteria-values", law="223-ФЗ, ст. 3.2 ч. 13"),
             finding(code="unregistered", criterion_keys=["bid_security"])]
    for item in cases:
        result = qualify_record(item)
        assert result["kind"] == "review" and result["status"] == "check"
        assert result["methodology_reasons"]
    partial = qualify_record(finding(kind="check", status="ok"))
    assert "criterion_not_fully_checked" in partial["methodology_reason_codes"]
    ordinary_review = qualify_record(finding(kind="check", status="check"))
    assert "review_outcome_not_verdict" in ordinary_review["methodology_reason_codes"]


def test_shared_qualifier_keeps_wider_rules_and_does_not_mutate_input():
    p, originals = purchase(), [doc()]
    good, wrong = finding(), finding(law="ГК РФ, ст. 450")
    wider = {"code": "223-4-12-PROTOCOL-PUBLICATION-LATE", "kind": "violation", "law": "223-ФЗ, ст. 4 ч. 12"}
    checks = [finding(kind="check", status="check")]
    before = copy.deepcopy((p, originals, [good, wrong, wider], checks))
    findings, reviews = qualify_outcomes(p, originals, [good, wrong, wider], checks)
    assert findings[0]["code"] == good["code"] and findings[0]["kind"] == "violation"
    assert findings[1] == wider
    assert len(reviews) == 2 and reviews[0]["reported_kind"] == "violation"
    assert (p, originals, [good, wrong, wider], checks) == before
    assert qualify_outcomes(p, originals, findings, reviews) == (findings, reviews)


def test_explicit_example_quoted_norm_and_superseded_source_are_reviews():
    text = "Дата поступления запроса разъяснения: 01.10.2026. Дата публикации ответа в ЕИС: 07.10.2026."
    event = finding(code="223-3.2-3-CLARIFICATION-RESPONSE-LATE", law="223-ФЗ, ст. 3.2 ч. 3", evidence=text, match_text=text)
    cases = [(doc(text, name="Шаблон разъяснения.docx"), "template_or_example"),
             (doc("Пример заполнения: "+text), "illustrative_passage"),
             (doc("Цитата из закона: "+text), "quoted_norm"),
             (doc(text, is_superseded=True), "explicitly_superseded_revision")]
    for original, expected in cases:
        item = {**event, "document": original["name"]}
        findings, reviews = qualify_outcomes(purchase(), [original], [item], [])
        assert not findings and len(reviews) == 1, expected
        assert expected in reviews[0]["methodology_reason_codes"]
        assert reviews[0]["source_context_available"] is True
        assert reviews[0]["source_context_eligible"] is False
        target = row(evaluate(purchase(), [original], [item]), "clarifications")
        assert target["status"] == "unknown" and target["unresolved"]


def test_real_date_engine_example_is_downgraded_but_real_event_stays_confirmable():
    from v341_precise_legal import evaluate as precise
    text = "Дата поступления запроса разъяснения: 01.10.2026. Дата публикации ответа в ЕИС: 07.10.2026."
    p = purchase(submission_end="2026-10-30")
    for prefix, expected_count in (("", 1), ("Пример заполнения: ", 0)):
        originals = [doc(prefix+text, name="Разъяснение.pdf")]
        findings, checks = precise(p, originals)
        # Source-backed deadline qualification can reject the sample before
        # the common stage; it must retain the actual event and review reason.
        assert findings if expected_count else checks
        qualified, reviews = qualify_outcomes(p, originals, findings, checks)
        assert len(qualified) == expected_count
        assert reviews if not expected_count else not reviews


def test_contract_project_can_supply_terms_but_cannot_prove_completed_act():
    original = doc(name="Проект договора.docx")
    findings, _ = qualify_outcomes(purchase(), [original], [finding(document=original["name"], decision_kind="document_requirement")], [])
    assert len(findings) == 1 and findings[0]["kind"] == "violation"
    event = finding(document=original["name"], code="223-3.2-6-CANCELLATION-POSTED-LATE", law="223-ФЗ, ст. 3.2 ч. 6")
    findings, reviews = qualify_outcomes(purchase(), [original], [event], [])
    assert not findings and "project_is_not_completed_act" in reviews[0]["methodology_reason_codes"]


def test_authoritative_history_without_binary_match_is_not_fabricated_or_suppressed():
    from datetime import date
    from unittest.mock import patch
    import v330_amendments_223 as amendments
    history = {"decision_date": date(2026, 10, 6), "publication_date": date(2026, 10, 13),
               "document": "История изменений ЕИС", "kind": "eis_history",
               "source": "https://zakupki.gov.ru/changes.html?regNumber=32612345678"}
    with patch.object(amendments, "manifest_changes", return_value=[history]):
        events, _ = amendments.evaluation(purchase(), [])
    assert len(events) == 1
    event = events[0]
    findings, checks = qualify_outcomes(purchase(), [], [event], [])
    assert findings and not checks
    assert findings[0]["source_context_available"] is False
    assert findings[0]["source_context_eligible"] is None
    assert findings[0]["decision_basis"]["legal_verification_scope"] == "citation_consistency"
    # The sheet still needs its own original-evidence gate before a mark.
    target = row(evaluate(purchase(), [], findings), "amendments")
    assert target["status"] == "unknown"
    assert target["unresolved"][0]["evidence"] == event["evidence"]


def test_result_preserves_requirement_kind_for_explanation_and_blank_comments():
    item = finding(decision_kind="document_requirement")
    result = evaluate(purchase(), [doc()], [item])
    assert row(result, "bid_security")["results"][0]["decision_kind"] == "document_requirement"
    assert all(value["comment"] == "" for value in result["rows"])


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                              if name.startswith("test_") and callable(value))


if __name__ == "__main__":
    import unittest
    unittest.main()
