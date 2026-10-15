"""Regression: thematic cards use the same HTML renderer as main audit cards."""
import sqlite3
import tempfile
from pathlib import Path
from fastapi import FastAPI
from starlette.requests import Request
import v342_thematic_queue as thematic
import v343_fast_cards as shared

REG="32615969056"
BATCH="abcdef012345abcdef012345"

def run():
    with tempfile.TemporaryDirectory() as tmp:
        file=Path(tmp)/"test.sqlite"
        def connect():
            db=sqlite3.connect(file,check_same_thread=False)
            db.row_factory=sqlite3.Row
            return db
        db=connect()
        db.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,
            customer_inn TEXT,method TEXT,price TEXT,price_num REAL,published_at TEXT,
            submission_start TEXT,submission_end TEXT,documents_checked INTEGER,
            documents_found INTEGER,url TEXT);
          CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,
            title TEXT,kind TEXT,law TEXT,law_url TEXT,evidence TEXT,document TEXT,source_url TEXT);
          CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,item TEXT,
            status TEXT,law TEXT,law_url TEXT,evidence TEXT,document TEXT,source_url TEXT);
          CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,
            doc_type TEXT,status TEXT,url TEXT,text_chars INTEGER);
          INSERT INTO purchases VALUES('32615969056','Предмет & описание',
            'ООО Тестовый заказчик','7700000000','Конкурс','100 000 ₽',100000,
            '2026-10-10','2026-10-11','2026-10-20',1,1,'https://zakupki.gov.ru/test');
          INSERT INTO findings VALUES(1,'32615969056','main-law',
            'Общее нарушение','violation','223-ФЗ, ст. 4','','Документально установлено','notice','');
          INSERT INTO checks VALUES(1,'32615969056','Требуется уточнить срок',
            'check','223-ФЗ, ст. 3.2','','Проверить документ','','');
          INSERT INTO docs VALUES(1,'32615969056','Извещение.pdf','notice','checked',
            'https://zakupki.gov.ru/download',1600);
        """)
        db.commit();db.close()
        app=FastAPI()
        ns={"conn":connect,"app":app}
        thematic.install(ns)
        db=connect()
        db.execute("""INSERT INTO thematic_batches
             (id,categories,created_at,status,legal_forms)
             VALUES(?,?,'2026-10-15T16:00:00+00:00','active','["ООО"]')""",
             (BATCH,'["dates"]'))
        db.execute("""INSERT INTO thematic_items(batch_id,reg,status,detail,updated_at)
             VALUES(?,?,'completed','Оригиналы проверены','2026-10-15T16:00:00+00:00')""",
             (BATCH,REG))
        db.execute("""INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence)
             VALUES(?,?,?,'Нарушение тематического направления','223-ФЗ, ст. 3.2 ч. 3',?)""",
             (BATCH,REG,"dates",'Извещение подтверждает факт <незаконный>'))
        db.commit();db.close()
        endpoint=next(x.endpoint for x in app.routes if x.path=="/thematic/card/{batch}/{reg}")
        request=Request({"type":"http","method":"GET","path":"/thematic/card/"+BATCH+"/"+REG,
                         "query_string":b"","headers":[]})
        result=endpoint(BATCH,REG,request)
        assert result.status_code==200
        page=result.body.decode()
        main=shared.render(REG,shared.read(ns,REG))
        assert shared.STYLE in page and shared.STYLE in main
        assert 'class="panel"' in page and 'class="grid"' in page
        assert '<h2>Нарушения: 1</h2>' in page
        assert '<section class="panel"><h2>Нарушения общей проверки' not in page
        assert 'Нарушение тематического направления' in page
        assert 'Общее нарушение' not in page
        assert 'Требует проверки' not in page
        assert 'Документы ЕИС (1)' in page
        assert 'Извещение.pdf' in page
        assert '/thematic#'+BATCH in page
        assert '/thematic/card/'+BATCH+'/'+REG+'?print=1' in page
        assert 'Извещение подтверждает факт &lt;незаконный&gt;' in page
        assert 'Извещение подтверждает факт <незаконный>' not in page
        assert '/api/v304/memo/'+REG+'.odt' in page
        print_request=Request({"type":"http","method":"GET","path":"/thematic/card/"+BATCH+"/"+REG,
                       "query_string":b"print=1","headers":[]})
        document=endpoint(BATCH,REG,print_request)
        assert document.status_code==200 and "window.print()" in document.body.decode()
        db=connect()
        try:
            assert db.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0]==1
            assert db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]==1
        finally:db.close()
        print("V346_THEMATIC_CARD_SELFTEST_OK shared_layout=True separate_findings=True legal_source=True memo_links=True html_safe=True persisted=True")

if __name__=="__main__":
    run()
