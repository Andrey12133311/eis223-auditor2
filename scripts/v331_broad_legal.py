"""Comprehensive evidence-based legal issue review for locally held 223-FZ originals.

This does not purport to automate every legally contested question: any issue
which needs a procurement regulation, product classification, EIS timestamp or
an exception is explicitly marked "Требует проверки".  Broaden *coverage* of
every available checked document without inventing confirmed infringements.
"""
from __future__ import annotations
import re
from functools import wraps
from v295_eis_originals import uid_from_url

URL="https://www.consultant.ru/document/cons_doc_LAW_116964/"
LAW3="223-ФЗ, ст. 3 ч. 1, ч. 6; ст. 2"
LAW4="223-ФЗ, ст. 4 ч. 8–12"
LAW32="223-ФЗ, ст. 3.2"
LAW34="223-ФЗ, ст. 3.4 ч. 19.1–19.3"
MAX_DOCS=500
MAX_CHECKS_PER_PURCHASE=90
MAX_CHECKS_PER_CATEGORY=3

# A catalog with explicit evidence, law, context and legal qualification. Only
# narrow positive tests (not generic keywords) produce review candidates.
CATALOG=(
    ("competitive","Ограничение допуска к участию","запрет конкурентных ограничений",
     LAW3,[
        r"заявк\w*.{0,100}?будут\s+рассмотрен\w*\s+только\s+от\s+"
        r"(?:производител\w*|дилер\w*|дистрибьютор\w*)",
        r"(?:поставщик|участник)\s+обязан\s+иметь\s+(?:офис|склад|представительств\w*)"
        r".{0,80}?(?:на\s+территории|в\s+городе|в\s+регионе)",
        r"к\s+участию\s+допускают\w*\s+только\s+"
        r"(?:региональн\w*|местн\w*|постоянн\w*)\s+(?:компани\w*|поставщик\w*)",
    ]),
    ("exclusive","Требование авторизации от производителя","возможное ограничение конкуренции",
     LAW3,[
        r"(?:участник\w*|поставщик\w*).{0,120}?(?:обязан\w*|должен|необходим\w*).{0,95}?"
        r"(?:авторизационн\w*\s+письм\w*|письм\w*\s+производител\w*|дилерск\w*\s+сертификат\w*)",
        r"(?:представ\w*|предостав\w*)\s+в\s+составе\s+заявк\w*.{0,110}?"
        r"(?:сертификат\w*\s+дилер\w*|авторизационн\w*\s+письм\w*)",
    ]),
    ("experience","Несоразмерное требование к опыту","необходимость проверки положения о закупке",
     LAW3,[
        r"(?:договор\w*|контракт\w*).{0,80}?"
        r"(?:не\s+менее\s+\d+|за\s+последни\w*\s+\d+\s+лет).{0,80}?"
        r"(?:в\s+качестве\s+опыт\w*|опыт\w*\s+работ)",
        r"опыт\s+исполнени\w*.{0,160}?"
        r"(?:исключительно|только)\s+(?:государственн\w*|муниципальн\w*)",
    ]),
    ("staff","Требование штатных работников или ресурсов","проверка объективной необходимости",
     LAW3,[
        r"наличи\w*\s+(?:именно\s+)?в\s+штате.{0,135}?"
        r"(?:специалист\w*|сотрудник\w*|работник\w*)",
        r"обязан\w*\s+иметь\s+собственн\w*\s+"
        r"(?:транспорт\w*|строительн\w*\s+техник\w*|оборудовани\w*)",
    ]),
    ("single_brand","Указание производителя или товарного знака","эквивалентность товара по документации",
     "223-ФЗ, ст. 3 ч. 1 п. 2; ст. 4 ч. 6.1",[
        r"(?:только|исключительно)\s+(?:торгов\w*\s+марк\w*|товарн\w*\s+знак\w*|бренд\w*)"
        r".{0,110}?без\s+(?:эквивалент\w*|аналоги\w*)",
        r"поставка\s+эквивалент\w*\s+не\s+допускается",
    ]),
    ("application","Дополнительные документы в составе заявки","исчерпывающий перечень для МСП, пропорциональность для остальных",
     LAW34,[
        r"участник.{0,90}?представля\w*\s+в\s+составе\s+заявк\w*.{0,95}?"
        r"(?:нотариально\s+заверен\w*|платн\w*\s+выписк\w*|оригинал\w*\s+документ\w*)",
        r"в\s+составе\s+заявки\s+(?:должн\w*\s+быть\s+)?"
        r"(?:обязательн\w*\s+)?(?:предоставлен\w*|представлен\w*).{0,120}?"
        r"(?:выписк\w*\s+из\s+егрюл|выписк\w*\s+из\s+егрип)",
    ]),
    ("collective","Объединение участников и ответственность","соотношение ст. 3 ч. 5 и ст. 3.4",
     LAW34,[
        r"коллективн\w*\s+участник\w*.{0,170}?"
        r"(?:солидарн\w*\s+ответственност\w*|договор\w*\s+объединени\w*)",
        r"нескольк\w*\s+юридическ\w*\s+лиц.{0,170}?"
        r"(?:заключить|предоставить|представить)\s+соглашени\w*",
    ]),
    ("bid_guarantee","Обеспечение заявки","сопоставить НМЦД, МСП, способ и требования",
     "223-ФЗ, ст. 3.2 ч. 25–27; ст. 3.4",[
        r"размер\s+обеспечени\w*\s+заявк\w*.{0,110}?"
        r"(?:\d+[,.]?\d*\s*%|\d[\d ]{2,}\s*(?:руб|₽))",
        r"обеспечени\w*\s+заявк\w*.{0,75}?"
        r"(?:блокировк\w*\s+денежн\w*|банковск\w*\s+гаранти\w*)",
    ]),
    ("contract_guarantee","Обеспечение договора","не смешивать с обеспечением заявки",
     "223-ФЗ, ст. 3.2; положение о закупке",[
        r"обеспечени\w*\s+(?:исполнени\w*\s+)?договора.{0,120}?"
        r"(?:\d+[,.]?\d*\s*%|банковск\w*\s+гаранти\w*)",
    ]),
    ("payment","Порядок оплаты по договору","проверка исключений и сроков оплаты",
     "223-ФЗ, ст. 3 ч. 5.3; положение о закупке",[
        r"(?:оплат\w*|расчет\w*).{0,130}?(?:в\s+течени\w*|не\s+позднее)"
        r".{0,45}?(?:[1-9]\d|[2-9])\s+рабоч\w*\s+дн",
        r"отсрочк\w*\s+платеж\w*.{0,60}?"
        r"(?:[1-9]\d|[2-9])\s+(?:рабоч\w*|календарн\w*)\s+дн",
    ]),
    ("deadline","Срок подачи и изменения документации","начало/окончание только по датам",
     LAW4,[
        r"(?:(?:окончани\w*\s+срока|дата\s+окончани\w*)\s+подач\w*\s+заявк\w*)"
        r".{0,100}?(?:[0-3]?\d[.][01]?\d[.]20\d{2}|20\d{2}-[01]\d-[0-3]\d)",
        r"(?:дата\s+подачи\s+заявок\s+продлен\w*|"
        r"срок\s+подачи\s+заявок\s+перенесен\w*)",
    ]),
    ("clarification","Разъяснения документации","запрос, размещение ответа и запрет изменения предмета",
     "223-ФЗ, ст. 3.2 ч. 2–4",[
        r"(?:запрос\s+разъяснени\w*|ответ\s+на\s+запрос).{0,120}?"
        r"(?:[0-3]?\d[.][01]?\d[.]20\d{2}|20\d{2}-[01]\d-[0-3]\d)",
    ]),
    ("protocol","Протокол комиссии","полнота содержания и сроки публикации",
     "223-ФЗ, ст. 3.2 ч. 13–14; ст. 4 ч. 12",[
        r"(?:протокол\s+рассмотрени\w*|протокол\s+подведени\w*\s+итогов).{0,140}?"
        r"(?:[0-3]?\d[.][01]?\d[.]20\d{2}|20\d{2}-[01]\d-[0-3]\d)",
    ]),
    ("cancellation","Отмена конкурентной закупки","сроки и основания отмены",
     "223-ФЗ, ст. 3.2 ч. 5–7",[
        r"решени\w*\s+об\s+отмене\s+(?:конкурс\w*|закупк\w*).{0,130}?"
        r"(?:[0-3]?\d[.][01]?\d[.]20\d{2}|20\d{2}-[01]\d-[0-3]\d)",
    ]),
    ("national","Национальный режим","сопоставить коды ОКПД2, режим, исключения",
     "223-ФЗ, ст. 3.1-4; постановление Правительства РФ № 1875",[
        r"(?:запрет|ограничени\w*)\s+допуск\w*\s+товаров?\s+"
        r"иностранн\w*\s+происхождени\w*",
        r"заявк\w*\s+с\s+предложени\w*\s+о\s+поставк\w*\s+"
        r"иностранн\w*\s+товар\w*.{0,90}?(?:приравнив\w*|отклон\w*)",
    ]),
    ("licenses","Лицензии и обязательное членство","сопоставить предмет договора и законные основания",
     LAW3,[
        r"(?:в\s+составе\s+заявк\w*|участник\w*\s+должн\w*)"
        r".{0,90}?(?:лицензи\w*|свидетельств\w*\s+сро|допуск\w*\s+сро)",
    ]),
    ("quality","Технические характеристики","отсутствие дискриминации и эквивалентность",
     "223-ФЗ, ст. 3; ст. 4 ч. 6.1",[
        r"производител\w*\s+товар\w*.{0,90}?не\s+подлежит\s+замене",
        r"(?:показател\w*|характеристик\w*)\s+товар\w*"
        r".{0,90}?точн\w*\s+значени\w*\s+без\s+допуск\w*",
    ]),
    ("subcontract","Запрет субподряда","проверить связь с предметом закупки",
     LAW3,[
        r"(?:не\s+допуска\w*|запреща\w*)\s+(?:привлечени\w*|использовани\w*)"
        r".{0,80}?(?:субподрядчик\w*|соисполнител\w*)",
    ]),
    ("evaluation","Оценка и сопоставление заявок","критерии, веса, предсказуемость",
     "223-ФЗ, ст. 3.2 ч. 6–7; ст. 4 ч. 10",[
        r"критери\w*\s+оценк\w*.{0,100}?на\s+усмотрени\w*\s+комисси\w*",
        r"комисси\w*\s+вправе\s+измен\w*\s+(?:значимост\w*|порядок\s+оценк\w*)",
    ]),
    ("signatures","Электронная подпись и бумажная форма","проверка соразмерности цифровым закупкам",
     "223-ФЗ, ст. 3.3; ст. 3.4",[
        r"одновременно\s+с\s+электронн\w*\s+заявк\w*.{0,140}?"
        r"(?:бумажн\w*\s+оригинал\w*|лично\s+предостав\w*)",
    ]),
    ("anti_dumping","Антидемпинговые меры","законность дополнительного обеспечения",
     "223-ФЗ, ст. 3; положение о закупке",[
        r"при\s+снижени\w*\s+цен\w*.{0,100}?"
        r"(?:увеличива\w*|повыша\w*)\s+обеспечени\w*",
    ]),
)

COMPILED=tuple((code,title,desc,law,tuple(re.compile(p,re.I|re.S) for p in patterns))
               for code,title,desc,law,patterns in CATALOG)
NEGATE=re.compile(r"(?i)(?:не\s+требуется|не\s+установлено|"
                  r"не\s+предусмотрено|отсутствует\s+обязанность|"
                  r"не\s+обязан\w*|требовани\w*\s+не\s+предъявляются)")

def verified(doc):
    if doc.get("status")!="checked":return False
    m=doc.get("meta") or {}
    return bool(m.get("source") in ("official_eis_original","official_eis_archive_member")
                and re.fullmatch(r"[0-9a-f]{64}",str(m.get("sha256") or ""))
                and uid_from_url(m.get("source_url") or ""))

def category_checks(p,docs):
    results=[]
    counts={}
    seen=set()
    for d in docs[:MAX_DOCS]:
        if d.get("status")!="checked" or not str(d.get("text") or "").strip():continue
        original=verified(d)
        body=str(d.get("text") or "")[:650000].replace("\xa0"," ")
        for code,title,desc,law,patterns in COMPILED:
            if counts.get(code,0)>=MAX_CHECKS_PER_CATEGORY:continue
            for pattern in patterns:
                match=pattern.search(body)
                if not match:continue
                context=" ".join(body[max(0,match.start()-160):min(len(body),match.end()+230)].split())[:650]
                if not context:continue
                if NEGATE.search(context[max(0,160-55):min(len(context),160+130)]):
                    continue
                signature=(code,re.sub(r"\W+","",match.group(0).casefold())[:100])
                if signature in seen:continue
                seen.add(signature)
                counts[code]=counts.get(code,0)+1
                results.append({
                    "scope":"223-ФЗ / расширенная юридическая проверка",
                    "item":title,
                    "status":"check",
                    "evidence":context,
                    "source":d.get("name"),
                    "source_url":(d.get("meta") or {}).get("source_url") or d.get("url"),
                    "document":d.get("name"),
                    "field_path":code,
                    "law":law,
                    "law_url":URL,
                    "location":f"Оригинал: {d.get('name')}, текст ~{match.start()+1}",
                    "note":(f"{desc}. Найден конкретный фрагмент в "
                            f"{'проверенном оригинале ЕИС' if original else 'доступном локальном документе, происхождение которого не подтверждено'}. "
                            "Требуется оценить исключения, редакцию положения о закупке и "
                            "обстоятельства. Одного совпадения текста недостаточно для "
                            "установленного нарушения.")
                })
                break
            if len(results)>=MAX_CHECKS_PER_PURCHASE:return results
    return results

def missing_checks(p,docs):
    # These cannot be established as infringements merely by failing to
    # extract a keyword: explicitly disclose unchecked areas.
    result=[]
    trusted=[d for d in docs if verified(d)]
    missing=[
        ("Извещение","notice",r"(?:извещени\w*|информаци\w*\s+о\s+закупк\w*)",
         "223-ФЗ, ст. 4 ч. 9"),
        ("Документация и техническое задание","documentation",
         r"(?:документаци\w*|техническ\w*\s+задани\w*)","223-ФЗ, ст. 4 ч. 10"),
        ("Положение о закупке","regulation",r"положени\w*\s+о\s+закупк\w*",
         "223-ФЗ, ст. 2"),
    ]
    allnames=" ".join(str(d.get("name") or "") for d in docs)
    for label,key,pattern,law in missing:
        if re.search(pattern,allnames,re.I):continue
        result.append({
            "scope":"223-ФЗ / полнота исходных данных",
            "item":"Не получен отдельный документ: "+label,
            "status":"check",
            "evidence":"Не найден среди доступных локальных файлов ЕИС. "
                       "Отсутствие документа в выгрузке не доказывает отсутствие публикации.",
            "source":"Локальное хранилище","source_url":p.get("url") or "",
            "document":None,"field_path":"source-missing-"+key,
            "law":law,"law_url":URL,"location":"",
            "note":"Правовая оценка будет неполной до получения источника."
        })
    if not trusted:
        result.append({
            "scope":"223-ФЗ / подлинность источников",
            "item":"Документы доступны, но официальное происхождение не подтверждено",
            "status":"check",
            "evidence":"Юридические индикаторы показаны, установленные нарушения не утверждаются.",
            "source":"Локальные файлы","source_url":p.get("url") or "",
            "document":None,"field_path":"source-unverified",
            "law":"223-ФЗ, ст. 3–4","law_url":URL,"location":"",
            "note":"Проверка по доступному тексту не равна подтверждению подлинного оригинала ЕИС."
        })
    return result

def install(ns):
    old=ns.get("build_checks")
    if not callable(old):raise RuntimeError("Missing base checklist")
    if getattr(old,"__v331_broad__",False):return
    @wraps(old)
    def build_checks(p,detail,card,docs):
        output=list(old(p,detail,card,docs))
        extra=category_checks(p,docs)
        output.extend(extra)
        output.extend(missing_checks(p,docs))
        return output
    build_checks.__v331_broad__=True
    ns["build_checks"]=build_checks
    print(f"V331_BROAD_LEGAL_READY categories={len(CATALOG)} "
          "source_only=True automatic_violations=0 independent_review=True",flush=True)
