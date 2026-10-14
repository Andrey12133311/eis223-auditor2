"""Regression tests for deeper 223-FZ coverage without false qualifications."""
from v336_legal_matrix import (
    CATEGORIES, BID_CLAUSE, MONEY_CLAUSE, deeper_checks,
    security_findings, verified_price,
)
UID="0123456789ABCDEF0123456789ABCDEF"
SRC=("https://zakupki.gov.ru/223/filestore/public/1.0/"
     "download/fz223/file.html?uid="+UID)

def original(text,name="Документация.docx",trusted=True):
    return {"name":name,"text":text,"status":"checked",
            "meta":{"source":"official_eis_original" if trusted else "outside_reference",
                    "sha256":"a"*64,"source_url":SRC}}

def run():
    assert len(CATEGORIES)>=20
    assert len({x[0] for x in CATEGORIES})==len(CATEGORIES)
    common={"reg":"32616233661","method":"Аукцион в электронной форме",
            "price_num":4000000,"price":"4 000 000 руб"}
    price=original("Начальная (максимальная) цена договора: 4 000 000 рублей.",
                   "Извещение.docx")
    bid=original("Обеспечение заявки устанавливается в размере 100 000 рублей.")
    found,checks=security_findings(common,[price,bid])
    assert len(found)==1,(found,checks)
    assert "не более 5 млн" in found[0]["title"]
    assert found[0]["document"]=="Документация.docx"
    assert found[0]["kind"]=="violation"
    no_bidding=original("Обеспечение исполнения договора в размере 150 000 рублей.")
    assert security_findings(common,[price,no_bidding])[0]==[]
    neg=original("Обеспечение заявки не требуется. Обеспечение исполнения договора 100 000 рублей.")
    assert security_findings(common,[price,neg])[0]==[]
    without_receipt=original(bid["text"],trusted=False)
    assert security_findings(common,[price,without_receipt])[0]==[]
    wrong_price={**common,"price_num":1900000}
    assert verified_price(wrong_price,[price]) is None
    assert security_findings(wrong_price,[price,bid])[0]==[]
    assert security_findings({**common,"method":"Единственный поставщик"},[price,bid])[0]==[]
    expensive={**common,"price_num":8000000,"price":"8 000 000 руб"}
    expensive_price=original("НМЦД: 8 000 000 рублей.","Извещение.docx")
    percent=original("Обеспечение заявки 6 % от НМЦД.")
    found,_=security_findings(expensive,[expensive_price,percent])
    assert len(found)==1 and "5%" in found[0]["title"],found
    allowed=original("Обеспечение заявки 4 % от НМЦД.")
    assert security_findings(expensive,[expensive_price,allowed])[0]==[]
    measure=original("К участию допускается только участник, имеющий офис на территории региона.")
    review=deeper_checks(common,[measure])
    assert len(review)>=1 and all(x["status"]=="check" for x in review),review
    assert all(x.get("evidence") and x.get("law") and x.get("source_url") for x in review)
    print("V336_LEGAL_SELFTEST_OK categories="+str(len(CATEGORIES))+
          " bid_lt_5m=True above_5pct=True negative=True "
          "no_contract_mix=True source_gate=True review_only=True",flush=True)

if __name__=="__main__":run()
