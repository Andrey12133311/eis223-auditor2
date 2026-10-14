"""223-FZ art 4(11): dates of amendments, EIS publication and bid extension.

Calendar DAYS only. A confirmed publication breach requires a verified
original decision date and separately verified official EIS publication date.
No official publication event => review-only, never an invented violation.
"""
from __future__ import annotations

import json
import re
from datetime import date,datetime
from v295_eis_originals import root

LAW="223-ФЗ, статья 4, часть 11"
LAW_URL="https://www.consultant.ru/document/cons_doc_LAW_116964/441d00be62e3224cdc0514cffaf2a26b5b40a1c7/"
DAY=r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})"
DECISION=[
    re.compile(r"дата\s+приняти[яе]\s+решени\w*\s+о\s+внесени\w*\s+изменени\w*[^.;\n]{0,70}?"+DAY,re.I),
    re.compile(r"(?:решени\w*|приказ\w*|распоряжени\w*)\s*(?:№\s*[\w/-]{1,25})?"
               r"\s*от\s+"+DAY+r"[^.;\n]{0,140}?(?:изменени\w*|корректировк\w*)",re.I),
    re.compile(r"(?:изменени\w*\s+в\s+(?:извещени\w*|документаци\w*)|"
               r"изменени\w*\s+срок\w*\s+подач\w*).{0,125}?"
               r"(?:от\s+|принят\w*\s+|утвержден\w*\s+)"
               +DAY,re.I|re.S)
]
END_DATE=re.compile(
    r"(?:дата\s+и\s+время\s+окончани\w*\s+срока\s+подач\w*\s+заяв\w*|"
    r"(?:дата\s+)?окончани\w*\s+(?:срока\s+)?подач\w*\s+заяв\w*|"
    r"подач\w*\s+заяв\w*\s+до)\s*[:\-–\s]{0,45}"+DAY,re.I)
EXPLICIT_PERIOD=re.compile(
    r"(?:положени\w*\s+о\s+закупк\w*|для\s+данного\s+способа\s+закупк\w*)"
    r".{0,140}?(?P<count>\d{1,3})\s+(?P<kind>календарн\w*|рабоч\w*)?\s*дн\w*",re.I|re.S)
AMENDMENT=re.compile(r"изменени\w*|редакци\w*|корректировк\w*|продлени\w*",re.I)
DOMAIN="zakupki.gov.ru"

def parse_day(text):
    v=str(text or "").strip()[:30]
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d.*",v):return date.fromisoformat(v[:10])
        if re.fullmatch(r"\d\d\.\d\d\.\d{4}",v):return datetime.strptime(v,"%d.%m.%Y").date()
    except ValueError:pass
    return None

def verified(doc):
    if doc.get("status")!="checked":return False
    m=doc.get("meta") or {}
    return (m.get("source") in ("official_eis_original","official_eis_archive_member")
            and bool(m.get("sha256")) and bool(m.get("source_url"))
            and len(str(doc.get("text") or "").strip())>20)

def _decision_sources(docs):
    found=[]
    for doc in docs:
        if not verified(doc):continue
        text=str(doc.get("text") or "")[:650000]
        name=doc.get("name") or ""
        # Ignore generic passages reproducing the legal deadline; look
        # explicitly for a concrete decision/order approving changes.
        for regex in DECISION:
            for match in list(regex.finditer(text))[:8]:
                raw=match.group(1)
                day=parse_day(raw)
                if not day:continue
                around=text[max(0,match.start()-170):match.end()+190]
                if not AMENDMENT.search(around):continue
                found.append({"day":day,"doc":doc,"fragment":" ".join(around.split())[:560],
                              "location":f"Оригинал «{name}», позиция {match.start()+1}"})
    return found[:20]

def _official_events(reg):
    path=root()/"eis223_official_change_events"/reg/"changes.json"
    try:
        if path.stat().st_size>100_000:return []
        data=json.loads(path.read_text(encoding="utf-8"))
        if (data.get("reg_number")!=reg or
            data.get("source")!="verified_eis_changes_tab" or
            not isinstance(data.get("events"),list)):
            return []
        events=[]
        for ev in data["events"][:100]:
            if not isinstance(ev,dict):continue
            day=parse_day(ev.get("publication_date"))
            url=str(ev.get("source_url") or "")
            if (day and url.startswith("https://"+DOMAIN+"/") and
                (ev.get("type")=="amendment") and ev.get("evidence") and
                re.search(r"изменени|редакци|корректировк",str(ev["evidence"]),re.I)):
                events.append({"day":day,"url":url,
                               "evidence":str(ev["evidence"])[:550],
                               "decision_date":parse_day(ev.get("decision_date"))})
        return events
    except (OSError,ValueError,TypeError,AttributeError):return []

def _date_data(p,docs):
    reg=str(p.get("reg") or p.get("reg_number") or "")
    decisions=_decision_sources(docs)
    published=_official_events(reg)
    return reg,decisions,published

def findings(p,docs):
    reg,decisions,publications=_date_data(p,docs)
    if not decisions or not publications:return []
    # Require an unambiguous event: one unique decision date and one
    # publication date, or matching EIS decision_date supplied in the event.
    dates={x["day"] for x in decisions}
    results=[]
    for ev in publications:
        if ev["decision_date"]:
            chosen=[x for x in decisions if x["day"]==ev["decision_date"]]
        elif len(dates)==1 and len(publications)==1:
            chosen=decisions
        else:
            chosen=[]
        if not chosen:continue
        chosen=chosen[0]
        gap=(ev["day"]-chosen["day"]).days
        if not 3<gap<3660:continue
        if ev["day"]<chosen["day"]:continue
        doc=chosen["doc"]
        results.append({
            "code":"223-4-11-AMENDMENT-PUBLICATION-LATE",
            "kind":"violation",
            "title":"Изменения документации размещены в ЕИС позднее трёх дней после решения",
            "law":LAW,"law_url":LAW_URL,
            "evidence":f"Решение: {chosen['day']}. Размещение в ЕИС: {ev['day']}. "
                       f"Интервал: {gap} календарных дней. "
                       f"Оригинал: {chosen['fragment']} | "
                       f"ЕИС: {ev['evidence']}",
            "note":"Ч. 11 ст. 4 Закона № 223-ФЗ. Обе даты подтверждены разными "
                   "источниками: решение — проверенный оригинал ЕИС, "
                   "публикация — официальная вкладка ЕИС «Изменения и разъяснения». "
                   "Сопоставьте конкретную редакцию и применимые исключения.",
            "document":doc.get("name"),
            "source_url":ev["url"],
            "location":chosen["location"]+"; вкладка ЕИС «Изменения и разъяснения»",
            "match_text":chosen["fragment"][:300]})
        break
    return results

def checks(p,docs):
    reg,decisions,publications=_date_data(p,docs)
    out=[]
    if decisions and not publications:
        x=decisions[0]
        out.append({
            "scope":LAW,
            "item":"Подтвердить дату размещения изменений во вкладке ЕИС",
            "status":"check","evidence":f"Дата решения: {x['day']}. "+x["fragment"][:850],
            "source":x["doc"].get("name"),
            "source_url":x["doc"].get("url") or "",
            "document":x["doc"].get("name"),
            "field_path":"amendment-publication-date",
            "note":"Нет подтверждённой даты публикации ЕИС. Утверждать нарушение "
                   "трёхдневного срока нельзя. Проверить вкладку ЕИС.",
            "law":LAW,"law_url":LAW_URL,"location":x["location"]})
    elif decisions and publications and not findings(p,docs):
        first=decisions[0];ev=publications[0]
        if (first["day"]!=ev.get("decision_date") and
            (len({x["day"] for x in decisions})>1 or len(publications)>1)):
            out.append({
                "scope":LAW,"item":"Сопоставить редакции изменений и даты публикаций",
                "status":"check",
                "evidence":f"Решение: {first['day']}, публикация: {ev['day']}",
                "source":"Оригинал + официальная вкладка ЕИС",
                "source_url":ev["url"],"document":first["doc"].get("name"),
                "field_path":"amendment-revisions",
                "note":"Есть несколько редакций или дат. Без однозначного соответствия "
                       "конкретных решений и публикаций нарушение не подтверждено.",
                "law":LAW,"law_url":LAW_URL,"location":first["location"]})
    # Half of the bid period: the applicable procurement regulation period
    # must be known. Do NOT calculate from arbitrary two dates or assume 15d.
    if publications:
        period=None
        for doc in docs:
            if not verified(doc):continue
            value=EXPLICIT_PERIOD.search(str(doc.get("text") or "")[:650000])
            if value:
                n=int(value.group("count"))
                if 3<=n<=180 and not value.group("kind"):period=n
                elif 3<=n<=180 and value.group("kind") and value.group("kind").lower().startswith("календар"):period=n
                if period:break
        if period:
            end=parse_day(p.get("submission_end"))
            if end:
                for ev in publications:
                    remaining=(end-ev["day"]).days
                    if remaining<period/2:
                        out.append({
                          "scope":LAW,
                          "item":"Срок подачи заявок после изменений менее половины нормативного",
                          "status":"check", # regulation and time-window must be reviewed
                          "evidence":f"ЕИС: {ev['day']}; срок окончания: {end}; "
                                     f"осталось дней: {remaining}; исходный срок по тексту: {period}",
                          "source":"ЕИС + закупочная документация",
                          "source_url":ev["url"],"document":None,
                          "field_path":"amendment-bid-extension",
                          "note":"Проверить положение о закупке, способ закупки, "
                                 "редакцию извещения и календарные сроки. "
                                 "Несоблюдение половины срока требует подтверждения.",
                          "law":LAW,"law_url":LAW_URL,"location":"Изменения ЕИС"})
        else:
            out.append({
              "scope":LAW,"item":"Сверить продление срока заявок с положением о закупке",
              "status":"check",
              "evidence":f"Размещены изменения в ЕИС: {publications[0]['day']}",
              "source":"ЕИС", "source_url":publications[0]["url"],
              "document":None,"field_path":"amendment-bid-extension",
              "note":"Не найден подтверждённый первоначальный срок, установленный "
                     "положением о закупке для данного способа. Без него "
                     "нарушение половины срока не устанавливается.",
              "law":LAW,"law_url":LAW_URL,"location":"Изменения ЕИС"})
    return out

def install(ns):
    from functools import wraps
    a=ns.get("analyze");b=ns.get("build_checks")
    if not callable(a) or not callable(b):raise RuntimeError("Missing legal analyzer")
    if getattr(a,"__v330__",False):return
    @wraps(a)
    def analyze(p,docs):
        previous=list(a(p,docs))
        fresh=findings(p,docs)
        previous.extend(fresh)
        if str(p.get("reg"))=="32616233661":
            print(f"V330 DATE_CHECK reg={p.get('reg')} "
                  f"verified_decisions={len(_decision_sources(docs))} "
                  f"official_publications={len(_official_events(str(p.get('reg'))))} "
                  f"confirmed_breaches={len(fresh)}",flush=True)
        return previous
    @wraps(b)
    def build_checks(p,detail,card,docs):
        return [*b(p,detail,card,docs),*checks(p,docs)]
    analyze.__v330__=True
    ns["analyze"]=analyze
    ns["build_checks"]=build_checks
    print("V330 ART4_11_READY confirmed_only_cross_source=True half_term_review=True",flush=True)
