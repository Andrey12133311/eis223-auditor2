"""Supplemental explicit EIS clarification timestamps spanning different originals.

A positive violation requires exactly one verified request and one verified
publication in distinct EIS originals, an applicable competitive purchase,
and an independently known submission deadline. Multiple/missing events are
NOT turned into violations.
"""
from __future__ import annotations
from functools import wraps
import re
from datetime import date
from v331_broad_legal import verified
from v341_precise_legal import matches,REQUEST,ANSWER,shifted,day,COMPETITIVE,LAW32

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

def install(ns):
    previous=ns.get("analyze")
    if not callable(previous):raise RuntimeError("Missing legal analyzer")
    if getattr(previous,"__v348__",False):return
    @wraps(previous)
    def with_cross_originals(p,docs):
        base=list(previous(p,docs))
        extra=cross_doc_clarifications(p,docs)
        # Avoid inflating totals when v341 has already proved the same request
        # and publication event inside a single canonical EIS document.
        for issue in extra:
            evidence=issue["evidence"]
            request_date=re.search(r"Запрос получен (\d{4}-\d\d-\d\d)",evidence)
            pub_date=re.search(r"разъяснение опубликовано (\d{4}-\d\d-\d\d)",evidence)
            duplicate=any(
                x.get("kind")=="violation"
                and "CLARIFICATION" in str(x.get("code") or "")
                and request_date and pub_date
                and request_date.group(1) in str(x.get("evidence") or "")
                and pub_date.group(1) in str(x.get("evidence") or "")
                for x in base if isinstance(x,dict))
            if not duplicate:base.append(issue)
        return base
    with_cross_originals.__v348__=True
    ns["analyze"]=with_cross_originals
    print("V348_CROSS_DOCUMENT_CLARIFICATION_READY verified_pair_only=True",flush=True)
