"""V372: fully automatic review of procurement regulations and bid protocols.

Runs inside the existing server-side EIS legal analyzer for general, thematic,
manual, and repeated checks. Examines ONLY receipt-verified EIS originals that
were already downloaded and extracted. No browser, credentials, user actions,
new network requests, destructive database changes, or synthetic findings.

The only confirmed violation is an explicit, procurement-specific admission
in an authentic competitive-procurement protocol that a rejected numbered bid
has no stated grounds. Mere absence of searched words or uncertainty about
the customer's regulation produces a "Требует проверки" item, not a violation.
"""
from __future__ import annotations
from datetime import date
from functools import wraps
import re

from v331_broad_legal import verified as receipt_verified
from v341_precise_legal import COMPETITIVE
from v330_amendments_223 import day

LAW_REG = "https://www.consultant.ru/document/cons_doc_LAW_116964/fa59dfcdcfd8c5c80928d33fa840280273464c74/"
LAW_4 = "https://www.consultant.ru/document/cons_doc_LAW_116964/441d00be62e3224cdc0514cffaf2a26b5b40a1c7/"
LAW_PROT = "https://www.consultant.ru/document/cons_doc_LAW_116964/05cd0add21b39d478c8b8af91fb2f2cd80d4a6e8/"
DATE = r"(?:[0-3]?\d\.[01]?\d\.20\d{2}|20\d{2}-[01]\d-[0-3]\d)"
RX_REG = re.compile(r"положени[еяю]\s+о\s+закупк", re.I)
RX_PROT = re.compile(r"\bпротокол\b|\bпротокола\b",re.I)
RX_EXCLUDE = re.compile(r"протокол\s+(?:разъяснен|отмены\s+разъяснени)",re.I)
RX_BIDS = re.compile(r"(?:отклонен\w*|отказ\w*\s+в\s+допуске|не\s+допущен\w*)",re.I)
RX_BID_REFERENCE = re.compile(
    r"(?:заявк\w*|участник\w*)\s*(?:(?:№|N)\s*[\w\-]{1,15}|номер\s*[\w\-]{1,15})", re.I)
RX_REASON = re.compile(r"(?:основани\w*|причин\w*|обосновани\w*)\s+(?:отклонени\w*|отказа)",re.I)
RX_CLAUSE = re.compile(
    r"(?:пункт(?:а|у|ом)?|пп?\.|подпункт(?:а)?|раздел(?:а)?|ч\.?)"
    r"\s*(?:№\s*)?\d{1,3}(?:\.\d{1,3}){0,4}",re.I)
RX_NO_GROUNDS = re.compile(
    r"(?:основани\w*|причин\w*|обосновани\w*)\s+отклонени\w*"
    r"\s+(?:(?:по\s+)?(?:заявк\w*|участник\w*)\s*(?:№|N)\s*[\w\-]{1,15}\s+)?"
    r"(?:не\s+указан\w*|не\s+приведен\w*|отсутству\w*)", re.I)
RX_GROUNDS_NOT_STATED = re.compile(
    r"(?:отклонен\w*|отказан\w*\s+в\s+допуске)\s+"
    r"(?:заявк\w*|участник\w*)\s*(?:№|N)\s*[\w\-]{1,15}"
    r".{0,120}?(?:основани\w*|причин\w*)\s+(?:отклонени\w*\s+)?(?:не\s+указан\w*|отсутству\w*)",
    re.I|re.S)
RX_APPROVAL = re.compile(
    r"(?:дата\s+утверждени\w*\s+(?:положени\w*\s+о\s+закупк\w*)?|"
    r"положени\w*\s+о\s+закупк\w*\s+утвержден\w*)"
    r"\s*[:：–—=\-\s]{0,18}(?P<date>"+DATE+r")",re.I)
RX_PUBLICATION = re.compile(
    r"(?:дата\s+размещени\w*\s+(?:положени\w*\s+о\s+закупк\w*)?\s+в\s+ЕИС|"
    r"положени\w*\s+о\s+закупк\w*\s+размещен\w*\s+в\s+ЕИС)"
    r"\s*[:：–—=\-\s]{0,18}(?P<date>"+DATE+r")",re.I)
RX_METHOD_SECTION = re.compile(
    r"(?:способ[ыа]?\s+(?:осуществлени\w*\s+)?закуп\w*|"
    r"вид[ыа]?\s+закуп\w*)\s*[:\n]",re.I)
METHODS = {
    "конкурс":re.compile(r"\bконкурс\w*",re.I),
    "аукцион":re.compile(r"\bаукцион\w*",re.I),
    "котировки":re.compile(r"запрос\s+котировок",re.I),
    "предложения":re.compile(r"запрос\s+предложени\w*",re.I),
    "оферты":re.compile(r"запрос\s+оферт\w*",re.I),
}
MAX_DOCS=75
MAX_TEXT=150000

def _text(doc):
    return str(doc.get("text") or "")[:MAX_TEXT].replace("\xa0"," ").replace("\r","\n")

def _link(doc):
    return str((doc.get("meta") or {}).get("source_url") or doc.get("url") or "")

def _reg_doc(doc):
    title=str(doc.get("name") or "")
    heading=_text(doc)[:350]
    return bool(RX_REG.search(title) or re.search(r"^\s*(?:утверждено.{0,150}?)?положени\w*\s+о\s+закупк",heading,re.I|re.S))

def _protocol_doc(doc):
    title=str(doc.get("name") or "")
    heading=_text(doc)[:220]
    if RX_EXCLUDE.search(title):return False
    return bool(RX_PROT.search(title) or re.match(r"\s*протокол\s",heading,re.I))

def _competitive(p, doc):
    method=str(p.get("method") or p.get("purchase_method") or "")
    return bool(COMPETITIVE.search(method) or
        re.search(r"конкурентн\w*\s+закупк\w*",method,re.I) or
        (not method and re.search(r"(?:конкурс|аукцион|запрос\s+котировок|запрос\s+предложени\w*)",
                                  str(doc.get("name") or ""),re.I)))

def _fragment(body,start,end,limit=570):
    return " ".join(body[max(0,start-170):min(len(body),end+350)].split())[:limit]

def _record(doc,code,title,law,law_url,fragment,pos,note="",violation=False):
    return {
        "code":code,
        "title":title,
        "law":law,
        "law_url":law_url,
        "evidence":fragment[:1600],
        "document":str(doc.get("name") or "")[:255],
        "source_url":_link(doc),
        "location":"Оригинал ЕИС «"+str(doc.get("name") or "")[:170]+"», позиция "+str(pos+1),
        "note":note or "Сопоставлено автоматически по оригиналу ЕИС; отсутствие ключевых слов само по себе не доказывает нарушение.",
        "match_text":fragment[:480],
        **({"kind":"violation"} if violation else {
            "scope":"223-ФЗ / автоматическая проверка положения и протоколов",
            "item":title,"status":"check","field_path":code,
            "source":str(doc.get("name") or "")[:255]
        })
    }

def review_regulation(p,documents):
    """Compare with authentic applicable available regulation; warn when unresolved."""
    findings,checks=[],[]
    reg_docs=[d for d in documents if receipt_verified(d) and _reg_doc(d)]
    if not reg_docs:
        # Existing V331 warns when the regulation original has not been
        # received. Don't create a duplicate missing-document warning here.
        return findings,checks
    reg_docs=reg_docs[:3]
    purchase_day=day(p.get("published") or p.get("published_at") or "")
    for d in reg_docs:
        text=_text(d)
        # An externally published document can be older than the notice;
        # the correct version must be determined from the official EIS record.
        approvals=list(RX_APPROVAL.finditer(text[:9000]))[:2]
        publications=list(RX_PUBLICATION.finditer(text[:9000]))[:2]
        if approvals and publications:
            approved=day(approvals[0].group("date"))
            published=day(publications[0].group("date"))
            if approved and published and published>approved and (published-approved).days>15:
                proof=("Положение о закупке: "+approvals[0].group(0)+
                       "; "+publications[0].group(0)+". "
                       "Интервал: "+str((published-approved).days)+" календарных дней. "
                       "Необходимо подтвердить дату размещения по отдельной истории ЕИС.")
                checks.append(_record(
                    d,"v372-regulation-publication-timing","Проверить срок размещения положения о закупке",
                    "223-ФЗ, ст. 4 ч. 1",LAW_4,proof,approvals[0].start(),
                    "Даты найдены в одном оригинале. Без независимого подтверждения времени размещения в ЕИС не считается установленным нарушением."))
        if purchase_day and approvals:
            dates=[day(m.group("date")) for m in approvals]
            newer=[(m,dte) for m,dte in zip(approvals,dates) if dte and dte>purchase_day]
            if newer:
                first,date_value=newer[0]
                checks.append(_record(
                    d,"v372-regulation-version","Проверить применимость редакции положения о закупке",
                    "223-ФЗ, ст. 2 ч. 1–2; ст. 4 ч. 1",LAW_REG,
                    "Дата размещения извещения: "+purchase_day.isoformat()+
                    "; положение содержит: "+first.group(0)+". "
                    "Проверьте предыдущую редакцию и дату её действия.",first.start(),
                    "Поздняя редакция не доказывает незаконность ранее объявленной закупки. Нужна редакция на дату извещения."))
        method=str(p.get("method") or "")
        method_name=next((name for name,pattern in METHODS.items() if pattern.search(method)),None)
        section=RX_METHOD_SECTION.search(text[:20000])
        if method_name and section:
            # The section may continue in an annex or on another page;
            # non-matching extraction is only an incomplete-evidence check.
            excerpt=text[section.end():min(len(text),section.end()+6000)]
            if len(excerpt)>500 and not METHODS[method_name].search(excerpt):
                checks.append(_record(
                    d,"v372-regulation-purchase-method","Сопоставить способ закупки с положением заказчика",
                    "223-ФЗ, ст. 2 ч. 1–2; ст. 3 ч. 3.1–3.2",LAW_REG,
                    "Способ в карточке: "+method[:170]+
                    ". Фрагмент раздела положения о способах закупки: "+
                    " ".join(excerpt[:700].split()),section.start(),
                    "Способ не обнаружен в извлечённом фрагменте, но может присутствовать в приложениях или другой редакции. Требуется проверка."))
    return findings,checks

def review_protocols(p,documents):
    findings,checks=[],[]
    for doc in documents:
        if not receipt_verified(doc) or not _protocol_doc(doc) or not _competitive(p,doc):
            continue
        text=_text(doc)
        if len(text)<100:
            continue
        is_final=bool(re.search(r"итогов\w*|итогов\w*\s+протокол|подведени\w*\s+итог",str(doc.get("name") or "")+" "+text[:120],re.I))
        article="223-ФЗ, ст. 3.2 ч. "+("14 п. 5 пп. «б»" if is_final else "13 п. 3 пп. «б»")
        # A direct numbered-bid statement in an authenticated original is
        # stronger than an absence inferred from text search. Multiple
        # duplicate copies of that same passage are collapsed by the ledger.
        explicit=list(RX_GROUNDS_NOT_STATED.finditer(text))[:2]
        if explicit:
            for m in explicit:
                nearby=text[max(0,m.start()-220):min(len(text),m.end()+150)]
                # Require procurement-specific numbered bid and rejection
                # together with express lack of reasons, not a quote of law.
                if not RX_BID_REFERENCE.search(nearby) or not RX_BIDS.search(nearby):
                    continue
                if re.search(r"образец\s+протокол|проект\s+протокол|пример\s+заполнени",text[:350],re.I):
                    continue
                findings.append(_record(
                    doc,"v372-protocol-rejected-bid-no-reasons",
                    "В протоколе прямо указано отсутствие основания отклонения заявки",
                    article,LAW_PROT,_fragment(text,m.start(),m.end(),1100),m.start(),
                    "Конкретная заявка и отсутствие основания прямо зафиксированы в полученном оригинале. "
                    "Окончательную квалификацию и применимую редакцию документа подтверждает проверяющий.",
                    violation=True))
                break
        # Detect questionable protocol content automatically. These are not
        # confirmed violations because an excerpt can omit information on
        # another page or in an annex.
        rejected=RX_BIDS.search(text[:MAX_TEXT])
        if rejected:
            around=text[max(0,rejected.start()-180):min(len(text),rejected.end()+900)]
            if RX_BID_REFERENCE.search(around) and not RX_CLAUSE.search(around):
                checks.append(_record(
                    doc,"v372-protocol-rejected-bid-document-clause",
                    "Проверить ссылку на пункт документации при отклонении заявки",
                    article,LAW_PROT,_fragment(text,rejected.start(),rejected.end(),1100),
                    rejected.start(),
                    "В ближайшем фрагменте решения комиссии не распознан конкретный пункт документации. "
                    "Проверка продолжается автоматически по доступным оригиналам; отсутствие ссылки в отрывке "
                    "не означает установленного нарушения."))
        assessed=re.search(r"(?:присвоен\w*\s+\d{1,3}\s+балл|"
                           r"оценк\w*\s+заявк\w*|результат\w*\s+оценк\w*)",text,re.I)
        if assessed and not re.search(r"критери\w*[\s\S]{0,180}\d{1,3}\s+балл",
                                       text[max(0,assessed.start()-120):assessed.start()+1500],re.I):
            checks.append(_record(
                doc,"v372-protocol-criteria-values",
                "Сверить результаты оценки заявок с критериями документации",
                "223-ФЗ, ст. 3.2 ч. "+("14 п. 6" if is_final else "13 п. 4"),
                LAW_PROT,_fragment(text,assessed.start(),assessed.end(),800),assessed.start(),
                "Критерии, значения и правила балльной оценки должны подтверждаться протоколом и "
                "документацией; отсутствие рядом не означает нарушения."))
    return findings,checks

def evaluate(p,docs):
    """Pure deterministic assessment, no I/O or changes to purchase originals."""
    originals=[d for d in (docs or [])[:MAX_DOCS] if isinstance(d,dict)
               and d.get("status")=="checked" and receipt_verified(d)
               and str(d.get("text") or "").strip()]
    reg_find,reg_checks=review_regulation(p,originals)
    proto_find,proto_checks=review_protocols(p,originals)
    # No duplicate of the same check across near-identical versions.
    unique={}
    for ck in reg_checks+proto_checks:
        key=(ck["field_path"],ck["source_url"],ck["document"])
        unique.setdefault(key,ck)
    return reg_find+proto_find,list(unique.values())

def install(ns):
    """Automatically join all general, manual, thematic and repeated EIS audits."""
    previous_analyze=ns.get("analyze")
    previous_checks=ns.get("build_checks")
    if not callable(previous_analyze) or not callable(previous_checks):
        raise RuntimeError("V372 requires legal analyze and build_checks")
    if getattr(previous_analyze,"__v372__",False):return
    @wraps(previous_analyze)
    def analyze(p,docs):
        old=list(previous_analyze(p,docs))
        found,_=evaluate(p,docs)
        old.extend(found)
        return old
    @wraps(previous_checks)
    def build_checks(p,detail,card,docs):
        old=list(previous_checks(p,detail,card,docs))
        _,checks=evaluate(p,docs)
        old.extend(checks)
        return old
    analyze.__v372__=True
    build_checks.__v372__=True
    ns["analyze"]=analyze
    ns["build_checks"]=build_checks
    print("V372_AUTO_REGULATION_PROTOCOLS_READY official_originals_only=True "
          "general_and_thematic=True automatic=True no_fake_violations=True",flush=True)
