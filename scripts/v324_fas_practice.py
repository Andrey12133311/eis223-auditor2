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
    {
        "date": "2026-07-02", "authority": "Московское УФАС",
        "number": "29558/26",
        "url": "https://base.garant.ru/483665951/",
        "principle": "Контроль коллективного участия и дополнительных документов по ст. 3.4",
        "scope": "Административная линия практики"
    },
    {
        "date": "2026-05-28", "authority": "Арбитражный суд Московского округа",
        "number": "Ф05-6397/26 (А40-45512/2025)",
        "url": "https://base.garant.ru/69541352/",
        "principle": "Специальная ст. 3.4 не исключает коллективное участие МСП при выполнении условий закона",
        "scope": "Судебная позиция, противоположная ряду решений УФАС; учитывать факты дела"
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

# Current FAS enforcement practice also treats the explicit admission of
# several legal entities AS ONE PARTICIPANT in an SME-only tender as an
# independent issue, not merely the requirement to attach their agreement.
# Narrowly require an operative invitation, collective membership, and an
# SME-only clause in the same tender paragraph of an EIS verified original.
_ADMISSION = re.compile(
    r"(?:могут\s+принять\s+участи\w*|"
    r"допуска\w*\s+к\s+участи\w*|"
    r"допускается\s+участи\w*).{0,260}?"
    r"(?:нескольк\w*\s+юридическ\w*\s+лиц|"
    r"нескольк\w*\s+физическ\w*\s+лиц|"
    r"коллективн\w*\s+участник\w*)",
    re.I | re.S,
)
_ADMISSION_REVERSE = re.compile(
    r"(?:нескольк\w*\s+юридическ\w*\s+лиц|"
    r"коллективн\w*\s+участник\w*).{0,220}?"
    r"(?:могут\s+принять\s+участи\w*|"
    r"допуска\w*\s+к\s+участи\w*)",re.I | re.S,
)
_PROHIBITED = re.compile(
    r"(?:не\s+допуска\w*|не\s+могут\s+принять\s+участи\w*|"
    r"запреща\w*|не\s+предусматрива\w*|"
    r"не\s+допускается\s+участи\w*)",re.I,
)
_LAW_CITATION = re.compile(
    r"(?:част[ьи]\s+5\s+стать[ьи]\s+3|"
    r"в\s+соответствии\s+со\s+стать[её]й\s+3|"
    r"стат[ьея]\s+3\.4\s+закона|"
    r"настоящ(?:ий|его)\s+федеральн\w*\s+закон)",re.I,
)

_DIAGNOSTIC_DONE=set()
def _target_evidence_probe(p,docs,admissions):
    """Small excerpts from already verified public EIS originals, for debugging."""
    reg=str(p.get("reg") or "")
    if reg!="32616324151" or reg in _DIAGNOSTIC_DONE:return
    _DIAGNOSTIC_DONE.add(reg)
    from v324_official_price import LABEL,prices_in_text,verified_nmcd
    verified=[d for d in docs if _verified(d)]
    evidence=[]
    nmcd_labels=nmcd_amounts=0
    for doc in verified:
        text=_plain(doc.get("text"))
        nmcd_labels+=len(LABEL.findall(text))
        nmcd_amounts+=len(prices_in_text(text))
        terms=list(re.finditer(
           r"(?:коллективн\w*|нескольк\w*\s+юридическ\w*\s+лиц|"
           r"могут\s+принять\s+участи\w*|"
           r"член\w*\s+объединени\w*)",text,re.I))
        for match in terms[:3]:
            if len(evidence)>=8:break
            context=" ".join(text[max(0,match.start()-160):match.end()+210].split())
            evidence.append((str(doc.get("name") or "")[:90],context[:390]))
        if len(evidence)>=8:break
    candidates=verified_nmcd(docs)
    print(f"V326 EVIDENCE_PROBE reg={reg} originals={len(verified)} "
          f"nmcd_labels={nmcd_labels} nmcd_candidates={nmcd_amounts} "
          f"nmcd_conflict={bool(candidates.get('price_conflict'))} "
          f"admission_candidates={len(admissions)} excerpt_count={len(evidence)}",
          flush=True)
    for index,(name,excerpt) in enumerate(evidence):
        print(f"V326 EVIDENCE_EXCERPT reg={reg} i={index} "
              f"document={name!r} excerpt={excerpt!r}",flush=True)


def _admission_cases(p,docs):
    confirmed,_=_sme_scope(p,docs)
    if not confirmed:return []
    seen=set()
    result=[]
    for doc in docs:
        if not _verified(doc):continue
        full=_plain(doc.get("text"))
        # Parsed PDFs / antiword often put a whole page or table on one very
        # long line. Never reject the entire line because it exceeds 2400
        # characters: inspect a bounded local window at each operative verb.
        hits=list(_ADMISSION.finditer(full))
        hits+=list(_ADMISSION_REVERSE.finditer(full))
        for m in sorted(hits,key=lambda x:x.start()):
            window=full[max(0,m.start()-250):min(len(full),m.end()+260)]
            clause=" ".join(window.split())
            if not _SME.search(clause):continue
            if _PROHIBITED.search(clause[max(0,m.start()-max(0,m.start()-250)-65):
                                         m.end()-max(0,m.start()-250)+100]):
                continue
            if not re.search(r"\b(?:в\s+конкурс\w*|в\s+закупк\w*|"
                             r"в\s+аукцион\w*|настоящ\w*\s+закупк\w*)\b",
                             clause,re.I):
                continue
            if _LAW_CITATION.search(clause) and not re.search(
                    r"в\s+(?:конкурсе|закупке|аукционе)",clause,re.I):
                continue
            if not re.search(
                    r"на\s+сторон[еы]\s+одного\s+[Уу]частник\w*|"
                    r"коллективн\w*\s+участник\w*",
                    clause,re.I):
                continue
            # Only actual procurement conditions, not the definition of
            # participants quoted from art. 3(5) 223-FZ.
            if re.search(r"согласно\s+(?:част[ьи]\s+5\s+)?стать[еёи]\s+3",clause,re.I):
                continue
            canonical=re.sub(r"\W+","",clause.casefold().replace("ё","е"))
            signature=hashlib.sha256(canonical.encode()).hexdigest()[:16]
            if signature in seen:continue
            seen.add(signature)
            result.append({
                "doc":doc,"section":clause[:900],
                "location":f"Оригинал: {doc.get('name')}; условия участия; позиция ~{m.start()+1}",
                "seed":m.group(0)[:140],
                "classification":"collective_admission","scope":confirmed
            })
            if len(result)>=5:return result
    return result

def _admission_check(record):
    doc=record["doc"]
    return {
        "code":"SME-COLLECTIVE-ADMISSION-PRACTICE-CONFLICT",
        "scope":"Документация МСП / коллективный участник",
        "item":"Проверить допуск коллективного участника в закупку только для МСП",
        "status":"check",
        "law":"223-ФЗ, ст. 3.4; ст. 3, ч. 5; ст. 3, ч. 1 п. 2",
        "law_url":"https://www.consultant.ru/document/cons_doc_LAW_116964/",
        "evidence":record["section"],
        "note":(
           "В проверенном оригинале найдено условие о коллективном "
           "участии нескольких юридических лиц в закупке только среди МСП. "
           "Письмо ФАС России 22.06.2023 № 28/48914/23 и ряд решений "
           "территориальных УФАС считают такой допуск неправомерным. "
           "Вместе с тем Арбитражный суд Московского округа в постановлении "
           "28.05.2026 № Ф05-6397/26 по делу А40-45512/2025 допустил "
           "коллективное участие при соответствующих обстоятельствах. "
           "Требуется оценка положения о закупке, редакции документации "
           "и правового статуса каждого члена группы. "
           "Это сигнал проверки, НЕ автоматически установленное нарушение."),
        "document":doc.get("name"),"source":doc.get("name"),
        "source_url":(doc.get("meta") or {}).get("source_url"),
        "location":record["location"],"field_path":"collective-sme-admission"
    }

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
        admissions=_admission_cases(p,docs)
        _target_evidence_probe(p,docs,admissions)
        if p.get("reg")=="32616324151" or cases:
            print(f"V324 FAS_EVIDENCE reg={p.get('reg')} "
                  f"sme_confirmed={any(c.get('scope') for c in cases)} "
                  f"agreements={len(cases)} verified_extra_documents={len(definitive)} "
                  f"review_only={sum(c['classification']!='violation' for c in cases)} "
                  f"collective_admission={len(admissions)} "
                  f"nmcd_verified={p.get('price_num') is not None}",
                  flush=True)
        # FAS decisions and a 2026 circuit-court judgment disagree about
        # collective SME admission; never inflate violation counts based on
        # admission alone. Add an evidence-backed "check" below instead.
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
        # An established extra-document finding supersedes an ambiguous
        # agreement warning for the same procurement.
        candidates=[x for x in cases if x["classification"]!="violation"] if not (
            any(x["classification"]=="violation" for x in cases)
            or _admission_cases(p,docs)) else []
        if candidates:
            output.append(_check(candidates[0]))
        admissions=_admission_cases(p,docs)
        if admissions:
            output.append(_admission_check(admissions[0]))
        return output

    ns["analyze"]=analyze
    ns["build_checks"]=build_checks
    print("V325 FAS_PRACTICE_READY sources=6 verified_only=True SME_scope_required=True collective_admission_review=True",flush=True)
