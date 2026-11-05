"""Evidence-led adapter between the common legal engine and the user's sheet.

The sheet is a projection of legal outcomes, never a second keyword-based
verdict engine. A source fragment, applicable rule and independently established
status are retained together. Missing evidence never means compliance.
"""
from __future__ import annotations

from functools import wraps
from datetime import date
import hashlib
import json
import os
import re

from vp_inspection_criteria import CRITERIA, BY_KEY, inspection_rows
from v331_broad_legal import verified
from v383_msp_scope import resolve as resolve_sme
from legal_applicability import competitive_rule_scope

REVISION = "inspection-sheet-19-v1"

# Exact rule identities take precedence over titles. In particular, a mention
# of "documentation" in a payment finding cannot become a documentation defect.
_RULE_KEYS = {
    "payment": ("payment_223",),
    "payment_without_acceptance": ("payment_223",),
    "single_brand": ("trademark_equivalent",),
    "clarification": ("clarifications",),
    "clarification_essential": ("clarifications",),
    "personal_data": ("clarifications",),
    "cancellation": ("cancellation",),
    "protocol": ("protocol_compliance",),
    "bid_guarantee": ("bid_security",),
    "security_expiry": ("bid_security",),
    "amendment": ("amendments",),
    "clar-late": ("clarifications",),
    "clar-no-answer": ("clarifications",),
    "clar-changes": ("clarifications",),
    "cancel-late": ("cancellation",),
    "cancel-after-deadline": ("cancellation",),
    "protocol-field": ("protocol_compliance",),
    "no-equiv": ("trademark_equivalent",),
    "v337-notice_missing_data": ("notice_content",),
    "v337-notice_customer_contacts": ("notice_content",),
    "v337-notice_procurement_subject": ("notice_content",),
    "v337-notice_method": ("notice_content",),
    "v337-notice_financing": ("notice_content",),
    "v337-notice_multi_lot": ("notice_content",),
    "v337-notice_currency": ("notice_content",),
    "v337-specification_open_ended": ("documentation_content",),
    "v337-unpublished_tech_appendix": ("documentation_content",),
    "v337-technical_ambiguity": ("documentation_content",),
    "v337-project_contract_missing": ("documentation_content",),
    "v337-brand_equivalence": ("trademark_equivalent",),
    "v337-short_bid_period": ("notice_deadlines",),
    "v337-late_changes": ("post_deadline_extension",),
    "v337-change_extension": ("amendments",),
    "v337-publication_lag": ("amendments",),
    "v337-clarification_lastday": ("clarifications",),
    "v337-clarification_late_answer": ("clarifications",),
    "v337-clarification_change_subject": ("clarifications",),
    "v337-cancel_procedure": ("cancellation",),
    "v337-bid_security_clause": ("bid_security",),
    "v337-security_no_return": ("bid_security",),
    "v337-security_disproportion": ("bid_security",),
    "v337-contract_signing_period": ("contract_signing",),
    "v337-opening_protocol": ("protocol_compliance",),
    "v337-application_protocol": ("protocol_compliance",),
    "v337-final_protocol": ("protocol_compliance",),
    "v337-protocol_no_reasons": ("protocol_compliance",),
    "v337-final_winner": ("protocol_compliance",),
    "v337-cancellation_protocol": ("cancellation",),
    "v337-scoring_criteria": ("documentation_content",),
    "v337-scoring_weights": ("documentation_content",),
    "v337-contract_payment_delay": ("payment_223",),
    "v337-contract_no_deadline": ("payment_223",),
    "v337-contract_payment_exceptions": ("payment_223",),
    "v337-sme_payment": ("sme_payment",),
    "v337-sme_payment_7workdays": ("sme_payment",),
    "v337-sme_price_limits": ("sme_notice_deadlines",),
    "v337-sme_quote_limit": ("sme_notice_deadlines",),
    "v337-sme_proposal_limit": ("sme_notice_deadlines",),
    "v337-sme_declaration": ("sme_application_requirements",),
    "v337-sme_special_account": ("sme_bid_security",),
    "v337-sme_bid_security": ("sme_bid_security",),
    "v337-application_contents": ("sme_application_requirements",),
    "v337-application_document_list": ("sme_application_requirements",),
    "v337-notarized_papers": ("sme_application_requirements",),
    "v337-commercial_offer": ("sme_application_requirements",),
    "v337-dealer_letter": ("sme_application_requirements",),
    "v337-bank_certificates": ("sme_application_requirements",),
    "v337-paper_originals": ("sme_application_requirements",),
    "v337-only_original_sign": ("sme_application_requirements",),
    "v337-sme_collective": ("sme_collective_participation",),
    "v337-sme_application_documents": ("sme_application_requirements",),
    "v337-request_response_3workdays": ("clarifications",),
    "v337-clarification_3workdays_cutoff": ("clarifications",),
    "v337-amendment_3days": ("amendments",),
    "v337-amendment_half_term": ("amendments",),
    "v337-cancellation_publication": ("cancellation",),
    "v337-security_bid_5million_gate": ("bid_security",),
    "v337-security_bid_percent_gate": ("bid_security",),
    "v336-payment_without_acceptance": ("payment_223",),
    "v336-clarification_essential": ("clarifications",),
    "v336-personal_data": ("clarifications",),
    "v336-security_expiry": ("bid_security",),
    "v336-original_application": ("sme_application_requirements",),
    "v336-notarization": ("sme_application_requirements",),
    "v336-bid-security-needs-verified-nmcd": ("bid_security",),
    "v383-bid-security-msp-scope": ("sme_bid_security",),
    "223-sme-bid-security-over-two-percent": ("sme_bid_security",),
    "223-4-11-amendment-publication-delay": ("amendments",),
    "223-4-11-change-after-bid-deadline": ("post_deadline_extension",),
    "v372-protocol-rejected-bid-no-reasons": ("protocol_compliance",),
    "v372-protocol-rejected-bid-document-clause": ("protocol_compliance",),
    "v372-protocol-criteria-values": ("protocol_compliance",),
    "vp-payment-term": ("payment_223",),
    "vp-trademark-exclusion": ("trademark_equivalent",),
    "vp-sme-purchase-list": ("sme_criteria",),
}

_REQUIRED_EVIDENCE = {
    "payment_223": "Условие оплаты, событие приёмки, применимое положение о закупке и основания исключений",
    "trademark_equivalent": "Конкретный товарный знак, условие эквивалентности и основания исключений",
    "national_1875": "ОКПД2 и характеристики товара, режим и перечни № 1875 в редакции на дату закупки, исключения",
    "clarifications": "Сам запрос, ответ, даты получения и размещения, срок подачи и календарь рабочих дней",
    "cancellation": "Решение об отмене, дата его принятия и размещения, срок подачи и основания отмены",
    "protocol_compliance": "Все страницы и приложения соответствующего протокола, документация и решения по каждой заявке",
    "contract_signing": "Итоговый протокол, дата заключения договора, жалобы и приостановления, применимые исключения",
    "notice_deadlines": "Способ закупки, первое извещение, крайний срок подачи, положение о закупке",
    "bid_security": "Условие обеспечения именно заявки, подтверждённая НМЦД лота, размер и порядок возврата",
    "notice_content": "Полный текст действующей редакции извещения со всеми обязательными сведениями",
    "documentation_content": "Действующая документация и приложения, перечень обязательных сведений с учётом способа закупки",
    "amendments": "Решение, история и редакции изменений, публикация в ЕИС и новый срок подачи",
    "post_deadline_extension": "Какой этап и на каком основании продлён, история изменений и сроки подачи, подачи окончательных предложений и итогов",
    "sme_criteria": "Перечень товаров, работ и услуг заказчика для МСП, принадлежность предмета перечню и НМЦД конкретного лота",
    "sme_notice_deadlines": "Подтверждённый режим МСП, способ, НМЦД, дата извещения и срок подачи",
    "sme_bid_security": "Подтверждённый режим МСП, НМЦД лота, размер и допустимые способы обеспечения заявки",
    "sme_application_requirements": "Подтверждённый режим МСП, полный состав заявки и применимый перечень ч. 19.1, 19.2 ст. 3.4",
    "sme_collective_participation": "Подтверждённый режим МСП и конкретное условие коллективного участия с учётом правовой позиции ФАС",
    "sme_payment": "Основание закупки по № 1352, условие оплаты, документ о приёмке и предусмотренные исключения",
}

# These scope families concern competitive procedures. We deliberately keep
# the general payment, documentation and national-regime rows outside this set.
_COMPETITIVE_KEYS = frozenset({"trademark_equivalent", "clarifications",
    "cancellation", "protocol_compliance", "contract_signing", "notice_deadlines",
    "bid_security", "post_deadline_extension"})


def criterion_keys(finding):
    """Return precise sheet rows for a legal outcome, without reading evidence.

    Generic group labels and document titles are intentionally insufficient.
    A rule outside the supplied sheet remains available in the wider audit.
    """
    if not isinstance(finding, dict):
        return ()
    explicit = finding.get("criterion_keys")
    if isinstance(explicit, str):
        explicit = (explicit,)
    if isinstance(explicit, (list, tuple)):
        keys = tuple(dict.fromkeys(x for x in explicit if isinstance(x, str) and x in BY_KEY))
        if keys:
            return keys
    code = str(finding.get("code") or finding.get("field_path") or "").strip().lower()
    if code in BY_KEY:
        return (code,)
    if code in _RULE_KEYS:
        return _RULE_KEYS[code]
    if code.startswith("223-3.2-3-clarification-") or code.startswith("v341-clarification-"):
        return ("clarifications",)
    if code.startswith("223-3.2-6-cancellation-") or code.startswith("v341-cancellation-"):
        return ("cancellation",)
    if code.startswith("223-3.2-27-bid-security-"):
        return ("bid_security",)
    if code in {
        "v337-national_okpd2_scope", "v337-national_exceptions", "v337-national_eaeu_2026",
        "national", "v337-ban_goods", "v337-restriction_goods", "v337-advantage_goods",
        "v337-okpd2_mismatch", "v337-country_declaration", "v337-registry_confirmation",
        "v337-exception_1875", "v337-minpromtorg_authorization", "v337-eaeu_production",
        "v337-offer_rejection", "v337-lot_mix", "v337-unreliable_origin"}:
        return ("national_1875",)
    # A precise legal reference can classify legacy engines lacking rule codes.
    # Require a narrow provision, not a general citation to article 3 or 4.
    law = str(finding.get("law") or "").lower().replace("ё", "е")
    for pattern, replacement in ((r"\bстать(?:я|и|е|ю|ей)\b", "ст."),
                                 (r"\bчаст(?:ь|и|ью)\b", "ч."),
                                 (r"\bпункт(?:ы|а|ов)?\b", "п.")):
        law = re.sub(pattern, replacement, law)
    law = re.sub(r"[,;:]\s*(?=(?:ст|ч|п)\.)", " ", law)
    law = re.sub(r"\s+", " ", law)
    if re.match(r"223-4-9(?:-|$)", code):
        return ("notice_content",)
    if re.match(r"223-4-10(?:-|$)", code):
        return ("documentation_content",)
    if "1352" in law:
        if re.search(r"\bп\.?\s*(?:18|19)\b", law):
            return ("sme_criteria",)
        if re.search(r"\bп\.?\s*(?:14\s*\(\s*3\s*\)|28)(?!\d)", law):
            return ("sme_payment",)
    refs = (
        (r"(?:ст\.?\s*3\s+ч\.?\s*5[.,]3|ч\.?\s*5[.,]3.{0,25}ст\.?\s*3\b)", "payment_223"),
        (r"(?:ст\.?\s*3\s+ч\.?\s*6[.,]1|ч\.?\s*6[.,]1.{0,25}ст\.?\s*3\b)", "trademark_equivalent"),
        (r"(?:ст\.?\s*4\s+ч\.?\s*9\b|ч\.?\s*9\s+ст\.?\s*4\b)", "notice_content"),
        (r"(?:ст\.?\s*4\s+ч\.?\s*10\b|ч\.?\s*10\s+ст\.?\s*4\b)", "documentation_content"),
        (r"(?:ст\.?\s*3[.,]2\s+ч\.?\s*15\b|ч\.?\s*15\s+ст\.?\s*3[.,]2\b)", "contract_signing"),
        (r"(?:ст\.?\s*3[.,]2\s+ч\.?\s*(?:13|14)\b|ч\.?\s*(?:13|14)\s+ст\.?\s*3[.,]2\b)", "protocol_compliance"),
        (r"(?:ст\.?\s*3[.,]2\s+ч\.?\s*[34]\b|ч\.?\s*[34]\s+ст\.?\s*3[.,]2\b)", "clarifications"),
        (r"(?:ст\.?\s*3[.,]2\s+ч\.?\s*[567]\b|ч\.?\s*[567]\s+ст\.?\s*3[.,]2\b)", "cancellation"),
        (r"(?:ст\.?\s*4\s+ч\.?\s*11\b|ч\.?\s*11\s+ст\.?\s*4\b)", "amendments"),
        (r"(?:ст\.?\s*3[.,]4\s+ч\.?\s*12\b|ч\.?\s*12\s+ст\.?\s*3[.,]4\b)", "sme_bid_security"),
        (r"(?:ст\.?\s*3[.,]4\s+ч\.?\s*19[.,][12]\b|ч\.?\s*19[.,][12].{0,25}ст\.?\s*3[.,]4\b)", "sme_application_requirements"),
        (r"(?:ст\.?\s*3[.,]4\s+ч\.?\s*3\b|ч\.?\s*3\s+ст\.?\s*3[.,]4\b)", "sme_notice_deadlines"),
        (r"(?:ст\.?\s*3[.,]2\s+ч\.?\s*(?:17|19|21|23)\b|ч\.?\s*(?:17|19|21|23)\s+ст\.?\s*3[.,]2\b)", "notice_deadlines"),
    )
    matched = tuple(key for pattern, key in refs if re.search(pattern, law))
    # A composite citation cannot turn one general signal into many findings.
    return matched if len(matched) == 1 else ()


def _text(value):
    return " ".join(str(value or "").replace("\xa0", " ").replace("\u202f", " ").split())


def _doc_identity(doc):
    meta = doc.get("meta") or {}
    return (str(meta.get("sha256") or ""), str(meta.get("source_url") or ""),
            str(meta.get("archive_member") or meta.get("member") or ""))


def document_fingerprint(docs):
    """Stable source identity for exporter merge/cache guards, without text."""
    identities = sorted({_doc_identity(d) for d in docs if isinstance(d, dict) and verified(d)})
    return hashlib.sha256(json.dumps(identities, ensure_ascii=False).encode()).hexdigest()


def _source_support(item, docs, body_cache=None):
    """Return an exact normalized source fragment and the unique original."""
    requested = _text(item.get("document") or item.get("source"))
    url = str(item.get("source_url") or "").strip()
    candidates = []
    for doc in docs:
        names = {_text(doc.get(k)) for k in ("name", "local", "local_path")}
        names.add(_text((doc.get("meta") or {}).get("filename")))
        names.discard("")
        name_match = bool(requested and (requested in names or
            os.path.basename(requested.replace("\\", "/")) in {
                os.path.basename(x.replace("\\", "/")) for x in names}))
        url_match = bool(url and url == str((doc.get("meta") or {}).get("source_url") or doc.get("url") or ""))
        if requested and url:
            matched = name_match and url_match
        else:
            matched = name_match or url_match
        if matched:
            candidates.append(doc)
    if not candidates:
        return None
    # A filename shared by different source versions is not a unique citation.
    if len({_doc_identity(d) for d in candidates}) != 1:
        return None
    doc = candidates[0]
    if body_cache is None:
        body_cache = {}
    body = body_cache.get(id(doc))
    if body is None:
        body = _text(doc.get("text"))
        body_cache[id(doc)] = body
    for field in ("source_excerpt", "match_text", "quote", "evidence"):
        excerpt = _text(item.get(field))
        if len(excerpt) >= 15 and excerpt in body:
            return doc, excerpt[:2200]
    # Existing date engines append a calculation around quoted source labels.
    # A complete semicolon/newline-delimited source clause remains exact evidence.
    for fragment in re.split(r"[;\n]", str(item.get("evidence") or "")):
        excerpt = _text(fragment).strip(" .«»\"")
        if len(excerpt) >= 25 and excerpt in body:
            return doc, excerpt[:2200]
    return None


def targeted_reviews(p, docs):
    """Mine concrete payment/brand clauses, retaining exceptions for legal review.

    A long payment term can be lawful under an applicable exception/regulation;
    a brand can be permitted for compatibility/spares. These signals therefore
    never create violations or a blanket compliance conclusion.
    """
    results, seen = [], set()
    payment = re.compile(r"(?:срок\s+оплат\w*|оплат\w*\s+(?:производ\w*|осуществля\w*))"
        r"[^\n;!?]{0,180}?\b(?:\d{1,3}|семи|семь|десяти|тридцати)\s*"
        r"(?:\([^\n)]{0,35}\)\s*)?(?:рабоч\w*|календарн\w*)\s+дн\w*", re.I)
    brand = re.compile(r"(?:эквивалент\w*\s+не\s+допуска\w*|"
        r"без\s+возможност\w*\s+(?:поставки\s+)?эквивалент\w*)", re.I)
    sme_list = re.compile(r"переч(?:ень|н\w*)\s+товар\w*[^\n;!?]{0,130}"
        r"(?:мсп|субъект\w*\s+мал\w*[^\n;!?]{0,60}предпринимательств\w*)", re.I)
    generic = re.compile(r"\b(?:например|образец|пример|цитата)\b|"
        r"(?:запрещается|не\s+допускается)\s+устанавливать", re.I)
    for doc in list(docs)[:650]:
        if not isinstance(doc, dict) or not verified(doc):
            continue
        name = str(doc.get("name") or "")
        # Regulations and worked examples are not actual tender requirements.
        if re.search(r"положени\w*\s+о\s+закупк|образец|пример|шаблон", name, re.I):
            continue
        body = str(doc.get("text") or "")[:750000].replace("\xa0", " ")
        for rule, pattern, title, law, note in (
            ("vp-payment-term", payment, "Сверить условие срока оплаты с применимыми исключениями",
             BY_KEY["payment_223"].law,
             "Сопоставить начало отсчёта с приёмкой, вид договора, положение о закупке и исключения ч. 5.3, 5.4 ст. 3. Условие проекта не доказывает фактическую просрочку оплаты."),
            ("vp-trademark-exclusion", brand, "Проверить запрет эквивалента и законные исключения",
             BY_KEY["trademark_equivalent"].law,
             "Проверить конкретный товар, совместимость, запасные части и другие исключения п. 3 ч. 6.1 ст. 3. Упоминание товарного знака само по себе не доказывает нарушение."),
            ("vp-sme-purchase-list", sme_list, "Сверить предмет и НМЦД лота с перечнем закупок МСП заказчика",
             BY_KEY["sme_criteria"].law,
             "Пункты 18, 19 постановления № 1352 относятся к перечню товаров, работ, услуг заказчика и НМЦД лота, а не к критериям оценки заявок. Нужны соответствующая позиция перечня и подтверждённая НМЦД."),
        ):
            for match in list(pattern.finditer(body))[:8]:
                start = max(body.rfind("\n", 0, match.start()) + 1,
                            body.rfind(";", 0, match.start()) + 1, match.start()-220)
                end = min(len(body), match.end()+220)
                stop = re.search(r"[\n;!?]", body[match.end():end])
                if stop:
                    end = match.end()+stop.start()
                excerpt = _text(body[start:end])
                if generic.search(excerpt):
                    continue
                identity = (rule, _doc_identity(doc), excerpt)
                if identity in seen:
                    continue
                seen.add(identity)
                results.append({"status": "check", "field_path": rule,
                    "item": title, "law": law, "evidence": excerpt,
                    "match_text": match.group(0), "source_excerpt": excerpt,
                    "document": name, "source": name,
                    "source_url": (doc.get("meta") or {}).get("source_url") or "",
                    "location": "Оригинал ЕИС, позиция "+str(match.start()+1),
                    "note": note, "criterion_keys": list(criterion_keys({"code": rule}))})
    return results


def _status(item):
    if str(item.get("code") or item.get("field_path") or "").lower() == "223-4-11-change-after-bid-deadline":
        # An amendment after the submission deadline is not by itself the
        # statutory offence. Historical detectors used to overstate that fact.
        if not (item.get("statutory_breach_verified") is True and
                item.get("post_deadline_breach_basis") and item.get("legal_basis_verified") is True):
            return "unknown"
    if item.get("kind") == "violation" or item.get("status") == "violation":
        return "violation"
    # An individual 'ok' field is not a finding of whole-row compliance.
    if item.get("status") in {"compliant", "ok", "pass"} and item.get("criterion_complete") is True:
        return "compliant"
    return "unknown"


def _administrative_basis(item, key):
    """Retain a separately verified, dated administrative event qualification.

    This adapter never infers a KoAP offence from a tender clause. The narrow
    allowlist prevents a historical blanket 7.32.3 mapping from being copied.
    """
    if not (item.get("administrative_basis_verified") is True and
            item.get("administrative_event_date_verified") is True and
            item.get("administrative_event_source_verified") is True and
            item.get("administrative_event_evidence")):
        return ""
    try:
        event_day = date.fromisoformat(str(item.get("administrative_event_date") or "")[:10])
    except (TypeError, ValueError):
        return ""
    if event_day < date(2025, 3, 1):
        return ""
    event = item.get("administrative_event_type")
    allowed = {
        "document_requirements": (2, {"trademark_equivalent", "national_1875", "clarifications",
            "cancellation", "protocol_compliance", "notice_deadlines", "bid_security", "notice_content",
            "documentation_content", "amendments", "post_deadline_extension", "sme_notice_deadlines", "sme_bid_security"}),
        "participant_requirements": (3, {"sme_criteria", "sme_application_requirements", "sme_collective_participation"}),
        "contract_conclusion": (5, {"contract_signing"}),
        "actual_payment_delay": (7, {"payment_223", "sme_payment"}),
    }
    if event not in allowed or key not in allowed[event][1]:
        return ""
    part = allowed[event][0]
    basis = str(item.get("administrative_basis") or "").strip()
    if not re.search(r"\b7[.]30[.]4\b", basis) or "7.32.3" in basis:
        return ""
    if not re.search(r"\b(?:ч\.?|часть)\s*"+str(part)+r"(?!\d|[.,]\d)", basis, re.I):
        return ""
    return basis[:500]


def _sme_payment_scope(p, sme, docs):
    if sme["value"] is True:
        return True
    # A nonexclusive procurement can still have an SME contractor (p. 14(3)).
    # Unrestricted participation alone says nothing about the winning party.
    from v311_verified_customer import _official_source
    card = p.get("card_fields_json") or {}
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except (TypeError, ValueError):
            card = {}
    evidence = [p.get("fact_evidence") or {},
                card.get("fact_evidence") or {} if isinstance(card, dict) else {}]
    states = set()
    reg = str(p.get("reg") or p.get("reg_number") or "")
    for facts in evidence:
        fact = facts.get("supplier_sme") or {}
        if not isinstance(fact, dict) or fact.get("verified") is not True or not isinstance(fact.get("value"), bool):
            continue
        if fact.get("source") in {"official_eis_card", "official_eis_getDocsIP"}:
            if _official_source(fact.get("source_url") or "", reg) and fact.get("supplier_inn"):
                states.add(fact["value"])
        elif fact.get("source") == "official_eis_document":
            if fact.get("supplier_inn") and _source_support(fact, docs):
                states.add(fact["value"])
    return True if states == {True} else None


def evaluate(p, docs, findings=(), checks=(), detail=None, card=None):
    """Project the shared engine's findings onto all 19 rows without I/O."""
    p, detail, card = dict(p or {}), detail or {}, card or {}
    docs = [d for d in list(docs or ()) if isinstance(d, dict)]
    originals = [d for d in docs if verified(d) and str(d.get("text") or "").strip()]
    if card and not p.get("card_fields_json"):
        p["card_fields_json"] = card
    sme = resolve_sme(p, originals)
    sme_payment_status = _sme_payment_scope(p, sme, originals)
    competitive = competitive_rule_scope(p)
    outcomes = {criterion.key: [] for criterion in CRITERIA}
    unknown_items = {criterion.key: [] for criterion in CRITERIA}
    seen, body_cache = set(), {}
    for source_index, item in enumerate(list(findings or ()) + list(checks or ())):
        if not isinstance(item, dict):
            continue
        keys = criterion_keys(item)
        if not keys:
            continue
        support = _source_support(item, originals, body_cache)
        status = _status(item)
        for key in keys:
            criterion = BY_KEY[key]
            applicable = (sme_payment_status is True if key == "sme_payment" else
                sme["value"] is True if criterion.sme_only else
                competitive is True if key in _COMPETITIVE_KEYS else True)
            if not support or not item.get("law") or not applicable:
                unknown_items[key].append({"code": str(item.get("code") or item.get("field_path") or ""),
                    "status": "unknown", "reason": "Применимость или точный фрагмент проверенного оригинала не подтверждены"})
                continue
            doc, excerpt = support
            identity = (key, status, str(item.get("code") or item.get("field_path") or ""),
                        _doc_identity(doc), _text(item.get("evidence")))
            if identity in seen:
                continue
            seen.add(identity)
            result = {"status": status, "source_index": source_index,
                "evidence": _text(item.get("evidence"))[:3000],
                "source_excerpt": excerpt, "document": str(doc.get("name") or item.get("document") or ""),
                "source_url": str((doc.get("meta") or {}).get("source_url") or ""),
                "sha256": str((doc.get("meta") or {}).get("sha256") or ""),
                "archive_member": str((doc.get("meta") or {}).get("archive_member") or ""),
                "law": str(item.get("law") or ""), "law_url": str(item.get("law_url") or ""),
                "code": str(item.get("code") or item.get("field_path") or ""),
                "title": str(item.get("title") or item.get("item") or ""),
                "note": str(item.get("note") or ""), "legal_basis_verified": True,
                # Administrative qualification requires its own dated event
                # investigation. Never copy obsolete blanket mappings.
                "administrative_basis": _administrative_basis(item, key) if status == "violation" else ""}
            outcomes[key].append(result)
    chosen = {}
    for key, items in outcomes.items():
        violations = [x for x in items if x["status"] == "violation"]
        compliant = [x for x in items if x["status"] == "compliant"]
        unresolved = [x for x in items if x["status"] == "unknown"]
        if violations:
            chosen[key] = violations[0]
        elif compliant and not unresolved and not unknown_items[key]:
            chosen[key] = compliant[0]
    rows = inspection_rows(chosen, sme_status=sme["value"], sme_payment_status=sme_payment_status)
    for row in rows:
        criterion = BY_KEY[row["key"]]
        if row["key"] == "sme_payment":
            applicable = sme_payment_status
        elif criterion.sme_only:
            applicable = sme["value"]
        elif row["key"] in _COMPETITIVE_KEYS:
            applicable = competitive
        else:
            applicable = True
        status = "violation" if row["has_violation"] else "compliant" if row["no_violation"] else "not_applicable" if applicable is False else "unknown"
        row.update(status=status, applicable=applicable,
            results=outcomes[row["key"]], unresolved=unknown_items[row["key"]],
            review_required=(status == "unknown"),
            status_label={"violation": "Есть нарушение", "compliant": "Нет нарушения",
                "not_applicable": "Не применяется", "unknown": "Требует проверки"}[status],
            reason=("Режим закупки не подтверждён" if applicable is None else
                "Критерий не применяется к установленному режиму закупки" if applicable is False else
                "Недостаточно подтверждённых данных для вывода по критерию" if status == "unknown" else ""),
            required_evidence=_REQUIRED_EVIDENCE[row["key"]], comment="",
            koap="; ".join(dict.fromkeys(x["administrative_basis"] for x in outcomes[row["key"]]
                if x["status"] == "violation" and x["administrative_basis"])))
    metadata = {"reg": str(p.get("reg") or p.get("reg_number") or detail.get("registrationNumber") or ""),
        "title": str(p.get("title") or detail.get("purchaseName") or ""),
        "customer": str(p.get("customer") or ""), "customer_inn": str(p.get("customer_inn") or ""),
        "method": str(p.get("method") or detail.get("purchaseMethodName") or ""),
        "published": str(p.get("published") or p.get("published_at") or detail.get("publicationDate") or ""),
        "submission_start": str(p.get("submission_start") or detail.get("submissionStart") or ""),
        "submission_end": str(p.get("submission_end") or detail.get("submissionEnd") or ""),
        "price": str(p.get("price") or ""), "price_kind": str(p.get("price_kind") or ""),
        "url": str(p.get("url") or "")}
    return {"revision": REVISION, "rows": rows, "outcomes": outcomes, "metadata": metadata,
        "sme_status": sme["value"], "sme_scope": sme, "competitive_status": competitive,
        "sme_payment_status": sme_payment_status,
        "document_fingerprint": document_fingerprint(docs),
        "verified_documents": len(originals), "review_required": any(r["review_required"] for r in rows),
        "confirmed_criteria": sum(bool(r["has_violation"]) for r in rows),
        "comments_blank": True}


def _tag(items):
    return [{**item, "criterion_keys": list(criterion_keys(item))} if isinstance(item, dict) else item
            for item in list(items or ())]


def install(ns):
    """Use the same criterion mapping in general, manual and thematic audits."""
    analyze, build = ns.get("analyze"), ns.get("build_checks")
    if not callable(analyze) or not callable(build):
        raise RuntimeError("Inspection sheet requires the common legal analyzer and checklist")
    if getattr(analyze, "__vp_inspection__", False):
        return

    @wraps(analyze)
    def analyze_with_criteria(p, docs):
        return _tag(analyze(p, docs))

    @wraps(build)
    def checks_with_criteria(p, detail, card, docs):
        existing = _tag(build(p, detail, card, docs))
        signatures = {(_text(x.get("document")), _text(x.get("evidence")))
                      for x in existing if isinstance(x, dict)}
        for item in targeted_reviews(p, docs):
            signature = (_text(item.get("document")), _text(item.get("evidence")))
            if signature not in signatures:
                existing.append(item)
                signatures.add(signature)
        return existing

    analyze_with_criteria.__vp_inspection__ = True
    ns["analyze"], ns["build_checks"] = analyze_with_criteria, checks_with_criteria
    ns["vp_inspection_evaluate"] = evaluate
    print("VP_INSPECTION_ENGINE_READY criteria=19 comments_blank=True evidence_required=True", flush=True)
