"""Supplemental explicit EIS clarification timestamps spanning different originals.

A positive violation requires a uniquely paired verified request and publication
in distinct EIS originals, an applicable competitive purchase, and an independently
known submission deadline. Explicit request identifiers support separate episodes;
ambiguous or missing event evidence is not turned into a violation.
"""
from __future__ import annotations
from functools import wraps
import re
from datetime import date,timedelta
from v331_broad_legal import verified
from legal_applicability import competitive_rule_scope
from v295_eis_originals import uid_from_url
from v341_precise_legal import (matches,REQUEST,ANSWER,SIGNED,PROTOCOL_PUBLISHED,
                                 shifted,day,COMPETITIVE,LAW32,LAW4,federal_workday)

EVENT_DOC=re.compile(r"разъяснен|запрос",re.I)
EXCLUDE=re.compile(r"образец|пример|шаблон|проект|инструкци",re.I)

# Only explicit request labels establish identity. Neither a purchase number,
# an answer's own number nor a numeric/date-shaped filename identifies a request.
_CLARIFICATION_LABEL = (r"(?:запрос[а-я]*(?:\s+(?:на\s+)?разъяснени[а-я]*)?)")
_CLARIFICATION_PREFIX = (r"\b"+_CLARIFICATION_LABEL+r"\s*(?:№|N(?:o|º)?\.?)\s*|"
    r"\b(?:номер|идентификатор|ID)\s+запроса(?:\s*[:：|=]\s*|\s+)")
_CLARIFICATION_NUMBER_LABEL = re.compile(_CLARIFICATION_PREFIX,re.I)
_CLARIFICATION_UNSUPPORTED_LABEL = re.compile(
    r"\bзапрос[а-я]*[^\n.;:№]{0,120}?(?:№|\bN(?:o|º)?\.?)\s*",re.I)


def _clarification_identity(text,title=False):
    """Use the same complete-token safety checks as protocol identifiers.

    Translate only explicit request labels; other labels stay untouched. This
    keeps identifier syntax consistent without altering protocol behavior.
    """
    text=str(text or "")
    recognized=list(_CLARIFICATION_NUMBER_LABEL.finditer(text))
    starts={match.start() for match in recognized}
    unsupported=any(match.start() not in starts
        for match in _CLARIFICATION_UNSUPPORTED_LABEL.finditer(text))
    translated=_CLARIFICATION_NUMBER_LABEL.sub("Протокол № ",text)
    identifiers,invalid=_number_identity(translated,title=title)
    # Protocol numbers mentioned incidentally are not request identities.
    if PROTOCOL_NUMBER.search(text):invalid=True
    return identifiers,invalid or unsupported


def finding_clarification_number(item):
    # Evidence begins with a machine-authored explicit identifier. Parse this
    # field separately: quoted document names and URLs are not event labels.
    evidence=str(item.get("evidence") or "")
    field=re.match(r"Номер запроса: (.+?)\. Запрос получен ",evidence)
    if field:
        numbers,invalid=_clarification_identity("Номер запроса: "+field[1])
        return next(iter(numbers)) if not invalid and len(numbers)==1 else ""
    identifiers=set()
    for key in ("title","match_text","document"):
        numbers,invalid=_clarification_identity(item.get(key) or "")
        if invalid:return ""
        identifiers.update(numbers)
    return next(iter(identifiers)) if len(identifiers)==1 else ""


def cross_doc_clarifications(p,docs):
    if competitive_rule_scope(p) is not True:
        return []
    deadline=day(p.get("submission_end") or p.get("deadline"))
    if not deadline:return []
    requests,answers=[],[]
    ambiguous_numbers=set()
    originals={}
    conflicting_originals=set()
    for doc in docs:
        if not isinstance(doc,dict) or not verified(doc):continue
        source=(doc.get("meta") or {}).get("source_url") or doc.get("url") or ""
        uid=uid_from_url(source)
        fingerprint=(str(doc.get("name") or ""),str(doc.get("text") or ""),
                     (doc.get("meta") or {}).get("sha256"))
        if uid in originals:
            if originals[uid][0]!=fingerprint:conflicting_originals.add(uid)
        else:originals[uid]=(fingerprint,doc)
    for uid,(_,doc) in originals.items():
        title=str(doc.get("name") or "")
        if not EVENT_DOC.search(title) or EXCLUDE.search(title):continue
        body=str(doc.get("text") or "")[:750000].replace("\xa0"," ")
        title_numbers,title_invalid=_clarification_identity(title,title=True)
        body_numbers,body_invalid=_clarification_identity(body)
        identifiers=title_numbers|body_numbers
        invalid=(title_invalid or body_invalid or len(identifiers)>1
                 or uid in conflicting_originals)
        if invalid:ambiguous_numbers.update(identifiers)
        number=None if invalid else next(iter(identifiers),"")
        for pattern,events in ((REQUEST,requests),(ANSWER,answers)):
            for m in matches(pattern,body):
                at=day(m["date"])
                if at:events.append((uid,doc,m,at,number))
    groups={}
    # An unidentified competing event makes even an explicitly numbered pair
    # unsafe: it may be a different version of that same request or answer.
    unknown=any(event[4] in (None,"") for event in requests+answers)
    for side,events in enumerate((requests,answers)):
        for event in events:
            number=event[4]
            if number:groups.setdefault(number,[[],[]])[side].append(event)
    if len(requests)==len(answers)==1 and requests[0][4]==answers[0][4]=="":
        groups[""]=[requests,answers]
    findings=[]
    for number,(qs,ans) in groups.items():
        if number and (unknown or number in ambiguous_numbers):continue
        if len(qs)!=1 or len(ans)!=1:continue
        qi,qdoc,request,received,_=qs[0]
        ai,adoc,response,published,_=ans[0]
        if qi==ai or received>published:continue
        latest=shifted(deadline,-3)
        due=shifted(received,3)
        if latest is None or due is None or received>latest or published<=due:
            continue
        if (published-received).days>90:continue
        original=qdoc.get("meta") or {}
        reply=adoc.get("meta") or {}
        request_source=original.get("source_url") or qdoc.get("url") or ""
        reply_source=reply.get("source_url") or adoc.get("url") or ""
        if not request_source or not reply_source:continue
        findings.append({
      "code":"223-3.2-3-CLARIFICATION-CROSS-DOC-LATE",
      "kind":"violation",
      "title":"Срок публикации разъяснения превышает три рабочих дня (разные оригиналы ЕИС)",
      "law":"223-ФЗ, ст. 3.2 ч. 3",
      "law_url":LAW32,
      "evidence":(
          ("Номер запроса: "+number+". " if number else "")+
          "Запрос получен "+received.isoformat()+" по оригиналу ЕИС «"+
          str(qdoc.get("name") or "")+"» ("+request_source+"); "
          "разъяснение опубликовано "+published.isoformat()+
          " по оригиналу «"+str(adoc.get("name") or "")+"» ("+reply_source+"); "
          "крайний день ответа "+due.isoformat()+
          "; окончание подачи заявок "+deadline.isoformat()+
          ". Даты учитываются по дням, без времени."
      )[:2500],
      "document":str(adoc.get("name") or ""),
      "source_url":reply_source,
      "location":"ЕИС: подтверждённые запрос и публикация",
      "match_text":response.group(0)[:500],
      "note":("В обоих оригиналах совпадает явный номер запроса. " if number else
              "Установлена единственная ненумерованная пара. ")+
             "Неоднозначные идентификаторы и версии не объединяются автоматически."
        })
    return findings

PROTOCOL_DOC=re.compile(r"протокол",re.I)
PROTOCOL_KINDS=(
 ("итогов",re.compile(r"итогов",re.I)),
 ("рассмотрения",re.compile(r"рассмотрени",re.I)),
 ("оценки",re.compile(r"оценк",re.I)),
 ("допуска",re.compile(r"допуск",re.I))
)

# Identity is documentary evidence, not the position of a file in the list.
# Never use a purchase number, bid number, or bare numeric filename as the
# protocol identifier. Require an explicit protocol-number label.
_NUMBER_TOKEN=r"[0-9a-zа-я][0-9a-zа-я./_-]{0,79}(?![0-9a-zа-я./_-])"
_PROTOCOL_LABEL=(r"протокол[ауе]?(?:\s+(?:подведения\s+)?итогов|"
                 r"\s+рассмотрения(?:\s+и\s+оценки)?(?:\s+заявок)?|"
                 r"\s+оценки(?:\s+заявок)?|\s+допуска(?:\s+к\s+участию)?)?")
_NUMBER_PREFIX=(r"(?:\b"+_PROTOCOL_LABEL+r"\s*(?:№|N(?:o|º)?\.?)\s*|"
                r"\bномер\s+(?:итогового\s+)?протокола\s*[:：|=]\s*)")
PROTOCOL_NUMBER=re.compile(_NUMBER_PREFIX+r"(?P<number>"+_NUMBER_TOKEN+r")",re.I)
ANY_PROTOCOL_NUMBER_LABEL=re.compile(
    _NUMBER_PREFIX+r"|\bпротокол[а-я]*[^\n.;:№]{0,120}?(?:№|\bN(?:o|º)?\.?)\s*",re.I)
NUMBER_MARKER=re.compile(r"№|\bN(?:o|º)?\.?(?=\s|[0-9])",re.I)


def _number_identity(text,title=False):
    """Parse complete supported identifiers and flag unsafe partial matches.

    Unicode dashes and spaces around slash/dash separators are spelling
    differences. Parenthesized/unsupported continuations are not silently
    discarded. An unsupported explicit number cannot become an unnamed pair.
    """
    text=str(text or "").replace("\xa0"," ")
    text=re.sub(r"[‐‑‒–—−]","-",text)
    text=re.sub(r"(?<=\w)[ \t]*([/-])[ \t]*(?=\w)",r"\1",text)
    text=re.sub(r"\.(?:pdf|docx?|odt|rtf|txt|xml)(?=$|[\s»”\"'])","",text,flags=re.I)
    parsed=list(PROTOCOL_NUMBER.finditer(text))
    starts={match.start() for match in parsed}
    invalid=any(label.start() not in starts
                for label in ANY_PROTOCOL_NUMBER_LABEL.finditer(text))
    if title:
        invalid=invalid or any(not any(match.start()<=marker.start()<match.end()
                                      for match in parsed)
                              for marker in NUMBER_MARKER.finditer(text))
    result=set()
    for match in parsed:
        number=match["number"].rstrip(".").casefold()
        raw_suffix=text[match.end():]
        suffix=raw_suffix.lstrip()
        # Do not accept just "1" from "1(2)", "1 / 2" with a newline,
        # or an unsupported adjacent character. Unknown formats need review.
        adjacent=text[match.end():match.end()+1]
        continuation=(suffix[:1] in ("/","\\","-","_","(","[","№")
                      or bool(re.match(r"[,;]\s*\d",suffix))
                      or bool(adjacent and not adjacent.isspace()
                              and adjacent not in (",",";",":","»","”",'"',"'"))
                      or bool(re.match(r"[,;:][^\s]",raw_suffix)))
        # A space alone does not prove the identifier has ended: "1 А" and
        # "1 Б" must not collapse to "1". Accept only a new line, sentence
        # boundary or a small set of explicit date/event continuations.
        whitespace=raw_suffix[:len(raw_suffix)-len(suffix)]
        word=re.match(r"[а-яa-z0-9]+",suffix,re.I)
        if (word and adjacent.isspace() and "\n" not in whitespace
                and not match["number"].endswith(".")
                and not re.match(r"(?:от|дата|подписан[аоы]?|размещ[её]н[аоы]?|"
                                 r"опубликован[аоы]?)\b",suffix,re.I)):
            continuation=True
        if (not any(char.isdigit() for char in number)
                or not number[-1:].isalnum() or continuation):
            invalid=True
        else:
            result.add(number)
    return result,invalid


def protocol_numbers(text):
    """Return explicit identifiers; multiple identifiers remain ambiguous."""
    identifiers,invalid=_number_identity(text)
    return set() if invalid else identifiers


def finding_protocol_number(item):
    """Read persisted evidence too: custom in-memory fields are not stored."""
    evidence = str(item.get("evidence") or "")
    field = re.match(r"Номер протокола: (.+?)\. (?:Вид протокола:|Протокол подписан) ", evidence)
    if field:
        identifiers, invalid = _number_identity("Номер протокола: " + field[1])
        return next(iter(identifiers)) if not invalid and len(identifiers) == 1 else ""
    identifiers=protocol_numbers("\n".join(str(item.get(key) or "")
        for key in ("title","evidence","match_text","document")))
    return next(iter(identifiers)) if len(identifiers)==1 else ""

def _protocol_kind(name,matched):
    """Don't pair the final protocol with a different interim protocol."""
    label=str(name or "")+" "+str(matched or "")
    found=[tag for tag,pattern in PROTOCOL_KINDS if pattern.search(label)]
    return found[0] if len(found)==1 else ""

def cross_doc_protocols(p,docs):
    """Find a late EIS publication when signing and posting are in distinct,
    independently authenticated originals describing the same named protocol.
    Explicit numbers allow independent episodes in the same purchase. A
    missing number retains the legacy single-pair fallback only; conflicting
    numbers, duplicate events and multiple versions are never guessed."""
    if competitive_rule_scope(p) is not True:
        return []
    signed,published=[],[]
    ambiguous_numbers=set()
    for index,doc in enumerate(docs):
        if not isinstance(doc,dict) or not verified(doc):continue
        title=str(doc.get("name") or "")
        if not PROTOCOL_DOC.search(title) or EXCLUDE.search(title):continue
        body=str(doc.get("text") or "")[:750000].replace("\xa0"," ")
        title_numbers,title_invalid=_number_identity(title,title=True)
        body_numbers,body_invalid=_number_identity(body)
        identifiers=title_numbers|body_numbers
        # A compilation referring to several protocols is not a safe source
        # for document-level pairing. The within-original analyzer can still
        # inspect its local event chains independently.
        if len(identifiers)>1:
            ambiguous_numbers.update(identifiers)
            continue
        number=next(iter(identifiers),"")
        if title_invalid or body_invalid:
            # An explicit but unparseable identifier must not fall back to
            # type-only matching as though no number had been supplied.
            ambiguous_numbers.update(identifiers)
            number=None
        # Keep the strict existing event-date grammar. In particular, a date
        # embedded in a protocol number is never a signing/publication date.
        for m in matches(SIGNED,body):
            at=day(m["date"])
            label=_protocol_kind(title,m.group(0))
            if at and label:signed.append((index,doc,m,at,label,number))
        for m in matches(PROTOCOL_PUBLISHED,body):
            at=day(m["date"])
            label=_protocol_kind(title,m.group(0))
            if at and label:published.append((index,doc,m,at,label,number))
    groups={}
    unnamed_kinds={event[4] for event in signed+published if not event[5]}
    for side,events in enumerate((signed,published)):
        for event in events:
            kind,number=event[4:]
            if number:
                groups.setdefault((kind,number),[[],[]])[side].append(event)
    # Type alone is insufficient to join a numbered and an unnumbered
    # protocol, or to select one of several unnamed protocols.
    if len(signed)==len(published)==1 and signed[0][5]==published[0][5]=="":
        groups[(signed[0][4],"")]=[signed,published]
    findings=[]
    for (kind,number),(signatures,publications) in groups.items():
        if number and (number in ambiguous_numbers or kind in unnamed_kinds):continue
        if len(signatures)!=1 or len(publications)!=1:continue
        si,sdoc,signature,start,skind,_=signatures[0]
        pi,pdoc,publication,end,pkind,_=publications[0]
        if si==pi or skind!=pkind or not 0<=(end-start).days<365:continue
        due=start+timedelta(days=3)
        working=federal_workday(due)
        if working is None:continue
        if working is False:due=shifted(due,1)
        if due is None or end<=due:continue
        before=(sdoc.get("meta") or {}).get("source_url") or sdoc.get("url") or ""
        after=(pdoc.get("meta") or {}).get("source_url") or pdoc.get("url") or ""
        # Compare canonical receipt identities, not URL spelling: another
        # query parameter or EIS hostname does not create a second original.
        if not before or not after or uid_from_url(before)==uid_from_url(after):continue
        findings.append({
        "code":"223-4-12-PROTOCOL-CROSS-DOC-LATE",
        "kind":"violation",
        "title":"Протокол "+("№ "+number+" " if number else "")+
                "размещён в ЕИС с нарушением срока публикации",
        "law":"223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193",
        "law_url":LAW4,
        "evidence":(
            ("Номер протокола: "+number+". " if number else "")+
            "Вид протокола: "+kind+". "
            "Подписание "+start.isoformat()+" по оригиналу ЕИС «"+
            str(sdoc.get("name") or "")+"» ("+before+"); "
            "размещение "+end.isoformat()+" по оригиналу ЕИС «"+
            str(pdoc.get("name") or "")+"» ("+after+"); "
            "крайний день "+due.isoformat()+
            ". Даты проверены по календарным дням, без времени."
        )[:2500],
        "document":str(pdoc.get("name") or ""),
        "source_url":after,
        "location":"Раздельные подтверждённые оригиналы протокола в ЕИС",
        "match_text":publication.group(0)[:500],
        "note":("В обоих оригиналах совпадают явный номер и вид протокола. "
                if number else "Установлена единственная пара одного вида протокола. ")+
               "Разные версии и неоднозначные даты не объединяются автоматически."
        })
    return findings

def install(ns):
    previous=ns.get("analyze")
    if not callable(previous):raise RuntimeError("Missing legal analyzer")
    if getattr(previous,"__v348__",False):return
    @wraps(previous)
    def with_cross_originals(p,docs):
        base=list(previous(p,docs))
        extra=cross_doc_clarifications(p,docs)+cross_doc_protocols(p,docs)
        # Avoid inflating totals when the same documented event was already
        # established inside one canonical original.
        for issue in extra:
            if issue["code"].startswith("223-3.2-3-CLARIFICATION"):
                requested=re.search(r"Запрос получен (\d{4}-\d\d-\d\d)",issue["evidence"])
                posted=re.search(r"разъяснение опубликовано (\d{4}-\d\d-\d\d)",issue["evidence"])
                same_rule="CLARIFICATION"
            else:
                requested=re.search(r"Подписание (\d{4}-\d\d-\d\d)",issue["evidence"])
                posted=re.search(r"размещение (\d{4}-\d\d-\d\d)",issue["evidence"])
                same_rule="PROTOCOL"
            duplicate=any(
                isinstance(x,dict) and x.get("kind")=="violation"
                and same_rule in str(x.get("code") or "")
                and requested and posted
                and requested.group(1) in str(x.get("evidence") or "")
                and posted.group(1) in str(x.get("evidence") or "")
                and not(same_rule=="PROTOCOL" and finding_protocol_number(x)
                        and finding_protocol_number(issue)
                        and finding_protocol_number(x)!=finding_protocol_number(issue))
                and not(same_rule=="CLARIFICATION" and finding_clarification_number(x)
                        and finding_clarification_number(issue)
                        and finding_clarification_number(x)!=finding_clarification_number(issue))
                for x in base)
            if not duplicate:base.append(issue)
        return base
    with_cross_originals.__v348__=True
    ns["analyze"]=with_cross_originals
    print("V348_CROSS_DOCUMENT_CLARIFICATION_READY verified_pair_only=True",flush=True)
