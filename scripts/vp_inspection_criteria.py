"""Canonical 19-row checklist transcribed from the user's 2026 inspection sheet.

This is a criteria registry, not an automatic finding generator.
Unproved compliance and inapplicable/unknown SME status must remain undecided.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Criterion:
    key: str
    title: str
    law: str
    thematic_group: str
    sme_only: bool = False

CRITERIA = (
    Criterion("payment_223", "Срок оплаты", "ч. 5.3, 5.4 ст. 3 Закона № 223-ФЗ", "contract"),
    Criterion("trademark_equivalent", "Наличие товарного знака («эквивалент»)", "п. 2, 3 ч. 6.1 ст. 3 Закона № 223-ФЗ", "docs"),
    Criterion("national_1875", "Применение ПП РФ 1875 (что применено/должно быть применено)", "ПП РФ № 1875", "national"),
    Criterion("clarifications", "Запросы о даче разъяснений (суть, сроки)", "ч. 3, 4 ст. 3.2 Закона № 223-ФЗ", "dates"),
    Criterion("cancellation", "Отмена закупки (документация, действия)", "ч. 5, 6, 7 ст. 3.2 Закона № 223-ФЗ", "protocols"),
    Criterion("protocol_compliance", "Соответствие протоколов", "ч. 13, 14 ст. 3.2 Закона № 223-ФЗ", "protocols"),
    Criterion("contract_signing", "Срок заключения договора (документация, действия)", "ч. 15 ст. 3.2 Закона № 223-ФЗ", "contract"),
    Criterion("notice_deadlines", "Сроки размещения извещений", "ч. 17, 19, 21, 23 ст. 3.2 Закона № 223-ФЗ; положение о закупке для иных способов", "dates"),
    Criterion("bid_security", "Обеспечение заявки", "ч. 25, 27 ст. 3.2 Закона № 223-ФЗ", "security"),
    Criterion("notice_content", "Состав извещения", "ч. 9 ст. 4 Закона № 223-ФЗ", "docs"),
    Criterion("documentation_content", "Состав документации", "ч. 10 ст. 4 Закона № 223-ФЗ", "docs"),
    Criterion("amendments", "Внесение изменений", "ч. 11 ст. 4 Закона № 223-ФЗ", "dates"),
    Criterion("post_deadline_extension", "Продление сроков этапов после окончания срока подачи заявок", "ч. 11 ст. 4 Закона № 223-ФЗ", "dates"),
    Criterion("sme_criteria", "Критерии закупки (ПРИ МСП)", "п. 18, 19 ПП РФ № 1352", "sme", True),
    Criterion("sme_notice_deadlines", "Сроки размещения извещений (ПРИ МСП)", "ч. 3 ст. 3.4 Закона № 223-ФЗ", "sme", True),
    Criterion("sme_bid_security", "Обеспечение заявки (ПРИ МСП)", "ч. 12 ст. 3.4 Закона № 223-ФЗ", "sme", True),
    Criterion("sme_application_requirements", "Требования к составу заявки (ПРИ МСП)", "ч. 19.1, 19.2 ст. 3.4 Закона № 223-ФЗ", "sme", True),
    Criterion("sme_collective_participation", "Коллективное участие (ПРИ МСП) — запрет", "ч. 19.1 ст. 3.4 Закона № 223-ФЗ", "sme", True),
    Criterion("sme_payment", "Срок оплаты", "п. 14(3), 28 ПП РФ № 1352", "sme", True),
)
BY_KEY = {item.key: item for item in CRITERIA}

def inspection_rows(findings, *, sme_status=None):
    """Return 19 deterministic rows from independently verified, criterion-keyed outcomes.

    findings: mapping criterion key -> {status: violation|compliant|unknown,
    evidence: nonempty source-exact excerpt, document: nonempty source identifier,
    legal_basis_verified: bool, administrative_basis: optional string}.
    Do not turn missing findings into a 'no violation' claim.
    """
    findings = findings or {}
    rows = []
    for criterion in CRITERIA:
        item = findings.get(criterion.key) or {}
        status = item.get("status", "unknown")
        supported = bool(item.get("evidence") and item.get("document") and
                         item.get("legal_basis_verified") is True)
        applicable = not criterion.sme_only or sme_status is True
        verified = supported and applicable and status in ("violation", "compliant")
        violation = verified and status == "violation"
        compliant = verified and status == "compliant"
        rows.append({
            "key": criterion.key, "criterion": criterion.title, "law": criterion.law,
            "thematic_group": criterion.thematic_group,
            "has_violation": "✓" if violation else "",
            "no_violation": "✓" if compliant else "",
            "koap": str(item.get("administrative_basis") or "") if violation else "",
            "comment": "",
            "review_required": not verified,
            "evidence": str(item.get("evidence") or "") if verified else "",
            "document": str(item.get("document") or "") if verified else "",
        })
    return rows
