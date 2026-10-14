"""V336: evidence-led expanded legal examination of all readable 223-FZ documents.

Only a validated explicit bid-security clause plus a reliable matching price
can yield an automatic violation. All discretionary issues remain review items.
No remote calls or deletions, and no automatic claim of universal legality.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from functools import wraps

from v331_broad_legal import verified
from v335_price_recovery import money

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
BID_CLAUSE=re.compile(
 r"(?:размер\s+)?обеспечени\w*\s+заявк\w*"
 r"(?:\s+на\s+участие\s+в\s+закупк\w*)?"
 r"[^\n.;]{0,88}?"
 r"(?P<amount>\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?"
 r"|\d{4,15}(?:[,.]\d{1,2})?"
 r"|\d{1,2}(?:[,.]\d{1,3})?)"
 r"\s*(?P<unit>%|процент(?:а|ов)?|руб(?:лей|ля|\.|\b)|₽)",re.I)
# Only accept actual money and percentage with units, never paragraph numbering.
MONEY_CLAUSE=re.compile(
 r"(?:начальн\w*\s*\(\s*максимальн\w*\s*\)\s*цен\w*\s*договора|"
 r"начальн\w*\s+максимальн\w*\s+цен\w*\s+договора|нмцд)"
 r"[\s:—–-]{0,32}(?P<amount>\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?|\d{4,15}(?:[,.]\d{1,2})?)"
 r"\s*(?:руб(?:лей|ля|\.|\b)|₽)",re.I)
CONTRACT_LINE=re.compile(r"обеспечени\w*\s+(?:исполнени\w*\s+)?(?:договора|контракта)",re.I)
COMPETITIVE=re.compile(r"конкурс|аукцион|запрос\s+(?:котировок|предложений)|конкурентн\w*\s+закупк",re.I)

def _decimal(raw):
    s=str(raw or "").replace("\xa0","").replace(" ","").replace("%","").replace(",",".")
    try:
        x=Decimal(s)
        return x if x.is_finite() and x>=0 else None
    except (InvalidOperation,ValueError):
        return None

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
    """Use only a price backed by the same hash-verified original being audited.

    The DB price by itself is NOT sufficient proof to establish a bid-security
    violation; title/card values may be stale or not source-attributed.
    """
    expected=money(p.get("price_num"))
    if not expected or expected[1]<=0:return None
    expected_value=Decimal(str(expected[1]))
    for d in docs[:MAX_DOCS]:
        if not isinstance(d,dict) or not verified(d):continue
        for match in MONEY_CLAUSE.finditer(str(d.get("text") or "")[:MAX_TEXT]):
            observed=_decimal(match.group("amount"))
            if observed is not None and observed==expected_value:
                return (observed,d,match)
    return None

def security_findings(p,docs):
    """Very narrow checks under parts 25–27 of article 3.2.

    No inference from performance security; no automatic infringement without
    affirmative bid-security clause and certified NMCD corroboration.
    """
    finding=[]
    checks=[]
    if not COMPETITIVE.search(str(p.get("method") or "")):
        return finding,checks
    verified_nmcd=verified_price(p,docs)
    for doc in docs[:MAX_DOCS]:
        if not isinstance(doc,dict) or not verified(doc):continue
        body=str(doc.get("text") or "")[:MAX_TEXT].replace("\xa0"," ")
        if not re.search(r"обеспечени\w*\s+заявк",body,re.I):continue
        for match in list(BID_CLAUSE.finditer(body))[:8]:
            segment=body[max(0,match.start()-95):min(len(body),match.end()+125)]
            if CONTRACT_LINE.search(segment[:95]) and not re.search(
                    r"обеспечени\w*\s+заявк",match.group(0),re.I):continue
            if NEG.search(segment):continue
            unit=(match.group("unit") or "").strip().lower()
            amount=_decimal(match.group("amount"))
            if amount is None or not unit or amount<=0:continue
            # Non-monetary shorthand and accidental narrative percentages do
            # not pass; require explicit units.
            if unit.startswith("руб") or unit=="₽":
                value=amount
                is_percent=False
            elif unit.startswith("%") or unit.startswith("процент"):
                value=amount
                is_percent=True
            else:continue
            if value>Decimal("100") and is_percent:continue
            evidence=" ".join(segment.split())
            source=(doc.get("meta") or {}).get("source_url") or doc.get("url") or ""
            if verified_nmcd is None:
                checks.append({
                    "scope":"223-ФЗ / обеспечение заявки",
                    "item":"Проверить ограничение обеспечения заявки относительно НМЦД",
                    "status":"check","evidence":evidence,
                    "source":doc.get("name"),"source_url":source,"document":doc.get("name"),
                    "field_path":"v336-bid-security-needs-verified-nmcd",
                    "law":"223-ФЗ, ст. 3.2 ч. 25–27","law_url":LAW_URL,
                    "location":f"Оригинал ЕИС, позиция {match.start()+1}",
                    "note":"Не подтверждено совпадение числовой НМЦД с официальным оригиналом. "
                           "Не квалифицировать условие как нарушение до установления цены."
                })
                continue
            price,price_doc,_=verified_nmcd
            ratio=value if is_percent else value/price*Decimal("100")
            condition=None
            if price<=Decimal("5000000"):
                condition="Обеспечение заявки предусмотрено при НМЦД не более 5 млн рублей"
            elif ratio>Decimal("5"):
                condition="Размер обеспечения заявки превышает 5% НМЦД"
            if not condition:continue
            finding.append({
                "code":"223-3.2-27-BID-SECURITY-LOW-NMCD" if price<=Decimal("5000000")
                       else "223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT",
                "kind":"violation",
                "title":condition,"law":"223-ФЗ, ст. 3.2 ч. 27",
                "law_url":LAW_URL,
                "evidence":f"{evidence} НМЦД: {price:,.2f} руб.; "
                           f"обеспечение: {value}{'%' if is_percent else ' руб.'}; "
                           f"отношение: {ratio:.3f}%. "
                           f"Сверено с {price_doc.get('name')}.",
                "note":"Проверено по двум фрагментам сохранённых официальных оригиналов ЕИС. "
                       "Перед правоприменительным решением сверить действующую редакцию, "
                       "способ закупки и предусмотренные законом исключения.",
                "document":doc.get("name"),"source_url":source,
                "location":f"Обеспечение заявки, позиция {match.start()+1}",
                "match_text":match.group(0)
            })
            return finding,checks
    # Coalesce repeated review hints into a single source-specific item,
    # not a multitude of warnings from several archived versions.
    unique={}
    for ck in checks:
        unique.setdefault((ck.get("field_path"),ck.get("document")),ck)
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
