"""Concrete date chains and scope-aware legal validators for 223-FZ.

All findings require receipt-verified originals, explicit facts and applicable
rules. Generic law quotations, missing fields and unresolved exceptions remain
review items. Calendar dates deliberately ignore the time of day.
"""
from __future__ import annotations

import re
from datetime import timedelta
from functools import wraps

from v298_eis_only import federal_workday
from v331_broad_legal import verified
from v341_eis_facts import DATE_TOKEN, enrich, persist_facts, parse_day as day

LAW32 = "https://www.consultant.ru/document/cons_doc_LAW_116964/05cd0add21b39d478c8b8af91fb2f2cd80d4a6e8/"
LAW4 = "https://www.consultant.ru/document/cons_doc_LAW_116964/441d00be62e3224cdc0514cffaf2a26b5b40a1c7/"
REQUEST = r"(?:дата\s+(?:и\s+время\s+)?(?:поступления|получения|регистрации)\s+запроса(?:\s+(?:на\s+)?разъяснени[еяй])?|запрос\s+(?:на\s+)?разъяснени[еяй]\s+(?:поступил|получен|зарегистрирован))"
ANSWER = r"(?:дата\s+(?:и\s+время\s+)?(?:размещения|публикации)\s+(?:ответа|разъяснени[яй])(?:\s+на\s+запрос)?(?:\s+в\s+ЕИС)?|(?:ответ|разъяснени[ея])\s+(?:размещен[оы]?|опубликован[оы]?)\s+в\s+ЕИС)"
SIGNED = r"(?:дата\s+подписания\s+(?:итогового\s+)?протокола|протокол\s+подписан)"
PROTOCOL_PUBLISHED = r"(?:дата\s+(?:и\s+время\s+)?(?:размещения|публикации)\s+(?:итогового\s+)?протокола(?:\s+в\s+ЕИС)?|протокол\s+(?:размещ[её]н|опубликован)\s+в\s+ЕИС)"
COMPETITIVE = re.compile(r"конкурс|аукцион|запрос\s+(?:котировок|предложений)", re.I)


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


def finding(doc, code, title, law, evidence, match, url=LAW32):
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


def evaluate(p, docs):
    findings, checks = [], []
    competitive = bool(COMPETITIVE.search(str(p.get("method") or "")))
    for doc in docs[:650]:
        if not verified(doc):
            continue
        text = str(doc.get("text") or "")[:750000].replace("\xa0", " ")
        requests = matches(REQUEST, text)
        answers = matches(ANSWER, text)
        for request in requests:
            # Match only a single response in the same local request section.
            # Multiple requests/replies require a reliable event identifier.
            peers = [x for x in answers if abs(x.start()-request.start()) < 1500]
            nearby_requests = [x for x in requests if abs(x.start()-request.start()) < 1500]
            if len(peers) != 1 or len(nearby_requests) != 1:
                checks.append(review(doc, "v341-clarification-pair", "Сопоставить запрос разъяснений с публикацией ответа",
                                     request[0], "223-ФЗ, ст. 3.2 ч. 3", request))
                continue
            received, published = day(request["date"]), day(peers[0]["date"])
            deadline = day(p.get("submission_end") or p.get("deadline"))
            due, latest = shifted(received, 3), shifted(deadline, -3)
            snippet = " ".join(text[max(0, min(request.start(), peers[0].start())-60):max(request.end(), peers[0].end())+70].split())
            if not competitive or not due or not latest or received > latest or published < received:
                checks.append(review(doc, "v341-clarification-applicability", "Проверить обязанность и срок ответа на запрос разъяснений",
                                     snippet, "223-ФЗ, ст. 3.2 ч. 3", request))
                continue
            if published > due:
                findings.append(finding(doc, "223-3.2-3-CLARIFICATION-RESPONSE-LATE",
                    "Разъяснение опубликовано позднее трёх рабочих дней после поступления запроса",
                    "223-ФЗ, ст. 3.2 ч. 3",
                    f"Запрос получен {received}; крайний день ответа {due}; опубликован {published}; окончание подачи {deadline}. {snippet}", request))
        # Protocol signing and EIS publication must each be explicitly named.
        signed, published = matches(SIGNED, text), matches(PROTOCOL_PUBLISHED, text)
        if len(signed) == 1 and len(published) == 1:
            start, end = day(signed[0]["date"]), day(published[0]["date"])
            # Civil Code art. 193 moves a last day that is non-working to
            # the following working day; apply it before alleging a delay.
            due = start+timedelta(days=3)
            working = federal_workday(due)
            if working is False:
                due = shifted(due, 1)
            if due and working is not None and end > due and (end-start).days < 365:
                findings.append(finding(doc, "223-4-12-PROTOCOL-PUBLICATION-LATE",
                    "Протокол размещён в ЕИС с нарушением срока публикации", "223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193",
                    f"Протокол подписан {start}; крайний день размещения {due}; публикация в ЕИС {end}. {signed[0][0]}; {published[0][0]}",
                    signed[0], LAW4))
    # The same EIS original can be mirrored, renamed or parsed repeatedly.
    # Identify a legal occurrence by rule and the explicit dated event, not
    # by document title, whitespace, or a generated evidence paragraph.
    seen = set()
    result = []
    for item in findings:
        date_tokens = tuple(re.findall(DATE_TOKEN, item.get("match_text") or "", re.I))
        # Preserve separate occurrences with distinct dated evidence.
        key = (item["code"], date_tokens, " ".join((item.get("match_text") or "").lower().split()))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result, checks[:25]


def install(ns):
    analyze, build = ns["analyze"], ns["build_checks"]
    if getattr(analyze, "__v341__", False):
        return

    @wraps(analyze)
    def enriched_analyze(p, docs):
        enrich(ns, p, docs)
        persist_facts(ns, p)
        existing = list(analyze(p, docs))
        extra, _ = evaluate(p, docs)
        # Prefer a pre-existing finding for this same legal occurrence.
        for item in extra:
            if not any(x.get("kind") == "violation" and x.get("document") == item.get("document")
                       and (x.get("code") == item["code"] or
                            (x.get("law") == item["law"] and x.get("match_text") == item["match_text"])) for x in existing):
                existing.append(item)
        return existing

    @wraps(build)
    def enriched_checks(p, detail, card, docs):
        enrich(ns, p, docs, card, detail)
        _, extra = evaluate(p, docs)
        return list(build(p, detail, card, docs))+extra

    enriched_analyze.__v341__ = True
    ns["analyze"], ns["build_checks"] = enriched_analyze, enriched_checks
    print("V341 LEGAL_FACTS_READY clarification_workdays=True protocols=True source_metadata=True", flush=True)
