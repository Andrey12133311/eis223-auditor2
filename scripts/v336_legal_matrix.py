"""V336: evidence-led expanded legal examination of all readable 223-FZ documents.

Only a validated explicit bid-security clause plus a reliable matching price
can yield an automatic violation. All discretionary issues remain review items.
No remote calls or deletions, and no automatic claim of universal legality.
"""
from __future__ import annotations
from legal_applicability import competitive_rule_scope

import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from v331_broad_legal import verified
from v335_price_recovery import money
from v324_official_price import prices_in_text, AMOUNT as PRICE_AMOUNT, RUBLE_TOKEN
from legal_document_context import (document_context, local_context, source_scope_context,
                                    clause_variant_context, prepare_context_index, REASON_LABELS as CONTEXT_LABELS)

LAW_URL="https://www.consultant.ru/document/cons_doc_LAW_116964/"
MAX_DOCS=650
MAX_TEXT=750000
MAX_ADDITIONAL_REVIEWS=60
CATEGORIES=(
 ("regional_location","Территориальная привязка участника","223-ФЗ, ст. 3 ч. 1 п. 2; ч. 6",
  r"(?:участник|поставщик|исполнитель).{0,95}?(?:обязан|должен|обязательно).{0,90}?"
  r"(?:зарегистрирован|иметь\s+(?:юридический\s+)?адрес|находиться).{0,90}?"
  r"(?:в\s+городе|в\s+регионе|на\s+территории\s+субъекта)"),
 ("supply_from_stock","Обязательное наличие склада в указанном городе","223-ФЗ, ст. 3 ч. 1 п. 2",
  r"(?:участник|поставщик).{0,90}?(?:обязан|должен|необходимо).{0,65}?"
  r"(?:собственный|арендованный|наличие).{0,45}?склад.{0,75}?(?:на\s+территории|в\s+городе)"),
 ("past_with_customer","Требование опыта работы именно с заказчиком","223-ФЗ, ст. 3 ч. 1 п. 2; ч. 6",
  r"(?:опыт|исполненн\w*\s+договор\w*).{0,120}?(?:именно\s+с\s+заказчиком|с\s+данным\s+заказчиком|с\s+нашей\s+организацией)"),
 ("only_public_contract","Ограничение опыта государственными контрактами","223-ФЗ, ст. 3 ч. 1 п. 2; ч. 6",
  r"(?:учитыва\w*|принима\w*).{0,90}?(?:исключительно|только)\s+"
  r"(?:государственн\w*|муниципальн\w*)\s+(?:контракт\w*|договор\w*)"),
 ("affiliate_exclusion","Запрет участия связанных организаций","223-ФЗ, ст. 3 ч. 1 п. 2; ч. 6",
  r"(?:к\s+участию\s+не\s+допускаются|отклоня\w*\s+заявк\w*).{0,115}?"
  r"(?:аффилированн\w*|взаимозависим\w*)\s+(?:лиц\w*|организаци\w*)"),
 ("paid_application","Платная подача заявки или участие","223-ФЗ, ст. 3 ч. 1 п. 2; ст. 3.2",
  r"(?:за\s+подачу\s+заявк\w*|за\s+участие\s+в\s+закупк\w*).{0,90}?"
  r"(?:взимается\s+плата|обязан\w*\s+уплатить|вносится\s+платеж)"),
 ("paper_duplicate","Бумажный оригинал одновременно с электронной заявкой","223-ФЗ, ст. 3.3; ст. 3.4",
  r"(?:в\s+составе\s+электронн\w*\s+заявк\w*|при\s+подаче\s+электронн\w*\s+заявк\w*)"
  r".{0,165}?(?:обязан\w*|необходим\w*|требуется).{0,65}?"
  r"(?:передать\s+бумажн\w*|предоставить\s+оригинал\w*\s+на\s+бумаге)"),
 ("exclusive_sro","Свидетельство конкретной СРО без выбора","223-ФЗ, ст. 3 ч. 1 п. 2; ч. 6",
  r"(?:участник|исполнитель).{0,115}?(?:должен|обязан).{0,70}?"
  r"(?:состоять\s+в\s+сро|свидетельство\s+сро).{0,75}?(?:указанн\w*|конкретн\w*|определенн\w*)"),
 ("mandatory_inspection","Обязательный очный осмотр до подачи заявки","223-ФЗ, ст. 3 ч. 1 п. 2",
  r"(?:участник|поставщик).{0,90}?(?:обязан|должен).{0,95}?"
  r"(?:посетить\s+объект|провести\s+осмотр\s+объекта|принять\s+участие\s+в\s+осмотре)"
  r".{0,100}?(?:до\s+подачи\s+заявк\w*|для\s+допуска)"),
 ("original_application","Обязательные оригиналы документов внутри заявки","223-ФЗ, ст. 3 ч. 6; ст. 3.4 ч. 19.1",
  r"(?:в\s+составе\s+заявк\w*|к\s+заявке).{0,100}?"
  r"(?:обязан\w*\s+приложить|необходимо\s+представить|обязательно\s+представить)"
  r".{0,90}?оригинал\w*\s+(?:документ\w*|лицензи\w*|сертификат\w*)"),
 ("notarization","Нотариальное удостоверение в составе заявки","223-ФЗ, ст. 3 ч. 6; ст. 3.4 ч. 19.1",
  r"(?:в\s+составе\s+заявк\w*|к\s+заявке).{0,110}?"
  r"(?:нотариально\s+заверен\w*|нотариально\s+удостоверенн\w*)\s+(?:копи\w*|документ\w*)"),
 ("post_submission_changes","Право заказчика менять критерии после заявок","223-ФЗ, ст. 3 ч. 6; ст. 4 ч. 10",
  r"заказчик\s+вправе.{0,115}?измен\w*.{0,85}?"
  r"(?:критери\w*|порядок\s+оценк\w*).{0,85}?(?:после\s+подачи|после\s+окончания\s+подачи)"),
 ("committee_discretion","Оценка предложений без заранее заданных критериев","223-ФЗ, ст. 3 ч. 6; ст. 4 ч. 10",
  r"(?:оценк\w*\s+заявк\w*|выбор\s+победител\w*).{0,130}?"
  r"(?:по\s+усмотрению\s+комисси\w*|по\s+субъективн\w*\s+мнению)"),
 ("winner_substitution","Подмена предмета поставки после определения победителя","223-ФЗ, ст. 3 ч. 6; ст. 4 ч. 8",
  r"заказчик\s+вправе.{0,120}?(?:заменить|изменить).{0,95}?"
  r"(?:предмет\s+поставки|предмет\s+договора).{0,100}?после\s+(?:определения\s+победителя|подведения\s+итогов)"),
 ("unilateral_nonprice","Одностороннее изменение существенных условий","223-ФЗ, ст. 3 ч. 1; ст. 4 ч. 8",
  r"заказчик\s+вправе\s+в\s+односторонн\w*\s+порядке.{0,180}?"
  r"(?:изменить\s+(?:предмет|объем|количество)|увеличить\s+объем\s+поставки)"),
 ("hidden_admission","Дополнительные основания отклонения по усмотрению","223-ФЗ, ст. 3 ч. 6; ст. 4 ч. 10",
  r"комисси\w*\s+вправе\s+отклон\w*\s+заявк\w*.{0,140}?"
  r"(?:по\s+иным\s+основаниям|по\s+своему\s+усмотрению)"),
 ("lot_combination","Объединение технологически несвязанных товаров","223-ФЗ, ст. 3 ч. 1 п. 2",
  r"(?:один\s+лот|в\s+одном\s+лоте).{0,90}?"
  r"(?:не\s+связанн\w*\s+между\s+собой|технологически\s+не\s+связанн\w*)"),
 ("personal_data","Раскрытие персональных данных участника в разъяснении","223-ФЗ, ст. 3.2 ч. 3",
  r"(?:разъяснени\w*|ответ\s+на\s+запрос).{0,160}?"
  r"(?:фамили\w*\s+участник\w*|фио\s+участник\w*)"),
 ("clarification_essential","Изменение предмета или существенных условий разъяснением","223-ФЗ, ст. 3.2 ч. 4",
  r"(?:разъяснени\w*\s+документаци\w*|ответ\s+на\s+запрос).{0,180}?"
  r"(?:изменя\w*\s+предмет\s+закупк\w*|изменя\w*\s+существенн\w*\s+услови\w*)"),
 ("security_expiry","Возврат обеспечения заявки вне предусмотренных случаев","223-ФЗ, ст. 3.2 ч. 25–27",
  r"(?:обеспечени\w*\s+заявк\w*).{0,150}?"
  r"(?:не\s+возвраща\w*|удержива\w*).{0,110}?"
  r"(?:по\s+решению\s+заказчика|в\s+любом\s+случае)"),
 ("payment_without_acceptance","Неопределённость отсчёта срока оплаты","223-ФЗ, ст. 3 ч. 5.3; положение о закупке",
  r"срок\s+оплаты.{0,105}?(?:по\s+усмотрению\s+заказчика|в\s+момент,\s+определяемый\s+заказчиком)"),
 ("sme_unsupported","Неподтверждённый режим закупки только среди МСП","223-ФЗ, ст. 3.4; ПП РФ № 1352",
  r"(?:предназначен\w*\s+исключительно\s+для\s+мсп|участни\w*\s+только\s+субъекты\s+мсп|"
  r"только\s+для\s+субъектов\s+малого\s+и\s+среднего\s+предпринимательства)"),
)
COMPILED=tuple((code,title,law,re.compile(pattern,re.I|re.S))
               for code,title,law,pattern in CATEGORIES)

# Restrict to the explicit requirement sentence. The check must not be triggered
# by a table of contents or a negative example.
NEG=re.compile(r"(?:не\s+требуется|не\s+установлено|не\s+предусмотрено|"
               r"не\s+допускается\s+установлени\w*|запрещается\s+требовать)",
               re.I)
BID_LABEL=(r"(?:размер\s+)?обеспечени\w*\s+заявк\w*"
           r"(?:\s+на\s+участие\s+в\s+"
           r"(?:(?:конкурентн\w*|электронн\w*|открыт\w*|закрыт\w*)\s+){0,2}"
           r"(?:закупк\w*|аукцион\w*|конкурс\w*|запрос\w*\s+(?:котировок|предложений)))?")
# A value belongs to this exact requirement field. Do not scan arbitrary
# characters until a number: that borrowed a neighbouring contract-security
# amount, while rejecting ordinary DOCX label/value cells on separate lines.
BID_CLAUSE=re.compile(
 BID_LABEL+
 r"(?P<prefix>[\s:：|=–—]{0,60}"
 r"(?:(?:устанавливается|установлен[оа]?|составляет|равен|предусмотрен[оа]?|"
 r"требуется|размером)\s*)?(?:(?:в\s+(?:размере|сумме)|сумму)\s*)?[\s:：|=–—]{0,20})"
 r"(?P<amount>\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[,.]\d{1,2})?"
 r"|\d{4,15}(?:[,.]\d{1,2})?|\d{1,3}(?:[,.]\d{1,3})?)"
 r"\s*(?:\((?P<written>[а-яё -]{1,180})\)\s*)?"
 r"(?P<unit>%|процент(?:а|ов)?\b|руб(?:ль|ля|лей)\b|руб\b\.?|RUB\b|₽)",re.I)
BID_ABSENT=re.compile(BID_LABEL+r"[\s:：|=–—]{0,60}(?:не\s+(?:требуется|устанавливается|"
                      r"установлено|предусмотрено)|отсутствует)\b",re.I)
# Only accept actual money and percentage with units, never paragraph numbering.
MONEY_CLAUSE=re.compile(
 r"(?:начальн\w*\s*\(\s*максимальн\w*\s*\)\s*цен\w*\s*договора|"
 r"начальн\w*\s+максимальн\w*\s+цен\w*\s+договора|нмцд)"
 r"[\s:—–-]{0,32}(?P<amount>\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?|\d{4,15}(?:[,.]\d{1,2})?)"
 r"\s*(?:руб(?:лей|ля|\.|\b)|₽)",re.I)
CONTRACT_LINE=re.compile(r"обеспечени\w*\s+(?:исполнени\w*\s+)?(?:договора|контракта)",re.I)
COMPETITIVE=re.compile(r"конкурс|аукцион|запрос\s+(?:котировок|предложений)|конкурентн\w*\s+закупк",re.I)
SECURITY_LABEL=re.compile(BID_LABEL+r"|"+CONTRACT_LINE.pattern,re.I)
CLAUSE_END=re.compile(r"[!?;]|(?<!\d)\.(?!\d)|(?<=\d)\.(?!\d)")
FORMULA_CONTINUATION=re.compile(
    r"\s*[,;:]?\s*(?:\(\s*)?(?:но\s+)?(?:не\s+(?:более|менее|выше|ниже)\b|"
    r"за\s+вычетом\b|(?:уменьш|увелич)\w*\b|или\b|либо\b)",re.I)
# Original DOCX table rows are preserved as newlines and pipe-separated cells.
# An exact next field is a boundary, unlike a line-wrapped cap/alternative in
# the current amount. Requiring a full label plus separator avoids promoting
# arbitrary prose (or a formula continuation) to an independent field.
SECURITY_NEXT_FIELD=re.compile(
    r"(?:\r\n|[\r\n\t])[ \t|]*"
    r"(?:\d{1,3}(?:\.\d{1,3})*[.)]?[ \t]*(?:\|[ \t]*)?)?"
    r"(?:(?:порядок|сроки?|форм[аы]|способы?)\s+"
    r"(?:предоставления|внесения|возврата)\s+(?:обеспечения\s+заявки|денежных\s+средств)|"
    r"(?:порядок|сроки?|дата)\s+(?:подачи|окончания\s+подачи)\s+заявок|"
    r"адрес\s+электронной\s+площадки|"+BID_LABEL+r"|"+CONTRACT_LINE.pattern+r")"
    r"[ \t]*(?=[:：|]|\r?\n|$)",re.I)
PERCENT_NMCD_BASE=re.compile(
    r"\s*(?:от\s+)?(?:нмц[дк]\b|начальн\w*\s*"
    r"(?:\(\s*максимальн\w*\s*\)\s*|максимальн\w*\s+)?"
    r"цен\w*\s+(?:договора|контракта|лота)\b)", re.I)
PERCENT_NMCD_SUFFIX=re.compile(r"\s*(?:\(\s*(?:НМЦ[ДК]|цен[аы]\s+лота)\s*\)\s*)?[,|:\s]*",re.I)
GENERIC_SECURITY=re.compile(
    r"\b(?:если|например|образец|пример|допустим|предположим)\b|"
    r"\bв\s+случае\b|\bпри\s+(?:проведении|осуществлении|условии)\b|"
    r"\b(?:закон|положение)\w*\s+(?:предусматривает|устанавливает)\b|"
    r"\b(?:не\s+допускается|запрещается)\s+(?:требовать|устанавливать)\b",re.I)
LOT_LABEL=re.compile(r"\bлот(?:а|у|ом)?\s*(?:№|N(?:o)?\.?|:)?\s*(\d+)\b",re.I)
NON_NOTICE_KIND={"protocol","final_protocol","contract","clarification","clarification_request"}
NON_PRICE_ORIGINAL=re.compile(
    r"положени\w*\s+о\s+закупк|договор\s+заключен|итоговый\s+протокол|"
    r"протокол\s+итог|\b(?:образец|пример|шаблон)\b",re.I)
WRITTEN_PRICE=re.compile(r"(?P<amount>"+PRICE_AMOUNT.pattern+
    r")\s*\((?P<written>[^)]{1,220})\)\s*"+RUBLE_TOKEN,re.I)
WRITTEN_PRICE_START=re.compile(r"(?P<amount>"+PRICE_AMOUNT.pattern+r")\s*\(",re.I)
VAT_PRICE_NOTE=re.compile(r"\s*(?:с|без)\s+НДС\s*",re.I)
WRITTEN_NUMBERS=dict(zip(
    ("ноль один два три четыре пять шесть семь восемь девять десять одиннадцать "
     "двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать "
     "восемнадцать девятнадцать двадцать тридцать сорок пятьдесят шестьдесят "
     "семьдесят восемьдесят девяносто сто двести триста четыреста пятьсот "
     "шестьсот семьсот восемьсот девятьсот").split(),
    list(range(20))+list(range(20,100,10))+list(range(100,1000,100))))
WRITTEN_NUMBERS.update({"одна":1,"одно":1,"две":2})
WRITTEN_SCALES={word:scale for words,scale in (
    ("тысяча тысячи тысяч",1000),("миллион миллиона миллионов",1000000),
    ("миллиард миллиарда миллиардов",1000000000)) for word in words.split()}


def _written_value(text):
    """Only an unambiguous integer spelling may qualify a new parenthesis.

    Unsupported decimal/case forms remain unqualified instead of silently
    discarding a potentially contradictory amount written in words.
    """
    words=str(text or "").casefold().split()
    if words==["ноль"]:return Decimal(0)
    total=group=0
    previous_scale=1000000000000
    rank=4
    for word in words:
        if word in WRITTEN_SCALES:
            scale=WRITTEN_SCALES[word]
            if scale>=previous_scale:return None
            total+=(group or 1)*scale
            group=0
            rank=4
            previous_scale=scale
            continue
        value=WRITTEN_NUMBERS.get(word)
        if not value:return None
        next_rank=3 if value>=100 else 2 if value>=10 else 1
        if next_rank>=rank:return None
        group+=value
        rank=0 if 10<=value<20 else next_rank
    return Decimal(total+group) if words else None


def _bid_context(body,match):
    """Keep negatives/examples attached to this field, not adjacent subjects.

    A line break may be a table-cell boundary, so it does not on its own
    terminate the value. A following security label always starts a new field.
    """
    left=max(0,match.start()-180)
    boundaries=list(CLAUSE_END.finditer(body,left,match.start()))
    if boundaries:left=boundaries[-1].end()
    previous=list(SECURITY_LABEL.finditer(body,left,match.start()))
    if previous:
        # The words before this label describe a separate security subject.
        left=match.start()
    right=min(len(body),match.end()+160)
    for pattern in (SECURITY_NEXT_FIELD,SECURITY_LABEL):
        following=pattern.search(body,match.end(),right)
        if following:right=following.start()
    boundary=CLAUSE_END.search(body,match.end(),right)
    if boundary:right=boundary.end()
    return body[left:right]

def _decimal(raw):
    s=str(raw or "").replace("\xa0","").replace("\u202f","").replace(" ","").replace("%","").replace(",",".")
    try:
        x=Decimal(s)
        return x if x.is_finite() and x>=0 else None
    except (InvalidOperation,ValueError):
        return None


def _security_unit(value):
    return "money" if re.fullmatch(RUBLE_TOKEN+r"|RUB|₽",str(value or ""),re.I) else "percent"


def _security_requirement_tail(body,match):
    """Return the bounded continuation of this requirement, not a new field."""
    start=match.end()
    end=min(len(body),start+240)
    for pattern in (SECURITY_NEXT_FIELD,SECURITY_LABEL):
        boundary=pattern.search(body,start,end)
        if boundary:end=boundary.start()
    for boundary in CLAUSE_END.finditer(body,start,end):
        following=body[boundary.end():end]
        # A unit abbreviation may precede an attached parenthetical as well
        # as a comma. Neither it nor a semicolon before an explicit formula
        # continuation can turn the first operand into a fixed obligation.
        if (boundary[0]=="." and re.search(r"\bкоп$",body[start:boundary.start()],re.I)
                and following.lstrip().startswith((",",";",":","("))):
            continue
        if boundary[0] in ".;" and FORMULA_CONTINUATION.match(following):
            continue
        end=boundary.start()
        break
    return start,end


def _security_percent_basis(body,match):
    """A percentage needs its own explicit denominator, never a nearby price.

    The evidence remains tied to the exact requirement. No clause after a
    sentence/field boundary can repair an unspecified or different base.
    """
    start,end=_security_requirement_tail(body,match)
    tail=body[start:end]
    basis=PERCENT_NMCD_BASE.match(tail)
    # Only a complete simple base is known. Caps, deductions, VAT qualifiers,
    # another lot and historical procurement references require a calculation
    # and source relationship which this numerical rule cannot establish.
    modified=bool(tail.strip(" \r\n\t,|:") and
                  (basis is None or not PERCENT_NMCD_SUFFIX.fullmatch(tail[basis.end():])))
    kind="nmcd" if basis and not modified else "unverified"
    proof_end=start+basis.end() if kind=="nmcd" else end
    return {"kind":kind,"modified_formula":modified,"start":start,"end":proof_end,
            "excerpt":body[start:proof_end]}


def _security_money_basis(body,match):
    """Do not mistake the first RUB operand for a complete, fixed obligation.

    Caps, deductions, alternatives and unknown suffixes require interpretation.
    Keep their original text for both a fresh finding and legacy reassessment.
    """
    start,end=_security_requirement_tail(body,match)
    tail=body[start:end].strip(" \r\n\t,|:")
    value=_decimal(match["amount"])
    # Explicit zero kopecks add no amount. Nonzero/inconsistent fractions are
    # retained for review, never truncated before comparing legal thresholds.
    zero_kopecks=bool(re.fullmatch(r"0{1,2}\s+коп(?:еек|ейки|ейка)?\b\.?",tail,re.I))
    fixed=not tail or (zero_kopecks and value is not None and value==value.to_integral_value())
    return {"kind":"fixed" if fixed else "unverified", "start":start,"end":end,
            "excerpt":body[start:end]}


def _evidence(d,m,law,title,code,qualified):
    body=str(d.get("text") or "")
    start=max(0,m.start()-125)
    end=min(len(body),m.end()+180)
    return {
        "scope":"223-ФЗ / углубленная проверка условий документации",
        "item":title,"status":"check",
        "evidence":" ".join(body[start:end].split())[:550],
        "source":d.get("name"),"source_url":(d.get("meta") or {}).get("source_url") or d.get("url"),
        "document":d.get("name"),"field_path":"v336-"+code,
        "location":f"{d.get('name') or 'Документ'}, символ {m.start()+1}",
        "law":law,"law_url":LAW_URL,
        "note":("Проверить применимость и возможные исключения. "
                "Условие извлечено из "+("подтверждённого оригинала ЕИС" if qualified
                    else "локального файла без подтверждения происхождения ЕИС")+
                ". Само наличие формулировки не является установленным нарушением.")
    }

def deeper_checks(p,docs):
    checks=[]
    seen=set()
    for doc in docs[:MAX_DOCS]:
        if not isinstance(doc,dict) or doc.get("status")!="checked":
            continue
        body=str(doc.get("text") or "")[:MAX_TEXT].replace("\xa0"," ")
        if len(body.strip())<30:continue
        for code,title,law,pattern in COMPILED:
            if len(checks)>=MAX_ADDITIONAL_REVIEWS:return checks
            m=pattern.search(body)
            if m is None:continue
            window=body[max(0,m.start()-80):min(len(body),m.end()+80)]
            if NEG.search(window):continue
            key=(code,str(doc.get("name") or ""),re.sub(r"\W+","",m.group(0).casefold())[:90])
            if key in seen:continue
            seen.add(key)
            checks.append(_evidence(doc,m,law,title,code,verified(doc)))
    return checks

def verified_price(p,docs):
    """Use the same explicit monetary parser as official price recovery.

    The DB price by itself is NOT sufficient proof to establish a bid-security
    violation. A missing cached price must not discard an otherwise unanimous
    explicit NMCD in verified originals. Conflicting revisions or lot prices
    remain unknown even when one happens to equal the DB/card price.
    """
    if "price" in (p.get("_official_context_conflicts") or {}):return None
    # Equality of amounts does not establish equality of legal price kinds.
    # A generic heading in an original must not relabel a selected unit-price
    # sum, maximum contract value, or other explicitly non-NMCD field.
    if p.get("price_kind") not in (None,"","nmcd"):return None
    official=p.get("verified_price_source")
    context=p.get("_current_soap_context") or {}
    record=context.get("record") or {}
    xml_price=context.get("price") or {}
    xml_verified=(record.get("reg_number")==str(p.get("reg") or "") and
                  record.get("xml_family")=="notice" and
                  bool(re.fullmatch(r"[0-9a-f]{64}",str(record.get("xml_sha256") or ""))))
    if xml_verified and xml_price and xml_price.get("price_kind")!="nmcd":return None
    if isinstance(official,dict) and official.get("kind") not in (None,"","nmcd"):
        from v311_verified_customer import _official_source
        if _official_source(official.get("source"),str(p.get("reg") or "")):return None
    expected=money(p.get("price_num"))
    if expected and expected[1]<=0:return None
    expected_value=Decimal(str(expected[1])) if expected else None
    records=[]
    observed_prices=set()
    lots=set()
    lot_ids=set()
    for d in docs[:MAX_DOCS]:
        if not isinstance(d,dict) or not verified(d):continue
        context=document_context(d)
        scope=source_scope_context(p,d)
        if "source_other_purchase" in scope["reasons"]:continue
        if "source_lot_ambiguous" in scope["reasons"]:return None
        if not scope["eligible_actual"] or not context["eligible_actual"]:continue
        if context["role"] not in {"documentation","notice","unknown"}:continue
        if (str(d.get("type") or "").casefold() in NON_NOTICE_KIND or
                NON_PRICE_ORIGINAL.search(str(d.get("name") or ""))):continue
        body=str(d.get("text") or "")[:MAX_TEXT].replace("\u202f"," ")
        lots.update(scope["lots"])
        lot_ids.update(scope["lot_ids"])
        lots.update(LOT_LABEL.findall(body))
        if len(lots)>1 or len(lot_ids)>1:return None
        # prices_in_text already rejects dates, percentages, unit/winner
        # prices, conditional statutory thresholds and unrelated table cells.
        values=prices_in_text(body,typed=True)
        if not values:continue
        amounts={Decimal(value[0]) for value in values}
        if len(amounts)!=1:return None
        observed=next(iter(amounts))
        if observed<=0:return None
        if expected_value is not None and observed!=expected_value:return None
        observed_prices.add(observed)
        if len(observed_prices)>1:return None
        # Unknown/generic and ceiling values still participate in conflict
        # checks. Filtering them first would hide a different revision/price.
        if not any(value[3]=="nmcd" for value in values):continue
        # Inspect the original, not its capped evidence excerpt. Recovery
        # accepts numeric fields with a spelling; a legal finding must not
        # choose the digits when that spelling conflicts or cannot be read.
        # The integer alternative covers a separately printed kopek suffix.
        for opening in WRITTEN_PRICE_START.finditer(body):
            printed=_decimal(opening["amount"])
            if printed is None or printed>Decimal("1000000000000000"):continue
            if (printed.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)!=observed
                    and printed!=Decimal(int(observed))):continue
            match=WRITTEN_PRICE.match(body,opening.start())
            if match is None:return None
            # A complete VAT note is not an amount written in words. Mixed
            # or incomplete parentheticals still require readable spelling.
            if VAT_PRICE_NOTE.fullmatch(match["written"]):continue
            if _written_value(match["written"])!=printed:
                return None
        # A maximum contract value is a different price kind; it cannot
        # corroborate the exact NMCD needed by this rule.
        if any(re.search(r"максимальн\w*\s+значени\w*\s+цен",value[2],re.I)
               for value in values):continue
        if ((scope["requested_lots"] and scope["lots"]!=scope["requested_lots"]) or
                (scope["requested_lot_ids"] and scope["lot_ids"]!=scope["requested_lot_ids"])):
            continue
        records.append((observed,d,None))
    # The authenticated EIS card has its own verified browser-source
    # provenance. Only the explicit NMCD price kind can confirm this value,
    # not a maximum/subject-cost field from an unrelated lot.
    if isinstance(official,dict) and official.get("kind")=="nmcd":
        from v311_verified_customer import _official_source
        card_value=money(official.get("price"))
        if _official_source(official.get("source"),str(p.get("reg") or "")):
            card_scope=source_scope_context(p,{"meta":official})
            if (not card_scope["eligible_actual"] or
                    card_scope["requested_lots"] and card_scope["lots"]!=card_scope["requested_lots"] or
                    card_scope["requested_lot_ids"] and card_scope["lot_ids"]!=card_scope["requested_lot_ids"]):
                return records[0] if records else None
            if not card_value or card_value[1]<=0:return None
            confirmed_value=Decimal(str(card_value[1]))
            if expected_value is not None and confirmed_value!=expected_value:return None
            if observed_prices and observed_prices!={confirmed_value}:return None
            if records and confirmed_value!=records[0][0]:return None
            if records:return records[0]
            return (confirmed_value,{"name":"Карточка ЕИС — подтверждённая НМЦД",
                    "meta":{**official,"source_url":official.get("source")}},None)
    return records[0] if records else None

def price_basis_reason(p):
    """Bounded diagnostic labels; never include source text, amounts or URLs."""
    if "price" in (p.get("_official_context_conflicts") or {}):return "price_basis_conflict"
    if p.get("price_kind") in ("unit_price","total_unit_prices","maximum_contract_value",
                                "subject_cost","lot_price","contract_price"):
        return "price_basis_non_nmcd"
    return "verified_nmcd_missing"


SECURITY_CONTEXT_LABELS={
    **CONTEXT_LABELS,
    "security_source_unverified":"Происхождение исходного условия обеспечения не подтверждено",
    "security_clause_unresolved":"Не установлен единственный точный фрагмент условия обеспечения заявки",
    "security_source_not_requirement":"Источник не устанавливает требование к обеспечению заявки этой закупки",
    "security_nmcd_unverified":"Не подтверждена НМЦД для проверяемого условия обеспечения заявки",
    "security_price_lot_unbound":"Не подтверждено, что НМЦД и условие обеспечения относятся к одному лоту",
    "security_lot_identity_conflict":"Номер лота и идентификатор лота в проверенных источниках противоречат друг другу",
    "security_lot_identity_unavailable":"Не удалось сохранить подтверждённую привязку условия обеспечения к конкретному лоту",
    "security_sme_scope_unverified":"Режим участия только субъектов МСП для этого условия не подтверждён",
    "security_conflicting_terms":"В оригиналах есть разные условия обеспечения заявки; применимая редакция не установлена",
    "security_relation_unresolved":"Не установлена связь противоречащих условий обеспечения с одним лотом",
    "security_conditional_clause":"Фрагмент является условным, отрицательным или справочным, а не установленным размером обеспечения",
    "security_amount_conflict":"Числовой размер обеспечения не согласован с его написанием прописью",
    "security_percent_basis_unverified":"Не подтверждено, что процент обеспечения рассчитан именно от НМЦД проверяемого лота",
    "security_money_amount_unverified":"Не подтверждён однозначный денежный размер обеспечения с учётом ограничений и иных условий формулы",
    "security_collection_limited":"Сопоставление условий обеспечения ограничено объёмом доступного текста",
}


def _security_identity(doc):
    meta=doc.get("meta") or {}
    return (str(meta.get("sha256") or ""),str(meta.get("source_url") or doc.get("url") or ""),
            str(meta.get("archive_member") or meta.get("member") or ""))


def _security_source(doc,start=0,end=None):
    meta=doc.get("meta") or {}
    body=str(doc.get("text") or "")
    end=min(len(body),start+650) if end is None else min(len(body),end)
    return {"document":str(doc.get("name") or ""),
            "source_url":str(meta.get("source_url") or doc.get("url") or ""),
            "sha256":str(meta.get("sha256") or ""),
            "archive_sha256":str(meta.get("archive_sha256") or ""),
            "archive_member":str(meta.get("archive_member") or meta.get("member") or ""),
            "start":start,"end":end,"excerpt":body[start:end][:650]}


def _security_purchase(p,doc,position):
    scope=source_scope_context(p,doc,position)
    result=dict(p)
    if not (scope["requested_lots"] or scope["requested_lot_ids"]):
        if len(scope["lots"])==1:result["lot_number"]=scope["lots"][0]
        if len(scope["lot_ids"])==1:result["lot_id"]=scope["lot_ids"][0]
    return result


def _security_scope_facts(selected,docs,scope_cache):
    """Facts calculated by this analysis, shared only within its input snapshot.

    Callers allocate this internal cache; no cache or assessment supplied in
    purchase/finding data is treated as documentary proof.
    """
    key=tuple(str(selected.get(field) or "") for field in
              ("reg","reg_number","lot_number","lot","lot_id"))
    if key not in scope_cache:
        from v383_msp_scope import resolve
        scope_cache[key]=(verified_price(selected,docs),resolve(selected,docs))
    return scope_cache[key]


def _security_terms(docs):
    """Prepare source-specific clauses once; records are internal, not persisted."""
    terms=[]
    for doc in list(docs)[:MAX_DOCS]:
        if not isinstance(doc,dict) or not verified(doc):continue
        body=str(doc.get("text") or "")[:MAX_TEXT].replace("\xa0"," ").replace("\u202f"," ")
        if not re.search(r"обеспечени\w*\s+заявк",body,re.I):continue
        context=document_context(doc)
        context_index=prepare_context_index(body)
        for pattern in (BID_CLAUSE,BID_ABSENT):
            matches=list(pattern.finditer(body))
            for match in matches[:100]:
                absent=pattern is BID_ABSENT
                value=Decimal(0) if absent else _decimal(match["amount"])
                if value is None:continue
                unit="money" if absent else _security_unit(match["unit"])
                percent_basis=_security_percent_basis(body,match) if unit=="percent" else None
                money_basis=_security_money_basis(body,match) if unit=="money" and not absent else None
                segment=_bid_context(body,match)
                local=local_context(body,match.start(),match.end())
                variant=clause_variant_context(body,match.start(),match.end(),context_index=context_index)
                reasons=[reason for reason in context["reasons"] if reason!="project_is_not_completed_act"]
                reasons.extend(local["reasons"])
                reasons.extend(variant["reasons"])
                if context["role"] in {"regulation","protocol","instruction","template","draft"}:
                    reasons.append("security_source_not_requirement")
                if GENERIC_SECURITY.search(segment) or not absent and NEG.search(segment):
                    reasons.append("security_conditional_clause")
                if not absent and match["written"] and _written_value(match["written"])!=value:
                    reasons.append("security_amount_conflict")
                if money_basis and money_basis["kind"]!="fixed":
                    reasons.append("security_money_amount_unverified")
                if len(matches)>100 or len(str(doc.get("text") or ""))>MAX_TEXT or len(docs)>MAX_DOCS:
                    reasons.append("security_collection_limited")
                terms.append({"doc":doc,"start":match.start(),"end":match.end(),"value":value,
                              "unit":unit,"absent":absent,"variant":variant,"percent_basis":percent_basis,
                              "money_basis":money_basis,
                              "context_index":context_index,
                              "reasons":list(dict.fromkeys(reasons))})
    return terms


def assess_security_context(p,docs,source_doc,start,end,*,rule_code="",prepared=None,scope_cache=None):
    """Limit a claimed bid-security breach using current, same-lot evidence.

    ``confirmed`` means only that this contextual limiter found no objection;
    the detector still proves the threshold and law. Unknown/conflicting terms
    remain review evidence, never a conclusion that the procurement complies.
    All returned fields are JSON-serializable; the input originals stay intact.
    """
    docs=list(docs or ())
    terms=_security_terms(docs) if prepared is None else prepared
    scope_cache={} if scope_cache is None else scope_cache
    reasons=[]
    related=[]
    result={"source":_security_source(source_doc,start,end),"related_sources":related}
    if not verified(source_doc):reasons.append("security_source_unverified")
    # Equal bytes may have different explicit revision annotations in two
    # records. Bind to the cited source object before considering an identity
    # fallback; a current copy cannot lend its status to an obsolete record.
    same_source=[term for term in terms if term["doc"] is source_doc]
    if not same_source:
        same_source=[term for term in terms if _security_identity(term["doc"])==_security_identity(source_doc)
                     and term["doc"].get("name")==source_doc.get("name")]
    source_terms=[term for term in same_source if start<=term["start"] and term["end"]<=end]
    # Repeated identical source objects are one original, not conflicting acts.
    source_terms=list({(term["start"],term["end"]):term for term in source_terms}.values())
    source=source_terms[0] if len(source_terms)==1 else None
    if source is None:
        reasons.append("security_clause_unresolved")
    else:
        reasons.extend(source["reasons"])
        result["variant_context"]=source["variant"]
        if source["money_basis"] is not None:result["money_basis"]=source["money_basis"]
        scope=source_scope_context(p,source_doc,source["start"],context_index=source["context_index"])
        reasons.extend(scope["reasons"])
        result["source_scope"]=scope
        selected=_security_purchase(p,source_doc,source["start"])
        basis,sme=_security_scope_facts(selected,docs,scope_cache)
        price=None
        if basis is None:
            reasons.append("security_nmcd_unverified")
        else:
            price,price_doc,_=basis
            price_scope=source_scope_context(selected,price_doc)
            result["price_basis"]={"value":str(price),"source":_security_source(price_doc),"scope":price_scope}
            # Explicit lot identities must bind both sides of the comparison.
            # An unscoped clause cannot borrow a price belonging to another lot.
            if (scope["lots"]!=price_scope["lots"] or scope["lot_ids"]!=price_scope["lot_ids"]):
                reasons.append("security_price_lot_unbound")
        if source["unit"]=="percent":
            result["percent_basis"]=source["percent_basis"]
            # At/below 5m, a positive bid-security requirement itself is the
            # supported breach. The 2%/5% comparison needs an exact NMCD base.
            if (source["percent_basis"].get("modified_formula") or
                    (price is not None and price>Decimal("5000000")) or
                    str(rule_code or "").upper().endswith(("OVER-FIVE-PERCENT","OVER-TWO-PERCENT"))):
                if source["percent_basis"]["kind"]!="nmcd":
                    reasons.append("security_percent_basis_unverified")
        if str(rule_code or "").upper()=="223-SME-BID-SECURITY-OVER-TWO-PERCENT":
            result["sme_scope"]=sme
            if sme["value"] is not True:reasons.append("security_sme_scope_unverified")
        for peer in terms:
            if (peer["doc"] is source_doc and
                    peer["start"]==source["start"] and peer["end"]==source["end"]):continue
            # A nonselected/template/historical peer cannot contradict an
            # operative requirement. Unselected alternatives remain in the
            # source assessment itself, without choosing the lower amount.
            # An operative but unresolved amount is possible contradictory
            # evidence; it cannot simply disappear because its formula needs
            # interpretation. Nonoperative/template sources still stay out.
            if any(reason!="security_money_amount_unverified" for reason in peer["reasons"]):continue
            peer_scope=source_scope_context(selected,peer["doc"],peer["start"],context_index=peer["context_index"])
            if not set(peer_scope["reasons"])-{"source_other_lot"}:
                same_number=bool(scope["lots"] and scope["lots"]==peer_scope["lots"])
                same_id=bool(scope["lot_ids"] and scope["lot_ids"]==peer_scope["lot_ids"])
                incompatible_id=bool(scope["lot_ids"] and peer_scope["lot_ids"] and
                                     scope["lot_ids"]!=peer_scope["lot_ids"])
                incompatible_number=bool(scope["lots"] and peer_scope["lots"] and
                                         scope["lots"]!=peer_scope["lots"])
                if (same_number and incompatible_id) or (same_id and incompatible_number):
                    reasons.append("security_lot_identity_conflict")
                    if len(related)<12:related.append(_security_source(peer["doc"],peer["start"],peer["end"]))
                    continue
            if not peer_scope["eligible_actual"]:continue
            unbound_percent=any(term["unit"]=="percent" and term["percent_basis"]["kind"]!="nmcd"
                                for term in (peer,source))
            unbound_money=any(term["money_basis"] and term["money_basis"]["kind"]!="fixed"
                              for term in (peer,source))
            if unbound_percent or unbound_money:
                equal=False
            elif peer["unit"]==source["unit"]:
                equal=peer["value"]==source["value"]
            elif price is not None:
                first=source["value"]*price/100 if source["unit"]=="percent" else source["value"]
                second=peer["value"]*price/100 if peer["unit"]=="percent" else peer["value"]
                equal=abs(first-second)<=Decimal("0.01")
            else:
                equal=False
            if equal:continue
            same_original=_security_identity(peer["doc"])==_security_identity(source_doc)
            if same_original and peer["variant"]["section_start"]!=source["variant"]["section_start"]:
                # Separate explicit sections may concern different lots or
                # procedures; their relationship is not a proven replacement.
                reason="security_relation_unresolved"
            elif (scope["lots"]!=peer_scope["lots"] or scope["lot_ids"]!=peer_scope["lot_ids"]):
                reason="security_relation_unresolved"
            else:
                reason="security_conflicting_terms"
            reasons.append(reason)
            if len(related)<12:related.append(_security_source(peer["doc"],peer["start"],peer["end"]))
    reasons=list(dict.fromkeys(reasons))
    result.update(status="review" if reasons else "confirmed",reasons=reasons,
                  reason_labels=[SECURITY_CONTEXT_LABELS.get(reason,reason) for reason in reasons])
    return result


def _security_context_review(doc,match,assessment):
    return {"scope":"223-ФЗ / обеспечение заявки","item":"Сверить применимое условие обеспечения заявки",
            "status":"check","field_path":"v336-bid-security-context","criterion_keys":["bid_security"],
            "evidence":str(doc.get("text") or "")[match.start():match.end()],
            "match_text":match[0],"source_position":match.start(),
            "source":doc.get("name"),"document":doc.get("name"),
            "source_url":(doc.get("meta") or {}).get("source_url") or doc.get("url") or "",
            "law":"223-ФЗ, ст. 3.2 ч. 25–27","law_url":LAW_URL,
            "location":f"Оригинал ЕИС, позиция {match.start()+1}",
            "security_assessment":assessment,"methodology_reason_codes":assessment["reasons"],
            "note":"; ".join(assessment["reason_labels"])+". Сохранить исходное условие для проверки, не считать подтверждённым нарушением."}


def security_findings(p,docs):
    """Very narrow checks under parts 25–27 of article 3.2.

    No inference from performance security; no automatic infringement without
    affirmative bid-security clause and certified NMCD corroboration.
    """
    finding=[]
    checks=[]
    seen=set()
    if competitive_rule_scope(p) is not True:
        return finding,checks
    docs=list(docs or ())
    prepared=None
    scope_cache={}
    for doc in docs[:MAX_DOCS]:
        if not isinstance(doc,dict) or not verified(doc):continue
        if NON_PRICE_ORIGINAL.search(str(doc.get("name") or "")):continue
        body=str(doc.get("text") or "")[:MAX_TEXT].replace("\xa0"," ").replace("\u202f"," ")
        if not re.search(r"обеспечени\w*\s+заявк",body,re.I):continue
        # Examples/negative clauses at the beginning of a document do not
        # consume the actual requirement's chance to be examined. Text and
        # document limits remain bounded; findings are still deduplicated.
        for candidate_index,match in enumerate(BID_CLAUSE.finditer(body)):
            # The prepared context covers the first 100 clauses; candidates
            # outside that bounded inventory cannot obtain a proven scope.
            if candidate_index>=100:break
            segment=_bid_context(body,match)
            if NEG.search(segment) or GENERIC_SECURITY.search(segment):continue
            unit=(match.group("unit") or "").strip().lower()
            amount=_decimal(match.group("amount"))
            if amount is None or not unit or amount<=0:continue
            if match.group("written") and _written_value(match["written"])!=amount:continue
            # Non-monetary shorthand and accidental narrative percentages do
            # not pass; require explicit units.
            if _security_unit(unit)=="money":
                value=amount
                is_percent=False
            elif unit.startswith("%") or unit.startswith("процент"):
                value=amount
                is_percent=True
            else:continue
            if value>Decimal("100") and is_percent:continue
            evidence=" ".join(segment.split())
            source=(doc.get("meta") or {}).get("source_url") or doc.get("url") or ""
            selected=_security_purchase(p,doc,match.start())
            verified_nmcd,sme_classification=_security_scope_facts(selected,docs,scope_cache)
            sme_only=sme_classification["value"] is True
            percent_basis=_security_percent_basis(body,match) if is_percent else None
            money_basis=None if is_percent else _security_money_basis(body,match)
            if verified_nmcd is not None:
                price,price_doc,_=verified_nmcd
                ratio=(value if percent_basis["kind"]=="nmcd" else None) if is_percent else value/price*Decimal("100")
                fixed_value=is_percent or money_basis["kind"]=="fixed"
                if price>Decimal("5000000") and fixed_value and ratio is not None and ratio<=Decimal("2"):
                    # This candidate cannot violate any supported numerical
                    # threshold. Skipping peer comparisons makes no statement
                    # about whole-procurement compliance. A conflicting larger
                    # condition still undergoes the full context gate below.
                    continue
            if prepared is None:
                prepared=_security_terms(docs)
            assessment=assess_security_context(p,docs,doc,match.start(),match.end(),
                                               prepared=prepared,scope_cache=scope_cache)
            if verified_nmcd is None:
                checks.append({
                    "scope":"223-ФЗ / обеспечение заявки",
                    "item":"Проверить ограничение обеспечения заявки относительно НМЦД",
                    "status":"check","evidence":evidence,
                    "source":doc.get("name"),"source_url":source,"document":doc.get("name"),
                    "field_path":"v336-bid-security-needs-verified-nmcd",
                    "price_basis_reason":price_basis_reason(selected),
                    "match_text":match[0],"source_position":match.start(),
                    "security_assessment":assessment,
                    "law":"223-ФЗ, ст. 3.2 ч. 25–27","law_url":LAW_URL,
                    "location":f"Оригинал ЕИС, позиция {match.start()+1}",
                    "note":"Не подтверждены вид цены и числовая НМЦД по официальным источникам. "
                           "Не квалифицировать условие как нарушение до установления цены."
                })
                continue
            if assessment["status"]!="confirmed":
                checks.append(_security_context_review(doc,match,assessment))
                continue
            condition=None
            law="223-ФЗ, ст. 3.2 ч. 27"
            code="223-3.2-27-BID-SECURITY-LOW-NMCD"
            if price<=Decimal("5000000"):
                condition="Обеспечение заявки предусмотрено при НМЦД не более 5 млн рублей"
            elif sme_only and ratio>Decimal("2"):
                condition="Размер обеспечения заявки превышает 2% НМЦД в закупке только для МСП"
                law="223-ФЗ, ст. 3 ч. 8; ПП РФ № 1352, п. 23"
                code="223-SME-BID-SECURITY-OVER-TWO-PERCENT"
            elif ratio>Decimal("5"):
                condition="Размер обеспечения заявки превышает 5% НМЦД"
                code="223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT"
            if not condition:
                if ratio>Decimal("2") and sme_classification["state"] in ("unknown","conflict"):
                    checks.append({"scope":"223-ФЗ / обеспечение заявки", "status":"check",
                        "item":"Установить, ограничено ли участие в закупке только МСП",
                        "evidence":evidence, "document":doc.get("name"), "source":doc.get("name"),
                        "source_url":source, "field_path":"v383-bid-security-msp-scope",
                        "law":"ПП РФ № 1352, п. 23", "law_url":LAW_URL,
                        "note":"Для применения предела 2% категория закупки не подтверждена. "
                               + ("Официальные сведения противоречат друг другу." if sme_classification["state"]=="conflict" else "Нужно проверить извещение и условия участия."),
                        "sme_scope":sme_classification})
                continue
            scope=assessment.get("source_scope") or {}
            identity=(code,tuple(scope.get("lots") or ()),tuple(scope.get("lot_ids") or ()))
            if identity in seen:continue
            from security_episode_identity import security_scope_evidence
            scope_evidence=security_scope_evidence(p,assessment)
            if (scope.get("lots") or scope.get("lot_ids")) and not scope_evidence:
                reason="security_lot_identity_unavailable"
                assessment={**assessment,"status":"review",
                    "reasons":list(assessment.get("reasons") or [])+[reason],
                    "reason_labels":list(assessment.get("reason_labels") or [])+[SECURITY_CONTEXT_LABELS[reason]]}
                checks.append(_security_context_review(doc,match,assessment))
                continue
            seen.add(identity)
            finding.append({
                "code":code,
                "kind":"violation",
                "title":condition,"law":law,
                "law_url":LAW_URL,
                "evidence":f"{evidence} НМЦД: {price:,.2f} руб.; "
                           f"обеспечение: {value}{'%' if is_percent else ' руб.'}; "
                           + (f"отношение: {ratio:.3f}%. " if ratio is not None else "База процента не установлена. ")+
                           f"Сверено с {price_doc.get('name')}."+scope_evidence,
                "note":"Проверено по двум фрагментам сохранённых официальных оригиналов ЕИС. "
                       "Перед правоприменительным решением сверить действующую редакцию, "
                       "способ закупки и предусмотренные законом исключения.",
                "document":doc.get("name"),"source_url":source,
                "location":f"Обеспечение заявки, позиция {match.start()+1}",
                "sme_scope":sme_classification,
                "match_text":match.group(0),"source_position":match.start(),
                "security_assessment":assessment
            })
    # Coalesce repeated review hints into a single source-specific item,
    # not a multitude of warnings from several archived versions.
    unique={}
    for ck in checks:
        scope=(ck.get("security_assessment") or {}).get("source_scope") or {}
        unique.setdefault((ck.get("field_path"),ck.get("document"),ck.get("source_url"),
                           tuple(scope.get("lots") or ()),tuple(scope.get("lot_ids") or ())),ck)
    return finding,list(unique.values())[:5]

def install(ns):
    orig_analyze=ns.get("analyze")
    orig_checks=ns.get("build_checks")
    if not callable(orig_analyze) or not callable(orig_checks):
        raise RuntimeError("V336 requires existing legal analyzer and checklist")
    if getattr(orig_analyze,"__v336__",False):return
    @wraps(orig_analyze)
    def analyze(p,docs):
        old=list(orig_analyze(p,docs))
        findings,_=security_findings(p,docs)
        return old+findings
    @wraps(orig_checks)
    def build_checks(p,detail,card,docs):
        old=list(orig_checks(p,detail,card,docs))
        _,safety=security_findings(p,docs)
        return old+deeper_checks(p,docs)+safety
    analyze.__v336__=True
    build_checks.__v336__=True
    ns["analyze"]=analyze
    ns["build_checks"]=build_checks
    print("V336 LEGAL_MATRIX_READY total_categories=43 verified_price_gate=True "
          "bid_vs_contract_security=True no_guessing=True",flush=True)
