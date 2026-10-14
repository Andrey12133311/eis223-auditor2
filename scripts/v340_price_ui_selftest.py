"""V340 regression: blank price and duplicated findings do not leak into UI."""
import sqlite3
import tempfile
from pathlib import Path
from v340_price_display import decorate
from v333_dedup_ui import collapse

REG="32616471929"

def run():
    with tempfile.TemporaryDirectory(prefix="v340-price-") as folder:
        path=Path(folder)/"db.sqlite"
        def conn():
            db=sqlite3.connect(path)
            db.row_factory=sqlite3.Row
            return db
        db=conn()
        db.executescript("""CREATE TABLE v338_card_price (
           reg TEXT PRIMARY KEY,price TEXT,price_num REAL,
           price_kind TEXT,price_label TEXT,source_page TEXT,
           captured_at TEXT, conflict INTEGER DEFAULT 0,alternative_price TEXT);
           CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,price TEXT,price_num REAL);
           CREATE TABLE v322_verified_prices(reg TEXT PRIMARY KEY,price TEXT,
             evidence_page TEXT,price_kind TEXT);""")
        db.execute("INSERT INTO purchases VALUES(?,?,?)",(REG,"",None))
        db.execute("""INSERT INTO v338_card_price
          (reg,price,price_num,price_kind,price_label,source_page,captured_at,conflict)
          VALUES(?,?,?,?,?,?,?,?)""",
          (REG,"41081.60",41081.60,"subject_cost","Стоимость предмета закупки",
           "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+REG,
           "2026-10-14T20:00:00",0))
        db.commit();db.close()
        ns={"conn":conn}
        page={"purchases":[{"reg_number":REG,"price":None,"price_num":None},
                           {"reg_number":"32616471930","price":"12 300,50 рублей","price_num":None},
                           {"reg_number":"32616471931","price":"","price_num":None}]}
        output=decorate(ns,page)
        a,b,c=output["purchases"]
        assert a["price_num"]==41081.60 and a["price_kind"]=="subject_cost",a
        assert a["price_label"]=="Стоимость предмета закупки"
        assert b["price_num"]==12300.50,b
        assert c["price_num"] is None and c["price"]=="",c
        assert a["price_source_url"].startswith("https://zakupki.gov.ru/")
        purchase=decorate(ns,{"purchase":{"reg_number":REG,"price_num":None}})["purchase"]
        assert purchase["price_num"]==41081.60
        db=conn()
        assert db.execute("SELECT price_num FROM purchases WHERE reg_number=?",(REG,)).fetchone()[0] is None
        db.execute("UPDATE v338_card_price SET conflict=1 WHERE reg=?",(REG,))
        db.commit();db.close()
        conflict=decorate(ns,{"purchases":[{"reg_number":REG,"price":"100.00","price_num":100.0}]})["purchases"][0]
        assert conflict["price_num"]==100.0 and conflict["price_conflict"]
        assert conflict["official_price"]=="41081.60"
        db=conn()
        db.execute("DELETE FROM v338_card_price WHERE reg=?",(REG,))
        db.execute("INSERT INTO v322_verified_prices VALUES(?,?,?,?)",
                   (REG,"72000.25","https://zakupki.gov.ru/223/", "nmcd"))
        db.commit();db.close()
        verified=decorate(ns,{"items":[{"reg_number":REG,"price_num":None,"price":""}]})["items"][0]
        assert verified["price_num"]==72000.25 and verified["price_kind"]=="nmcd"

    title="Срок оплаты требует проверки: превышен предел для МСП"
    original="Расчет производится на основании счета в течение 15 рабочих дней с даты подписания документов о приемке товара."
    duplicate=[{"title":title,"evidence":original,"kind":"violation","location":"символ 50"},
               {"title":title,"evidence":original,"kind":"violation","location":"символ 200"},
               {"title":title,"evidence":original.replace("15","12"),"kind":"violation"}]
    assert len(collapse(duplicate))==2
    print("V340_PRICE_UI_SELFTEST_OK source_price=True fallback_price=True no_fake_price=True "
          "typed_subject_cost=True discrepancy_visible=True duplicate_clauses=True",flush=True)

if __name__=="__main__":run()
