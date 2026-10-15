"""Supplemental explicit EIS clarification timestamps spanning different originals.

A positive violation requires exactly one verified request and one verified
publication in distinct EIS originals, an applicable competitive purchase,
and an independently known submission deadline. Multiple/missing events are
NOT turned into violations.
"""
from __future__ import annotations
from functools import wraps
import re
from datetime import date,timedelta
from v331_broad_legal import verified
from v341_precise_legal import (matches,REQUEST,ANSWER,SIGNED,PROTOCOL_PUBLISHED,
                                 shifted,day,COMPETITIVE,LAW32,LAW4,federal_workday)

EVENT_DOC=re.compile(r"разъяснен|запрос",re.I)
EXCLUDE=re.compile(r"образец|пример|шаблон|проект|инструкци",re.I)

def cross_doc_clarifications(p,docs):
    if not COMPETITIVE.search(str(p.get("method") or "")):
        return []
    deadline=day(p.get("submission_end") or p.get("deadline"))
    if not deadline:return []
    requests,answers=[],[]
    for index,doc in enumerate(docs):
        if not isinstance(doc,dict) or not verified(doc):continue
        title=str(doc.get("name") or "")
        if not EVENT_DOC.search(title) or EXCLUDE.search(title):continue
        body=str(doc.get("text") or "")[:750000].replace("\xa0"," ")
        for m in matches(REQUEST,body):
            at=day(m["date"])
            if at:
                requests.append((index,doc,m,at))
        for m in matches(ANSWER,body):
            at=day(m["date"])
            if at:
                answers.append((index,doc,m,at))
    # One event of each kind in distinct originals is the only automatically
    # defensible cross-document pairing. Do not guess which reply belongs to
    # which request when multiple protocol versions exist.
    if len(requests)!=1 or len(answers)!=1:
        return []
    qi,qdoc,request,received=requests[0]
    ai,adoc,response,published=answers[0]
    if qi==ai or received>published:return []
    latest=shifted(deadline,-3)
    due=shifted(received,3)
    if latest is None or due is None or received>latest or published<=due:
        return []
    if (published-received).days>90:return []
    original=qdoc.get("meta") or {}
    reply=adoc.get("meta") or {}
    request_source=original.get("source_url") or qdoc.get("url") or ""
    reply_source=reply.get("source_url") or adoc.get("url") or ""
    if not request_source or not reply_source:return []
    # Both documents must be independently verified; one unverified request
    # alongside a verified answer cannot establish an offence.
    return [{
      "code":"223-3.2-3-CLARIFICATION-CROSS-DOC-LATE",
      "kind":"violation",
      "title":"Срок публикации разъяснения превышает три рабочих дня (разные оригиналы ЕИС)",
      "law":"223-ФЗ, ст. 3.2 ч. 3",
      "law_url":LAW32,
      "evidence":(
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
      "note":"Соответствие подтверждено при единственных датах запроса и публикации в доступных оригиналах."
    }]

PROTOCOL_DOC=re.compile(r"протокол",re.I)
PROTOCOL_KINDS=(
 ("итогов",re.compile(r"итогов",re.I)),
 ("рассмотрения",re.compile(r"рассмотрени",re.I)),
 ("оценки",re.compile(r"оценк",re.I)),
 ("допуска",re.compile(r"допуск",re.I))
)

def _protocol_kind(name,matched):
    """Don't pair the final protocol with a different interim protocol."""
    label=str(name or "")+" "+str(matched or "")
    found=[tag for tag,pattern in PROTOCOL_KINDS if pattern.search(label)]
    return found[0] if len(found)==1 else ""

def cross_doc_protocols(p,docs):
    """Find a late EIS publication when signing and posting are in distinct,
    independently authenticated originals describing the same named protocol.
    Multiple versions/dates are intentionally left for manual review."""
    if not COMPETITIVE.search(str(p.get("method") or "")):
        return []
    signed,published=[],[]
    for index,doc in enumerate(docs):
        if not isinstance(doc,dict) or not verified(doc):continue
        title=str(doc.get("name") or "")
        if not PROTOCOL_DOC.search(title) or EXCLUDE.search(title):continue
        body=str(doc.get("text") or "")[:750000].replace("\xa0"," ")
        for m in matches(SIGNED,body):
            at=day(m["date"])
            label=_protocol_kind(title,m.group(0))
            if at and label:signed.append((index,doc,m,at,label))
        for m in matches(PROTOCOL_PUBLISHED,body):
            at=day(m["date"])
            label=_protocol_kind(title,m.group(0))
            if at and label:published.append((index,doc,m,at,label))
    if len(signed)!=1 or len(published)!=1:
        return []
    si,sdoc,signature,start,skind=signed[0]
    pi,pdoc,publication,end,pkind=published[0]
    if si==pi or skind!=pkind or not 0<=(end-start).days<365:
        return []
    due=start+timedelta(days=3)
    working=federal_workday(due)
    if working is None:return []
    if working is False:
        due=shifted(due,1)
    if due is None or end<=due:return []
    before=(sdoc.get("meta") or {}).get("source_url") or sdoc.get("url") or ""
    after=(pdoc.get("meta") or {}).get("source_url") or pdoc.get("url") or ""
    # Independent originals must have independently identifiable EIS
    # download receipts, not two aliases of the same uploaded document.
    if not before or not after or before==after:return []
    return [{
        "code":"223-4-12-PROTOCOL-CROSS-DOC-LATE",
        "kind":"violation",
        "title":"Протокол размещён в ЕИС с нарушением срока публикации",
        "law":"223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193",
        "law_url":LAW4,
        "evidence":(
            "Вид протокола: "+skind+". "
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
        "note":"Установлена однозначная пара одного вида протокола. "
               "Разные версии/разные даты не объединяются автоматически."
    }]

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
                for x in base)
            if not duplicate:base.append(issue)
        return base
    with_cross_originals.__v348__=True
    ns["analyze"]=with_cross_originals
    print("V348_CROSS_DOCUMENT_CLARIFICATION_READY verified_pair_only=True",flush=True)
