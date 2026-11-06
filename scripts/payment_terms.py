"""Context limits for a claimed SME payment-term violation.

This module does not decide that a procurement complies with the law. A
``confirmed`` result means only that the supplied, independently established
term has no objection from the checks below. Receipt, SME scope, applicable
law and the legal threshold still belong to the calling rule. ``inactive``
means that this source clause cannot establish the current term; retain its
history. A different signed contract never retrospectively cures a draft.

All work is on a supplied evidence snapshot: no network, database or writes.
Extract terms once with ``collect_payment_terms`` and reuse them for findings.
"""
from __future__ import annotations

import re

from legal_document_context import document_context


MAX_DOCUMENTS = 650
MAX_TEXT = 750000
MAX_TERMS_PER_DOCUMENT = 100

REASON_LABELS = {
    "payment_source_unverified": "Происхождение исходного документа не подтверждено",
    "payment_source_other_purchase": "Исходный документ относится к другой закупке",
    "payment_source_other_lot": "Исходное условие относится к другому лоту",
    "payment_fragment_unresolved": "Не установлен точный фрагмент условия оплаты",
    "payment_source_not_contract_term": "Образец, инструкция или нормативная цитата не устанавливает условие этого договора",
    "payment_historical_source": "Документ явно заменён или отменён; сохранён для оценки соответствующего периода",
    "payment_historical_clause": "Фрагмент явно описывает прежнее или исключённое условие",
    "payment_non_sme_clause": "Этот вариант оплаты прямо предназначен для контрагента, не являющегося субъектом МСП",
    "payment_sme_scope_unresolved": "Применимость специального условия для МСП не установлена",
    "payment_specific_sme_clause": "В том же документе есть применимое специальное условие оплаты для МСП",
    "payment_alternative_unselected": "В документе есть альтернативные редакции условия, применимый вариант не установлен",
    "payment_alternative_not_selected": "Документ прямо выбирает другой вариант условия оплаты",
    "payment_conflicting_terms": "Обнаружены разные условия оплаты; их применимость и приоритет требуют сопоставления",
    "payment_cross_phase_terms": "Условия проекта и заключённого договора оцениваются раздельно; поздний договор не определяет условия опубликованного проекта",
    "payment_relation_unresolved": "Не подтверждено, что разные условия относятся к тому же лоту и договору",
    "payment_explicit_replacement": "Подтверждена прямая связь с действующей заменяющей редакцией на том же этапе закупки",
    "payment_phase_conflict": "Противоречивые сведения об этапе и статусе договора",
    "payment_incomplete_originals": "Не завершено получение или чтение оригиналов; возможны уточняющие условия оплаты",
    "payment_collection_limited": "Достигнута граница анализа документов или условий; сопоставление неполное",
    "payment_source_within_bound": "Указанный фрагмент не содержит срока свыше семи рабочих дней",
    "payment_subcontract_condition": "Условие относится к субподрядчику; связь с оплатой основному исполнителю не установлена",
    "payment_negated_or_proposed_clause": "Отрицание, предложение или дополнительное условие не подтверждает действующий порядок оплаты",
    "payment_obligation_unresolved": "Не подтверждено, что сроки относятся к одному платежу или этапу исполнения",
}

_SME = r"(?:субъект\w*\s+(?:мал\w*\s+и\s+средн\w*\s+предпринимательств\w*|МСП)|СМСП|МСП)"
_NON_SME = re.compile(r"(?:не\s+(?:явля\w*|относя\w*|относ\w*)\s+(?:к\s+)?" + _SME +
                      r"|не\s+из\s+числа\s+" + _SME + r"|не\s+" + _SME + r")", re.I)
_SME_CONDITION = re.compile(
    r"(?:\bдля\s+" + _SME + r"|\b(?:если|в\s+случае|при\s+условии)\b[^.;!?]{0,160}" + _SME +
    r"|\bпри\s+заключении\s+договора\s+с\s+" + _SME +
    r"|\b(?:поставщик|подрядчик|исполнитель)\s+явля\w*\s+" + _SME + r")", re.I)
_SUBCONTRACT = re.compile(r"\b(?:субподрядчик\w*|соисполнител\w*|субисполнител\w*)\b", re.I)
_ALTERNATIVE = re.compile(r"\b(?:редакци[яи]|вариант)\s*(?:№|N)?\s*(\d{1,3})(?!\d)", re.I)
_SELECTION = re.compile(r"(?:^|\n)\s*(?:(?:для\s+(?:настоящего\s+договора|данной\s+закупки))\s+)?"
                        r"(?:применяется|выбрана|выбран)\s+(?:редакция|вариант)\s*(?:№|N)?\s*(\d{1,3})\s*(?:[.:;]|$)", re.I)
_LOT_HEADING = re.compile(r"(?:^|\n)\s*(?:\(?\d+[.)]\s*)?Лот\s*(?:№|N|номер)?\s*([\w/-]{1,40})", re.I)
_CONTRACT_HEADING = re.compile(r"(?:^|\n)\s*(?:ЗАКЛЮЧ[ЕЁ]ННЫЙ\s+|ПОДПИСАННЫЙ\s+|ПРОЕКТ\s+)?"
                               r"(?:ДОГОВОР|КОНТРАКТ)\s*(?:№|N)\s*([^\s,;]{1,80})", re.I)
_HISTORICAL = re.compile(r"\b(?:условие\s+(?:исключено|не\s+применяется|утратило\s+силу)|"
                          r"исключено\s+(?:следующее\s+)?условие|"
                          r"в\s+(?:предыдущ\w*|прежн\w*|отмен[её]нн\w*)\s+редакци\w*)", re.I)
_EXAMPLE = re.compile(r"\b(?:например|справочно|цитата|шаблон|пример\s+(?:условия|расч[её]та)|"
                      r"образец\s+расч[её]та|для\s+справки|выдержка\s+из\s+закона)\b", re.I)
_LAW_QUOTE = re.compile(r"\b(?:пунктом|положением|постановлением|законом)\b[^.;!?]{0,180}"
                        r"(?:1352|223[-–]?\s*ФЗ)[^.;!?]{0,100}\b(?:предусмотрено|установлено|определено)\b", re.I)
_ACCEPTANCE = re.compile(r"(?:со?\s+дня|с\s+дат[ыа]|с\s+момента|после(?:\s+дат[ыа])?)\s+"
                         r"(?:подписания|утверждения)\s+(?:заказчиком\s+|сторонами\s+)?"
                         r"(?:акт(?:а|ов)|документ(?:а|ов))\s+"
                         r"(?:сдачи[-–]при[её]мки|при[её]мки|о\s+при[её]мке)\b", re.I)
_SUBJECT = r"\b(?:срок\s+оплаты|оплата|расч[её]т[ыа]?|оплат[уы]|заказчик\s+оплачивает)\b"
# This supplementary grammar only supplies counter-evidence. It never creates
# a violation. In particular it reads the ordinary second variant beginning
# with "Заказчик оплачивает", and word-only seven-day terms.
_COUNTERPART = re.compile(
    r"(?P<subject>" + _SUBJECT + r")(?:(?!" + _SUBJECT + r")[^.;!?]){0,280}?"
    r"(?:в\s+течение|не\s+позднее(?:\s+чем)?|не\s+более|"
    r"не\s+(?:(?:должен|может)\s+)?превышать|не\s+превышает|составляет|[:：])\s*"
    r"(?P<days>\d{1,3}|сем[ьи]|шест[ьи]|пят[ьи]|четыр[её]х?|тр[иеё]х?|дв[ау]х?|одного)\s*"
    r"(?:\((?P<spelling>[^)]{0,45})\)\s*)?"
    r"(?P<unit>рабоч(?:их|его)|календарн(?:ых|ого))\s+(?:дней|дня|дн\.?)(?!\w)", re.I)
_SMALL_NUMBERS = {"семь": 7, "семи": 7, "шесть": 6, "шести": 6, "пять": 5, "пяти": 5,
                  "четыре": 4, "четырех": 4, "три": 3, "трех": 3,
                  "два": 2, "двух": 2, "одного": 1}
_NEGATED_PAYMENT = re.compile(
    r"\b(?:оплат[ауы]|расч[её]т\w*|срок\s+оплаты)\b[^.;!?]{0,65}?\bне\s+"
    r"(?:производ\w*|осуществля\w*|провод\w*|предусмотр\w*|установлен\w*)|"
    r"\bне\s+(?:допускается|предусмотрена|установлена)\s+(?:такая\s+)?оплат\w*|"
    r"\b(?:срок|условие|вариант|редакция)\b[^.;!?]{0,180}?\bне\s+(?:применя\w*|действ\w*)|"
    r"\b(?:применение|использование)\s+(?:услови\w*|формулировк\w*)[^.;!?]{0,550}\bне\s+допускается|"
    r"\bзапрещается\s+(?:применять|использовать|указывать)\b", re.I)
_PROPOSED = re.compile(r"\b(?:предлагается|планируется|предлагаем\w*|планируем\w*|предполагаем\w*|"
                        r"если\s+будет\s+(?:подписан\w*|заключ[её]н\w*))\b", re.I)
_OTHER_CONDITION = re.compile(
    r"\b(?:при\s+(?:условии\s+)?(?:наличии|получении|поступлении|предоставлении)|"
    r"после\s+получения\s+(?:сч[её]та|финансирования)|"
    r"при\s+условии\s+заключения|если\s+будет|в\s+случае\s+подписания)\b", re.I)
_ORDINAL = r"(?:\d{1,3}(?:[-–]?[а-я]{1,3})?|перв\w*|втор\w*|трет\w*|четв[её]рт\w*|пят\w*)"
_STAGES = re.compile(r"\b(?:(?P<before>" + _ORDINAL + r")\s+этап\w*|"
                     r"этап\w*\s*(?:№|номер)?\s*(?P<after>\d{1,3})(?!\d))", re.I)
_INSTALLMENTS = re.compile(r"\b(?:(?P<before>" + _ORDINAL + r")\s+плат[её]ж\w*|"
                           r"плат[её]ж\w*\s*(?:№|номер)\s*(?P<after>\d{1,3})(?!\d))", re.I)
_CONDITION_START = re.compile(r"\b(?:если|для\s+(?:субъект\w*|МСП|СМСП)|в\s+случае|при\s+условии|при\s+заключении)\b", re.I)
_SIMPLE_SME_LEAD = re.compile(
    r"\s*(?:для\s+" + _SME + r"|"
    r"(?:(?:в\s+случае\s*,?\s*)?если\s+)"
    r"(?:поставщик|подрядчик|исполнитель|контрагент)\s+"
    r"(?:является\s+|относится\s+к\s+)" + _SME + r"|"
    r"(?:при|в\s+случае)\s+заключени[ия]\s+договора\s+с\s+" + _SME + r")\s*[,：:–—-]?\s*", re.I)
_SECTION_HEADING = re.compile(r"(?:^|\n)[ \t]*(?:\d{1,2}(?:\.\d{1,2})*[.)]?[ \t]+)"
                              r"(?P<title>[А-ЯЁA-Z][^\n\r]{2,140})(?=\r?\n|$)", re.I)


def _snapshot(doc):
    text = str(doc.get("text") or "")[:MAX_TEXT].replace("\xa0", " ")
    context = document_context(doc)
    sections = [match for match in _SECTION_HEADING.finditer(text)
                if not re.search(r"[;!?]|\d|\b(?:производ\w*|осуществля\w*|оплачива\w*)\b", match["title"], re.I)]
    return {"text": text, "context": context, "phase": _phase(doc, context),
            "lots": list(_LOT_HEADING.finditer(text)),
            "contracts": list(_CONTRACT_HEADING.finditer(text)),
            "variants": list(_ALTERNATIVE.finditer(text)),
            "selections": list(_SELECTION.finditer(text)), "sections": sections}


def _section(snapshot, position):
    preceding = [match for match in snapshot["sections"] if match.start() <= position]
    return str(preceding[-1].start())+":"+preceding[-1]["title"].strip() if preceding else ""


def _meta(doc):
    return doc.get("meta") if isinstance(doc.get("meta"), dict) else {}


def _values(record, names):
    return {str(record[key]).strip() for key in names
            if record.get(key) is not None and not isinstance(record[key], (dict, list, bool))
            and str(record[key]).strip()}


def _identifier(value):
    value = str(value or "").strip().casefold().replace("ё", "е")
    value = re.sub(r"^(?:№|n)\s*", "", value)
    return "" if not value or set(value) <= set("_-./ ") else value.rstrip(".,;)")


def _declared(doc, names):
    values = set()
    for record in (doc, _meta(doc)):
        values.update(_identifier(value) for value in _values(record, names))
    return values - {""}


def _purchase_id(p):
    return _identifier(p.get("reg_number") or p.get("reg"))


def _belongs(p, doc):
    declared = _declared(doc, ("reg_number", "purchase_reg_number", "procurement_reg_number"))
    return not declared or not _purchase_id(p) or declared == {_purchase_id(p)}


def _window(text, start, end):
    # A condition belongs to its own sentence, not a neighbouring example or
    # acceptance event. A variant heading can be immediately before it.
    left = max((text.rfind(mark, 0, start)+1 for mark in ".;!?"), default=0)
    stops = [text.find(mark, end) for mark in ".;!?"]
    right = min((value for value in stops if value >= 0), default=len(text))
    return text[max(left, start-350):min(right, end+400)]


def _identity(doc, start=0, snapshot=None):
    meta = _meta(doc)
    snapshot = _snapshot(doc) if snapshot is None else snapshot
    lots = _declared(doc, ("lot_number", "lot_id", "lot"))
    headings = [match for match in snapshot["lots"] if match.end() <= start]
    if headings:
        lots.add(_identifier(headings[-1][1]))
    contracts = _declared(doc, ("contract_number", "contract_id", "contract_registry_number"))
    headings = [match for match in snapshot["contracts"] if match.end() <= start]
    if headings and _identifier(headings[-1][1]):
        contracts.add(_identifier(headings[-1][1]))
    return {
        "purchase": sorted(_declared(doc, ("reg_number", "purchase_reg_number", "procurement_reg_number"))),
        "lots": sorted(lots), "contracts": sorted(contracts),
        "document": str(doc.get("name") or ""),
        "source_url": str(meta.get("source_url") or doc.get("url") or ""),
        "sha256": str(meta.get("sha256") or ""),
        "archive_sha256": str(meta.get("archive_sha256") or ""),
        "archive_member": str(meta.get("archive_member") or meta.get("member") or ""),
        "document_id": str(meta.get("document_id") or doc.get("document_id") or doc.get("id") or ""),
        "uid": str(meta.get("uid") or ""),
    }


def _phase(doc, context):
    declarations = _declared(doc, ("contract_stage", "contract_status", "document_stage"))
    draft = context["role"] == "project_contract" or bool(declarations & {"draft", "project", "tender", "проект"})
    # Neither a filename saying "signed" nor empty signature lines establish
    # execution. Require an explicit structured status or an actual-date field
    # inside the verified source; prospective clauses do not match.
    executed = bool(declarations & {"signed", "concluded", "executed", "подписан", "заключен", "заключён"})
    text = str(doc.get("text") or "")[:MAX_TEXT]
    if re.search(r"(?:^|\n)\s*(?:дата\s+заключения\s+(?:договора|контракта)|"
                 r"(?:договор|контракт)\s+заключ[её]н)\s*[:：]?\s*"
                 r"(?:\d{1,2}\.\d{1,2}\.20\d{2}|20\d{2}-\d{2}-\d{2})(?!\d)", text, re.I):
        executed = True
    return ("conflict" if draft and executed else "executed_contract" if executed else
            "tender_terms" if draft or context["role"] in {"documentation", "notice"} else "contract_terms")


def _scope(window):
    if _SUBCONTRACT.search(window) and re.search(_SME, window, re.I):
        return "subcontract"
    if _NON_SME.search(window):
        return "non_sme"
    return "sme" if _SME_CONDITION.search(window) else "general"


def _ordinal(value):
    for prefix, number in (("перв", "1"), ("втор", "2"), ("трет", "3"), ("четверт", "4"), ("пят", "5")):
        if value.casefold().replace("ё", "е").startswith(prefix):
            return number
    return re.match(r"\d+", value)[0] if re.match(r"\d+", value) else value.casefold()


def _obligation(window):
    stages = sorted({_ordinal(match["before"] or match["after"]) for match in _STAGES.finditer(window)})
    installments = sorted({_ordinal(match["before"] or match["after"]) for match in _INSTALLMENTS.finditer(window)})
    kind = "general"
    if re.search(r"\b(?:аванс\w*|предоплат\w*|предварительн\w*\s+оплат\w*)\b", window, re.I):
        kind = "advance"
    elif re.search(r"\bгарантийн\w*\s+удержани\w*", window, re.I):
        kind = "retention"
    elif re.search(r"\bокончательн\w*\s+(?:оплат\w*|расч[её]т\w*|плат[её]ж\w*)", window, re.I):
        kind = "final"
    elif stages:
        kind = "stage"
    elif installments:
        kind = "installment"
    return {"kind": kind, "stages": stages, "installments": installments}


def _obligation_relation(source, peer):
    a, b = source["obligation"], peer["obligation"]
    for field in ("stages", "installments"):
        if a[field] and b[field] and set(a[field]).isdisjoint(b[field]):
            return "different"
        if a[field] and b[field] and set(a[field]) & set(b[field]) and a["kind"] != b["kind"]:
            return "unresolved"
    specific = {"advance", "retention", "final", "stage", "installment"}
    if a["kind"] in specific and b["kind"] in specific and a["kind"] != b["kind"]:
        return "different"
    return "same" if a == b else "unresolved"


def _same_scope(a, b):
    return all(a[key] == b[key] and len(a[key]) <= 1 for key in ("purchase", "lots", "contracts"))


def _variant_context(doc, source_identity, position, snapshot):
    section = _section(snapshot, position)
    variants = [match for match in snapshot["variants"]
                if _same_scope(source_identity, _identity(doc, match.start(), snapshot))
                and section == _section(snapshot, match.start())]
    selected = {match[1] for match in snapshot["selections"]
                if _same_scope(source_identity, _identity(doc, match.start(), snapshot))
                and section == _section(snapshot, match.start())}
    preceding = [match for match in variants if match.end() <= position]
    return preceding, {match[1] for match in variants}, selected


def _record(doc, start, end, days, day_type="working", snapshot=None):
    snapshot = _snapshot(doc) if snapshot is None else snapshot
    text, context = snapshot["text"], snapshot["context"]
    identity = _identity(doc, end, snapshot)
    before, variants, selected = _variant_context(doc, identity, end, snapshot)
    window = _window(text, start, end)
    # The next/previous payment's condition in the same sentence is not this
    # payment's qualifier. Retain offsets rather than splitting on a repeated
    # word such as "оплата". Separate variant headings also reset the scope.
    left = max(max((text.rfind(mark, 0, start)+1 for mark in ".;!?"), default=0), start-350)
    if before and before[-1].end() > left:
        left = before[-1].end()
    previous = list(re.finditer(_SUBJECT, text[left:start], re.I))
    if previous:
        previous_end = left+previous[-1].end()
        conditions = list(_CONDITION_START.finditer(text, previous_end, start))
        if conditions:
            left = conditions[-1].start()
        else:
            comma = text.rfind(",", previous_end, start)
            if comma >= 0:
                left = comma+1
    stops = [text.find(mark, end) for mark in ".;!?"]
    right = min([value for value in stops if value >= 0] + [len(text), end+400])
    next_subject = re.search(_SUBJECT, text[end:right], re.I)
    if next_subject:
        following = end+next_subject.start()
        conditions = list(_CONDITION_START.finditer(text, end, following))
        comma = text.rfind(",", end, conditions[0].start() if conditions else following)
        right = comma if comma >= 0 else conditions[0].start() if conditions else following
    qualified_window = text[left:right]
    subject = re.search(_SUBJECT, qualified_window, re.I)
    simple_sme = bool(subject and _SIMPLE_SME_LEAD.fullmatch(qualified_window[:subject.start()]))
    scope = _scope(qualified_window)
    tail = text[end:right]
    other_condition = (bool(_OTHER_CONDITION.search(qualified_window)) or
                       bool(_CONDITION_START.search(tail)) or scope == "sme" and not simple_sme)
    return {"doc": doc, "start": start, "end": end, "days": days,
            "day_type": day_type, "clock": "acceptance", "excerpt": window,
            "scope": scope, "identity": identity,
            "phase": snapshot["phase"], "context": context,
            "section": _section(snapshot, end),
            "obligation": _obligation(qualified_window),
            "inoperative": bool(_NEGATED_PAYMENT.search(qualified_window) or _PROPOSED.search(qualified_window)),
            "other_condition": other_condition,
            "variant": before[-1][1] if before else "",
            "variants": variants, "selected_variants": selected,
            "inactive_clause": bool(_HISTORICAL.search(window)),
            "illustrative": bool(_EXAMPLE.search(window) or _LAW_QUOTE.search(window))}


def collect_payment_terms(docs):
    """Extract acceptance-based terms, retaining exact original identities.

    These internal records contain their source ``doc`` and may be passed to
    ``assess_payment_term(terms=...)``. Only its returned evidence is intended
    for JSON persistence. Unreadable/unverified documents cannot supply a
    seven-day counter-clause.
    """
    from v331_broad_legal import verified
    from v341_precise_legal import PAYMENT_DAYS, _payment_acceptance_clause, _payment_spelling_matches

    result = []
    for doc in list(docs)[:MAX_DOCUMENTS]:
        if not isinstance(doc, dict) or not verified(doc):
            continue
        snapshot = _snapshot(doc)
        text = snapshot["text"]
        occupied, found = set(), []
        for match in PAYMENT_DAYS.finditer(text):
            if _payment_acceptance_clause(text, match):
                found.append(_record(doc, match.start(), match.end(), int(match["days"]), snapshot=snapshot))
                occupied.add(match.start("days"))
        for match in _COUNTERPART.finditer(text):
            if match.start("days") in occupied:
                continue
            value = match["days"].casefold().replace("ё", "е")
            days = int(value) if value.isdecimal() else _SMALL_NUMBERS.get(value)
            if days is None:
                continue
            if match["spelling"]:
                if not value.isdecimal() or not _payment_spelling_matches(match):
                    continue
            window = _window(text, match.start(), match.end())
            before = text[max(0, match.start()-100):match.start()]
            if (re.search(r"\b(?:авансов\w*|предварительн\w*|планируем\w*|предлагаем\w*)\s*$", before, re.I)
                    or re.search(r"\b(?:аванс\w*|предоплат\w*|гарантийн\w*\s+удержани\w*)\b", window, re.I)):
                continue
            tail = text[match.end():match.end()+350]
            direct = _ACCEPTANCE.match(tail, re.match(r"[\s,:：–—-]*", tail).end())
            if not direct:
                continue
            # A following unrelated action does not establish payment's clock.
            if re.match(r"\s+(?:заказчик|поставщик|исполнитель|комиссия)\s+"
                        r"(?:составляет|готовит|формирует|оформляет)\b", tail[direct.end():], re.I):
                continue
            found.append(_record(doc, match.start(), match.end(), days,
                                 "working" if match["unit"].casefold().startswith("рабоч") else "calendar", snapshot))
            occupied.add(match.start("days"))
        found.sort(key=lambda item: item["start"])
        if len(found) > MAX_TERMS_PER_DOCUMENT or len(str(doc.get("text") or "")) > MAX_TEXT:
            for item in found:
                item["limited"] = True
        result.extend(found[:MAX_TERMS_PER_DOCUMENT])
    return result


def detect_payment_coverage(p, docs):
    """Use actual collection markers; missing markers mean unknown, not done."""
    reasons, missing = [], []
    if p.get("source_incomplete") is True:
        reasons.append("payment_incomplete_originals")
    for key in ("documents_unreadable", "unreadable"):
        try:
            if int(p.get(key) or 0) > 0:
                reasons.append("payment_incomplete_originals")
        except (TypeError, ValueError):
            pass
    expected, received = p.get("original_expected"), p.get("original_received")
    if isinstance(expected, int) and not isinstance(expected, bool) and isinstance(received, int):
        if received < expected:
            reasons.append("payment_incomplete_originals")
    for doc in docs:
        if not isinstance(doc, dict) or not _belongs(p, doc):
            continue
        meta = _meta(doc)
        if (doc.get("status") in {"downloaded_no_text", "archive_unreadable"} or meta.get("archive_warnings")
                or doc.get("status") == "checked" and not str(doc.get("text") or "").strip()):
            missing.append(_identity(doc))
            reasons.append("payment_incomplete_originals")
    if len(docs) > MAX_DOCUMENTS:
        reasons.append("payment_collection_limited")
    complete = False if reasons else (True if p.get("source_incomplete") is False
                                      and expected is not None and expected == received else None)
    return {"complete": complete, "reasons": list(dict.fromkeys(reasons)),
            "documents": missing[:40], "original_expected": expected, "original_received": received}


def _same_source(left, right):
    if left["doc"] is right["doc"]:
        return True
    a, b = left["identity"], right["identity"]
    return bool(a["sha256"] and a["sha256"] == b["sha256"]
                and a["source_url"] == b["source_url"] and a["archive_member"] == b["archive_member"])


def _relation(left, right):
    a, b = left["identity"], right["identity"]
    for field in ("purchase", "lots", "contracts"):
        if a[field] and b[field] and set(a[field]).isdisjoint(b[field]):
            return "different"
    if any(len(a[field]) > 1 or len(b[field]) > 1 for field in ("purchase", "lots", "contracts")):
        return "ambiguous"
    if _same_source(left, right):
        return "same"
    if any(bool(a[field]) != bool(b[field]) for field in ("lots", "contracts")):
        return "ambiguous"
    return "same" if (a["lots"] or a["contracts"]) else "unresolved"


def _replacement(source, peer):
    if (_relation(source, peer) == "different" or source["phase"] != peer["phase"]
            or source["phase"] not in {"tender_terms", "executed_contract"}
            or peer["context"]["version_status"] != "current"):
        return False
    a, b = source["identity"], peer["identity"]
    # An exact pointer is evidence of a version relationship, but cannot
    # override different/unknown lots when a lot was explicitly declared.
    if a["lots"] != b["lots"] or (a["contracts"] and b["contracts"] and a["contracts"] != b["contracts"]):
        return False
    # An archive UID/URL names the container, not every individual contract in
    # it. Only a member-specific ID/hash may replace that member's condition.
    own_fields = ("document_id", "sha256") if a["archive_member"] else ("document_id", "uid", "sha256", "source_url")
    peer_fields = ("document_id", "sha256") if b["archive_member"] else ("document_id", "uid", "sha256", "source_url")
    own_ids = {a[key] for key in own_fields if a[key]}
    peer_ids = {b[key] for key in peer_fields if b[key]}
    forward = ("supersedes_document_id", "replaces_document_id", "supersedes_sha256", "supersedes_uid", "replaces", "supersedes")
    backward = ("superseded_by", "replaced_by_document_id", "superseded_by_document_id", "superseded_by_sha256")
    return any(_values(record, forward) & own_ids for record in (peer["doc"], _meta(peer["doc"]))) or any(
        _values(record, backward) & peer_ids for record in (source["doc"], _meta(source["doc"])))


def _evidence(term, *, relation="source"):
    return {**term["identity"], "start": term["start"], "end": term["end"],
            "days": term["days"], "day_type": term["day_type"], "clock": term["clock"],
            "excerpt": term["excerpt"][:1200], "phase": term["phase"],
            "scope": term["scope"], "variant": term["variant"], "relation": relation,
            "obligation": term["obligation"],
            "section": term["section"],
            "version_status": term["context"]["version_status"]}


def assess_payment_term(p, docs, source_doc, start, end, days, *, terms=None, coverage_complete=None):
    """Limit a proposed ``>7 working days from acceptance`` finding.

    ``start/end`` are zero-based offsets in the original text (NBSP may be
    replaced by space without changing offsets). Pass the complete documents
    snapshot, including unreadable archive records. Unknown completeness does
    not invent missing documents; known incomplete coverage requires review.
    """
    from v331_broad_legal import verified
    from v383_msp_scope import resolve

    docs = list(docs)
    reasons, related, inactive = [], [], []
    coverage = detect_payment_coverage(p, docs)
    if coverage_complete is False:
        coverage["complete"] = False
        coverage["reasons"] = list(dict.fromkeys([*coverage["reasons"], "payment_incomplete_originals"]))
    reasons.extend(coverage["reasons"])
    body = str(source_doc.get("text") or "") if isinstance(source_doc, dict) else ""
    valid_span = (isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int)
                  and not isinstance(end, bool) and 0 <= start < end <= len(body)
                  and isinstance(days, int) and not isinstance(days, bool) and days > 0)
    if not valid_span:
        return {"status": "review", "reasons": ["payment_fragment_unresolved"],
                "reason_labels": [REASON_LABELS["payment_fragment_unresolved"]],
                "source": {}, "related_terms": [], "phase": "unknown", "coverage": coverage,
                "missing_evidence": ["Точный фрагмент исходного условия оплаты"]}
    terms = collect_payment_terms(docs) if terms is None else terms
    exact = [item for item in terms if item.get("doc") is source_doc and item["start"] == start
             and item["end"] == end and item["days"] == days]
    source = exact[0] if exact else _record(source_doc, start, end, days)
    phase, context = source["phase"], source["context"]
    if not verified(source_doc):
        reasons.append("payment_source_unverified")
    if not _belongs(p, source_doc):
        inactive.append("payment_source_other_purchase")
    requested_lot = _identifier(p.get("lot_number") or p.get("lot_id"))
    if requested_lot and source["identity"]["lots"] and requested_lot not in source["identity"]["lots"]:
        inactive.append("payment_source_other_lot")
    if context["version_status"] in {"superseded", "cancelled"}:
        inactive.append("payment_historical_source")
    if context["role"] in {"template", "instruction", "draft", "regulation", "protocol"} or source["illustrative"]:
        reasons.append("payment_source_not_contract_term")
    if source["inactive_clause"]:
        inactive.append("payment_historical_clause")
    if phase == "conflict":
        reasons.append("payment_phase_conflict")
    if source["inoperative"] or source["other_condition"]:
        reasons.append("payment_negated_or_proposed_clause")
    if days <= 7:
        inactive.append("payment_source_within_bound")
    if any(item.get("limited") for item in terms):
        reasons.append("payment_collection_limited")
    own = [item for item in terms if item.get("doc") is source_doc]
    if not any(item["days"] == days and item["start"] < end and start < item["end"] for item in own):
        reasons.append("payment_fragment_unresolved")
    # Only one copy of each variant label is meaningful. Dates/version numbers
    # in filenames are deliberately absent from this decision.
    variants = source["variants"]
    selected = source["selected_variants"]
    if source["variant"] and len(selected) == 1 and source["variant"] not in selected:
        inactive.append("payment_alternative_not_selected")
    alternative_unresolved = bool(source["variant"] and len(variants) > 1 and len(selected) != 1)
    # Resolve scope from independent procurement evidence; an "if SME" clause
    # never proves that the actual procurement is restricted to SMEs.
    need_scope = source["scope"] in {"sme", "non_sme"} or any(item["scope"] == "sme" for item in own)
    sme = resolve(p, [doc for doc in docs if isinstance(doc, dict) and _belongs(p, doc)])["value"] if need_scope else None
    if source["scope"] == "non_sme":
        if sme is True:
            inactive.append("payment_non_sme_clause")
        else:
            reasons.append("payment_sme_scope_unresolved")
    elif source["scope"] == "sme" and sme is not True:
        reasons.append("payment_sme_scope_unresolved")
    elif source["scope"] == "subcontract":
        reasons.append("payment_subcontract_condition")
    for peer in terms:
        if not _belongs(p, peer["doc"]):
            continue
        if _same_source(source, peer) and peer["days"] == days and peer["start"] <= end and start <= peer["end"]:
            continue
        relation = _relation(source, peer)
        if relation == "different":
            continue
        if (peer["context"]["version_status"] in {"superseded", "cancelled"}
                or peer["context"]["role"] in {"template", "instruction", "draft", "regulation", "protocol"}
                or peer["inactive_clause"] or peer["illustrative"] or peer["inoperative"]
                or peer["scope"] in {"non_sme", "subcontract"}):
            continue
        if peer["variant"] and len(peer["selected_variants"]) == 1 and peer["variant"] not in peer["selected_variants"]:
            continue
        obligation_relation = _obligation_relation(source, peer)
        if obligation_relation == "different":
            continue
        if peer["days"] == days and peer["day_type"] == "working":
            continue
        if peer["scope"] == "sme" and sme is not True:
            # A conditional alternative still makes applicability uncertain;
            # it cannot establish a seven-day operative term by itself.
            reasons.append("payment_sme_scope_unresolved")
        related.append(_evidence(peer, relation=relation))
        if _replacement(source, peer):
            inactive.append("payment_explicit_replacement")
            continue
        if (relation == "same" and _same_source(source, peer) and sme is True
                and source["scope"] == "general" and peer["scope"] == "sme"
                and peer["days"] <= 7 and peer["phase"] == phase
                and obligation_relation == "same" and not peer["other_condition"]
                and source["section"] == peer["section"]
                and not source["inoperative"] and not source["other_condition"]):
            inactive.append("payment_specific_sme_clause")
            continue
        if {phase, peer["phase"]} == {"tender_terms", "executed_contract"}:
            reasons.append("payment_cross_phase_terms")
        else:
            reasons.append("payment_conflicting_terms")
        if relation in {"ambiguous", "unresolved"}:
            reasons.append("payment_relation_unresolved")
        if obligation_relation == "unresolved":
            reasons.append("payment_obligation_unresolved")
    if alternative_unresolved and not inactive:
        reasons.append("payment_alternative_unselected")
    reasons = list(dict.fromkeys([*inactive, *reasons]))
    return {"status": "inactive" if inactive else "review" if reasons else "confirmed",
            "reasons": reasons, "reason_labels": [REASON_LABELS[reason] for reason in reasons],
            "source": _evidence(source), "related_terms": related[:40], "phase": phase,
            "coverage": coverage,
            "missing_evidence": (["Применимая редакция и связь условия с конкретным лотом/договором"] if
                                 any(reason in reasons for reason in ("payment_conflicting_terms", "payment_relation_unresolved", "payment_alternative_unselected")) else [])
                                + (["Непрочитанные или неполученные оригиналы"] if coverage["complete"] is False else [])}
