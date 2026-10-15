"""Temporary-DB and date-chain regressions; never touches production data."""
import json
import sqlite3
import tempfile
from pathlib import Path

from v338_card_prices import initialize as initialize_prices
from v341_eis_facts import (initialize, validate_snapshot, save_snapshot, read_card,
    document_facts, enrich, persist_facts, chosen_price, LABELS, validate_prices)
from v341_precise_legal import evaluate, shifted
from v340_price_display import decorate
from v336_legal_matrix import security_findings
from v330_amendments_223 import day, evaluation as amendment_evaluation

REG="32616471929"
SOURCE="https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+REG
FILE="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32


def doc(text,name="Извещение и документация.docx",trusted=True):
    return {"name":name,"status":"checked","text":text,"url":FILE,
            "meta":{"source":"official_eis_original" if trusted else "unverified",
                    "sha256":"a"*64,"source_url":FILE}}


def price(value="41081.60",kind="subject_cost",label="Стоимость предмета закупки"):
    return {"price":value,"price_kind":kind,"price_label":label}


def run():
    assert len(LABELS)==7
    for kind,label in LABELS.items():
        normalized=validate_prices([price(kind=kind,label=label)])
        assert normalized[0]["price_kind"]==kind
    assert validate_prices([price("0")])[0]["price_num"]==0
    for bad in (price(label="Цена победителя"),price(label="Обеспечение заявки"),
                {**price(),"currency":"USD"},price("NaN")):
        try:validate_prices([bad])
        except ValueError:pass
        else:raise AssertionError(bad)

    sample=doc("Способ закупки | Аукцион в электронной форме\n"
        "Дата начала подачи заявок | 01.10.2026 08:00\n"
        "Дата и время окончания срока подачи заявок | 22.10.2026 19:00\n"
        "Дата размещения извещения в ЕИС | 01.10.2026 10:25")
    facts=document_facts([sample])
    assert facts["method"]["value"]=="Аукцион в электронной форме",facts
    assert facts["submission_end"]["value"]=="2026-10-22",facts
    assert facts["published"]["value"]=="2026-10-01",facts
    assert document_facts([doc(sample["text"],trusted=False)])=={}
    conflicting=doc(sample["text"].replace("22.10.2026","23.10.2026"),"Документация новая редакция.docx")
    assert "submission_end" not in document_facts([sample,conflicting])
    russian=doc('Способ размещения закупки | Аукцион в электронной форме\n'
        'Порядок, дата начала и окончания подачи заявок | '
        'Дата начала срока приема заявок: «14» октября 2026; '
        'Дата и время окончания срока приема заявок: «22» октября 2026 08:15')
    parsed=document_facts([russian])
    assert parsed["submission_start"]["value"]=="2026-10-14",parsed
    assert parsed["submission_end"]["value"]=="2026-10-22",parsed
    assert parsed["method"]["value"]=="Аукцион в электронной форме"

    # Regression: start and end dates in one EIS section, with line breaks.
    section = doc("Срок подачи заявок\\nНачало: 14.10.2026 08:00\\nОкончание: 22.10.2026 17:00")
    section_facts = document_facts([section])
    assert section_facts["submission_start"]["value"] == "2026-10-14", section_facts
    assert section_facts["submission_end"]["value"] == "2026-10-22", section_facts
    assert document_facts([doc(section["text"], trusted=False)]) == {}

    with tempfile.TemporaryDirectory(prefix="eis-v341-") as tmp:
        path=Path(tmp)/"db.sqlite"
        def conn():
            db=sqlite3.connect(path);db.row_factory=sqlite3.Row
            return db
        db=conn()
        db.executescript("""CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,
            price TEXT,price_num REAL,card_fields_json TEXT,method TEXT,
            submission_start TEXT,submission_end TEXT,deadline TEXT,published_at TEXT,
            documents_checked INTEGER DEFAULT 3,last_seen TEXT);
            CREATE TABLE v322_verified_prices(reg TEXT PRIMARY KEY,price TEXT,evidence_page TEXT,price_kind TEXT);
        """)
        db.execute("INSERT INTO purchases(reg_number,price,price_num,card_fields_json) VALUES(?,?,?,?)",
                   (REG,"1000.00",1000,"{}"));db.commit();db.close()
        ns={"conn":conn}
        initialize_prices(ns);initialize(ns)
        p={"reg":REG}
        enrich(ns,p,[sample]);persist_facts(ns,p)
        assert p["submission_end"]=="2026-10-22" and p["method"]
        db=conn()
        assert db.execute("SELECT deadline FROM purchases").fetchone()[0]=="2026-10-22"
        db.close()
        raw={"reg_number":REG,"source_page":SOURCE,"prices":[price()],
             "fields":{"method":"Конкурс в электронной форме","submission_end":"23.10.2026"},
             "field_evidence":{"method":"Способ закупки: Конкурс в электронной форме",
                 "submission_end":"Окончание подачи заявок: 23.10.2026 12:00"}}
        data=validate_snapshot(raw)
        assert save_snapshot(ns,data)["accepted"]
        again=save_snapshot(ns,data)
        assert again["prices"]==1
        db=conn();row=dict(db.execute("SELECT * FROM purchases").fetchone());db.close()
        assert row["price_num"]==41081.6 and row["documents_checked"]==3,row
        card=json.loads(row["card_fields_json"])
        assert len(card["previous_prices"])==1 and card["previous_prices"][0]["price"]=="1000.00"
        assert row["submission_end"]=="2026-10-23"
        assert row["method"]=="Конкурс в электронной форме"
        assert read_card(ns,REG)["prices"][0]["price_kind"]=="subject_cost"
        actual=decorate(ns,{"purchase":row})["purchase"]
        assert actual["price_num"]==41081.6 and actual["price_label"]=="Стоимость предмета закупки"
        # Empty documents tabs preserve the captured common-info facts.
        save_snapshot(ns,validate_snapshot({"reg_number":REG,"source_page":SOURCE,
                                           "fields":{},"prices":[]}))
        assert read_card(ns,REG)["prices"][0]["price"]=="41081.60"
        multi=validate_snapshot({"reg_number":REG,"source_page":SOURCE,"fields":{},
            "prices":[price("10000","lot_price","Цена лота"),price("20000","lot_price","Цена лота")]})
        assert chosen_price(multi["prices"]) is None
        save_snapshot(ns,multi)
        actual=decorate(ns,{"purchase":row})["purchase"]
        assert actual["price_num"] is None and len(actual["price_values"])==2,actual
        # A formula is retained without inventing a numeric procurement total.
        formula=validate_snapshot({"reg_number":REG,"source_page":SOURCE,"fields":{},
                                   "price_formula":"Ц = Т × V","prices":[]})
        save_snapshot(ns,formula)
        assert read_card(ns,REG)["price_formula"]=="Ц = Т × V"

    p={"reg":REG,"method":"Аукцион в электронной форме","submission_end":"2026-10-22T19:00"}
    late=doc("Дата поступления запроса разъяснений: 01.10.2026 18:00\n"
             "Дата размещения ответа в ЕИС: 08.10.2026 08:00", "Разъяснение.docx")
    found,checks=evaluate(p,[late])
    assert len(found)==1 and "2026-10-06" in found[0]["evidence"],(found,checks)
    assert evaluate(p,[doc(late["text"],trusted=False)])[0]==[]
    timely=doc(late["text"].replace("08.10.2026 08:00","06.10.2026 23:59"))
    assert evaluate(p,[timely])[0]==[]
    assert evaluate({**p,"submission_end":"2026-10-02"},[late])[0]==[]
    assert evaluate({**p,"method":"Единственный поставщик"},[late])[0]==[]
    incomplete=doc("Дата поступления запроса разъяснений: 01.10.2026. Запрос не сопоставлен с ответом.")
    assert evaluate(p,[incomplete])[0]==[] and evaluate(p,[incomplete])[1]
    assert shifted(day("2026-10-09"),3)==day("2026-10-14")
    protocol=doc("Дата подписания протокола: 08.10.2026\nДата размещения протокола в ЕИС: 13.10.2026", "Итоговый протокол.docx")
    # Missing or malformed dates must not crash the entire recheck batch.
    malformed=doc("Дата подписания протокола: 31.02.2026\nДата размещения протокола в ЕИС: 13.10.2026")
    assert evaluate(p,[malformed])[0]==[]
    malformed_answer=doc("Дата поступления запроса разъяснений: 31.02.2026\nДата размещения ответа в ЕИС: 08.10.2026")
    assert evaluate(p,[malformed_answer])[0]==[]
    assert evaluate(p,[doc(protocol["text"],"Копия протокола.docx"),protocol])[0]
    assert len(evaluate(p,[protocol])[0])==1
    assert evaluate(p,[doc(protocol["text"].replace("13.10.2026","12.10.2026"))])[0]==[]
    # Publication on the next working day after a weekend is timely.
    amendment=doc('Дата принятия решения о внесении изменений: 08.10.2026\nДата публикации изменений в ЕИС: 12.10.2026')
    assert amendment_evaluation(p,[amendment])[0]==[]
    delayed=doc(amendment['text'].replace('12.10.2026','13.10.2026'))
    assert len(amendment_evaluation(p,[delayed])[0])==1
    # A stale general-card bid deadline is not proof that a specific
    # amendment was published after the revised submission deadline.
    assert not any(x['code']=='223-4-11-CHANGE-AFTER-BID-DEADLINE' for x in amendment_evaluation({**p,'submission_end':'2026-10-01'},[delayed])[0])
    # Explicit SME scope tightens bid security to 2%; a mere legal quote
    # or performance-security requirement must not trigger that result.
    bid=doc("Размер обеспечения заявки: 3 % от НМЦД.")
    nmcd=doc("НМЦД: 8 000 000 рублей.")
    base={**p,"price_num":8000000,"price":"8000000"}
    assert security_findings(base,[bid,nmcd])[0]==[]
    sme={**base,"method":"Конкурс в электронной форме, участниками которого могут быть только субъекты малого и среднего предпринимательства"}
    result=security_findings(sme,[bid,nmcd])[0]
    assert len(result)==1 and "2%" in result[0]["title"],result
    assert security_findings(sme,[doc("Обеспечение исполнения договора: 3 % от НМЦД."),nmcd])[0]==[]
    print("V341_FACTS_SELFTEST_OK price_kinds=7 snapshot_history=True multi_lot=True dates_and_method=True clarification_calendar=True protocol_calendar=True sme_security=True")


if __name__=="__main__":run()
