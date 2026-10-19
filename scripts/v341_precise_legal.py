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
REQUEST = r"(?:дата\s+(?:и\s+время\s+)?(?:поступления|получения|регистрации)\s+запроса(?:\s+(?:на\s+)?разъяснени[еяй])?|запрос\s+(?:на\s+)?разъяснени[еяй]\s+(?:поступил|получен|зарегистрирован))"
ANSWER = r"(?:дата\s+(?:и\s+время\s+)?(?:размещения|публикации)\s+(?:ответа|разъяснени[яй])(?:\s+на\s+запрос)?(?:\s+в\s+ЕИС)?|(?:ответ|разъяснени[ея])\s+(?:размещен[оы]?|опубликован[оы]?)\s+в\s+ЕИС)"
SIGNED = r"(?:дата\s+подписания\s+(?:итогового\s+)?протокола|протокол\s+подписан)"
PROTOCOL_PUBLISHED = r"(?:дата\s+(?:и\s+время\s+)?(?:размещения|публикации)\s+(?:итогового\s+)?протокола(?:\s+в\s+ЕИС)?|протокол\s+(?:размещ[её]н|опубликован)\s+в\s+ЕИС)"
COMPETITIVE = re.compile(r"конкурс|аукцион|запрос\s+(?:котировок|предложений)", re.I)
CANCEL_DECISION = r"(?:дата\s+(?:принятия\s+)?решения\s+об\s+отмене\s+закупки|решение\s+об\s+отмене\s+закупки\s+принято)"
CANCEL_POSTED = r"(?:дата\s+(?:размещения|публикации)\s+(?:извещения|решения)\s+об\s+отмене\s+закупки(?:\s+в\s+ЕИС)?|(?:извещение|решение)\s+об\s+отмене\s+закупки\s+размещено)"
SME_EXCLUSIVE = re.compile(r"(?:только|исключительно)\s+субъект[ыа-я\s]{0,35}малого\s+и\s+среднего\s+предпринимательства|участниками\s+которо[йго]+\s+могут\s+быть\s+только\s+субъект",re.I)
PAYMENT_DAYS = re.compile(r"(?:оплата|расч[её]т[ыа]?|оплат[уы])[^.;]{0,280}?(?:в\s+течение|не\s+позднее|срок\s+оплаты\s+составляет)\s*(?P<days>\d{1,3})\s*(?:\([^)]{0,40}\)\s*)?рабочих\s+дн",re.I)


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
    return list(re.finditer(pattern+r"\s*[:：|=–—\-\s]{0,15}(?P<date>"+DATE_TOKEN+r")", text, re.I))[:40]


def finding(doc, code, title, law, evidence, match, url=LAW32, event_number=None):
    family = next((value for value in ("PROTOCOL", "CLARIFICATION") if value + "-" in code), None)
    if family:
        from v348_cross_document_legal import document_event_number, event_identity_evidence
        if event_number is None:event_number = document_event_number(doc, family)
        evidence = event_identity_evidence(evidence, family, event_number, doc)
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
                                           same_original_identity)
    if same_original_identity(left, right):return False
    a, b = str(left.get("code") or ""), str(right.get("code") or "")
    for family, number in (("PROTOCOL-", finding_protocol_number),
                           ("CLARIFICATION-", finding_clarification_number)):
        if family in a and family in b:
            first, second = number(left), number(right)
            return bool(first and second and first != second)
    return False


def _identified_event(item):
    from v348_cross_document_legal import finding_protocol_number, finding_clarification_number
    code = str(item.get("code") or "")
    for family, reader in (("PROTOCOL-", finding_protocol_number),
                           ("CLARIFICATION-", finding_clarification_number)):
        if family in code:return bool(reader(item))
    return False


def _deduplicate_events(items, event_key):
    # A generic/ambiguous row must never absorb all independently numbered
    # events solely because it arrived first. Prefer established IDs in each
    # date bucket; retain the old fallback when no reliable ID is available.
    grouped = {}
    for index, item in enumerate(items):
        key = ("event", event_key(item)) if item.get("kind") == "violation" else ("other", index)
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
    from v348_cross_document_legal import (ambiguous_original_documents, document_event_number,
                                          event_evidence_text)
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
            # A reply before its request or after another request is ambiguous.
            next_request = requests[index+1].start() if index+1 < len(requests) else len(text)
            peers = [x for x in answers
                     if request.end() <= x.start() < next_request
                     and x.start()-request.end() <= 1500]
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
                snippet = text[max(0,payment.start()-100):payment.end()+500]
                # A payment term must run from acceptance. Generic law
                # quotations and a mere mention of SME support are not scope.
                acceptance = re.search(r"(?:со?\s+дня|с\s+дат[ыа]|после)\s+"
                    r"(?:подписания|утверждения)\s+(?:заказчиком\s+|сторонами\s+)?"
                    r"(?:акта|документа)\s+(?:(?:сдачи[-–]при[её]мки|при[её]мки)|о\s+при[её]мке)",snippet,re.I)
                if days>7 and acceptance and not re.search(r"например|образец\s+расч[её]та",snippet,re.I):
                    findings.append(finding(doc,"1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS",
                        "В закупке только для субъектов МСП установлен срок оплаты свыше семи рабочих дней",
                        "Постановление Правительства РФ № 1352, Положение, п. 28",
                        f"Условие об оплате: {days} рабочих дней. {snippet}",payment,
                        "https://www.consultant.ru/document/cons_doc_LAW_172683/"))
        # Protocol signing and EIS publication must each be explicitly named.
        signed, published = matches(SIGNED, text), matches(PROTOCOL_PUBLISHED, text)
        official = {}
        protocol_conflict = False
        if competitive and len(signed) == 1 and number(doc, "PROTOCOL"):
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
                          and not any(signature.end() < other.start() < publication.start()
                                      for other in signed if other is not signature)
                          and not any(signature.end() < other.start() < publication.start()
                                      for other in published if other is not publication)]
            if len(candidates) == 1 and candidates[0].start() not in used:
                pairs.append((signature, candidates[0]))
                used.add(candidates[0].start())
        if len(signed) == len(published) == 1 and not pairs:
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
        for signature, publication in pairs:
            if protocol_conflict:continue
            start, end = day(signature["date"]), day(publication["date"])
            due = start+timedelta(days=3) if start else None
            working = federal_workday(due) if due else None
            if working is False:
                due = shifted(due, 1)
            if start and end and due and working is not None and end > due and 0 <= (end-start).days < 365:
                findings.append(finding(doc, "223-4-12-PROTOCOL-PUBLICATION-LATE",
                    "Протокол размещён в ЕИС с нарушением срока публикации", "223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193",
                    f"Протокол подписан {start}; крайний день размещения {due}; публикация в ЕИС {end}. {signature[0]}; {publication[0]}",
                    signature, LAW4, event_number=number(doc, "PROTOCOL")))
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
        dates = tuple(re.findall(DATE_TOKEN, event_evidence_text(item.get("evidence")), re.I))
        if not dates:
            dates = tuple(re.findall(DATE_TOKEN, item.get("match_text") or "", re.I))
        return item["code"], dates
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
            from v348_cross_document_legal import event_evidence_text
            dates = tuple(re.findall(DATE_TOKEN, event_evidence_text(item.get("evidence")), re.I))
            return (item.get("code"), dates) if dates else (
                item.get("code"), " ".join(str(item.get("match_text") or "").split()))
        return _deduplicate_events(existing + extra, event_key)

    @wraps(build)
    def enriched_checks(p, detail, card, docs):
        enrich(ns, p, docs, card, detail)
        _, extra = shared_evaluate(p, docs, consume=True)
        combined = list(build(p, detail, card, docs)) + extra
        seen = set()
        unique = []
        for item in combined:
            if not isinstance(item, dict):
                unique.append(item)
                continue
            dates = tuple(re.findall(DATE_TOKEN, str(item.get('evidence') or ''), re.I))
            key = (item.get('code'), dates) if dates and item.get('code') else None
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            unique.append(item)
        return unique

    enriched_analyze.__v341__ = True
    ns["analyze"], ns["build_checks"] = enriched_analyze, enriched_checks
    print("V341 LEGAL_FACTS_READY clarification_workdays=True protocols=True source_metadata=True", flush=True)
