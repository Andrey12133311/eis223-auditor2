"""Article 4(11) of 223-FZ: amendment publication lag and bid-deadline extension.

Uses only verified EIS originals and a source-linked EIS change history. Date
comparisons ignore clock hours (user requirement). A newly published revision
does not prove when its adoption decision was made. No guessed dates/counts.
"""
from __future__ import annotations

import json
import re
from datetime import date,datetime
from functools import wraps
from urllib.parse import urlsplit

from v295_eis_originals import root,uid_from_url

LAW="223-ФЗ, статья 4, часть 11"
LAW_URL="https://www.consultant.ru/document/cons_doc_LAW_116964/441d00be62e3224cdc0514cffaf2a26b5b40a1c7/"
REG=re.compile(r"^[0-9]{11,20}$")
DATE_RX=r"(?P<day>(?:[0-3]?\d[.][01]?\d[.]20\d{2})|(?:20\d{2}-[01]\d-[0-3]\d))"
DECISION=re.compile(
    r"(?:дата\s+(?:принятия\s+)?решения\s+о\s+внесении\s+изменени\w*|"
    r"решени\w*\s+о\s+внесении\s+изменени\w*\s+(?:от|принят\w*)|"
    r"(?:изменени\w*|редакци\w*)\s+документаци\w*\s+(?:от|утвержден\w*\s*))"
    r"\s*[:№–—\-]*\s*"+DATE_RX,re.I)
PUBLISHED=re.compile(
    r"(?:дата\s+(?:и\s+время\s+)?(?:публикации|размещения)\s+"
    r"(?:изменени\w*|новой\s+редакци\w*)\s+(?:в\s+)?ЕИС|"
    r"(?:изменени\w*|редакци\w*)\s+(?:размещен\w*|опубликован\w*)\s+(?:в\s+)?ЕИС)"
    r"\s*[:№–—\-]*\s*"+DATE_RX,re.I)
# Separate clues: a document dated Aug 10 is NOT proof of formal adoption;
# a publication date of a general notice is NOT an amendment's publication.
DOC_DATE=re.compile(
    r"(?:изменени\w*|редакци\w*|документаци\w*)"
    r".{0,95}?\b(?:от|утвержден\w*)\s*"+DATE_RX,re.I)
SUBMISSION_DATE=re.compile(
    r"(?:дата\s+(?:и\s+время\s+)?окончани\w*\s+(?:срока\s+)?"
    r"подачи\s+заявок|окончани\w*\s+(?:срока\s+)?подачи\s+заявок)"
    r".{0,70}?"+DATE_RX,re.I)

def day(value):
    if not value:return None
    try:
        if isinstance(value,(date,datetime)):return value.date() if isinstance(value,datetime) else value
        raw=str(value).strip()
        if raw[:10].count("-")==2:return date.fromisoformat(raw[:10])
        m=re.search(r"\b([0-3]?\d)[.]([01]?\d)[.](20\d{2})\b",raw)
        if m:return date(int(m.group(3)),int(m.group(2)),int(m.group(1)))
    except (ValueError,OverflowError):return None
    return None

def official_url(value):
    try:
        p=urlsplit(str(value or ""))
        return (p.scheme=="https" and p.hostname=="zakupki.gov.ru"
                and not p.username and not p.password and p.port in (None,443))
    except (ValueError,TypeError):return False

def verified(d):
    if not isinstance(d,dict) or d.get("status")!="checked":return False
    m=d.get("meta") or {}
    return (isinstance(m,dict) and
            m.get("source") in ("official_eis_original","official_eis_archive_member")
            and bool(re.fullmatch(r"[0-9a-f]{64}",str(m.get("sha256") or "")))
            and uid_from_url(m.get("source_url") or "") is not None
            and len(str(d.get("text") or "").strip())>30)

def document_evidence(docs):
    """Find explicit decision and publication dates, with exact source excerpts."""
    out=[]
    for d in docs:
        if not verified(d):continue
        text=str(d.get("text") or "")[:650000].replace("\xa0"," ")
        for m in DECISION.finditer(text):
            decision=day(m.group("day"))
            if decision is None:continue
            # Pair only within the same local amendment section/document.
            snippet=text[max(0,m.start()-220):min(len(text),m.end()+480)]
            pub=PUBLISHED.search(snippet)
            publication=day(pub.group("day")) if pub else None
            out.append({"decision_date":decision,"publication_date":publication,
                        "document":d.get("name"),"source":(d.get("meta") or {}).get("source_url"),
                        "decision_evidence":" ".join(text[max(0,m.start()-90):m.end()+110].split())[:360],
                        "publication_evidence":" ".join(pub.group(0).split())[:270] if pub else "",
                        "kind":"document_explicit"})
            if len(out)>=40:return out
    return out

def manifest_changes(reg):
    """Structured EIS browser history. Never treat notice publication as a change."""
    if not REG.fullmatch(str(reg or "")):return []
    path=root()/"eis223_public_browser_intake"/reg/"manifest.json"
    try:
        manifest=json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(manifest,dict) or manifest.get("source")!="public_eis_223_browser"
            or manifest.get("reg_number")!=reg
            or not official_url(manifest.get("source_page"))):
            return []
    except (OSError,ValueError,TypeError):return []
    changes=manifest.get("changes")
    if not isinstance(changes,list):return []
    result=[]
    for ch in changes[:80]:
        if not isinstance(ch,dict):continue
        source=ch.get("source_page") or ch.get("source_url")
        if not official_url(source):continue
        if not re.search(r"(?:изменени\w*|редакци\w*|change|amend)",str(ch.get("type") or ""),re.I):
            continue
        published=day(ch.get("publication_date") or ch.get("published_at"))
        if published is None:continue
        decision=day(ch.get("decision_date"))
        deadline=day(ch.get("submission_end"))
        result.append({"decision_date":decision,"publication_date":published,
                       "submission_end":deadline,
                       "document":str(ch.get("document_name") or "Изменения документации ЕИС")[:240],
                       "source":source,"decision_evidence":str(ch.get("decision_evidence") or "")[:380],
                       "publication_evidence":str(ch.get("publication_evidence") or "")[:380],
                       "kind":"eis_history"})
    return result

def evaluation(p,docs):
    reg=str(p.get("reg") or "")
    if not REG.fullmatch(reg):return ([],[])
    proofs=document_evidence(docs)
    events=manifest_changes(reg)
    findings=[]
    reviews=[]
    pairs=set()
    # Publication timestamp MUST be from explicit amendment history or a
    # document stating "changes published in EIS" (not file date/mtime).
    for event in [*events,*proofs]:
        decided=event.get("decision_date")
        published=event.get("publication_date")
        matched=[event]
        if published and not decided:
            # Document decision date can be matched only when the same real
            # amendment is identifiable in the source text/date and history.
            for proof in proofs:
                if proof["decision_date"] and (
                    proof.get("document")==event.get("document")
                    or proof.get("source")==event.get("source")):
                    decided=proof["decision_date"]
                    matched.append(proof)
                    break
        key=(str(decided),str(published),event.get("source"))
        if key in pairs:continue
        pairs.add(key)
        if decided and published:
            delta=(published-decided).days
            if delta>3 and 0<delta<=365:
                detail=(f"Решение об изменении: {decided.isoformat()}; "
                        f"публикация изменений в ЕИС: {published.isoformat()}; "
                        f"прошло {delta} календарных дней. "
                        f"Источник: {event.get('publication_evidence') or event.get('source')}; "
                        f"решение: {event.get('decision_evidence') or 'подтверждённые данные ЕИС'}.")
                findings.append({
                    "code":"223-4-11-AMENDMENT-PUBLICATION-DELAY",
                    "kind":"violation",
                    "title":"Изменения документации опубликованы позднее трёх дней после решения",
                    "law":LAW,"law_url":LAW_URL,"evidence":detail[:1500],
                    "note":"Срок рассчитан по календарным датам без учёта времени; исключения и действующая редакция проверяются отдельно.",
                    "document":event.get("document"),"source_url":event.get("source"),
                    "location":"История изменений ЕИС / дата решения о внесении изменений",
                    "match_text":event.get("decision_evidence") or event.get("publication_evidence") or detail[:180]
                })
            elif delta<0:
                reviews.append({"reason":"Дата публикации предшествует дате решения","event":event})
        elif published or decided:
            reviews.append({"reason":"Для ч. 11 ст. 4 не установлены обе даты решения и публикации изменений","event":event})
    # The minimum extension depends on the procurement regulation for THIS
    # procurement method, not on a universal 7-day or 15-day guess.
    for event in events:
        published=event.get("publication_date")
        if not published:continue
        deadline=event.get("submission_end") or day(p.get("submission_end"))
        if not deadline:continue
        # Any deadline before publication cannot satisfy the half-term rule
        # regardless of a procurement regulation duration.
        remaining=(deadline-published).days
        if remaining<0:
            findings.append({
                "code":"223-4-11-CHANGE-AFTER-BID-DEADLINE",
                "kind":"violation","title":"Изменения размещены после окончания срока подачи заявок",
                "law":LAW,"law_url":LAW_URL,
                "evidence":f"Размещение изменений: {published}; окончание подачи заявок по проверенной редакции: {deadline}.",
                "note":"Срок подачи не мог быть продлён с даты размещения изменений; проверить последующую редакцию и применимые исключения.",
                "document":event.get("document"),"source_url":event.get("source"),
                "location":"Официальные изменения и срок подачи заявок",
                "match_text":event.get("publication_evidence") or str(published)
            })
        else:
            reviews.append({"reason":"Проверить минимум половины срока из положения о закупке для конкретного способа", "event":event})
    # Explicitly dated archived documentation alone is a reason to compare
    # the published change history, but NOT a confirmed legal violation.
    if not proofs and not events:
        candidates=0
        for d in docs:
            if not verified(d):continue
            name=str(d.get("name") or "")
            if not re.search(r"(?:изменен|редакц|документац)",name,re.I):continue
            text=str(d.get("text") or "")[:500000]
            match=DOC_DATE.search(text)
            if match:
                reviews.append({"reason":"В оригинале есть датированная редакция; дата размещения изменений в ЕИС ещё не подтверждена",
                                "event":{"document":name,"source":(d.get("meta") or {}).get("source_url"),
                                         "decision_date":day(match.group("day")),
                                         "decision_evidence":match.group(0)}})
                candidates+=1
                if candidates>=5:break
    unique={}
    for f in findings:
        unique.setdefault((f["code"],f.get("source_url"),str(f["evidence"])[:100]),f)
    return list(unique.values()),reviews[:15]

def review_check(item):
    e=item["event"]
    return {
        "scope":"223-ФЗ / изменения документации",
        "item":"Проверить срок публикации и продление подачи заявок (ч. 11 ст. 4)",
        "status":"check",
        "evidence":(
            f"{item['reason']}; документ: {e.get('document') or '—'}; "
            f"решение: {e.get('decision_date') or 'не установлено'}; "
            f"ЕИС: {e.get('publication_date') or 'не установлено'}")[:1900],
        "source":e.get("document") or "История изменений ЕИС",
        "source_url":e.get("source") or "",
        "document":e.get("document"),
        "field_path":"amendment-decision-vs-publication",
        "note":"Не считать нарушением без подтверждения даты решения и публикации, применимости конкурентной процедуры и положения о закупке.",
        "law":LAW,"law_url":LAW_URL,
        "location":"Изменения и разъяснения / версии документации"
    }

def install(ns):
    analyze=ns.get("analyze")
    checks=ns.get("build_checks")
    if not callable(analyze) or not callable(checks):
        raise RuntimeError("Cannot install amendment rule without legal analyzer")
    if getattr(analyze,"__art4_11_v330__",False):return
    @wraps(analyze)
    def wrapped_analyze(p,docs):
        old=list(analyze(p,docs))
        new,_=evaluation(p,docs)
        if str(p.get("reg"))=="32616233661" or new:
            print(f"V330 ART4_11 reg={p.get('reg')} proved={len(new)}",flush=True)
        return old+new
    wrapped_analyze.__art4_11_v330__=True
    @wraps(checks)
    def wrapped_checks(p,detail,card,docs):
        old=list(checks(p,detail,card,docs))
        _,uncertain=evaluation(p,docs)
        return old+[review_check(x) for x in uncertain]
    ns["analyze"]=wrapped_analyze
    ns["build_checks"]=wrapped_checks
    print("V330_ART4_11_READY verified_eis_only=True date_only=True minimum_half_term_requires_regulation=True",flush=True)
