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
# Whole-rouble text and spreadsheet numerical values are both supported. EIS
# originals often store "394633.3333333333" (Excel floating-point) or
# "394 633 (прописью) рубля 33 копейки" (information card).
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
AMOUNT=re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d{4,15}|\d{1,3})"
    r"(?:[,.]\d{1,18})?(?!\d)",re.I,
)
RUBLE_KOPEKS=re.compile(
    r"руб(?:ль|ля|лей|\.)\s*(\d{1,2})\s*коп(?:ейк[аи]|еек|\.?)",
    re.I
)
NOTE_ONLY=re.compile(
    r"(?:обосновани\w*\s+начальн\w*|начальн\w*\s+цен\w*\s+единиц\w*|"
    r"когда\s+начальн\w*|"
    r"при\s+условии\s+что\s+начальн\w*)",re.I,
)
NOT_ACTUAL=re.compile(
    r"(?:не\s+превыша\w*|менее\s+чем|более\s+чем|"
    r"в\s+случае|может\s+составля\w*|"
    r"не\s+подлеж\w*\s+определени\w*)",re.I,
)

def _verified(doc):
    if not isinstance(doc,dict) or doc.get("status")!="checked":return False
    meta=doc.get("meta") or {}
    return (isinstance(meta,dict) and
            meta.get("source") in ("official_eis_original","official_eis_archive_member")
            and bool(meta.get("sha256") or meta.get("archive_sha256"))
            and bool(uid_from_url(meta.get("source_url") or "")))

def _match_amount(part):
    # A legitimate value follows a label/column delimiter immediately.
    text=str(part or "")[:300]
    prefix=re.match(
        r"^\s*(?:(?:[:;|=,\-–—()])\s*|руб(?:\.|лей|ля|ль)\s*|"
        r"Российск\w*\s+рубл\w*\s*)*",
        text,re.I)
    start=prefix.end() if prefix else 0
    segment=text[start:].lstrip()
    found=AMOUNT.match(segment)
    if not found:return None
    digits=found.group(0).replace("\u00a0"," ").replace(" ","").replace(",",".")
    try:number=Decimal(digits)
    except InvalidOperation:return None
    if not number.is_finite() or number<0 or number>Decimal("1000000000000000"):
        return None
    # Written kopeks within the same labeled monetary cell, e.g.
    # "394 633 (Триста...) рубля 33 копейки".
    if "." not in digits:
        tail=segment[found.end():]
        kop=RUBLE_KOPEKS.search(tail[:220])
        if kop and re.search(r"^\s*(?:\([^)]{1,180}\)\s*)?руб",tail,re.I):
            number+=Decimal(int(kop.group(1)))/Decimal(100)
        elif kop and tail.lstrip().startswith("("):
            number+=Decimal(int(kop.group(1)))/Decimal(100)
    number=number.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    if number==0 and not re.match(r"^0(?:[.,]0+)?\s*(?:₽|руб)",segment,re.I):
        return None
    return f"{number:.2f}",float(number)

def prices_in_text(text):
    source=str(text or "")[:600000].replace("\xa0"," ")
    candidates=[]
    for line in source.splitlines():
        line=" ".join(line.split())
        if not line or len(line)>2000:continue
        for hit in LABEL.finditer(line):
            before=line[max(0,hit.start()-105):hit.start()]
            if UNSUITABLE.search(before) or NOTE_ONLY.search(before):continue
            after=line[hit.end():hit.end()+280]
            if NOT_ACTUAL.search(after[:95]):continue
            # Prices in procurement documents are structured as a labeled
            # cell or clear "НМЦД: <amount>" field. A mere prose mention of
            # initial pricing followed by a number of days/law sections isn't.
            field=re.match(r"^\s*(?:[,;:|=\-–—()]|руб\.?|"
                           r"Российск\w*\s+рубл\w*)",after,re.I)
            if not field and not re.match(r"^\s*\d",after):
                continue
            result=_match_amount(after)
            if result is not None:
                price,number=result
                candidates.append((price,number,line[max(0,hit.start()-80):hit.end()+240][:350]))
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
