"""Narrow control of an express contract-conclusion condition under 223-FZ.

The ordinary range in article 3.2(15) is 10--20 calendar days from EIS
publication of the final protocol. This module assesses a tender condition,
never the actual signature date or an administrative offence. Incomplete
sources, special cases and competing terms remain matters for review.

2019-01-01 is a conservative automation boundary for the 505-FZ transition,
not the date on which article 3.2(15) first entered into force.
"""
from __future__ import annotations

from collections import defaultdict
from bisect import bisect_right
from datetime import date
from functools import wraps
import os
import re

from legal_applicability import competitive_rule_scope
from legal_document_context import (clause_variant_context, document_context,
    local_context, prepare_context_index, source_scope_context)
from v331_broad_legal import verified
from v341_eis_facts import parse_day

CODE = "223-3.2-15-CONTRACT-CONCLUSION-TERM"
LAW = "223-ФЗ, ст. 3.2 ч. 15"
LAW_URL = "https://www.consultant.ru/document/cons_doc_LAW_116964/05cd0add21b39d478c8b8af91fb2f2cd80d4a6e8/"
SUPPORTED_FROM = date(2019, 1, 1)
MAX_DOCUMENTS = 650
MAX_TEXT = 750000
MAX_TERMS = 100
MAX_PACKAGE_TERMS = 1000
ROLES = frozenset({"documentation", "notice", "project_contract"})
EVIDENCE_PREFIX = "Условие о заключении договора: "

_EIS = r"(?:ЕИС|единой\s+информационной\s+системе(?:\s+в\s+сфере\s+закупок)?)"
_PROTOCOL = r"(?:итогового\s+протокола|протокола\s+подведения\s+итогов(?:\s+закупки)?)"
_RANGE = re.compile(
    r"\b(?:договор|контракт)\s+"
    r"(?:(?:по\s+результатам\s+(?:конкурентной\s+)?закупки|с\s+победителем(?:\s+закупки)?)\s+)?"
    r"(?:заключается|должен\s+быть\s+заключ[её]н|подлежит\s+заключению)\s+"
    r"не\s+ранее\s+(?:чем\s+)?(?:через\s+)?(?P<lower>\d{1,3})(?![\d.,])"
    r"\s*(?:\((?P<lower_words>[^)\n]{1,45})\)\s*)?"
    r"(?P<lower_unit>(?:календарн\w*|рабоч\w*)\s+)?(?:дней|дня|день)"
    r"\s*,?\s*(?:и|но)\s+не\s+позднее\s+(?:чем\s+)?(?:через\s+)?"
    r"(?P<upper>\d{1,3})(?![\d.,])\s*(?:\((?P<upper_words>[^)\n]{1,45})\)\s*)?"
    r"(?P<upper_unit>(?:календарн\w*|рабоч\w*)\s+)?(?:дней|дня|день)\s+"
    r"(?:с\s+даты|со?\s+дня)\s+размещения\s+(?:в\s+" + _EIS + r"\s+" + _PROTOCOL +
    r"|" + _PROTOCOL + r"\s+в\s+" + _EIS + r")(?!\w)", re.I)
_SPECIAL = re.compile(
    r"\bодобрени\w*|\bобжалован\w*|\bжалоб\w*|\bантимонопольн\w*|"
    r"\bприостанов\w*|\bнесостоявш\w*|\bединственн\w*\s+участник\w*|"
    r"\bуклонени\w*|\b(?:вторым|следующим)\s+участник\w*|"
    r"\bза\s+исключением\b|\bесли\s+иное\b", re.I)
_CALENDAR = re.compile(
    r"\b(?:если|в\s+случае)\b[^.!?;\n]{0,160}\b(?:день|дня|дней|срок\w*)\b"
    r"[^.!?;\n]{0,100}\b(?:приходится|выпадает)\b[^.!?;\n]{0,80}"
    r"\b(?:суббот\w*|воскресень\w*|нерабоч\w*|выходн\w*|праздничн\w*)\b|"
    r"\b(?:перенос\w*|перен[её]с\w*)[^.!?;\n]{0,160}"
    r"(?:ближайш\w*\s+рабоч\w*\s+д(?:ень|ня)|последн\w*\s+д(?:ень|ня)|193\b)|"
    r"\b193\s*(?:ГК\b|Гражданск\w*\s+кодекс\w*)|"
    r"\bГК\s*(?:РФ\s*)?(?:ст[.]?\s*|стать[еия]\s*)193\b", re.I)
_REPLACED_TERM = re.compile(
    r"\b(?:указанн\w*|данн\w*|эт[оа]\w*|вышепривед[её]нн\w*)\s+"
    r"(?:услови\w*|положени\w*|требовани\w*|срок\w*)[^.!?;\n]{0,140}?"
    r"\b(?:замен[её]н\w*|измен[её]н\w*|исключ[её]н\w*|отмен[её]н\w*)\b", re.I)
_INAPPLICABLE = re.compile(
    r"\bнеконкурентн\w*|\bединственн\w*\s+поставщик\w*|\b44\s*[-–—‑]?\s*ФЗ\b|"
    r"\bсубподряд\w*|\b(?:предлагаем\w*|планируем\w*)\s+редакци\w*|"
    r"\bне\s+устанавлива\w*\s+(?:следующ\w*\s+)?услови\w*|"
    r"\b(?:исключ[её]нн\w*|отмен[её]нн\w*)\s+(?:услови\w*|положени\w*)|"
    r"\bутративш\w*\s+силу\s+(?:услови\w*|положени\w*|редакци\w*)|"
    r"\b(?:указанн\w*|данн\w*|эт[оа]\w*|вышепривед[её]нн\w*)\s+"
    r"(?:услови\w*|положени\w*|требовани\w*)[^.!?\n]{0,90}\bне\s+применя\w*", re.I)
_TIMING_SIGNAL = re.compile(
    r"(?:\b(?:срок\w*\s+)?заключени\w*\s+(?:договора|контракта)|"
    r"\b(?:договор|контракт)\s+(?:(?:не\s+)?может\s+быть\s+заключ[её]н|"
    r"заключается|должен\s+быть\s+заключ[её]н|подлежит\s+заключению))"
    r"[^;!?\n]{0,350}?\b(?:дней|дня|день)\b", re.I)
_CONCLUSION_RELATION = re.compile(
    r"\bзаключ\w*[^.!?\n]{0,100}\b(?:договор\w*|контракт\w*)|"
    r"\b(?:договор\w*|контракт\w*)[^.!?\n]{0,100}\bзаключ\w*|"
    r"\b(?:иные|другие|особые)\s+срок\w*", re.I)
_WORDS = {
    "ноль": 0, "нуля": 0, "один": 1, "одного": 1, "одна": 1,
    "два": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4,
    "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11,
    "двенадцать": 12, "двенадцати": 12, "тринадцать": 13, "тринадцати": 13,
    "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15, "пятнадцати": 15,
    "шестнадцать": 16, "шестнадцати": 16, "семнадцать": 17, "семнадцати": 17,
    "восемнадцать": 18, "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19,
    "двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30,
    "сорок": 40, "сорока": 40, "пятьдесят": 50, "пятидесяти": 50,
    "шестьдесят": 60, "шестидесяти": 60, "семьдесят": 70, "семидесяти": 70,
    "восемьдесят": 80, "восьмидесяти": 80, "девяносто": 90, "девяноста": 90,
}
REASONS = {
    "contract_term_method": "Не подтверждён конкурентный способ закупки, к которому применяется ч. 15 ст. 3.2",
    "contract_term_date": "Не подтверждена согласованная дата извещения для определения применимой редакции нормы",
    "contract_term_historical": "Закупка относится к переходному или историческому периоду; нужен отдельный пакет применимости",
    "contract_term_source_revision": "Действующая редакция исходного условия не определена однозначно",
    "contract_term_scope": "Условие не связано однозначно с проверяемой закупкой и её лотом",
    "contract_term_variant": "Не подтверждён выбор этого условия из альтернативных вариантов",
    "contract_term_context": "Фрагмент относится к явно неприменимому разделу или объявлен не действующим условием этой закупки",
    "contract_term_exception": "В источнике есть специальный случай или исключение: нужен разбор его связи с данным сроком",
    "contract_term_calendar": "Условие связано с нерабочим днём или переносом по ст. 193 ГК РФ; срок требует отдельной проверки календаря",
    "contract_term_replaced": "Источник указывает на замену или изменение данного условия; исходный диапазон не подтверждён как действующий",
    "contract_term_working_days": "Указаны рабочие дни; их нельзя автоматически приравнять к календарному диапазону ч. 15",
    "contract_term_spelling": "Число дней и его словесная запись не согласованы или не разобраны однозначно",
    "contract_term_range_invalid": "Начало разрешённого диапазона позже его окончания; требуется проверка условия",
    "contract_term_coverage": "Полнота прочитанных оригиналов и приложений не подтверждена; возможно иное применимое условие",
    "contract_term_collection_limit": "Не все источники или условия разобраны в пределах установленного бюджета",
    "contract_term_conflict": "В действующих источниках сопоставимого лота указаны разные диапазоны заключения договора",
    "contract_term_other_condition": "Есть дополнительное условие о сроке заключения договора, не охваченное закрытой грамматикой диапазона",
    "contract_term_not_reproduced": "Заявленное условие не воспроизведено в текущих проверенных оригиналах",
    "contract_term_occurrence_ambiguous": "Точная позиция повторяющегося условия в оригинале не определена",
    "contract_term_source_changed": "Сохранённый SHA-256 или вложенный путь отличается от текущего проверенного источника",
    "contract_term_scope_identity": "Область условия конкретного лота не связана с проверенным оригиналом в формате идентичности эпизода",
}


def _text(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _number_words(value):
    words = _text(value).casefold().replace("ё", "е").split()
    if len(words) == 1:
        return _WORDS.get(words[0])
    if (len(words) == 2 and _WORDS.get(words[0]) in {20, 30, 40, 50, 60, 70, 80, 90}
            and _WORDS.get(words[1]) in set(range(1, 10))):
        return _WORDS[words[0]] + _WORDS[words[1]]
    return None


def _publication(p):
    values = [p.get(key) for key in ("published", "published_at") if p.get(key)]
    dates = [parse_day(value) for value in values]
    if not dates or any(value is None for value in dates) or len(set(dates)) != 1:
        return ["contract_term_date"]
    return ["contract_term_historical"] if dates[0] < SUPPORTED_FROM else []


def _coverage(p, docs):
    expected, received = p.get("original_expected"), p.get("original_received")
    complete = (p.get("source_incomplete") is False and
                isinstance(expected, int) and not isinstance(expected, bool) and
                isinstance(received, int) and not isinstance(received, bool) and
                expected == received and expected >= 0)
    for key in ("unreadable", "documents_unreadable"):
        try:
            if int(p.get(key) or 0) > 0:
                complete = False
        except (ValueError, TypeError):
            complete = False
    for doc in docs:
        if not isinstance(doc, dict):
            complete = False
            continue
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        if (doc.get("status") in {"downloaded_no_text", "archive_unreadable"}
                or meta.get("archive_warnings") or
                doc.get("status") == "checked" and not str(doc.get("text") or "").strip()):
            complete = False
    return [] if complete else ["contract_term_coverage"]


def _revision_groups(documents):
    groups = defaultdict(list)
    for doc in documents:
        if document_context(doc)["role"] not in ROLES:
            continue
        meta = doc.get("meta") or {}
        name = os.path.splitext(os.path.basename(str(doc.get("name") or "").replace("\\", "/")))[0]
        name = re.sub(r"(?<!\w)(?:v|версия|редакция|ред\.)[\s_№-]*\d+(?:\.\d+)*", "", name.casefold())
        name = re.sub(r"\b(?:от\s*)?(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})\b", "", name)
        name = re.sub(r"[\W_]+", "", name)
        if name:
            groups[(name, str(meta.get("lot_number") or meta.get("lot") or ""),
                    str(meta.get("lot_id") or ""))].append(doc)
    return {id(doc) for members in groups.values()
            if len({((doc.get("meta") or {}).get("sha256"),
                     (doc.get("meta") or {}).get("archive_member") or "") for doc in members}) > 1
            for doc in members}


def _same_scope(left, right):
    a, b = left["scope"], right["scope"]
    for key in ("lots", "lot_ids"):
        if a[key] and b[key] and a[key] != b[key]:
            return False
    return True


def _section(body, start, index):
    boundaries = index["boundaries"]
    position = bisect_right(boundaries, start)
    left = boundaries[max(0, position-1)]
    right = boundaries[min(position, len(boundaries)-1)]
    return left, right, body[left:right]


def _context(p, doc, body, start, end, index, document, revisions):
    if not local_context(body, start, end)["eligible_actual"]:
        return None
    variant = clause_variant_context(body, start, end, context_index=index)
    if "alternative_not_selected" in variant["reasons"]:
        return None
    reasons = []
    restricted = [reason for reason in document["reasons"] if
                  not (document["role"] == "project_contract" and reason == "project_is_not_completed_act")]
    if restricted or id(doc) in revisions:
        reasons.append("contract_term_source_revision")
    scope = source_scope_context(p, doc, start, context_index=index)
    if not scope["eligible_actual"]:
        reasons.append("contract_term_scope")
    if not variant["eligible_actual"]:
        reasons.append("contract_term_variant")
    if _INAPPLICABLE.search(_section(body, start, index)[2]):
        reasons.append("contract_term_context")
    return {"scope": scope, "reasons": reasons}


def _source(item, reason):
    doc = item["doc"]
    meta = doc.get("meta") or {}
    return {"document": str(doc.get("name") or ""),
        "source_url": str(meta.get("source_url") or doc.get("url") or ""),
        "sha256": str(meta.get("sha256") or ""),
        "source_position": item["start"], "excerpt": item["excerpt"][:1500],
        "reason_code": reason, "lots": item["scope"]["lots"], "lot_ids": item["scope"]["lot_ids"]}


def _collect(p, docs):
    documents = [doc for doc in docs[:MAX_DOCUMENTS]
                 if isinstance(doc, dict) and isinstance(doc.get("meta"), dict) and verified(doc)]
    revisions = _revision_groups(documents)
    shared = _publication(p) + _coverage(p, docs)
    if competitive_rule_scope(p) is not True:
        shared.append("contract_term_method")
    if (len(docs) > MAX_DOCUMENTS or
            any(len(str(doc.get("text") or "")) > MAX_TEXT for doc in docs if isinstance(doc, dict))):
        shared.append("contract_term_collection_limit")
    records, limitations, budget_limited = [], [], False
    for doc in documents:
        context = document_context(doc)
        if context["role"] not in ROLES:
            continue
        body = str(doc.get("text") or "")[:MAX_TEXT]
        if not re.search(r"договор|контракт|иные\s+сроки", body, re.I):
            continue
        matches = list(_RANGE.finditer(body))
        index = prepare_context_index(body)
        # An exception or an additional one-sided/worded condition can appear
        # in another current original. It can limit a claim without becoming a
        # statutory finding of its own. Source and lot vetoes apply to it too.
        signals = []
        # A single newline can be a plain extraction wrap. Keep its byte/char
        # position while preventing a paragraph break from joining subjects.
        signal_body = re.sub(r"(?<!\n)\n(?!\n)", " ", body)
        for signal in _TIMING_SIGNAL.finditer(signal_body):
            if not any(match.start() <= signal.end() <= match.end() for match in matches):
                signals.append((signal.start(), signal.end(), "contract_term_other_condition",
                                signal.start(), signal.end()))
        seen_sections = set()
        for pattern, reason in ((_SPECIAL, "contract_term_exception"),
                                (_CALENDAR, "contract_term_calendar"),
                                (_REPLACED_TERM, "contract_term_replaced")):
            for special in pattern.finditer(signal_body):
                left, right, section = _section(body, special.start(), index)
                if (left, right, reason) in seen_sections:
                    continue
                if (any(left <= match.start() < right for match in matches)
                        or _CONCLUSION_RELATION.search(section.replace("\n", " "))):
                    seen_sections.add((left, right, reason))
                    signals.append((left, right, reason, special.start(), special.end()))
        if len(signals) > MAX_TERMS or len(matches) > MAX_TERMS:
            budget_limited = True
        for start, end, reason, anchor_start, anchor_end in signals[:MAX_TERMS]:
            # Section excerpts include their lot heading. Bind scope at the
            # actual exception, after the heading, rather than its first byte.
            limited = _context(p, doc, body, anchor_start, anchor_end, index, context, revisions)
            if limited is not None and not limited["reasons"]:
                if len(records) + len(limitations) >= MAX_PACKAGE_TERMS:
                    budget_limited = True
                    break
                limitations.append({"doc": doc, "scope": limited["scope"], "start": start,
                                    "excerpt": body[start:end], "reason": reason})
        for match in matches[:MAX_TERMS]:
            limited = _context(p, doc, body, match.start(), match.end(), index, context, revisions)
            if limited is None:
                continue
            if len(records) + len(limitations) >= MAX_PACKAGE_TERMS:
                budget_limited = True
                break
            reasons = list(shared) + limited["reasons"]
            scope = limited["scope"]
            low, high = int(match["lower"]), int(match["upper"])
            if low > high:
                reasons.append("contract_term_range_invalid")
            if any(str(match[key] or "").casefold().startswith("рабоч") for key in ("lower_unit", "upper_unit")):
                reasons.append("contract_term_working_days")
            for key, value in (("lower_words", low), ("upper_words", high)):
                if match[key] and _number_words(match[key]) != value:
                    reasons.append("contract_term_spelling")
            if len(matches) > MAX_TERMS:
                reasons.append("contract_term_collection_limit")
            records.append({"doc": doc, "reg": str(p.get("reg") or p.get("reg_number") or ""),
                "scope": scope, "start": match.start(),
                "excerpt": match[0], "lower": low, "upper": high,
                "bad": low < 10 or high > 20,
                "range": (low, high, "working" if str(match["lower_unit"] or "").casefold().startswith("рабоч") else "calendar",
                          "working" if str(match["upper_unit"] or "").casefold().startswith("рабоч") else "calendar"),
                "reasons": list(dict.fromkeys(reasons)), "related_sources": []})
        if budget_limited and len(records) + len(limitations) >= MAX_PACKAGE_TERMS:
            break
    for item in records:
        if budget_limited:
            # The omitted signal may qualify another original of this lot.
            # A bounded scan cannot prove the absence of such a condition.
            item["reasons"].append("contract_term_collection_limit")
            continue
        for peer in records:
            if (peer is not item and _same_scope(item, peer) and peer["range"] != item["range"]
               and "contract_term_scope" not in peer["reasons"]
               and "contract_term_source_revision" not in peer["reasons"]
               and "contract_term_context" not in peer["reasons"]):
                item["reasons"].append("contract_term_conflict")
                item["related_sources"].append(_source(peer, "contract_term_conflict"))
                break
        for peer in limitations:
            if _same_scope(item, peer):
                if peer["reason"] not in item["reasons"]:
                    item["reasons"].append(peer["reason"])
                    item["related_sources"].append(_source(peer, peer["reason"]))
                if {"contract_term_exception", "contract_term_other_condition"} <= set(item["reasons"]):
                    break
    return records


def _record(item):
    doc, reasons = item["doc"], list(dict.fromkeys(item["reasons"]))
    meta = doc.get("meta") or {}
    labels = [REASONS[reason] for reason in reasons]
    assessment = {"state": "requires_review" if reasons else "confirmed",
        "minimum_days": item["lower"], "maximum_days": item["upper"],
        "statutory_minimum_days": 10, "statutory_maximum_days": 20,
        "clock": "eis_final_protocol_publication", "fact_type": "contract_condition",
        "lots": item["scope"]["lots"], "lot_ids": item["scope"]["lot_ids"],
        "reason_codes": reasons, "reasons": labels,
        "related_sources": item["related_sources"][:40],
        "source_binding": {"reg": item["reg"], "sha256": str(meta.get("sha256") or ""),
            "source_sha256": str(meta.get("sha256") or ""),
            "source_url": str(meta.get("source_url") or doc.get("url") or ""),
            "source_excerpt": item["excerpt"], "match_text": item["excerpt"],
            "source_position": item["start"], "archive_member": str(meta.get("archive_member") or "")}}
    scope_evidence = ""
    if not reasons and (assessment["lots"] or assessment["lot_ids"]):
        from security_episode_identity import contract_scope_evidence
        scope_evidence = contract_scope_evidence({"reg": item["reg"]}, assessment)
        if not scope_evidence:
            reasons.append("contract_term_scope_identity")
            labels.append(REASONS["contract_term_scope_identity"])
            assessment["state"] = "requires_review"
    title = ("Условие заключения договора допускает срок вне диапазона ч. 15 ст. 3.2" if item["bad"] else
             "Проверить применимость условия о сроке заключения договора")
    result = {"code": CODE, "title": title, "law": LAW, "law_url": LAW_URL,
        "criterion_keys": ["contract_signing"], "document": str(doc.get("name") or ""),
        "source_url": str(meta.get("source_url") or doc.get("url") or ""),
        "sha256": str(meta.get("sha256") or ""), "source_sha256": str(meta.get("sha256") or ""),
        "archive_member": str(meta.get("archive_member") or ""),
        "source_position": item["start"], "source_excerpt": item["excerpt"], "match_text": item["excerpt"],
        "location": "Оригинал ЕИС, позиция " + str(item["start"]+1),
        "evidence": EVIDENCE_PREFIX + item["excerpt"] + scope_evidence,
        "decision_kind": "contract_condition", "legal_fact_type": "document_requirement",
        "contract_conclusion_assessment": assessment,
        "note": ("Проверено условие закупки о заключении договора от размещения итогового протокола в ЕИС. "
                 "Вывод не устанавливает фактическую дату подписания договора и административное правонарушение. "
                 + " ".join(labels))}
    if reasons:
        result.update(kind="review", status="check", field_path=CODE,
            item="Сверить условие о сроке заключения договора и применимые исключения",
            scope="223-ФЗ / заключение договора", source=result["document"],
            methodology_reason_codes=reasons, methodology_reasons=labels)
    else:
        result["kind"] = "violation"
    return result


def evaluate(p, docs):
    """Return narrowly proved defective conditions and explicit review records."""
    prepared = prepare_assessment(p, docs)
    findings, checks, seen = [], [], set()
    for status in ("findings", "checks"):
        for record in prepared[status]:
            assessment = record["contract_conclusion_assessment"]
            identity = (status, tuple(assessment["lots"]), tuple(assessment["lot_ids"]),
                        assessment["minimum_days"], assessment["maximum_days"],
                        tuple(assessment["reason_codes"]))
            if identity in seen:
                continue
            seen.add(identity)
            (findings if status == "findings" else checks).append(record)
    return findings, checks


def prepare_assessment(p, docs):
    """Build one evaluation-scoped snapshot for repeated saved-finding checks."""
    documents = list(docs or ())
    findings, checks = [], []
    for item in _collect(dict(p or {}), documents):
        if item["bad"] or item["reasons"]:
            record = _record(item)
            (checks if record.get("kind") == "review" else findings).append(record)
    return {"findings": findings, "checks": checks, "documents": documents}


def finding_excerpt(item):
    """Read only explicit proof fields or this rule's exact saved serialization.

    Parsing this prefix does not authenticate the claim. ``assess_finding``
    must still reproduce it from the current independent source collection.
    """
    if not isinstance(item, dict):
        return ""
    evidence = item.get("evidence")
    if isinstance(evidence, str):
        from security_episode_identity import CONTRACT_PREFIX, condition_evidence_text, finding_contract_scope
        if CONTRACT_PREFIX in evidence:
            if finding_contract_scope(item) is None:
                return ""
            evidence = condition_evidence_text(item)
    source, match = str(item.get("source_excerpt") or ""), str(item.get("match_text") or "")
    if source and match and _text(source) != _text(match):
        return ""
    if source or match:
        return source or match
    if isinstance(evidence, str) and evidence.startswith(EVIDENCE_PREFIX):
        return evidence[len(EVIDENCE_PREFIX):].strip()
    return ""


def assess_finding(item, prepared):
    """Reproduce a reported condition; flags on the old record never prove it."""
    unknown = {"state": "requires_review", "reason_codes": ["contract_term_not_reproduced"],
               "reasons": [REASONS["contract_term_not_reproduced"]]}
    if (not isinstance(item, dict) or not isinstance(prepared, dict)
            or str(item.get("code") or item.get("field_path") or "").upper() != CODE):
        return unknown
    quote = _text(finding_excerpt(item))
    url, name = str(item.get("source_url") or ""), str(item.get("document") or "")
    if not quote or not (url or name):
        return unknown
    matches = [record for record in list(prepared.get("findings") or ()) + list(prepared.get("checks") or ())
        if (not url or record["source_url"] == url) and (not name or record["document"] == name)
        and _text(record["source_excerpt"]) == quote]
    position = item.get("source_position")
    if position is not None:
        if not isinstance(position, int) or isinstance(position, bool):
            return unknown
        matches = [record for record in matches if record["source_position"] == position]
    if len(matches) != 1:
        return unknown
    record = matches[0]
    if (any(item.get(key) and str(item[key]).strip().lower() != record["sha256"].lower()
            for key in ("sha256", "source_sha256")) or
            any(key in item and item[key] is not None and str(item[key]) != record["archive_member"]
                for key in ("archive_member", "source_archive_member"))):
        return {"state": "requires_review", "reason_codes": ["contract_term_source_changed"],
                "reasons": [REASONS["contract_term_source_changed"]]}
    if position is None:
        # One report may summarize repeated copies of the same clause. A saved
        # record without its occurrence cannot choose one among local repeats.
        for doc in prepared.get("documents") or ():
            if not isinstance(doc, dict):
                continue
            meta = doc.get("meta") or {}
            if (str(meta.get("source_url") or doc.get("url") or "") == record["source_url"]
                    and str(doc.get("name") or "") == record["document"]):
                exact = record["source_excerpt"]
                if str(doc.get("text") or "").count(exact) > 1:
                    return {"state": "requires_review", "reason_codes": ["contract_term_occurrence_ambiguous"],
                            "reasons": [REASONS["contract_term_occurrence_ambiguous"]]}
    return {**record["contract_conclusion_assessment"], "source_binding": {
        **record["contract_conclusion_assessment"]["source_binding"],
        "document": record["document"]}}


def install(ns):
    """Add this subject rule to the shared analyzer, preserving its prior work."""
    analyze, checker = ns.get("analyze"), ns.get("build_checks")
    if not callable(analyze) or not callable(checker):
        raise RuntimeError("Contract conclusion terms require the shared legal engine")
    if getattr(analyze, "__contract_conclusion_terms__", False):
        return

    @wraps(analyze)
    def analyze_terms(p, docs):
        previous = list(analyze(p, docs) or ())
        findings, _ = evaluate(p, docs)
        keys = {(item.get("code"), item.get("document"), item.get("match_text"))
                for item in previous if isinstance(item, dict)}
        return previous + [item for item in findings
                           if (item["code"], item["document"], item["match_text"]) not in keys]

    @wraps(checker)
    def checks_terms(p, detail, card, docs):
        previous = list(checker(p, detail, card, docs) or ())
        _, checks = evaluate(p, docs)
        keys = {(item.get("field_path"), item.get("document"), item.get("match_text"))
                for item in previous if isinstance(item, dict)}
        return previous + [item for item in checks
                           if (item["field_path"], item["document"], item["match_text"]) not in keys]

    analyze_terms.__contract_conclusion_terms__ = True
    ns["analyze"], ns["build_checks"] = analyze_terms, checks_terms
    print("CONTRACT_CONCLUSION_TERMS_READY condition_only=True ordinary_calendar_range=True", flush=True)
