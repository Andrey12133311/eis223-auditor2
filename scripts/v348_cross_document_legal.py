"""Supplemental explicit EIS clarification timestamps spanning different originals.

A positive violation requires a uniquely paired verified request and publication
in distinct EIS originals, an applicable competitive purchase, and an independently
known submission deadline. Explicit request identifiers support separate episodes;
ambiguous or missing event evidence is not turned into a violation.
"""
from __future__ import annotations
from functools import wraps
import re
import json
from datetime import date,timedelta
from v331_broad_legal import verified
from legal_applicability import competitive_rule_scope
from v295_eis_originals import uid_from_url
from v341_eis_facts import DATE_TOKEN
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
    local = _local_identity_number(item, "CLARIFICATION")
    if local is not None:return local
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
    unresolved_archives=set()
    for doc in docs:
        if not isinstance(doc,dict) or not verified(doc):continue
        identity=_document_original_identity(doc)
        if identity is None:
            # A missing member path cannot masquerade as another independent
            # original in the same verified archive.
            uid=uid_from_url((doc.get("meta") or {}).get("source_url") or "")
            if uid:unresolved_archives.add(uid)
            continue
        fingerprint=(str(doc.get("name") or ""),str(doc.get("text") or ""),
                     (doc.get("meta") or {}).get("sha256"))
        if identity in originals:
            if originals[identity][0]!=fingerprint:conflicting_originals.add(identity)
        else:originals[identity]=(fingerprint,doc)
    for identity,(_,doc) in originals.items():
        title=str(doc.get("name") or "")
        if not EVENT_DOC.search(title) or EXCLUDE.search(title):continue
        body=str(doc.get("text") or "")[:750000].replace("\xa0"," ")
        title_numbers,title_invalid=_clarification_identity(title,title=True)
        body_numbers,body_invalid=_clarification_identity(body)
        identifiers=title_numbers|body_numbers
        invalid=(title_invalid or body_invalid or len(identifiers)>1
                 or identity in conflicting_originals or identity[0] in unresolved_archives)
        if invalid:ambiguous_numbers.update(identifiers)
        number=None if invalid else next(iter(identifiers),"")
        for pattern,events in ((REQUEST,requests),(ANSWER,answers)):
            for m in matches(pattern,body):
                at=day(m["date"])
                if at:events.append((identity,doc,m,at,number))
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
      "evidence":_cross_original_evidence((
          ("Номер запроса: "+number+". " if number else "")+
          "Запрос получен "+received.isoformat()+" по оригиналу ЕИС «"+
          str(qdoc.get("name") or "")+"» ("+request_source+"); "
          "разъяснение опубликовано "+published.isoformat()+
          " по оригиналу «"+str(adoc.get("name") or "")+"» ("+reply_source+"); "
          "крайний день ответа "+due.isoformat()+
          "; окончание подачи заявок "+deadline.isoformat()+
          ". Даты учитываются по дням, без времени."
      )[:2500], "CLARIFICATION", number, adoc),
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
    local = _local_identity_number(item, "PROTOCOL")
    if local is not None:return local
    identifiers=protocol_numbers("\n".join(str(item.get(key) or "")
        for key in ("title","evidence","match_text","document")))
    return next(iter(identifiers)) if len(identifiers)==1 else ""


def document_event_number(doc, family):
    """Require a complete, unambiguous identity across the entire original."""
    number, invalid = _document_event_identity(doc, family)
    return "" if invalid else number


def _document_event_identity(doc, family):
    parser = _number_identity if family == "PROTOCOL" else _clarification_identity
    title, title_invalid = parser(doc.get("name") or "", title=True)
    body, body_invalid = parser(doc.get("text") or "")
    numbers = title | body
    invalid = title_invalid or body_invalid or len(numbers) > 1
    return (next(iter(numbers)) if len(numbers) == 1 and not invalid else ""), invalid


def ambiguous_original_documents(docs):
    """Copies or contradictory representations of one original are not new acts.

    Archive members have their own receipt-relative path. Exact copies remain
    harmless, while a changed name/body/hash cannot establish independent IDs
    for the same receipt or identical binary.
    """
    groups = {}
    for doc in docs:
        if not isinstance(doc, dict) or not verified(doc):continue
        meta = doc.get("meta") or {}
        uid = uid_from_url(meta.get("source_url") or "")
        identity = _document_original_identity(doc)
        member = identity[1] if identity else "unresolved:" + str(doc.get("name") or "")
        fingerprint = (str(doc.get("text") or ""), meta.get("sha256"))
        identities = {family:_document_event_identity(doc, family) for family in ("PROTOCOL", "CLARIFICATION")}
        for key in (("receipt", uid, member), ("binary", meta.get("sha256"))):
            groups.setdefault(key, []).append((id(doc), fingerprint, identities))
    ambiguous = set()
    for representations in groups.values():
        conflict = len({fingerprint for _, fingerprint, _ in representations}) > 1
        for family in ("PROTOCOL", "CLARIFICATION"):
            states = [identities[family] for _, _, identities in representations]
            numbers = {number for number, _ in states if number}
            conflict = conflict or len(numbers) > 1 or (bool(numbers) and any(invalid for _, invalid in states))
        if conflict:
            ambiguous.update(identity for identity, _, _ in representations)
    return ambiguous


def _identity_prefix(family):
    return "Идентификатор " + ("протокола" if family == "PROTOCOL" else "запроса") + " по оригиналу: "


def event_identity_evidence(evidence, family, number, doc=None):
    # Persist even the unresolved state: a later UI/persistence pass must not
    # recover an invalid filename identifier after the full original is gone.
    provenance = ""
    if doc is not None:
        identity = _document_original_identity(doc)
        if identity:
            digest = str((doc.get("meta") or {}).get("sha256") or "")
            if re.fullmatch(r"[0-9a-f]{64}", digest):identity = (*identity, digest)
            provenance = "Идентичность оригинала ЕИС: " + json.dumps(identity, ensure_ascii=False) + ".\n"
    return _identity_prefix(family) + (number or "не установлен") + ".\n" + provenance + evidence


def _cross_original_evidence(evidence, family, number, doc):
    # Split dates in one ZIP need the member's identity in stored evidence;
    # its parent receipt URL alone would collapse separate numbered acts.
    # Preserve the established format for ordinary cross-original findings.
    if (doc.get("meta") or {}).get("source") == "official_eis_archive_member":
        return event_identity_evidence(evidence, family, number, doc)
    return evidence


def finding_original_identity(item):
    """Stable identity survives storage; distinct archive members stay separate."""
    lines = str(item.get("evidence") or "").split("\n", 2)
    prefix = "Идентичность оригинала ЕИС: "
    if len(lines) < 2 or not lines[1].startswith(prefix) or not lines[1].endswith("."):
        # Historical archive findings used the persisted display convention
        # "archive.ext / member". Recover that member, never the parent UID
        # alone, before comparing a legacy finding with a new audit.
        if any(lines[0].startswith(_identity_prefix(family)) for family in ("PROTOCOL", "CLARIFICATION")):
            return None
        uid = uid_from_url(item.get("source_url") or "")
        name = str(item.get("document") or "")
        if not uid or not name:return None
        member = _legacy_archive_member(name)
        if " / " in name and member is None:return None
        return uid, member or ""
    if not any(lines[0].startswith(_identity_prefix(family)) for family in ("PROTOCOL", "CLARIFICATION")):
        return None
    try:identity = json.loads(lines[1][len(prefix):-1])
    except (ValueError, TypeError):return None
    if (not isinstance(identity, list) or len(identity) not in (2, 3)
            or not isinstance(identity[0], str) or not re.fullmatch(r"[0-9A-F]{32}", identity[0])
            or not isinstance(identity[1], str)
            or (len(identity) == 3 and (not isinstance(identity[2], str)
                                       or not re.fullmatch(r"[0-9a-f]{64}", identity[2])))):
        return None
    return tuple(identity[:2])


def finding_binary_identity(item):
    if finding_original_identity(item) is None:return ""
    lines = str(item.get("evidence") or "").split("\n", 2)
    prefix = "Идентичность оригинала ЕИС: "
    if len(lines) < 2 or not lines[1].startswith(prefix):return ""
    try:identity = json.loads(lines[1][len(prefix):-1])
    except (ValueError, TypeError):return ""
    return identity[2] if isinstance(identity, list) and len(identity) == 3 else ""


def _legacy_archive_member(name):
    match = re.match(r"^.+\.(?:zip|rar|7z|tar|gz|tgz|bz2|xz|tbz2|txz|cab) / (.+)$", str(name or ""), re.I)
    return match[1] if match else None


def _document_original_identity(doc):
    meta = doc.get("meta") or {}
    uid = uid_from_url(meta.get("source_url") or "")
    if not uid:return None
    if meta.get("source") == "official_eis_archive_member":
        member = str(meta.get("archive_member") or "") or _legacy_archive_member(doc.get("name"))
        return (uid, member) if member else None
    return uid, ""


def same_original_identity(left, right):
    identity = finding_original_identity(left)
    if identity and identity == finding_original_identity(right):return True
    digest = finding_binary_identity(left)
    return bool(digest and digest == finding_binary_identity(right))


def event_evidence_text(value):
    """Identity labels are not occurrence dates or part of the quoted proof."""
    text = str(value or "")
    first, separator, rest = text.partition("\n")
    if separator and first.endswith(".") and any(first.startswith(_identity_prefix(family))
                                                  for family in ("PROTOCOL", "CLARIFICATION")):
        text = rest
    first, separator, rest = text.partition("\n")
    if separator and first.startswith("Идентичность оригинала ЕИС: ") and first.endswith("."):
        text = rest
    return text


_CLARIFICATION_CONTEXT={
    "223-3.2-3-CLARIFICATION-RESPONSE-LATE":re.compile(
        r"\A\s*(?P<event>Запрос\s+получен\s+"+DATE_TOKEN+
        r";\s+крайний\s+день\s+ответа\s+"+DATE_TOKEN+
        r";\s+опубликован\s+"+DATE_TOKEN+
        r");\s+окончание\s+подачи\s+"+DATE_TOKEN+r"(?=\.)",re.I),
    "223-3.2-3-CLARIFICATION-CROSS-DOC-LATE":re.compile(
        r"(?P<event>крайний\s+день\s+ответа\s+"+DATE_TOKEN+
        r");\s+окончание\s+подачи\s+заявок\s+"+DATE_TOKEN+
        r"(?=\.\s+Даты\s+учитываются\s+по\s+дням,\s+без\s+времени\.?\s*\Z)",re.I),
}


def event_occurrence_text(item):
    """Compare the documented event independently of its applicability context.

    Only our exact generated clarification explanation has a removable
    submission deadline. The original quote, request/publication dates, event
    numbers and source links remain intact; saved evidence is never changed.
    """
    text=event_evidence_text(item.get("evidence"))
    pattern=_CLARIFICATION_CONTEXT.get(str(item.get("code") or item.get("field_path") or ""))
    return pattern.sub(lambda match:match["event"],text,count=1) if pattern else text


def _local_identity_number(item, family):
    evidence = str(item.get("evidence") or "")
    prefix = _identity_prefix(family)
    if not evidence.startswith(prefix):return None
    line = evidence.split("\n", 1)[0]
    if not line.endswith("."):return ""
    value = line[len(prefix):-1]
    parser = _number_identity if family == "PROTOCOL" else _clarification_identity
    label = "Номер протокола: " if family == "PROTOCOL" else "Номер запроса: "
    numbers, invalid = parser(label + value)
    return next(iter(numbers)) if not invalid and len(numbers) == 1 else ""

def _protocol_kinds(name,matched):
    """Read explicit kinds, retaining conflicts instead of choosing one."""
    label=str(name or "")+" "+str(matched or "")
    # Consideration and evaluation is one combined event type. It must not
    # be confused with either independently named interim protocol.
    combined=re.compile(r"рассмотрени\w*\s+и\s+оценк\w*",re.I)
    found={"рассмотрения и оценки"} if combined.search(label) else set()
    label=combined.sub(" ",label)
    found.update(tag for tag,pattern in PROTOCOL_KINDS if pattern.search(label))
    return found


def _protocol_kind(name,matched):
    """Don't pair the final protocol with a different interim protocol."""
    found=_protocol_kinds(name,matched)
    return next(iter(found)) if len(found)==1 else ""


_PROTOCOL_TEMPLATE_TITLE=re.compile(
    r"\b(?:образец|шаблон)\b|\bпример\s+(?:заполнени\w*|протокол\w*)|"
    r"\bпроект\s+(?:итогов\w*\s+)?протокол\w*|"
    r"\bинструкци[яи]\s+(?:по\s+)?(?:заполнени\w*|составлени\w*)",re.I)
_PROTOCOL_TEMPLATE_CONTEXT=re.compile(
    r"(?:^|[\n\f])[ \t]*(?:\d+[.)][ \t]*)?(?:"
    r"(?:образец|шаблон)\s*(?:[:：.]|(?:заполнени\w*|форм\w*|протокол\w*)\b)|"
    r"пример\s*(?:[:：.]|(?:заполнени\w*|форм\w*|протокол\w*)\b)|"
    r"проект\s+(?:итогов\w*\s+)?протокол\w*\b|"
    r"например\s*[:：])",re.I)
def protocol_template_title(name):
    """A draft protocol is a template; procurement subject 'проектирование' is not."""
    return bool(_PROTOCOL_TEMPLATE_TITLE.search(str(name or "")))


def protocol_date_context(doc,event,events):
    """Qualify an event's own bounded section and explicit protocol identity.

    A heading belongs to following dates up to the next explicit protocol
    heading. Earlier protocol events bound its introductory context, so an
    example elsewhere in a compilation does not suppress a later actual act.
    """
    text=str(doc.get("text") or "")[:750000].replace("\xa0"," ")
    headings=list(ANY_PROTOCOL_NUMBER_LABEL.finditer(text,0,event.start()))
    heading=headings[-1] if headings else None
    anchor=heading.start() if heading else event.start()
    previous=max((item.end() for item in events if item.end()<=anchor),default=0)
    context_start=max(0,anchor-500,previous)
    context=text[context_start:event.start()]
    if _PROTOCOL_TEMPLATE_CONTEXT.search(context):return {"valid":False,"number":""}
    local=text[heading.start():event.start()] if heading else ""
    numbers,invalid=_number_identity(local)
    # Unparseable identifiers retain the established unresolved-event path;
    # they cannot supply a number or defeat copy/episode deduplication. Only
    # different independently readable local headings prevent a date pair.
    number=next(iter(numbers)) if not invalid and len(numbers)==1 else ""
    return {"valid":True,"number":number}


def cross_doc_protocols(p,docs):
    """Find a late EIS publication when signing and posting are in distinct,
    independently authenticated originals describing the same named protocol.
    Explicit numbers allow independent episodes in the same purchase. A
    missing number retains the legacy single-pair fallback only; conflicting
    numbers, duplicate events and multiple versions are never guessed."""
    if competitive_rule_scope(p) is not True:
        return []
    from official_publication_evidence import protocol_publication_verifier
    official_publication = protocol_publication_verifier(p)
    signed,published=[],[]
    ambiguous_numbers=set()
    for index,doc in enumerate(docs):
        if not isinstance(doc,dict) or not verified(doc):continue
        title=str(doc.get("name") or "")
        if not PROTOCOL_DOC.search(title) or protocol_template_title(title):continue
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
        signatures=matches(SIGNED,body)
        publications=matches(PROTOCOL_PUBLISHED,body)
        date_events=signatures+publications
        for m in signatures:
            if not protocol_date_context(doc,m,date_events)["valid"]:continue
            at=day(m["date"])
            label=_protocol_kind(title,m.group(0))
            if at and label:signed.append((index,doc,m,at,label,number))
        for m in publications:
            if not protocol_date_context(doc,m,date_events)["valid"]:continue
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
        # A split-original detector must not reinstate a date rejected by the
        # within-original detector. Verify the exact two receipts, never a
        # notice timestamp or an unrelated protocol attachment.
        official = [official_publication(doc) for doc in (sdoc,pdoc)]
        if any(evidence.get("state") == "conflict" or
               (evidence.get("state") == "confirmed" and
                day(evidence.get("publication_date")) != end)
               for evidence in official):
            continue
        due=start+timedelta(days=3)
        working=federal_workday(due)
        if working is None:continue
        if working is False:due=shifted(due,1)
        if due is None or end<=due:continue
        before=(sdoc.get("meta") or {}).get("source_url") or sdoc.get("url") or ""
        after=(pdoc.get("meta") or {}).get("source_url") or pdoc.get("url") or ""
        # A receipt authenticates the ZIP, while the verified member path
        # identifies its individual original. URL aliases and copies of the
        # same member are still one source and cannot form a split pair.
        before_identity=_document_original_identity(sdoc)
        after_identity=_document_original_identity(pdoc)
        if (not before or not after or not before_identity or not after_identity
                or before_identity==after_identity):continue
        findings.append({
        "code":"223-4-12-PROTOCOL-CROSS-DOC-LATE",
        "kind":"violation",
        "title":"Протокол "+("№ "+number+" " if number else "")+
                "размещён в ЕИС с нарушением срока публикации",
        "law":"223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193",
        "law_url":LAW4,
        "evidence":_cross_original_evidence((
            ("Номер протокола: "+number+". " if number else "")+
            "Вид протокола: "+kind+". "
            "Подписание "+start.isoformat()+" по оригиналу ЕИС «"+
            str(sdoc.get("name") or "")+"» ("+before+"); "
            "размещение "+end.isoformat()+" по оригиналу ЕИС «"+
            str(pdoc.get("name") or "")+"» ("+after+"); "
            "крайний день "+due.isoformat()+
            ". Даты проверены по календарным дням, без времени."
        )[:2500], "PROTOCOL", number, pdoc),
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

