"""Restore missing NMCD from hash-verified EIS notice/documentation originals.

Only explicit starting/max contract price fields, never winner price, unit
price or arbitrary largest numeric amounts. Conflicting official revisions are
not silently combined into one invented value.
"""
from __future__ import annotations
import re
from v311_verified_customer import _verified_money
from v295_eis_originals import uid_from_url

LABEL=re.compile(
    r"(?:начальн\w*\s*\(максимальн\w*\)\s*цен\w*\s*договора|"
    r"начальн\w*\s+максимальн\w*\s+цен\w*\s*договора|"
    r"начальн\w*\s+цен\w*\s*договора|"
    r"начальн\w*\s*\(максимальн\w*\)\s*цен\w*|"
    r"нмцд)(?:\s*\(нмцд\))?",
    re.I,
)
UNSUITABLE=re.compile(
    r"(?:цен\w*\s+победител\w*|цен\w*\s+единиц\w*|"
    r"стоимост\w*\s+единиц\w*|начальн\w*\s+цен\w*\s+единиц\w*|"
    r"общ\w*\s+стоимост\w*\s+договора|стоимост\w*\s+контракт\w*)",re.I,
)
AMOUNT=re.compile(r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d{4,15}|\d{1,3})"
                  r"(?:[,.]\d{1,2})?\s*(?:₽|руб(?:\.|лей|ля|ль)?)?(?!\d)",re.I)

def _verified(doc):
    if not isinstance(doc,dict) or doc.get("status")!="checked":return False
    meta=doc.get("meta") or {}
    return (isinstance(meta,dict) and
            meta.get("source") in ("official_eis_original","official_eis_archive_member")
            and bool(meta.get("sha256") or meta.get("archive_sha256"))
            and bool(uid_from_url(meta.get("source_url") or "")))

def _match_amount(part):
    # Avoid treating a 223-FZ number or a date as a starting price.
    for candidate in AMOUNT.finditer(part[:130]):
        value=candidate.group(0).strip()
        if re.search(r"(?:\b20\d{2}\b|223)\s*[-./]\s*\d",value):continue
        try:return _verified_money(value)
        except ValueError:continue
    return None

def prices_in_text(text):
    source=str(text or "")[:600000].replace("\xa0"," ")
    candidates=[]
    # Label/value table cells from DOCX and OCR'ed PDFs.  The strict short
    # window never merges an unrelated lot amount several paragraphs away.
    for line in source.splitlines():
        line=" ".join(line.split())
        if not line or len(line)>1300:continue
        for hit in LABEL.finditer(line):
            before=line[max(0,hit.start()-45):hit.start()]
            if UNSUITABLE.search(before):continue
            after=line[hit.end():hit.end()+130].lstrip(" :;|=-\u2013")
            if re.match(r"(?:предусмотрен|указывает|обосновани\w*|не\s+установлен)",after,re.I):
                continue
            result=_match_amount(after)
            if result is not None:
                price,number=result
                # Prices < 10 are usually table numbering, and "0" needs an
                # explicit currency to be distinguished from section indexes.
                if number==0 and not re.match(r"^0(?:[.,]00)?\s*(?:₽|руб)",after,re.I):
                    continue
                candidates.append((price,number,line[max(0,hit.start()-45):hit.end()+130][:220]))
    return candidates

def verified_nmcd(docs):
    records=[]
    for d in docs:
        if not _verified(d):continue
        # Exclude final protocols and executed contracts: their prices are not
        # evidence of the initial/max price of the procurement notice.
        name=str(d.get("name") or "").casefold()
        kind=str(d.get("type") or "").casefold()
        if kind in ("protocol","final_protocol","contract","clarification","clarification_request"):
            continue
        if any(v in name for v in ("договор заключен","итоговый протокол","протокол итог")):
            continue
        values=prices_in_text(d.get("text") or "")
        if not values:continue
        # Some explanatory documentation lists several LOT prices; use a
        # unanimous amount only, otherwise require comparison with EIS XML.
        unique={round(n,2) for _,n,_ in values}
        if len(unique)!=1:continue
        value=values[0]
        records.append({"price":value[0],"price_num":value[1],
                        "evidence":value[2],"source_url":(d.get("meta") or {}).get("source_url"),
                        "document":d.get("name")})
    if not records:return {}
    amounts={round(x["price_num"],2) for x in records}
    if len(amounts)!=1:return {"price_conflict":True,"source_count":len(records)}
    # Prefer the notice over documentation while keeping only a unanimous value.
    records.sort(key=lambda x:(0 if re.search("извещени",x["document"],re.I) else 1,x["document"]))
    first=records[0]
    return {**first,"source_count":len(records)}
