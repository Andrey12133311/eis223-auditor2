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
        # Missing real card prices get a bounded, six-hour-spaced retry,
        # rather than disappearing from the backfill queue after one failure.
        import v301_search_jobs
        from v338_card_prices import enqueue_missing_prices
        from datetime import datetime,timezone
        db=conn()
        db.execute("""CREATE TABLE v301_search_jobs(status TEXT)""")
        db.execute("""CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT)""")
        from v332_all_procurements_audit import CAMPAIGN
        db.execute("INSERT INTO v332_audit_queue VALUES(?,?)",(CAMPAIGN,"32616471930"))
        db.execute("""INSERT INTO purchases(reg_number,documents_checked)
                      VALUES('32616471930',1)""")
        db.execute("""INSERT INTO purchases(reg_number,documents_checked)
                      VALUES('32616471931',0)""")
        db.execute("""INSERT INTO purchases(reg_number,documents_checked)
                      VALUES('32616471932',1)""")
        db.commit();db.close()
        calls=[]
        original=v301_search_jobs._enqueue
        try:
            def fake_enqueue(ns,kind,target,*args):
                calls.append(target)
                return ({"target":target},True)
            v301_search_jobs._enqueue=fake_enqueue
            assert enqueue_missing_prices(ns)==1,calls
            assert enqueue_missing_prices(ns)==0,calls
            for attempt in (2,3):
                db=conn()
                db.execute("""UPDATE v338_price_requests
                  SET created_at=datetime('now','-7 hours')
                  WHERE reg='32616471930'""")
                db.commit();db.close()
                assert enqueue_missing_prices(ns)==1,(attempt,calls)
            db=conn()
            db.execute("""UPDATE v338_price_requests
                  SET created_at=datetime('now','-7 hours')
                  WHERE reg='32616471930'""")
            attempts=db.execute("""SELECT attempts FROM v338_price_requests
                    WHERE reg='32616471930'""").fetchone()[0]
            db.commit();db.close()
            assert attempts==3
            assert enqueue_missing_prices(ns)==0,calls
            assert calls==["32616471930"]*3
        finally:
            v301_search_jobs._enqueue=original
        print("V338_CARD_PRICE_SELFTEST_OK direct_card=True typed_cost=True "
              "same_idempotent=True divergent_preserved=True nmcd_separation=True",flush=True)

if __name__=="__main__":run()
