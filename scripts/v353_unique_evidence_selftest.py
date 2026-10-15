"""V353: additional documentary discoveries without duplicate inflation."""
import sqlite3
import tempfile
from pathlib import Path

from v348_cross_document_legal import cross_doc_protocols
from v353_episode_guard import same_episode,event_dates
from v352_dashboard_truth import collapse_legal_events
from v332_all_procurements_audit import persist

REG="32615969056"
EIS="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32

def original(name,body,source=True):
    return {"name":name,"text":body,"status":"checked","url":EIS,
     "meta":{"source":"official_eis_original" if source else "unknown",
             "source_url":EIS,"sha256":"a"*64}}

def run():
    p={"reg":REG,"method":"Аукцион в электронной форме"}
    sign=original("Итоговый протокол.pdf","Дата подписания итогового протокола: 06.10.2026")
    post=original("Публикация итогового протокола.pdf",
        "Дата размещения итогового протокола в ЕИС: 13.10.2026")
    post["url"]=EIS.replace("A"*32,"B"*32)
    post["meta"]["source_url"]=post["url"]
    found=cross_doc_protocols(p,[sign,post])
    assert len(found)==1,found
    assert found[0]["code"]=="223-4-12-PROTOCOL-CROSS-DOC-LATE"
    assert "2026-10-09" in found[0]["evidence"]
    assert not cross_doc_protocols(p,[sign,post,{**post}])
    # Reusing one EIS receipt under two filenames is not independent proof.
    assert not cross_doc_protocols(p,[sign,original(post["name"],post["text"])])
    assert not cross_doc_protocols(p,[sign,original(post["name"],post["text"],False)])
    assert not cross_doc_protocols(p,[sign,original("Протокол рассмотрения.pdf",
         "Дата размещения протокола рассмотрения в ЕИС: 13.10.2026")])
    assert not cross_doc_protocols(p,[sign,original(post["name"],
         "Дата размещения итогового протокола в ЕИС: 08.10.2026")])
    assert not cross_doc_protocols({"method":"Единственный поставщик"},[sign,post])
    assert len(collapse_legal_events(found+[dict(found[0])]))==1
    assert event_dates("06.10.2026 и 13.10.2026")==("2026-10-06","2026-10-13")

    with tempfile.TemporaryDirectory(prefix="eis-v353-") as home:
        path=Path(home)/"events.sqlite"
        def connect():
            db=sqlite3.connect(path,timeout=5,check_same_thread=False)
            db.row_factory=sqlite3.Row
            return db
        c=connect()
        c.executescript("""CREATE TABLE purchases(
              reg_number TEXT PRIMARY KEY,violations INTEGER DEFAULT 0,
              checks_ok INTEGER DEFAULT 0,checks_attention INTEGER DEFAULT 0);
            CREATE TABLE findings(id INTEGER PRIMARY KEY,
              reg_number TEXT,code TEXT,kind TEXT,title TEXT,law TEXT,
              law_url TEXT,evidence TEXT,note TEXT,document TEXT,
              source_url TEXT,location TEXT,match_text TEXT);
            CREATE TABLE checks(id INTEGER PRIMARY KEY,
              reg_number TEXT,scope TEXT,item TEXT,status TEXT,evidence TEXT,
              source TEXT,source_url TEXT,document TEXT,field_path TEXT,
              note TEXT,law TEXT,law_url TEXT,location TEXT);
            INSERT INTO purchases(reg_number) VALUES('32615969056');""")
        c.commit();c.close()
        ns={"conn":connect}
        assert persist(ns,REG,found,[])==(1,0)
        # Same legal act, now written in a different date notation, is not new.
        restated=dict(found[0])
        restated["evidence"]=restated["evidence"].replace(
            "2026-10-06","06.10.2026").replace("2026-10-13","13.10.2026")
        assert same_episode(found[0],restated)
        assert persist(ns,REG,[restated],[])==(0,0)
        assert persist(ns,REG,found,[])==(0,0)
        other=dict(found[0])
        other["evidence"]=other["evidence"].replace(
            "2026-10-06","2026-10-07").replace(
            "2026-10-13","2026-10-15")
        assert not same_episode(found[0],other)
        assert persist(ns,REG,[other],[])==(1,0)
        text="Расчет производится в течение 15 рабочих дней с даты подписания заказчиком документов о приемке продукции."
        fee={"kind":"violation","code":"1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS",
          "title":"Нарушен срок оплаты МСП","law":"Положение № 1352, п. 28",
          "evidence":text,"document":"Договор.pdf","source_url":EIS}
        assert persist(ns,REG,[fee],[])==(1,0)
        alias={**fee,"evidence":"Условие: "+text,"document":"Копия договора.docx"}
        assert persist(ns,REG,[alias],[])==(0,0)
        new_term={**fee,"evidence":text.replace("15 рабочих","25 рабочих")}
        assert persist(ns,REG,[new_term],[])==(1,0)
        # One checklist result cannot re-insert a finding as a second violation.
        ck={"status":"violation","item":fee["title"],"scope":"223-ФЗ",
            "law":fee["law"],"evidence":fee["evidence"],"document":fee["document"],
            "source_url":EIS,"field_path":fee["code"]}
        assert persist(ns,REG,[],[ck])==(0,0)
        c=connect()
        assert c.execute("SELECT COUNT(*) FROM findings").fetchone()[0]==4
        assert c.execute("SELECT COUNT(*) FROM checks").fetchone()[0]==0
        c.close()
    print("V353_UNIQUE_EVIDENCE_SELFTEST_OK cross_original_protocol=True "
          "different_dated_events_retained=True dated_spelling_idempotent=True "
          "different_payment_days_retained=True duplicate_sources_collapsed=True "
          "cross_table_duplicate_blocked=True saved_evidence_not_deleted=True")

if __name__=="__main__":run()
