"""Concrete date chains and scope-aware legal validators for 223-FZ.

All findings require receipt-verified originals, explicit facts and applicable
rules. Generic law quotations, missing fields and unresolved exceptions remain
review items. Calendar dates deliberately ignore the time of day.
"""
from __future__ import annotations
from legal_applicability import competitive_rule_scope
from audit_db_retry import run_db_sync

import re
from datetime import timedelta
from functools import wraps
from collections import OrderedDict
from threading import Lock

# The analysis step and checklist step run separately but inspect identical
# document snapshots. Reuse their exact legal date evaluation once per audit.
_PAIR_CACHE=OrderedDict()
_PAIR_LOCK=Lock()

def shared_evaluate(p,docs,consume=False):
    key=(id(p),id(docs))
    with _PAIR_LOCK:
        cached=_PAIR_CACHE.pop(key,None)
        if cached is not None and cached[0] is p and cached[1] is docs:
            if not consume:_PAIR_CACHE[key]=cached
            return cached[2]
    result=evaluate(p,docs)
    if not consume:
        with _PAIR_LOCK:
            _PAIR_CACHE[key]=(p,docs,result)
            while len(_PAIR_CACHE)>2:_PAIR_CACHE.popitem(last=False)
    return result

from v298_eis_only import federal_workday
from v331_broad_legal import verified
from v341_eis_facts import DATE_TOKEN, enrich, persist_facts, parse_day as day, document_facts

LAW32 = "https://www.consultant.ru/document/cons_doc_LAW_116964/05cd0add21b39d478c8b8af91fb2f2cd80d4a6e8/"
LAW4 = "https://www.consultant.ru/document/cons_doc_LAW_116964/441d00be62e3224cdc0514cffaf2a26b5b40a1c7/"
# Recognize the complete ordinary field label. The date still has to follow
# that label directly; an answer's preparation/date or a generic document
# publication is not a substitute for receipt or EIS placement evidence.
_CLARIFICATION_SUBJECT = (r"(?:\s+(?:положени[йя]\s+)?документации"
                          r"(?:\s+о\s+(?:конкурентной\s+)?закупке)?)?")
_REQUEST_SUBJECT = (r"\s+(?:(?:на\s+)?(?:(?:дачу|предоставление)\s+)?разъяснени[еяй]|"
                    r"о\s+(?:(?:даче|предоставлении)\s+)?разъяснени[ийя])"
                    + _CLARIFICATION_SUBJECT)
_EIS_LABEL = r"(?:ЕИС|единой\s+информационной\s+системе(?:\s+в\s+сфере\s+закупок)?)"
REQUEST = (r"(?:дата\s+(?:и\s+время\s+)?(?:поступления|получения|регистрации)\s+"
           r"(?:заказчиком\s+)?запроса(?:" + _REQUEST_SUBJECT + r")?|"
           r"запрос" + _REQUEST_SUBJECT + r"\s+(?:поступил|получен|зарегистрирован))")
_ANSWER_OBJECT = (r"(?:ответа(?:\s+на\s+запрос(?:" + _REQUEST_SUBJECT + r")?)?|"
                  r"разъяснени[яй]" + _CLARIFICATION_SUBJECT + r"(?:\s+на\s+запрос)?)")
ANSWER = (r"(?:дата\s+(?:и\s+время\s+)?(?:размещения|публикации)\s+(?:"
          + _ANSWER_OBJECT + r"(?:\s+в\s+" + _EIS_LABEL + r")?|"
          r"в\s+" + _EIS_LABEL + r"\s+" + _ANSWER_OBJECT + r")|"
          r"(?:ответ(?:\s+на\s+запрос(?:" + _REQUEST_SUBJECT + r")?)?|"
          r"разъяснени[ея]" + _CLARIFICATION_SUBJECT + r")\s+"
          r"(?:размещ[её]н[оы]?|опубликован[оы]?)\s+в\s+" + _EIS_LABEL + r")")
# A protocol's ordinary event-kind suffix is part of its field label. Keep
# the vocabulary closed: arbitrary prose between the field and its date
# must not become a signing or EIS-publication event.
_PROTOCOL_DATE_KIND = (r"(?:подведения\s+итогов|рассмотрения(?:\s+и\s+оценки)?"
                       r"(?:\s+заявок)?|оценки(?:\s+заявок)?|допуска(?:\s+к\s+участию)?)")
_PROTOCOL_DATE_GENITIVE = (r"(?:итогового\s+протокола|протокола"
                           r"(?:\s+" + _PROTOCOL_DATE_KIND + r")?)")
_PROTOCOL_DATE_NOMINATIVE = (r"(?:итоговый\s+протокол|протокол"
                             r"(?:\s+" + _PROTOCOL_DATE_KIND + r")?)")
SIGNED = (r"(?:дата\s+(?:и\s+время\s+)?подписания\s+" + _PROTOCOL_DATE_GENITIVE
          + r"|" + _PROTOCOL_DATE_NOMINATIVE + r"\s+подписан)")
PROTOCOL_PUBLISHED = (
    r"(?:дата\s+(?:и\s+время\s+)?(?:размещения|публикации)\s+(?:"
    + _PROTOCOL_DATE_GENITIVE + r"(?:\s+в\s+" + _EIS_LABEL + r")?|в\s+"
    + _EIS_LABEL + r"\s+" + _PROTOCOL_DATE_GENITIVE + r")|"
    + _PROTOCOL_DATE_NOMINATIVE + r"\s+(?:размещ[её]н|опубликован)\s+в\s+"
    + _EIS_LABEL + r")")
COMPETITIVE = re.compile(r"конкурс|аукцион|запрос\s+(?:котировок|предложений)", re.I)
CANCEL_DECISION = r"(?:дата\s+(?:принятия\s+)?решения\s+об\s+отмене\s+закупки|решение\s+об\s+отмене\s+закупки\s+принято)"
CANCEL_POSTED = r"(?:дата\s+(?:размещения|публикации)\s+(?:извещения|решения)\s+об\s+отмене\s+закупки(?:\s+в\s+ЕИС)?|(?:извещение|решение)\s+об\s+отмене\s+закупки\s+размещено)"
SME_EXCLUSIVE = re.compile(r"(?:только|исключительно)\s+субъект[ыа-я\s]{0,35}малого\s+и\s+среднего\s+предпринимательства|участниками\s+которо[йго]+\s+могут\s+быть\s+только\s+субъект",re.I)
_PAYMENT_SUBJECT = r"\b(?:срок\s+оплаты|оплата|расч[её]т[ыа]?|оплат[уы])\b"
PAYMENT_DAYS = re.compile(
    r"(?:\bсрок\s+оплаты\s*(?P<label>составляет|[:：])|"
    r"\b(?:оплата|расч[её]т[ыа]?|оплат[уы])\b"
    r"(?:(?!" + _PAYMENT_SUBJECT + r")[^.;!?]){0,280}?"
    r"(?P<period>в\s+течение|не\s+позднее(?:\s+чем)?))"
    r"\s*(?P<days>\d{1,3})\s*(?:\((?P<spelling>[^)]{0,40})\)\s*)?"
    r"рабоч(?:их|его)\s+(?:дней|дня|дн\.?)(?!\w)", re.I)
_PAYMENT_ACCEPTANCE = re.compile(
    r"(?:со?\s+дня|с\s+дат[ыа]|с\s+момента|после(?:\s+дат[ыа])?)\s+"
    r"(?:подписания|утверждения)\s+(?:заказчиком\s+|сторонами\s+)?"
    r"(?:акт(?:а|ов)|документ(?:а|ов))\s+"
    r"(?:сдачи[-–]при[её]мки|при[её]мки|о\s+при[её]мке)\b", re.I)
_PAYMENT_ACCEPTANCE_BRIDGE = re.compile(
    r"(?:\s|[,–—-]|оказанных\b|услуг\b|выполненных\b|работ\b|"
    r"поставленн(?:ого|ых)\b|товар(?:а|ов)\b|продукции\b|заказчиком\b|"
    r"сторонами\b|производится\b|осуществляется\b|проводится\b)*", re.I)
_PAYMENT_SEPARATE_ACTION = re.compile(
    r"\s+(?:(?:оказанных\s+услуг|выполненных\s+работ|поставленн(?:ого|ых)\s+"
    r"товар(?:а|ов)|товар(?:а|ов)|услуг|работ|продукции)\s+)?"
    r"(?:заказчик|поставщик|исполнитель|комиссия|стороны)\s+"
    r"(?:составля(?:ет|ют)|готов(?:ит|ят)|формиру(?:ет|ют)|"
    r"подготавлива(?:ет|ют)|оформля(?:ет|ют))\b", re.I)
_PAYMENT_EXAMPLE = re.compile(
    r"\b(?:например|справочно|цитата|пример\s+(?:условия|расч[её]та)|"
    r"образец\s+расч[её]та|для\s+справки)\b", re.I)
_PAYMENT_INACTIVE_CLAUSE = re.compile(
    r"\b(?:условие\s+(?:исключено|не\s+применяется|утратило\s+силу)|"
    r"исключено\s+(?:следующее\s+)?условие)\b", re.I)
_PAYMENT_STATUTE_QUOTE = re.compile(
    r"\b(?:пунктом|положением|постановлением|законом)\b[^.;!?]{0,200}"
    r"(?:постановлени\w*|1352|223[-–]?\s*ФЗ)[^.;!?]{0,100}"
    r"\b(?:предусмотрено|установлено|определено)\b", re.I)
_PAYMENT_OTHER_TERM = re.compile(
    r"(?:[,\n]\s*(?:а\s+|при\s+этом\s+)?(?:срок\s+(?:поставки|доставки|"
    r"при[её]мки|выполнения|оказания|оформления|подписания)|"
    r"(?:поставка|доставка|при[её]мка|выполнение|оказание|оформление|подписание)\b)|"
    r"\bсч[её]т\w*\s*,\s*котор\w*[^,;.!?]{0,130}"
    r"\b(?:выставлен\w*|предоставлен\w*|получен\w*|передан\w*|направлен\w*)"
    r"(?:\s+(?:заказчиком|поставщиком|исполнителем))?\s*$)", re.I)
_PAYMENT_NUMBER_WORDS = (
    (1, 'один одного'), (2, 'два двух'), (3, 'три трех'), (4, 'четыре четырех'),
    (5, 'пять пяти'), (6, 'шесть шести'), (7, 'семь семи'), (8, 'восемь восьми'),
    (9, 'девять девяти'), (10, 'десять десяти'), (11, 'одиннадцать одиннадцати'),
    (12, 'двенадцать двенадцати'), (13, 'тринадцать тринадцати'),
    (14, 'четырнадцать четырнадцати'), (15, 'пятнадцать пятнадцати'),
    (16, 'шестнадцать шестнадцати'), (17, 'семнадцать семнадцати'),
    (18, 'восемнадцать восемнадцати'), (19, 'девятнадцать девятнадцати'),
    (20, 'двадцать двадцати'), (30, 'тридцать тридцати'), (40, 'сорок сорока'),
    (50, 'пятьдесят пятидесяти'), (60, 'шестьдесят шестидесяти'),
    (70, 'семьдесят семидесяти'), (80, 'восемьдесят восьмидесяти'),
    (90, 'девяносто девяноста'), (100, 'сто ста'), (200, 'двести двухсот'),
    (300, 'триста трехсот'), (400, 'четыреста четырехсот'),
    (500, 'пятьсот пятисот'), (600, 'шестьсот шестисот'),
    (700, 'семьсот семисот'), (800, 'восемьсот восьмисот'),
    (900, 'девятьсот девятисот'))
_PAYMENT_NUMBER_VALUES = {word: value for value, words in _PAYMENT_NUMBER_WORDS
                          for word in words.split()}


def _payment_spelling_matches(payment):
    """Conflicting digit/word amounts cannot establish a concrete duration."""
    spelling = payment['spelling']
    if spelling is None:
        return True
    words = spelling.casefold().replace('ё', 'е').split()
    if not words:
        return False
    total, previous_rank = 0, 4
    for word in words:
        value = _PAYMENT_NUMBER_VALUES.get(word)
        if value is None:
            return False
        rank = 3 if value >= 100 else (2 if value >= 20 else 1)
        if rank >= previous_rank:
            return False
        total += value
        previous_rank = rank
    return total == int(payment['days'])


def _payment_is_advance(prefix, term):
    return bool(re.search(r"\b(?:авансов\w*|предварительн\w*)\s*$", prefix, re.I)
        or re.match(r"оплат[ауы]\s+(?:аванса|предоплаты|авансового\s+платежа)\b", term, re.I)
        or re.search(r"\b(?:авансом|в\s+(?:виде|качестве)\s+"
                     r"(?:аванса|авансового\s+платежа))\b", term, re.I))


def _payment_advance_context(prefix, payment):
    if _payment_is_advance(prefix, payment.group(0)):
        return True
    if not payment['label']:
        return False
    # An inner field such as "Авансовая оплата: срок оплаты составляет ..."
    # inherits its owning payment heading. A later payment/settlement heading
    # establishes its own type; earlier advances cannot qualify that heading.
    headings = list(re.finditer(r"\b(?:срок\s+оплаты|оплата|расч[её]т[ы]?|плат[её]ж)\b",
                                prefix, re.I))
    advance = False
    for index, heading in enumerate(headings):
        before = prefix[:heading.start()]
        end = headings[index+1].start() if index+1 < len(headings) else len(prefix)
        if heading.group(0).casefold().startswith('срок'):
            if re.search(r"\bокончательн\w*\s*$", before, re.I):
                advance = False
        else:
            advance = _payment_is_advance(before, prefix[heading.start():end])
    return advance and not re.search(r"\bокончательн\w*\s*$", prefix, re.I)


def _payment_acceptance_clause(text, payment):
    """Return only a payment's own, explicit acceptance-based term.

    An invoice clock or a second payment condition must not borrow an unrelated
    acceptance phrase later in the same sentence or in a neighbouring clause.
    """
    if not _payment_spelling_matches(payment):
        return None
    start = max((text.rfind(mark, 0, payment.start()) + 1 for mark in '.;!?'), default=0)
    ends = [position for mark in '.;!?'
            if (position := text.find(mark, payment.end())) >= 0]
    end = min(ends) if ends else len(text)
    prefix = text[max(start, payment.start()-300):payment.start()]
    lead = prefix + payment.group(0)
    period = payment.start('period') if payment['period'] else payment.start('label')
    before = text[payment.start():period]
    if (_PAYMENT_EXAMPLE.search(lead) or _PAYMENT_STATUTE_QUOTE.search(lead)
            or _PAYMENT_INACTIVE_CLAUSE.search(lead) or _PAYMENT_OTHER_TERM.search(before)):
        return None
    if (_payment_advance_context(prefix, payment)
            or re.search(r"\b(?:предлагаем\w*|планируем\w*|предполагаем\w*)\s*$", prefix, re.I)):
        return None
    after = text[payment.end():min(end, payment.end()+350)]
    direct = re.match(r"[\s,:：–—-]*", after).end()
    acceptance = _PAYMENT_ACCEPTANCE.match(after, direct)
    if (acceptance is not None and ',' in after[:direct]
            and _PAYMENT_SEPARATE_ACTION.match(after, acceptance.end())):
        # "..., после подписания акта приёмки заказчик составляет отчёт"
        # introduces a separate action, rather than the payment's own clock.
        return None
    if acceptance is None:
        # Preserve the ordinary inverse order: "Оплата после подписания акта
        # приёмки производится в течение ...". The closed bridge prevents an
        # invoice/supply clause between acceptance and the payment duration.
        candidates = list(_PAYMENT_ACCEPTANCE.finditer(before))
        if not candidates or not _PAYMENT_ACCEPTANCE_BRIDGE.fullmatch(before[candidates[-1].end():]):
            return None
    return text[max(start, payment.start()-100):min(end, payment.end()+350)].strip()
_PLANNED_EVENT_PREFIX=re.compile(
    r"\b(?:планов\w*|планируем\w*|предполагаем\w*|ориентировочн\w*|"
    r"ожидаем\w*|предварительн\w*|расч[её]тн\w*)\s*$",re.I)


def shifted(date, count):
    if date is None:
        return None
    step = 1 if count >= 0 else -1
    result = date
    for _ in range(abs(count)):
        for _ in range(370):
            result += timedelta(days=step)
            working = federal_workday(result)
            if working is None:
                return None
            if working:
                break
        else:
            return None
    return result


def matches(pattern, text):
    found=[]
    for event in re.finditer(pattern+r"\s*[:：|=–—\-\s]{0,15}(?P<date>"+DATE_TOKEN+r")", text, re.I):
        # Do not match the actual-event substring inside a planned/estimated
        # field. The qualifier belongs immediately to this label, including
        # an ordinary table-cell line break; unrelated prose is not a veto.
        if _PLANNED_EVENT_PREFIX.search(text[max(0,event.start()-120):event.start()]):
            continue
        found.append(event)
        if len(found)>=40:break
    return found


def _event_dates(item):
    """Normalize event dates without identity labels or applicability context."""
    from v348_cross_document_legal import event_occurrence_text
    dates=re.findall(DATE_TOKEN,event_occurrence_text(item),re.I)
    if not dates:dates=re.findall(DATE_TOKEN,str(item.get("match_text") or ""),re.I)
    return tuple(sorted({parsed.isoformat() for value in dates
                         if (parsed:=day(value)) is not None}))


def finding(doc, code, title, law, evidence, match, url=LAW32, event_number=None, local_event=None):
    family = next((value for value in ("PROTOCOL", "CLARIFICATION") if value + "-" in code), None)
    if family:
        from v348_cross_document_legal import document_event_number, event_identity_evidence
        if event_number is None:event_number = document_event_number(doc, family)
        evidence = event_identity_evidence(evidence, family, event_number, doc, local_event=local_event)
    return {"code": code, "kind": "violation", "title": title,
            "law": law, "law_url": url, "evidence": evidence[:2200],
            "note": "Расчёт по подтверждённым календарным датам без времени. Источник и конкретная норма приведены в выводе.",
            "document": doc.get("name"),
            "source_url": (doc.get("meta") or {}).get("source_url") or doc.get("url"),
            "location": "Оригинал ЕИС, позиция "+str(match.start()+1),
            "match_text": match.group(0)[:500]}


def review(doc, code, title, evidence, law, match):
    out = finding(doc, code, title, law, evidence, match)
    return {"scope": "223-ФЗ / проверка фактов и сроков", "item": title,
            "status": "check", "field_path": code, "source": doc.get("name"),
            **{k: out[k] for k in ("evidence", "law", "law_url", "document", "source_url", "location")},
            "note": "Неполная цепочка фактов или применимость исключения. Не включается в подтверждённые нарушения."}


def _distinct_numbered_events(left, right):
    """Only independently established, different event numbers defeat dedup.

    Import lazily: the cross-original analyzers also reuse this module's date
    grammar. An absent/ambiguous identifier retains the existing date fallback.
    """
    from v348_cross_document_legal import (finding_protocol_number,
                                           finding_clarification_number,
                                           same_original_identity,distinct_local_events)
    if same_original_identity(left, right) and not distinct_local_events(left,right):return False
    a = str(left.get("code") or left.get("field_path") or "")
    b = str(right.get("code") or right.get("field_path") or "")
    for family, number in (("PROTOCOL-", finding_protocol_number),
                           ("CLARIFICATION-", finding_clarification_number)):
        if family in a and family in b:
            first, second = number(left), number(right)
            return bool(first and second and first != second)
    return False


def _identified_event(item):
    from v348_cross_document_legal import finding_protocol_number, finding_clarification_number
    if not isinstance(item,dict):return False
    code = str(item.get("code") or item.get("field_path") or "")
    for family, reader in (("PROTOCOL-", finding_protocol_number),
                           ("CLARIFICATION-", finding_clarification_number)):
        if family in code:return bool(reader(item))
    return False


def _deduplicate_events(items, event_key, *, checklist=False):
    # A generic/ambiguous row must never absorb all independently numbered
    # events solely because it arrived first. Prefer established IDs in each
    # date bucket; retain the old fallback when no reliable ID is available.
    grouped = {}
    for index, item in enumerate(items):
        eligible=isinstance(item,dict) and (checklist or item.get("kind")=="violation")
        value=event_key(item) if eligible else None
        key=("event",value) if value is not None else ("other",index)
        grouped.setdefault(key, []).append(item)
    result = []
    for peers in grouped.values():
        selected = []
        for item in sorted(peers, key=lambda row: not _identified_event(row)):
            if not any(not _distinct_numbered_events(old, item) for old in selected):
                selected.append(item)
        result.extend(selected)
    return result


def evaluate(p, docs):
    findings, checks = [], []
    from v348_cross_document_legal import (ambiguous_original_documents,
                                           document_event_number,
                                           _document_event_identity, EXCLUDE,
                                           _protocol_kinds,protocol_template_title,
                                           protocol_date_context,local_protocol_cohort)
    from official_publication_evidence import protocol_publication_verifier
    official_publication = protocol_publication_verifier(p)
    ambiguous = ambiguous_original_documents(docs[:650])
    identities = {}
    def number(doc, family):
        key = (id(doc), family)
        if key not in identities:
            identities[key] = "" if id(doc) in ambiguous else document_event_number(doc, family)
        return identities[key]
    competitive = competitive_rule_scope(p) is True
    from v383_msp_scope import resolve
    sme_scope = resolve(p,docs)["value"] is True
    for doc in docs[:650]:
        if not verified(doc):
            continue
        text = str(doc.get("text") or "")[:750000].replace("\xa0", " ")
        requests = matches(REQUEST, text)
        answers = matches(ANSWER, text)
        for index, request in enumerate(requests):
            # Resolve repeated clarification events automatically when each
            # request owns a single reply before the next request begins.
            # A reply after another request is ambiguous. A unique reversed
            # table is safe only within one unambiguous actual original.
            next_request = requests[index+1].start() if index+1 < len(requests) else len(text)
            peers = [x for x in answers
                     if request.end() <= x.start() < next_request
                     and x.start()-request.end() <= 1500]
            if (not peers and len(requests) == len(answers) == 1
                    and 0 <= request.start()-answers[0].end() <= 1500
                    and id(doc) not in ambiguous
                    and not _document_event_identity(doc, "CLARIFICATION")[1]
                    and not EXCLUDE.search(str(doc.get("name") or ""))):
                peers = [answers[0]]
            if len(peers) != 1:
                checks.append(review(doc, "v341-clarification-pair", "Сопоставить запрос разъяснений с публикацией ответа",
                                     request[0], "223-ФЗ, ст. 3.2 ч. 3", request))
                continue
            received, published = day(request["date"]), day(peers[0]["date"])
            deadline = day(p.get("submission_end") or p.get("deadline"))
            due, latest = shifted(received, 3), shifted(deadline, -3)
            snippet = " ".join(text[max(0, min(request.start(), peers[0].start())-60):max(request.end(), peers[0].end())+70].split())
            if not competitive or not received or not published or not deadline or not due or not latest or received > latest or published < received:
                checks.append(review(doc, "v341-clarification-applicability", "Проверить обязанность и срок ответа на запрос разъяснений",
                                     snippet, "223-ФЗ, ст. 3.2 ч. 3", request))
                continue
            if published > due:
                findings.append(finding(doc, "223-3.2-3-CLARIFICATION-RESPONSE-LATE",
                    "Разъяснение опубликовано позднее трёх рабочих дней после поступления запроса",
                    "223-ФЗ, ст. 3.2 ч. 3",
                    f"Запрос получен {received}; крайний день ответа {due}; опубликован {published}; окончание подачи {deadline}. {snippet}", request,
                    event_number=number(doc, "CLARIFICATION")))
        # Memo-derived rule: cancellation decision must be published on
        # the same calendar date (223-FZ art. 3.2(6)).
        cancel_decisions, cancel_posts = matches(CANCEL_DECISION,text), matches(CANCEL_POSTED,text)
        cancel_pairs = []
        for index, decision in enumerate(cancel_decisions):
            stop = cancel_decisions[index+1].start() if index+1 < len(cancel_decisions) else len(text)
            candidates = [post for post in cancel_posts
                          if decision.end() <= post.start() < stop
                          and post.start()-decision.end() <= 1500]
            if len(candidates) == 1:
                cancel_pairs.append((decision,candidates[0]))
        if len(cancel_decisions)==len(cancel_posts)==1 and not cancel_pairs:
            cancel_pairs=[(cancel_decisions[0],cancel_posts[0])]
        for decision, publication in cancel_pairs:
            decided, posted = day(decision["date"]), day(publication["date"])
            if competitive and decided and posted and 0 < (posted-decided).days < 365:
                findings.append(finding(doc,"223-3.2-6-CANCELLATION-POSTED-LATE",
                    "Решение об отмене конкурентной закупки размещено не в день принятия",
                    "223-ФЗ, ст. 3.2 ч. 6",
                    f"Решение об отмене принято {decided}; размещено в ЕИС {posted}.",
                    decision))
        if len(cancel_pairs)<len(cancel_decisions) or len(cancel_pairs)<len(cancel_posts):
            checks.append(review(doc,"v341-cancellation-date-chain",
                "Сверить дату решения об отмене с датой публикации в ЕИС",
                f"Дат решений: {len(cancel_decisions)}; дат публикаций: {len(cancel_posts)}.",
                "223-ФЗ, ст. 3.2 ч. 6",(cancel_decisions or cancel_posts)[0]))
        # Memo-derived SME payment term: only explicit exclusive SME scope
        # and contractual payment terms; not general legal quotations.
        if sme_scope:
            for payment in list(PAYMENT_DAYS.finditer(text))[:15]:
                days = int(payment["days"])
                snippet = _payment_acceptance_clause(text, payment) if days > 7 else None
                if snippet:
                    payment_finding = finding(doc,"1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS",
                        "В закупке только для субъектов МСП установлен срок оплаты свыше семи рабочих дней",
                        "Постановление Правительства РФ № 1352, Положение, п. 28",
                        f"Условие об оплате: {days} рабочих дней. {snippet}",payment,
                        "https://www.consultant.ru/document/cons_doc_LAW_172683/")
                    payment_finding['note'] = ("Проверено условие об оплате в рабочих днях от подписания "
                        "или утверждения документа о приёмке; подтверждены оригинал ЕИС и закупка только для МСП.")
                    findings.append(payment_finding)
        # Protocol signing and EIS publication must each be explicitly named.
        # Templates and draft/sample originals do not establish actual event
        # dates, even when their fields contain concrete example values.
        if protocol_template_title(doc.get("name")):
            continue
        signed, published = matches(SIGNED, text), matches(PROTOCOL_PUBLISHED, text)
        date_events=signed+published
        contexts={id(event):protocol_date_context(doc,event,date_events) for event in date_events}
        def compatible_protocol_dates(signature, publication=None):
            before=contexts[id(signature)]
            if not before["valid"]:return False
            first=_protocol_kinds(doc.get("name"),signature.group(0))
            if len(first)>1:return False
            if publication is None:return True
            after=contexts[id(publication)]
            if not after["valid"]:return False
            if before["number"] and after["number"] and before["number"]!=after["number"]:
                return False
            second=_protocol_kinds(doc.get("name"),publication.group(0))
            return len(second)<=1 and (not first or not second or first==second)
        official = {}
        protocol_conflict = False
        if (competitive and len(signed) == 1 and number(doc, "PROTOCOL")
                and compatible_protocol_dates(signed[0])):
            official = official_publication(doc)
            official_day = day(official.get("publication_date"))
            protocol_conflict = (official.get("state") == "conflict" or
                (official.get("state") == "confirmed" and
                 any(day(publication["date"]) != official_day for publication in published)))
            if protocol_conflict:
                checks.append(review(doc, "v341-protocol-publication-xml-conflict",
                    "Сверить противоречивые даты публикации протокола в XML ЕИС",
                    "Подтверждённые версии XML ЕИС или дата в оригинале содержат "
                    "противоречивые сведения о публикации; дата не используется "
                    "для установленного нарушения.", "223-ФЗ, ст. 4 ч. 12", signed[0]))
        # Pair multiple protocol events only when each signature/publication
        # has a unique local counterpart; never combine different protocols.
        pairs = []
        used = set()
        for signature in signed:
            candidates = [publication for publication in published
                          if 0 <= publication.start()-signature.end() <= 900
                          and compatible_protocol_dates(signature,publication)
                          and not any(signature.end() < other.start() < publication.start()
                                      for other in signed if other is not signature)
                          and not any(signature.end() < other.start() < publication.start()
                                      for other in published if other is not publication)]
            if len(candidates) == 1 and candidates[0].start() not in used:
                pairs.append((signature, candidates[0]))
                used.add(candidates[0].start())
        if (len(signed) == len(published) == 1 and not pairs
                and compatible_protocol_dates(signed[0],published[0])):
            # One pair can be distant within a single verified document.
            pairs = [(signed[0], published[0])]
            used.add(published[0].start())
        if len(pairs) < len(signed) or len(pairs) < len(published):
            anchor = (signed or published)[0] if (signed or published) else None
            if anchor is not None:
                checks.append(review(doc, "v341-protocol-ambiguous",
                    "Сопоставить даты подписания и размещения протоколов в ЕИС",
                    f"Подписаний: {len(signed)}; публикаций: {len(published)}; "
                    f"однозначных пар: {len(pairs)}. Остальные события требуют сверки.",
                    "223-ФЗ, ст. 4 ч. 12", anchor))
        local_events=local_protocol_cohort(doc,pairs,date_events) if id(doc) not in ambiguous else {}
        for signature, publication in pairs:
            if protocol_conflict:continue
            start, end = day(signature["date"]), day(publication["date"])
            due = start+timedelta(days=3) if start else None
            working = federal_workday(due) if due else None
            if working is False:
                due = shifted(due, 1)
            if start and end and due and working is not None and end > due and 0 <= (end-start).days < 365:
                local=local_events.get(signature.start())
                findings.append(finding(doc, "223-4-12-PROTOCOL-PUBLICATION-LATE",
                    "Протокол размещён в ЕИС с нарушением срока публикации", "223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193",
                    f"Протокол подписан {start}; крайний день размещения {due}; публикация в ЕИС {end}. {signature[0]}; {publication[0]}",
                    signature, LAW4, event_number=local["number"] if local else number(doc, "PROTOCOL"),
                    local_event=local))
        # Some official protocol originals contain the signature but publish
        # no EIS posting timestamp in their text. Only the receipt/XML-verified
        # attachment-specific accessor may supply that missing date.
        if competitive and len(signed) == 1 and not published and not protocol_conflict and number(doc, "PROTOCOL"):
            signature = signed[0]
            if official.get("state") == "confirmed":
                start, end = day(signature["date"]), day(official.get("publication_date"))
                due = start + timedelta(days=3) if start else None
                working = federal_workday(due) if due else None
                if working is False:due = shifted(due, 1)
                if start and end and due and working is not None and end > due and 0 <= (end-start).days < 365:
                    findings.append(finding(doc, "223-4-12-PROTOCOL-PUBLICATION-LATE",
                        "Протокол размещён в ЕИС с нарушением срока публикации",
                        "223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193",
                        f"Протокол подписан {start}; крайний день размещения {due}; публикация в ЕИС {end}. "
                        f"{signature[0]}. {official.get('evidence') or ''}", signature, LAW4,
                        event_number=number(doc, "PROTOCOL")))
    # Use normalized event dates and a stable legal rule to collapse copies
    # of the same original while retaining distinct dated occurrences.
    def event_key(item):
        return item["code"], _event_dates(item)
    return _deduplicate_events(findings, event_key), checks[:25]


def install(ns):
    analyze, build = ns["analyze"], ns["build_checks"]
    if getattr(analyze, "__v341__", False):
        return

    @wraps(analyze)
    def enriched_analyze(p, docs):
        enrich(ns, p, docs)
        run_db_sync(persist_facts,ns,p,stage="legal_persist_facts")
        existing = list(analyze(p, docs))
        extra, _ = shared_evaluate(p, docs)
        # Deduplicate the same dated legal event even if the EIS original
        # appears with different filenames or in several archive members.
        def event_key(item):
            dates = _event_dates(item)
            return (item.get("code"), dates) if dates else (
                item.get("code"), " ".join(str(item.get("match_text") or "").split()))
        return _deduplicate_events(existing + extra, event_key)

    @wraps(build)
    def enriched_checks(p, detail, card, docs):
        enrich(ns, p, docs, card, detail)
        _, extra = shared_evaluate(p, docs, consume=True)
        combined = list(build(p, detail, card, docs)) + extra
        def event_key(item):
            dates=_event_dates(item)
            code=item.get("code") or item.get("field_path")
            # A review note and a confirmed violation retain their own state.
            # The common identity guard also applies to legacy checklist rows
            # whose legal rule is stored only in field_path.
            return (item.get("status"),code,dates) if dates and code else None
        return _deduplicate_events(combined,event_key,checklist=True)

    enriched_analyze.__v341__ = True
    ns["analyze"], ns["build_checks"] = enriched_analyze, enriched_checks
    print("V341 LEGAL_FACTS_READY clarification_workdays=True protocols=True source_metadata=True", flush=True)

