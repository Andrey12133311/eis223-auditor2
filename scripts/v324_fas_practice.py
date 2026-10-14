"""Versioned evidence-based 223-FZ / FAS SME collective-participation review.

Only checked official EIS original files are considered established evidence.
Never convert a bare acronym "МСП" or reference to a general statute into proof
that a tender is restricted to SME participants. Review-only signals do not
increment confirmed violation counts.
"""
from __future__ import annotations
import hashlib
import re
from functools import wraps

LAW = "223-ФЗ, ст. 3.4, ч. 19.1–19.3; ст. 3, ч. 5"
LAW_URL = "https://www.consultant.ru/document/cons_doc_LAW_116964/a3b63487ad7d07ce045a07b0f1e7a9d9b064de95/"
PRACTICE = (
    {
        "date": "2023-03-16", "authority": "Минфин России",
        "number": "24-07-08/22356",
        "url": "https://www.garant.ru/products/ipo/prime/doc/406459201/",
        "principle": "Специальные правила ст. 3.4 не предполагают коллективное участие в МСП-конкурсе",
        "scope": "Разъяснение; оценивать конкретные условия закупки и редакцию закона"
    },
    {
        "date": "2023-06-22", "authority": "ФАС России",
        "number": "28/48914/23",
        "url": "https://www.consultant.ru/document/cons_doc_LAW_496803/",
        "principle": "Поддержана позиция Минфина по коллективному участию в МСП-закупках",
        "scope": "Письмо, не нормативный акт и не решение по любой конкретной закупке"
    },
    {
        "date": "2026-07-02", "authority": "Московское УФАС",
        "number": "29558/26",
        "url": "https://base.garant.ru/483665951/",
        "principle": "Дополнительные документы коллективной заявки в МСП-конкурсе оцениваются по ч. 19.3 ст. 3.4",
        "scope": "Решение по иной закупке; факты проверяются отдельно"
    },
    {
        "date": "2021-07-22", "authority": "ФАС России",
        "number": "223-ФЗ-377/21",
        "url": "https://base.garant.ru/75943003/",
        "principle": "Применение закрытого перечня документов по ч. 19.1–19.3 ст. 3.4",
        "scope": "Сопоставлять с обстоятельствами каждого дела"
    },
)

_SME = re.compile(
    r"(?:участник\w*.{0,85}?(?:только|исключительно).{0,85}?"
    r"(?:субъект\w*\s+мал\w+\s+и\s+средн\w+\s+предпринимательств\w*|"
    r"субъект\w*\s+мсп|с[у]?мсп))|"
    r"(?:(?:только|исключительно|среди)\s+(?:субъект\w*\s+)?"
    r"(?:мал\w+\s+и\s+средн\w+\s+предпринимательств\w*|мсп|с[у]?мсп))",
    re.I | re.S,
)
_COLLECTIVE = re.compile(
    r"(?:коллективн\w*\s+участник\w*|член\w*\s+объединени\w*|"
    r"нескольк\w*\s+юридическ\w*\s+лиц|"
    r"(?:участник\w*|объединени\w*)\s+закуп\w*.*?нескольк\w*\s+лиц)",
    re.I | re.S,
)
_AGREEMENT = re.compile(
    r"(?:соглашени\w*|договор\w*\s+(?:простого\s+товариществ\w*|"
    r"о\s+(?:совместн\w*\s+деятельност\w*|партнёрств\w*|партнерств\w*)))",
    re.I,
)
_DUTY = re.compile(
    r"(?:должн\w*\s+(?:иметь|заключить|представ\w*|предостав\w*)|"
    r"обязан\w*\s+(?:иметь|заключить|представ\w*|предостав\w*)|"
    r"обязательн\w*\s+(?:наличи\w*|представлени\w*|предоставлени\w*)|"
    r"требует\w*\s+(?:соглашени\w*|договора\w*|представлени\w*))",
    re.I,
)
_APPLY = re.compile(r"\bзаявк\w*|\bпредложени\w*\s+на\s+участи\w*", re.I)
_SUBMIT = re.compile(
    r"\b(?:представ\w*|предостав\w*|прилож\w*|прикладыва\w*|"
    r"пода\w*|переда\w*|включ\w*)", re.I,
)
# Directly ties a required agreement to the tender APPLICATION.  Merely
# "must have an agreement" is an attention signal, not a proven 19.3 breach.
_REQUIRE_IN_BID = (
    re.compile(r"(?:состав\w*|подач\w*|приложени\w*|част\w*)\s+"
               r"заявк\w*.{0,250}?"
               r"(?:соглашени\w*|договор\w*\s+(?:простого|о\s+совместн))",re.I|re.S),
    re.compile(r"(?:соглашени\w*|договор\w*\s+(?:простого|о\s+совместн))"
               r".{0,260}?"
               r"(?:в\s+состав[еа]?\s+заявк\w*|к\s+заявк\w*|"
               r"в\s+заявк\w*|подач\w*\s+заявк\w*)",re.I|re.S),
    re.compile(r"(?:представ\w*|предостав\w*|прилож\w*).{0,100}?"
               r"(?:соглашени\w*|договор\w*\s+(?:простого|о\s+совместн))"
               r".{0,110}?(?:в\s+состав[еа]?\s+заявк\w*|к\s+заявк\w*|"
               r"в\s+заявк\w*)",re.I|re.S),
)
_NEGATED = re.compile(
    r"(?:не\s+(?:требует\w*|обязан\w*|должн\w*|нужно|представля\w*)|"
    r"отсутстви\w*\s+обязанност\w*|не\s+является\s+обязательн\w*)",re.I,
)
# Avoid classifying a regulation quotation or a discussion of a later
# contract as an additional application document.
_NON_BID = re.compile(
    r"(?:после\s+(?:проведени\w*\s+)?закупк\w*|"
    r"при\s+заключении\s+договора\s+с\s+заказчик\w*)",re.I,
)

def _plain(value,limit=650_000):
    text=str(value or "")[:limit].replace("\xa0"," ").replace("\x00"," ")
    return re.sub(r"[ \t\r\f\v]+"," ",text)

def _verified(doc):
    if not isinstance(doc,dict) or doc.get("status")!="checked":
        return False
    meta=doc.get("meta") or {}
    return (isinstance(meta,dict) and
            meta.get("source") in ("official_eis_original","official_eis_archive_member")
            and bool(meta.get("sha256") or meta.get("archive_sha256"))
            and bool(meta.get("source_url")) and len(str(doc.get("text") or "").strip())>30)

def _sme_scope(p,docs):
    # The procurement method is an explicit category returned by official
    # EIS XML or the verified browser manifest (not a generic document mention).
    method=_plain(p.get("method"),500)
    if _SME.search(method):
        return ("method",method)
    for d in docs:
        if not _verified(d):
            continue
        body=_plain(d.get("text"),100_000)
        for m in _SME.finditer(body):
            phrase=body[max(0,m.start()-180):m.end()+110]
            # Reject a bare quotation of the law or example eligibility.
            # The wording must expressly identify THIS competition.
            if re.search(r"(?:данн\w*|настоящ\w*|проводим\w*|эт\w*)\s+"
                         r"(?:закупк\w*|конкурс\w*|аукцион\w*|процедур\w*)",phrase,re.I) or (
                re.search(r"\b(?:конкурс\w*|закупк\w*|участник\w*)\b",phrase,re.I)
                and not re.search(r"(?:согласно\s+стать[еёи]|част[ьи]\s+19|федеральн\w*\s+закон)",phrase,re.I)):
                return ("verified_document",phrase[:260])
    return (None,"")

def _candidate_chunks(doc):
    text=_plain(doc.get("text"))
    # Find agreement mentions and examine limited windows, not an arbitrary
    # full document where distant unrelated clauses could create false positives.
    for match in _AGREEMENT.finditer(text):
        start=max(0,match.start()-320)
        end=min(len(text),match.end()+390)
        section=text[start:end]
        if not _COLLECTIVE.search(section) or not _DUTY.search(section):
            continue
        # If the relevant sentence says no document is needed, do not flag.
        if _NEGATED.search(section[max(0,match.start()-start-55):match.end()-start+90]):
            continue
        yield match,section,text

def _classification(section):
    # In practice a tender-specific mandatory submission can be worded in
    # multiple ways. Both the application context and an affirmative duty
    # must be present within the SAME bounded extract.
    requires=bool(_APPLY.search(section) and _SUBMIT.search(section)
                  and any(p.search(section) for p in _REQUIRE_IN_BID))
    if requires and not _NON_BID.search(section):
        return "violation"
    return "review"

def _cases(p,docs):
    scope,scope_evidence=_sme_scope(p,docs)
    matches=[]
    dedup=set()
    for doc in docs:
        if not _verified(doc):continue
        for m,section,text in _candidate_chunks(doc):
            classification=_classification(section) if scope else "needs_scope"
            # The excerpts are identical in successive versions of the
            # documentation; report the substantive issue once per purchase.
            canonical=re.sub(r"\W+","",section.casefold().replace("ё","е"))
            digest=hashlib.sha256(canonical.encode()).hexdigest()[:16]
            if digest in dedup:continue
            dedup.add(digest)
            matches.append({"doc":doc,"section":section.strip()[:900],
                            "scope":scope,"scope_evidence":scope_evidence,
                            "classification":classification,
                            "location":f"Оригинал: {doc.get('name')}; примерное смещение {m.start()+1}",
                            "seed":m.group(0)[:100]})
            if len(matches)>=12:return matches
    return matches

def _finding(record):
    d=record["doc"]
    law="223-ФЗ, ст. 3.4 ч. 19.1, 19.2, 19.3"
    return {
        "code":"SME-COLLECTIVE-APPLICATION-EXTRA",
        "kind":"violation",
        "title":"Дополнительный обязательный документ коллективного участника в МСП-закупке",
        "law":law,
        "law_url":LAW_URL,
        "evidence":record["section"],
        "note":("Условие об обязательном представлении соглашения в заявке. "
                "Подтверждённый режим МСП: "+record["scope"]+
                ". Сопоставлено с закрытым перечнем ч. 19.1–19.2 ст. 3.4; "
                "письмо ФАС № 28/48914/23, решение Московского УФАС № 29558/26. "
                "Проверить актуальную редакцию документации и отсутствие "
                "специального законного основания."),
        "document":d.get("name"),"source_url":(d.get("meta") or {}).get("source_url"),
        "location":record["location"],"match_text":record["seed"],
    }

def _check(record):
    d=record["doc"]
    if record["classification"]=="needs_scope":
        title="Проверить применимость специальных правил МСП к коллективному участнику"
        message="Режим МСП не подтверждён по официальным данным; нельзя применять ч. 19.3 как установленное нарушение."
    else:
        title="Проверить условие о соглашении коллективных участников"
        message=("Указана обязанность иметь соглашение, но обязательное представление "
                 "в составе заявки по этому фрагменту не доказано.")
    return {
        "scope":"Документация МСП / коллективный участник",
        "item":title,"status":"check","evidence":record["section"],
        "source":d.get("name"),"source_url":(d.get("meta") or {}).get("source_url"),
        "document":d.get("name"),"field_path":"collective-participant-agreement",
        "note":message+" Практика: ФАС № 28/48914/23; Московское УФАС № 29558/26.",
        "law":LAW,"law_url":LAW_URL,"location":record["location"],
    }

def install(ns):
    base_analyze=ns.get("analyze")
    base_checks=ns.get("build_checks")
    if not callable(base_analyze) or not callable(base_checks):
        raise RuntimeError("Legal rules require original analyzer and checks")
    if getattr(base_analyze,"__fas_collective_v324__",False):
        return

    @wraps(base_analyze)
    def analyze(p,docs):
        output=list(base_analyze(p,docs))
        cases=_cases(p,docs)
        definitive=[v for v in cases if v["classification"]=="violation"]
        if definitive:
            # Avoid multiple historic archive revisions inflating counts:
            # same legal issue is represented once per procurement.
            output.append(_finding(definitive[0]))
        return output
    analyze.__fas_collective_v324__=True

    @wraps(base_checks)
    def build_checks(p,detail,card,docs):
        output=list(base_checks(p,detail,card,docs))
        cases=_cases(p,docs)
        candidates=[x for x in cases if x["classification"]!="violation"]
        if candidates:
            output.append(_check(candidates[0]))
        return output

    ns["analyze"]=analyze
    ns["build_checks"]=build_checks
    print("V324 FAS_PRACTICE_READY sources=4 verified_only=True SME_scope_required=True confirmed_findings_not_fabricated=True",flush=True)
