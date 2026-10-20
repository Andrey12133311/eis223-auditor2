"""Regression suite; never touches Railway data, counters, or sources."""
from __future__ import annotations
from v324_fas_practice import _cases,_admission_cases,install,PRACTICE
from v324_official_price import verified_nmcd,prices_in_text
from v298_eis_only import verified_xml_nmcd
from xml.etree import ElementTree as ET

REG="32616324151"
METHOD="Конкурс в электронной форме, участниками которого могут быть только субъекты малого и среднего предпринимательства"
ORIGINAL_URL=("https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/"
              "file.html?uid=0123456789ABCDEF0123456789ABCDEF")

def doc(body,source="official_eis_original",name="Раздел 1 Документация.pdf"):
    return {"name":name,"type":"documentation","status":"checked","text":body,
            "meta":{"source":source,"sha256":"a"*64,"source_url":ORIGINAL_URL}}

def run():
    assert len(PRACTICE)>=4 and all(r["url"].startswith("https://") for r in PRACTICE)
    existence=(
        "1.3.6. Члены объединений, являющиеся коллективными участниками "
        "закупки, должны иметь соглашение (договор или иной документ) "
        "между собой, соответствующее нормам Гражданского кодекса Российской Федерации.")
    assert (_cases({"method":METHOD},[doc(existence)])[0]["classification"]
            =="review"),"Possession-only agreement must not be counted as legal violation"
    mandatory=(
        "1.3.6. Коллективные участники закупки обязаны представить в составе "
        "заявки на участие в конкурсе соглашение между всеми членами объединения.")
    admitted=(
        "1.3.1. В конкурсе могут принять участие только субъекты малого "
        "и среднего предпринимательства из числа юридических лиц или "
        "нескольких юридических лиц, выступающих на стороне одного "
        "участника закупки, независимо от организационно-правовой формы."
    )
    excluded=(
        "1.3.1. В конкурсе не могут принять участие коллективные "
        "участники, в том числе несколько юридических лиц, выступающих "
        "на стороне одного участника закупки. Участниками могут быть "
        "только субъекты малого и среднего предпринимательства."
    )
    assert len(_admission_cases({"method":METHOD},[doc(admitted)]))==1
    # Production PDF extractor returns pages as long single paragraphs;
    # section 1.3.1 must remain detectable after thousands of characters.
    page=("Сведения о конкурсной документации и планах закупки. "*85)+admitted+(
        " Порядок работы комиссии и окончание приёма заявок. "*85)
    assert len(page)>2400
    assert _admission_cases({"method":METHOD},[doc(page)]), "Long PDF page must not suppress section 1.3.1"
    assert not _admission_cases({"method":METHOD},[doc(excluded)])
    assert not _admission_cases({"method":"Открытый конкурс"},[doc(admitted.replace(
        "только субъекты малого и среднего предпринимательства",
        "любые юридические лица"))])
    assert not _admission_cases({"method":METHOD},[doc(admitted,source="unverified_mirror")])
    cases=_cases({"method":METHOD},[doc(mandatory)])
    assert cases and cases[0]["classification"]=="violation",cases
    assert not _cases({"method":METHOD},[doc("Начальная цена договора 394 633,33 рублей")])
    assert not _cases({"method":METHOD},[doc(mandatory,source="unverified_mirror")])
    unknown=_cases({"method":"Открытый конкурс в электронной форме"},[doc(existence)])
    assert unknown and unknown[0]["classification"]=="needs_scope"
    # Only 1 issue per procurement even when the exact same clause appears
    # in 4 archive revisions.
    duplicates=_cases({"method":METHOD},[doc(mandatory,name=f"Правила {i}.pdf") for i in range(4)])
    assert len(duplicates)==1,duplicates
    price=verified_nmcd([doc("Начальная (максимальная) цена договора: 394 633,33 ₽",
                              name="Извещение.pdf")])
    assert price.get("price_num")==394633.33,price
    excel=verified_nmcd([doc("Начальная (максимальная) цена договора, руб. | 394633.3333333333",
                             name="Раздел 6 - НМЦД.xls")])
    assert excel.get("price_num")==394633.33,excel
    written=verified_nmcd([doc("| Сведения о начальной (максимальной) цене договора | "
                               "394 633 (Триста девяносто четыре тысячи "
                               "шестьсот тридцать три) рубля 33 копейки, в том числе НДС.",
                               name="Раздел 2 - Информационная карта.docx")])
    assert written.get("price_num")==394633.33,written
    invalid=verified_nmcd([doc("РАЗДЕЛ 6. «ОБОСНОВАНИЕ НАЧАЛЬНОЙ "
                               "(МАКСИМАЛЬНОЙ) ЦЕНЫ ДОГОВОРА» 23 "
                               "когда начальная (максимальная) цена договора "
                               "не превышает 1 млн. рублей",
                               name="Раздел 1 - Документация.pdf")])
    assert not invalid.get("price_num"),invalid
    same=verified_nmcd([
        doc("Начальная (максимальная) цена договора, руб. | 394633.3333333333",
            name="Раздел 6 - НМЦД.xls"),
        doc("| Сведения о начальной (максимальной) цене договора | "
            "394 633 (Триста девяносто четыре тысячи "
            "шестьсот тридцать три) рубля 33 копейки",
            name="Раздел 2 - Информационная карта.docx")
    ])
    assert same.get("price_num")==394633.33 and not same.get("price_conflict"),same
    assert not verified_nmcd([doc("Цена победителя: 237 700,00 ₽",
                                name="Итоговый протокол.pdf")]).get("price")
    conflict=verified_nmcd([
        doc("НМЦД 394 633,33 руб.",name="Извещение.pdf"),
        doc("НМЦД 250 000,00 руб.",name="Раздел документации.pdf"),
    ])
    assert conflict.get("price_conflict") and not conflict.get("price"),conflict
    assert verified_nmcd([doc("НМЦД 394 633,33 руб.",
                               source="unknown_other_source")])=={}
    xml=ET.fromstring("<purchaseNoticeData><lots><lot><lotData><initialSum>"
                      "394633.33</initialSum></lotData></lot></lots>"
                      "</purchaseNoticeData>")
    assert verified_xml_nmcd(xml)=="394633.33"
    conflicting=ET.fromstring("<purchaseNoticeData><initialSum>10.00</initialSum>"
                               "<initialSum>20.00</initialSum></purchaseNoticeData>")
    assert verified_xml_nmcd(conflicting)==""
    # Old parser must remain unmodified; wrapper dispatches actual findings
    # only when explicit vetted evidence exists.
    ns={"analyze":lambda p,docs:[],
        "build_checks":lambda p,detail,card,docs:[]}
    install(ns)
    p={"reg":REG,"method":METHOD}
    assert len([f for f in ns["analyze"](p,[doc(mandatory)]) if f.get("kind")=="violation"])==1
    assert not ns["analyze"](p,[doc(existence)])
    fa=ns["analyze"](p,[doc(admitted)])
    assert len(fa)==1 and fa[0]["kind"]=="violation",fa
    assert "ФАС" in fa[0]["title"] and "практика неоднородна" in fa[0]["note"].lower()
    assert fa[0]["document"]=="Раздел 1 Документация.pdf"
    assert fa[0]["source_url"]==ORIGINAL_URL
    assert len(ns["analyze"](p,[doc(admitted)]*4))==1
    assert ns["analyze"](p,[doc(excluded)])==[]
    assert ns["analyze"](p,[doc(admitted,source="unverified_mirror")])==[]
    assert ns["analyze"]({"reg":"32616324151","method":"Открытый конкурс"},[doc(
        admitted.replace("только субъекты малого и среднего предпринимательства","любые юридические лица"))])==[]
    assert ns["build_checks"](p,{}, {},[doc(admitted)])==[]
    assert len(ns["build_checks"](p,{}, {},[doc(existence)]))==1
    assert not ns["build_checks"](p,{}, {},[doc(mandatory)])
    print("V328_FAS_PRACTICE_SELFTEST_OK official_FAS_case=True "
          "SME_explicit=True agreement_review=True submit_violation=True "
          "unverified_rejected=True no_duplicate_counts=True NMCD_394633_33=True "
          "conflict_rejected=True collective_admission=True",flush=True)

if __name__=="__main__":
    run()
