"""Regression suite; never touches Railway data, counters, or sources."""
from __future__ import annotations
from v324_fas_practice import _cases,install,PRACTICE
from v324_official_price import verified_nmcd,prices_in_text

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
    assert not verified_nmcd([doc("Цена победителя: 237 700,00 ₽",
                                name="Итоговый протокол.pdf")]).get("price")
    conflict=verified_nmcd([
        doc("НМЦД 394 633,33 руб.",name="Извещение.pdf"),
        doc("НМЦД 250 000,00 руб.",name="Раздел документации.pdf"),
    ])
    assert conflict.get("price_conflict") and not conflict.get("price"),conflict
    assert verified_nmcd([doc("НМЦД 394 633,33 руб.",
                               source="unknown_other_source")])=={}
    # Old parser must remain unmodified; wrapper dispatches actual findings
    # only when explicit vetted evidence exists.
    ns={"analyze":lambda p,docs:[],
        "build_checks":lambda p,detail,card,docs:[]}
    install(ns)
    p={"reg":REG,"method":METHOD}
    assert len([f for f in ns["analyze"](p,[doc(mandatory)]) if f.get("kind")=="violation"])==1
    assert not ns["analyze"](p,[doc(existence)])
    assert len(ns["build_checks"](p,{}, {},[doc(existence)]))==1
    assert not ns["build_checks"](p,{}, {},[doc(mandatory)])
    print("V324_FAS_PRACTICE_SELFTEST_OK official_practice=4 "
          "SME_explicit=True agreement_review=True submit_violation=True "
          "unverified_rejected=True no_duplicate_counts=True NMCD_394633_33=True "
          "conflict_rejected=True",flush=True)

if __name__=="__main__":
    run()
