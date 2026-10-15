"""Regression for persistent main purchase history and distinct legal incidents."""
import sqlite3
import tempfile
from pathlib import Path
from v352_dashboard_truth import collapse_legal_events,load_counts,dashboard_counts

A="Порядок оплаты: расчет производится в течение 15 рабочих дней с даты подписания документов о приемке продукции заказчиком."
B="Расчет производится в течение 15 рабочих дней с даты подписания документов о приемке продукции заказчиком."
C="Расчет производится в течение 25 рабочих дней с даты подписания документов о приемке продукции заказчиком."
TITLE="Срок оплаты МСП превышен"

def run():
    findings=[
        {"title":TITLE,"law":"ПП РФ № 1352 п. 28","evidence":A,
         "document":"Документация.pdf","source_url":"https://zakupki.gov.ru/example/notice"},
        {"title":TITLE,"law":"ПП РФ № 1352 п. 28","evidence":B,
         "document":"Договор.pdf","source_url":"https://zakupki.gov.ru/example/contract"}
    ]
    same=collapse_legal_events(findings)
    assert len(same)==1,same
    assert len(same[0]["supporting_sources"])==2,same
    distinct=collapse_legal_events(findings+[{
        **findings[1],"evidence":C
    }])
    assert len(distinct)==2,distinct
    with tempfile.TemporaryDirectory() as home:
        file=Path(home)/"base.sqlite"
        conn=sqlite3.connect(file)
        conn.row_factory=sqlite3.Row
        conn.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,last_seen TEXT,
            violations INTEGER,risks INTEGER,documents_found INTEGER,
            documents_checked INTEGER);
          CREATE TABLE findings(reg_number TEXT,title TEXT,law TEXT,evidence TEXT,
            document TEXT,source_url TEXT,code TEXT,kind TEXT);
          CREATE TABLE checks(reg_number TEXT,item TEXT,law TEXT,evidence TEXT,
            document TEXT,source_url TEXT,status TEXT);
          INSERT INTO purchases VALUES('32615969056','2026-10-15',3,0,2,2);
          INSERT INTO purchases VALUES('32616432604','2026-10-14',4,0,1,1);
          INSERT INTO findings VALUES('32615969056','Срок оплаты МСП превышен',
            'ПП РФ № 1352 п. 28',
            'Порядок оплаты: расчет производится в течение 15 рабочих дней с даты подписания документов о приемке продукции заказчиком.',
            'Документация.pdf','https://zakupki.gov.ru/example/notice','1352-28','violation');
          INSERT INTO checks VALUES('32615969056','Срок оплаты МСП превышен',
            'ПП РФ № 1352 п. 28',
            'Расчет производится в течение 15 рабочих дней с даты подписания документов о приемке продукции заказчиком.',
            'Договор.pdf','https://zakupki.gov.ru/example/contract','violation');
        """)
        summary,items=dashboard_counts(conn,
          " FROM purchases p WHERE p.reg_number IN (?,?)",
          ["32615969056","32616432604"],0,20)
        assert summary["purchases"]==2,summary
        assert summary["violations"]==1,summary
        assert summary["legacy_violations_cache"]==7,summary
        assert items[0]["violations"]==1,items
        assert items[1]["violations"]==0,items
        assert load_counts(conn,["32615969056","32616432604"])=={
          "32615969056":1,"32616432604":0}
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]==1
        conn.close()
    print("V352_DASHBOARD_RECOVERY_TEST_OK purchase_history_stable=True cross_source_dedup=True no_evidence_delete=True separate_deadlines=True")
if __name__=="__main__":
    run()
