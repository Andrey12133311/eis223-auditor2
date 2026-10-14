"""Historical violation evidence survives the legacy destructive recheck."""
import sqlite3
import tempfile
from pathlib import Path
from fastapi import FastAPI
from v334_findings_ledger import install, summary

def run():
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/"db.sqlite"
        def conn():
            db=sqlite3.connect(path)
            db.row_factory=sqlite3.Row
            return db
        db=conn()
        db.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,violations INT);
          CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,kind TEXT,title TEXT,law TEXT,document TEXT,evidence TEXT);
          CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,item TEXT,document TEXT,evidence TEXT);
          INSERT INTO purchases VALUES('32616448060',2);
          INSERT INTO findings(reg_number,kind,title,law,document,evidence)
           VALUES('32616448060','violation','Срок разъяснений','223-ФЗ','a.docx','05.10.2026');
          INSERT INTO checks(reg_number,status,item,document,evidence)
           VALUES('32616448060','violation','Другой срок','b.docx','06.10.2026');
        """)
        db.commit()
        db.close()
        def legacy_save(p,*args):
            db=conn()
            with db:
                db.execute("DELETE FROM findings WHERE reg_number=?",(p["reg"],))
                db.execute("DELETE FROM checks WHERE reg_number=?",(p["reg"],))
                db.execute("UPDATE purchases SET violations=0 WHERE reg_number=?",(p["reg"],))
            db.close()
        ns={"app":FastAPI(),"conn":conn,"save":legacy_save}
        install(ns)
        assert summary(conn)["raw_rows_before_cross_table_review"]==2
        ns["save"]({"reg":"32616448060"})
        report=summary(conn)
        assert report["raw_rows_before_cross_table_review"]==0
        assert report["archived_finding_versions"]==1
        assert report["archived_check_versions"]==1
        assert report["changed_rechecks"]==1
        ns["save"]({"reg":"32616448060"})
        assert summary(conn)["changed_rechecks"]==1
        print("V334_HISTORY_SELFTEST_OK pre_recheck_saved=2 no_auto_reactivation=True",flush=True)
if __name__=="__main__":run()
