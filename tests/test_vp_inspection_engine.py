import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from vp_inspection_engine import (criterion_keys, evaluate, install, targeted_reviews,
                                  document_fingerprint)


URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32


def doc(text="Размер обеспечения заявки составляет 8 процентов НМЦД.", name="Документация.docx", **meta):
    return {"name": name, "text": text, "status": "checked", "meta": {
        "source": "official_eis_original", "sha256": "a"*64, "source_url": URL, **meta}}


def purchase(**extra):
    return {"reg": "32612345678", "customer": "ООО Заказчик", "title": "Поставка товара",
            "method": "Аукцион в электронной форме", "published": "2026-10-01",
            "price_num": 8000000,
            "verified_price_source": {"kind": "nmcd", "price": "8000000",
                "source": "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=32612345678"},
            "_verified_notice_revisions": [{"sha256": "e"*64, "xml_type": "purchaseNotice", "version": 1,
                "publication": "2026-09-30", "submission_end": "2026-10-30"}],
            **extra}


def finding(**extra):
    return {"code": "223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT", "kind": "violation",
        "title": "Превышен размер обеспечения заявки", "law": "223-ФЗ, ст. 3.2 ч. 27",
        "evidence": "Размер обеспечения заявки составляет 8 процентов НМЦД.",
        "document": "Документация.docx", "source_url": URL, **extra}


def row(result, key):
    return next(x for x in result["rows"] if x["key"] == key)


def test_criteria_mapping_uses_precise_rule_not_title_or_evidence():
    value = finding(title="Документация, оплата и протоколы", evidence="1875, МСП, сроки")
    assert criterion_keys(value) == ("bid_security",)
    assert criterion_keys({"code": "unknown", "title": "Нарушение документации"}) == ()
    assert criterion_keys({"code": "223-4-12-PROTOCOL-PUBLICATION-LATE"}) == ()
    assert criterion_keys({"code": "v372-protocol-rejected-bid-no-reasons"}) == ("protocol_compliance",)


def test_confirmed_violation_retains_exact_source_and_blank_comments():
    result = evaluate(purchase(), [doc()], [finding()])
    target = row(result, "bid_security")
    assert target["has_violation"] == "✓"
    assert target["no_violation"] == ""
    assert target["results"][0]["source_excerpt"] in doc()["text"]
    assert all(x["comment"] == "" and x["koap"] == "" for x in result["rows"])
    assert result["confirmed_criteria"] == 1
    assert len(result["rows"]) == 19


def test_no_documents_and_unproved_text_never_become_compliance():
    empty = evaluate(purchase(), [])
    assert all(not r["has_violation"] and not r["no_violation"] for r in empty["rows"])
    unsupported = evaluate(purchase(), [doc()], [finding(evidence="Условие 20% подтверждено")])
    assert row(unsupported, "bid_security")["status"] == "unknown"
    untrusted = evaluate(purchase(), [doc(source="unverified")], [finding()])
    assert row(untrusted, "bid_security")["status"] == "unknown"


def test_ambiguous_source_versions_cannot_supply_proof():
    result = evaluate(purchase(), [doc(), doc(sha256="b"*64)], [finding()])
    assert row(result, "bid_security")["status"] == "unknown"


def test_multiple_findings_are_preserved_and_violation_wins():
    second_text = "Размер обеспечения заявки составляет 9 процентов НМЦД."
    # Independent lot requirements are separate findings. Different values
    # for the same unselected lot/revision must instead remain a review.
    first = doc("НМЦД: 8 000 000 рублей.\n" + doc()["text"], lot_number="1")
    another = doc("НМЦД: 8 000 000 рублей.\n" + second_text,
                  name="Извещение.docx", sha256="b"*64, lot_number="2")
    bad = finding(document="Извещение.docx", evidence=second_text)
    compliant = finding(kind="check", status="compliant", criterion_complete=True)
    result = evaluate(purchase(), [first, another], [finding(), bad], [compliant])
    target = row(result, "bid_security")
    assert target["has_violation"] == "✓" and not target["no_violation"]
    assert len(target["results"]) == 3


def test_subfield_ok_does_not_mean_entire_criterion_compliant():
    item = finding(kind="check", status="ok")
    result = evaluate(purchase(), [doc()], [], [item])
    assert row(result, "bid_security")["status"] == "unknown"
    item["criterion_complete"] = True
    assert row(evaluate(purchase(), [doc()], [], [item]), "bid_security")["no_violation"] == "✓"


def test_unknown_noncompetitive_and_sme_scopes_remain_distinct():
    item = finding(code="223-SME-BID-SECURITY-OVER-TWO-PERCENT", law="223-ФЗ, ст. 3.4 ч. 12; ПП РФ № 1352, п. 23")
    unknown = evaluate(purchase(), [doc()], [item])
    assert row(unknown, "sme_bid_security")["status"] == "unknown"
    broad = evaluate(purchase(method="Закупка у единственного поставщика"), [doc()], [finding()])
    assert row(broad, "bid_security")["status"] == "not_applicable"
    assert not row(broad, "bid_security")["review_required"]
    assert not row(broad, "bid_security")["no_violation"]
    sme = evaluate(purchase(method="Аукцион только для субъектов МСП"), [doc()], [item])
    assert row(sme, "sme_bid_security")["has_violation"] == "✓"


def test_historical_after_deadline_flag_is_not_automatic_violation():
    item = finding(code="223-4-11-CHANGE-AFTER-BID-DEADLINE", law="223-ФЗ, ст. 4 ч. 11")
    result = evaluate(purchase(), [doc()], [item])
    assert row(result, "post_deadline_extension")["status"] == "unknown"


def test_targeted_terms_are_review_only_and_consider_source_context():
    text = "Оплата производится в течение 30 календарных дней с даты подписания акта приёмки. Эквивалент не допускается."
    items = targeted_reviews(purchase(), [doc(text)])
    assert {x["field_path"] for x in items} == {"vp-payment-term", "vp-trademark-exclusion"}
    assert all(x["status"] == "check" and x["source_excerpt"] in text for x in items)
    assert not targeted_reviews(purchase(), [doc(text, name="Положение о закупке.docx")])
    assert not targeted_reviews(purchase(), [doc("Например: "+text)])


def test_shared_installer_tags_all_modes_and_is_idempotent():
    ns = {"analyze": lambda p, d: [finding()], "build_checks": lambda p, detail, card, d: []}
    install(ns)
    first = ns["analyze"]
    install(ns)
    assert ns["analyze"] is first
    assert ns["analyze"](purchase(), [doc()])[0]["criterion_keys"] == ["bid_security"]
    text = "Оплата производится в течение 30 календарных дней с даты подписания акта приёмки."
    assert ns["build_checks"](purchase(), {}, {}, [doc(text)])[0]["status"] == "check"


def test_document_fingerprint_tracks_sources_without_using_text():
    assert document_fingerprint([doc()]) == document_fingerprint([doc("Другое извлечение тех же байтов")])
    assert document_fingerprint([doc()]) != document_fingerprint([doc(sha256="b"*64)])


def test_full_legal_references_and_actual_catalog_codes_are_mapped_precisely():
    assert criterion_keys({"law": "223-ФЗ, статья 4, часть 9, пункт 5"}) == ("notice_content",)
    assert criterion_keys({"law": "часть 10 статьи 4 Закона № 223-ФЗ"}) == ("documentation_content",)
    assert criterion_keys({"law": "пункты 18, 19 ПП РФ № 1352"}) == ("sme_criteria",)
    assert criterion_keys({"law": "ПП РФ № 1352, пункт 14(3)"}) == ("sme_payment",)
    assert criterion_keys({"code": "v337-sme_quote_limit"}) == ("sme_notice_deadlines",)
    assert criterion_keys({"code": "v337-sme_declaration"}) == ("sme_application_requirements",)
    assert criterion_keys({"code": "v337-scoring_weights"}) == ("documentation_content",)
    assert criterion_keys({"code": "v337-sme_quota"}) == ()
    assert criterion_keys({"code": "v337-national_annual_report"}) == ()


def test_customer_sme_list_is_reviewed_as_purchase_eligibility_not_scoring():
    body = "Перечень товаров, работ, услуг для закупок у субъектов малого и среднего предпринимательства."
    checks = targeted_reviews(purchase(), [doc(body, name="Перечень МСП.docx")])
    assert len(checks) == 1
    assert checks[0]["criterion_keys"] == ["sme_criteria"]
    result = evaluate(purchase(method="Аукцион только для субъектов МСП"), [doc(body, name="Перечень МСП.docx")], [], checks)
    target = row(result, "sme_criteria")
    assert target["results"] and not target["has_violation"]
    assert "НМЦД" in target["required_evidence"]


def test_koap_requires_separately_verified_dated_event_and_scope():
    admin = dict(administrative_basis="ч. 2 ст. 7.30.4 КоАП РФ",
        administrative_basis_verified=True, administrative_event_date="2026-10-01",
        administrative_event_date_verified=True, administrative_event_source_verified=True,
        administrative_event_type="document_requirements", administrative_event_evidence="Проверенное событие")
    result = evaluate(purchase(), [doc()], [finding(**admin)])
    assert row(result, "bid_security")["koap"] == admin["administrative_basis"]
    assert row(evaluate(purchase(), [doc()], [finding(**{**admin, "administrative_basis": "ч. 2 ст. 7.32.3 КоАП РФ"})]), "bid_security")["koap"] == ""
    assert row(evaluate(purchase(), [doc()], [finding(**{**admin, "administrative_event_date_verified": False})]), "bid_security")["koap"] == ""
    payment_text = "Оплата производится в течение 30 календарных дней после приёмки."
    payment_item = finding(code="payment_223", evidence=payment_text,
        **{**admin, "administrative_basis": "ч. 7 ст. 7.30.4 КоАП РФ"})
    assert row(evaluate(purchase(), [doc(payment_text)], [payment_item]), "payment_223")["koap"] == ""


def test_real_existing_analyzers_produce_usable_sheet_marks():
    from v341_precise_legal import evaluate as precise
    from v372_auto_regulation_protocols import evaluate as protocols
    from v372_automatic_legal_selftest import PROTOCOL_HEAD
    originals = [doc("Дата поступления запроса разъяснения: 01.10.2026. Дата публикации ответа в ЕИС: 07.10.2026.", name="Разъяснения.pdf"),
        doc("Дата принятия решения об отмене закупки: 01.10.2026. Дата публикации решения об отмене закупки в ЕИС: 02.10.2026.", name="Отмена.pdf", sha256="b"*64),
        doc(PROTOCOL_HEAD+"По заявке № 12 комиссия приняла решение: отклонена заявка № 12, основания отклонения не указаны. Итоги рассмотрения зафиксированы.", name="Протокол.pdf", sha256="c"*64)]
    p = purchase(submission_end="2026-10-30")
    first, checks = precise(p, originals)
    second, more = protocols(p, originals)
    result = evaluate(p, originals, first+second, checks+more)
    assert {x["key"] for x in result["rows"] if x["has_violation"]} == {"clarifications", "cancellation", "protocol_compliance"}


def test_general_procurement_sme_payment_does_not_require_exclusive_sme_scope():
    text = "Срок оплаты составляет 30 рабочих дней со дня подписания заказчиком документа о приемке товара."
    general = purchase(fact_evidence={"sme_only": {"source": "official_eis_card", "value": False}})
    item = finding(code="sme_payment", evidence=text, law="ПП РФ № 1352, п. 14(3)")
    unresolved = row(evaluate(general, [doc(text)], [item]), "sme_payment")
    assert unresolved["status"] == "unknown"
    assert unresolved["applicable"] is None
    general["fact_evidence"]["supplier_sme"] = {"source": "official_eis_card", "value": True,
        "verified": True, "supplier_inn": "7701234567",
        "source_url": "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=32612345678"}
    confirmed = row(evaluate(general, [doc(text)], [item]), "sme_payment")
    assert confirmed["applicable"] is True and confirmed["has_violation"] == "✓"
    # Scope alone cannot prove a seven-working-day breach from an unbound
    # calendar term; the legal rule and documentary proof remain separate.
    ambiguous = "Срок оплаты составляет 30 календарных дней с даты приёмки товара."
    limited = row(evaluate(general, [doc(ambiguous)],
        [{**item, "evidence": ambiguous}]), "sme_payment")
    assert limited["applicable"] is True and limited["status"] == "unknown"
    assert not limited["has_violation"] and not limited["no_violation"]


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                              if name.startswith("test_") and callable(value))


if __name__ == "__main__":
    import unittest
    unittest.main()
