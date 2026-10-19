"""V339: compare facts extracted from available purchase documents with EIS fields.

A mismatch is a sourced REVIEW, not an infringement. Provenance is shown for
both sides; price *types* are never merged, dates are compared as calendar days,
and independent procurement documents are not removed or overwritten.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from functools import wraps

from v331_broad_legal import verified
from v335_price_recovery import money
from v341_eis_facts import DATE_TOKEN, parse_day

LAW_URL="https://www.consultant.ru/document/cons_doc_LAW_116964/"
MAX_DOCUMENTS=400
MAX_LENGTH=650000
MAX_SIGNALS=35
# Keep comparison evidence aligned with the verified-document fact parser.
# A Russian-month date or DOCX table separator must not disappear here after
# the same explicitly labelled deadline was successfully extracted upstream.
DAY=r"(?P<date>"+DATE_TOKEN+r")"
DATE_FIELDS=(
 ("submission_end","Окончание подачи заявок","223-ФЗ, ст. 4 ч. 9 п. 7; ч. 10 п. 8",
  re.compile(r"(?:дат[аы]\s+и\s+врем[яе]\s+окончани\w*\s+(?:срока\s+)?подач\w*\s+заяв\w*|"
             r"окончани\w*\s+(?:срока\s+)?подач\w*\s+заяв\w*)"
             r"\s*[:：|—–=\-\s]{0,38}"+DAY,re.I)),
 ("submission_start","Начало подачи заявок","223-ФЗ, ст. 4 ч. 9 п. 7",
  re.compile(r"(?:дат[аы]\s+и\s+врем[яе]\s+начал\w*\s+подач\w*\s+заяв\w*|"
             r"начал\w*\s+(?:срока\s+)?подач\w*\s+заяв\w*)"
             r"\s*[:：|—–=\-\s]{0,38}"+DAY,re.I)),
)
PRICE_PATTERN=r"(?P<price>\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?|\d{4,15}(?:[,.]\d{1,2})?)"
PRICE_FIELDS=(
 ("nmcd","НМЦД","223-ФЗ, ст. 4 ч. 9 п. 5, ч. 10 п. 5",
  re.compile(r"(?:начальн\w*\s*\(?\s*максимальн\w*\s*\)?\s*цен\w*\s*(?:договора)?|"
             r"начальн\w*\s+цен\w*\s+договора|нмцд)"
             r"\s*[:—–=\-]{0,3}\s*"+PRICE_PATTERN+r"\s*(?:руб(?:лей|ля|ль|\.)?|₽)(?!\w)",re.I)),
 ("subject_cost","Стоимость предмета закупки","223-ФЗ, ст. 4 ч. 9 п. 5",
  re.compile(r"стоимост\w*\s+предмет\w*\s+закупк\w*"
             r"\s*[:—–=\-]{0,3}\s*"+PRICE_PATTERN+r"\s*(?:руб(?:лей|ля|ль|\.)?|₽)(?!\w)",re.I)),
 ("maximum_contract_value","Максимальное значение цены договора",
  "223-ФЗ, ст. 4 ч. 9 п. 5",
  re.compile(r"максимальн\w*\s+значени\w*\s+цен\w*\s+договора"
             r"\s*[:—–=\-]{0,3}\s*"+PRICE_PATTERN+r"\s*(?:руб(?:лей|ля|ль|\.)?|₽)(?!\w)",re.I)),
)
METHOD=re.compile(r"способ\s+закупк\w*\s*[:—–-]\s*"
                  r"(?P<method>аукцион\w*|конкурс\w*|запрос\s+котировок|"
                  r"запрос\s+предложений|закупк\w*\s+у\s+единственного\s+поставщика)",re.I)
INN=re.compile(r"(?:заказчик\w*[\s:;—–\S]{0,100}?)?"
               r"(?:инн\s+заказчика|заказчик\s*[:—–]\s*инн)\s*[:—–=\s]*"
               r"(?P<inn>\d{10}|\d{12})(?!\d)",re.I)
PAYMENT=re.compile(
 r"(?:расч[её]т|оплат[аыуе])\s+(?:производится|осуществляется|выполняется)"
 r"[\s\S]{0,175}?в\s+течени\w*\s+(?P<days>\d{1,3})"
 r"\s+(?P<type>рабоч\w*|календарн\w*)\s+дн\w*",re.I)

def _date(raw):
    match=re.search(DATE_TOKEN,str(raw or ""),re.I)
    return parse_day(match[0]) if match else None

def _price(raw):
    amount=money(raw)
    return Decimal(str(amount[1])) if amount else None

def _summary_type(p,card):
    raw=p.get("price_kind") or (card.get("summary") or {}).get("price_kind")
    return str(raw or "").lower()

def _method_category(raw):
    s=str(raw or "").lower()
    if re.search("аукцион",s):return "аукцион"
    if re.search("конкурс",s):return "конкурс"
    if re.search("котиров",s):return "запрос котировок"
    if re.search("предложен",s):return "запрос предложений"
    if re.search("единственн",s):return "единственный поставщик"
    return None

def _clip(text,match):
    return " ".join(text[max(0,match.start()-90):min(len(text),match.end()+115)].split())[:420]

def _signal(doc,match,code,title,law,card_value,doc_value,card_source,extra=""):
    meta=doc.get("meta") or {}
    name=str(doc.get("name") or "Документ")
    receipt=verified(doc)
    return {
        "scope":"223-ФЗ / фактическая сверка документов с карточкой ЕИС",
        "item":title,
        "status":"check",
        "evidence":("Карточка ЕИС: "+str(card_value)+". Документ: "+str(doc_value)+
                    ". Фрагмент: "+_clip(str(doc.get("text") or ""),match))[:900],
        "source":name,
        "source_url":meta.get("source_url") or doc.get("url") or "",
        "document":name,"field_path":"v339-"+code,
        "location":name+", позиция "+str(match.start()+1),
        "law":law,"law_url":LAW_URL,
        "note":("Нужна юридическая сверка версии извещения и приложений, лота, "
                "применимых исключений и источника суммы. "
                "Оригинал подтверждён: "+("да" if receipt else "нет")+
                ". Карточка: "+str(card_source or "сохранённые сведения ЕИС")+
                ". Несовпадение не является автоматически установленным нарушением. "+
                extra).strip()
    }

def fact_checks(p,detail,card,docs):
    """Strong evidence only: no inference of violation from a bare keyword."""
    result=[]
    seen=set()
    card_source=p.get("price_source_url") or p.get("url") or ""
    expected_price=_price(p.get("price_num") if p.get("price_num") is not None else p.get("price"))
    price_kind=_summary_type(p,card)
    for doc in docs[:MAX_DOCUMENTS]:
        if not isinstance(doc,dict) or doc.get("status")!="checked":continue
        text=str(doc.get("text") or "")[:MAX_LENGTH]
        if len(text)<30:continue
        def add(match,code,title,law,field_value,doc_value,extra=""):
            if len(result)>=MAX_SIGNALS:return
            key=(code,str(doc.get("name") or ""),
                 str(field_value).strip(),str(doc_value).strip())
            if key in seen:return
            seen.add(key)
            result.append(_signal(doc,match,code,title,law,field_value,doc_value,
                                  card_source,extra))
        for key,label,law,pattern in DATE_FIELDS:
            expected=_date(p.get(key))
            if expected is None:continue
            values={}
            for m in list(pattern.finditer(text))[:20]:
                parsed=_date(m.group("date"))
                if parsed and parsed!=expected:
                    values.setdefault(parsed,m)
            for value,match in list(values.items())[:2]:
                add(match,"date-"+key,"Сверить "+label.lower()+
                    " между карточкой ЕИС и документацией",law,
                    expected.isoformat(),value.isoformat(),
                    "Даты сравниваются по календарным дням, без времени.")
        for kind,label,law,pattern in PRICE_FIELDS:
            found={}
            for match in list(pattern.finditer(text))[:30]:
                parsed=_price(match.group("price"))
                if parsed is None:continue
                found.setdefault(parsed,match)
            if not found:continue
            if len(found)>1:
                first=list(found.values())[0]
                add(first,"price-multiple-"+kind,
                    "Несколько значений: "+label,law,
                    "Цена в разных лотах/редакциях ЕИС",
                    ", ".join(str(x) for x in list(found)[:7]),
                    "Сначала требуется отделить разные лоты и редакции.")
                continue
            extracted,match=next(iter(found.items()))
            if expected_price is None:
                add(match,"price-card-missing-"+kind,
                    "Цена есть в документации, но отсутствует в записи карточки ЕИС",
                    law,"не указана",str(extracted)+" руб.",
                    "Использовать официальную карточку ЕИС, не подставлять цену победителя.")
            elif price_kind and price_kind!=kind:
                # Correctly typed amounts are not comparable across price types.
                continue
            elif extracted!=expected_price:
                add(match,"price-difference-"+kind,
                    "Сверить "+label+" с карточкой ЕИС",law,
                    str(expected_price)+" руб. ("+(price_kind or "вид не подтверждён")+")",
                    str(extracted)+" руб.",
                    "При неизвестном виде суммы сверить НМЦД, максимальную цену и стоимость предмета отдельно.")
        official_method=_method_category(p.get("method"))
        if official_method:
            match=METHOD.search(text)
            if match:
                mentioned=_method_category(match.group("method"))
                if mentioned and official_method!=mentioned:
                    add(match,"purchase-method",
                        "Не совпадает способ закупки в документации и карточке ЕИС",
                        "223-ФЗ, ст. 4 ч. 9 п. 1; ч. 10",
                        str(p.get("method")),mentioned)
        official_inn=str(p.get("customer_inn") or "")
        if official_inn:
            match=INN.search(text)
            if match and match.group("inn")!=official_inn:
                add(match,"customer-inn","Сверить ИНН заказчика между ЕИС и документом",
                    "223-ФЗ, ст. 4 ч. 9 п. 2",
                    official_inn,match.group("inn"))
        # Record precise contractual payment obligations for legal assessment.
        # SME-only rules are NOT applied without official EIS SME designation;
        # 15 workdays in a non-SME competition is not automatically illegal.
        if verified(doc):
            for match in list(PAYMENT.finditer(text))[:3]:
                days=int(match.group("days"))
                if days>7:
                    add(match,"payment-"+str(days)+"-"+match.group("type").lower()[:4],
                        "Проверить применимость срока оплаты "+str(days)+" дней",
                        "223-ФЗ, ст. 3 ч. 5.3; ПП РФ № 1352",
                        "Статус МСП и исключения подлежат подтверждению",
                        match.group(0),
                        "Это не подтверждённое нарушение без установления применимого режима оплаты.")
    return result

def install(ns):
    old=ns.get("build_checks")
    if not callable(old):raise RuntimeError("V339 needs legal checks")
    if getattr(old,"__v339__",False):return
    @wraps(old)
    def build_checks(p,detail,card,docs):
        return list(old(p,detail,card,docs))+fact_checks(p,detail,card,docs)
    build_checks.__v339__=True
    ns["build_checks"]=build_checks
    print("V339 FACT_COMPARE_READY prices=typed dates=day_only "
          "sme=source_gated mismatch=review_only",flush=True)
