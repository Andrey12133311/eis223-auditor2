"""Quick offline smoke test for read-only card HTTP route."""
import asyncio
import sqlite3
import tempfile
from pathlib import Path
from v343_fast_cards import FastCardASGI, render, read

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"sample.sqlite"
        db=sqlite3.connect(path)
        db.executescript("""
CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,customer_inn TEXT,method TEXT,price TEXT,published_at TEXT,submission_start TEXT,submission_end TEXT,documents_checked INTEGER,documents_found INTEGER,url TEXT,price_num REAL);
CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,title TEXT,kind TEXT,law TEXT,law_url TEXT,evidence TEXT,document TEXT,source_url TEXT);
CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,item TEXT,status TEXT,law TEXT,law_url TEXT,evidence TEXT,document TEXT,source_url TEXT);
CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,doc_type TEXT,status TEXT,url TEXT,text_chars INTEGER);
INSERT INTO purchases VALUES('32615969056','Предмет закупки & <тест>','АО Заказчик','7700000000','Аукцион','12345 ₽','2026-10-10','2026-10-11','2026-10-25',2,2,'https://zakupki.gov.ru/example',12345);
INSERT INTO findings VALUES(1,'32615969056','LAW-1','Срок разъяснений','violation','223-ФЗ, ст. 3.2 ч. 3','', 'Разъяснение опубликовано с нарушением срока','Извещение','https://zakupki.gov.ru/test');
INSERT INTO checks VALUES(1,'32615969056','Проверка НМЦД','check','','','Требуется дополнительный документ','Документ','');
INSERT INTO docs VALUES(1,'32615969056','Оригинал.pdf','notice','checked','https://zakupki.gov.ru/test',1000);
""")
        db.commit();db.close()
        ns={"conn":lambda:sqlite3.connect(path)}
        data=read(ns,"32615969056")
        result=render("32615969056",data)
        assert "Предмет закупки &amp; &lt;тест&gt;" in result
        assert "Срок разъяснений" in result and "Требует проверки" in result
        assert "/api/v304/memo/32615969056.odt" in result
        assert "<тест>" not in result
        messages=[]
        async def recv():return {"type":"http.request","body":b"","more_body":False}
        async def send(item):messages.append(item)
        async def inner(scope,receive,send):
            await send({"type":"http.response.start","status":204,"headers":[]})
            await send({"type":"http.response.body","body":b""})
        middleware=FastCardASGI(inner,ns)
        await middleware({"type":"http","method":"GET","path":"/check-new/32615969056"},recv,send)
        assert messages[0]["status"]==200
        assert b"x-audit-card" in str(messages[0]["headers"]).lower().encode()
        assert b"32615969056" in messages[1]["body"]
        messages.clear()
        await middleware({"type":"http","method":"GET","path":"/health"},recv,send)
        assert messages[0]["status"]==204
        print("V343_FAST_CARD_SELFTEST_OK saved_evidence=True memo_odt=True html_escaped=True bypass_legacy=True")

if __name__=="__main__":
    asyncio.run(main())
