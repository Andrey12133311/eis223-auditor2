"""Test direct official EIS price persistence without touching production storage."""
import json
import sqlite3
import tempfile
from pathlib import Path
from v338_card_prices import KINDS,initialize,save_card_price,price_summary
from v335_price_recovery import sync_price_checks

REG="32616471929"
SOURCE="https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+REG

def run():
    with tempfile.TemporaryDirectory(prefix="eis223-v338-") as folder:
        path=Path(folder)/"price.sqlite"
        def conn():
            db=sqlite3.connect(path)
            db.row_factory=sqlite3.Row
            return db
        db=conn()
        db.executescript("""
            CREATE TABLE purchases(
                reg_number TEXT PRIMARY KEY,
                price TEXT,price_num REAL,card_fields_json TEXT,
                documents_checked INTEGER DEFAULT 1,last_seen TEXT
            );
            INSERT INTO purchases(reg_number) VALUES('32616471929');
        """)
        db.commit();db.close()
        ns={"conn":conn}
        initialize(ns)
        assert all(p.fullmatch(label) for p,label in [
            (KINDS["nmcd"],"Начальная (максимальная) цена договора"),
            (KINDS["maximum_contract_value"],"Максимальное значение цены договора"),
            (KINDS["subject_cost"],"Стоимость предмета закупки")])
        received=save_card_price(ns,REG,"1900000.00",1900000.0,"subject_cost",
                                 "Стоимость предмета закупки",SOURCE)
        assert received=={"updated":True,"conflict":False,"saved":True},received
        db=conn()
        row=db.execute("SELECT * FROM purchases WHERE reg_number=?",(REG,)).fetchone()
        assert row["price_num"]==1900000.0,dict(row)
        assert json.loads(row["card_fields_json"])["summary"]["price_kind"]=="subject_cost"
        assert price_summary(ns)["official_cards_captured"]==1
        db.close()
        again=save_card_price(ns,REG,"1900000.00",1900000.0,"subject_cost",
                              "Стоимость предмета закупки",SOURCE)
        assert again["saved"] and not again["conflict"]
        conflict=save_card_price(ns,REG,"2500000.00",2500000.0,"subject_cost",
                                 "Стоимость предмета закупки",SOURCE)
        assert conflict["conflict"] and not conflict["updated"],conflict
        db=conn()
        assert db.execute("SELECT price_num FROM purchases").fetchone()[0]==1900000.0
        db.close()
        # Different price meaning is not an accidental NMCD violation pass.
        db=conn()
        db.execute("""CREATE TABLE checks(
           reg_number TEXT,item TEXT,status TEXT,evidence TEXT,source TEXT,
           source_url TEXT,document TEXT,note TEXT)""")
        db.execute("""INSERT INTO checks(reg_number,item,status)
             VALUES(?,?,?)""",(REG,"НМЦД","check"))
        sync_price_checks(db,REG,"1900000.00",
                          {"price":"1900000.00","price_kind":"subject_cost"})
        assert db.execute("SELECT status FROM checks").fetchone()[0]=="check"
        db.close()
        assert price_summary(ns)["price_conflicts_require_review"]==1
        print("V338_CARD_PRICE_SELFTEST_OK direct_card=True typed_cost=True "
              "same_idempotent=True divergent_preserved=True nmcd_separation=True",flush=True)

if __name__=="__main__":run()
