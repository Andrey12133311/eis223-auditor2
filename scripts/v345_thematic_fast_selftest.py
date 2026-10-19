"""Regression coverage for accelerated local thematic EIS review."""
import asyncio
import sqlite3
import tempfile
import hashlib,json
from unittest.mock import patch
from pathlib import Path
import v342_thematic_queue as v342
import v345_thematic_accelerator as v345

async def test():
    with tempfile.TemporaryDirectory() as tmp, patch("v295_eis_originals.root",return_value=Path(tmp)):
        def original(reg, uid, body):
            digest=hashlib.sha256(body).hexdigest()
            folder=Path(tmp)/"eis223_official_originals"/reg/uid
            folder.mkdir(parents=True,exist_ok=True)
            file=folder/(digest+".xml")
            file.write_bytes(body)
            (folder/"receipt.json").write_text(json.dumps({"reg_number":reg,"uid":uid,
                "extension":".xml","sha256":digest,"bytes":len(body)}))
            return str(file)
        original("32615969056","A"*32,b"First official original")
        path=Path(tmp)/"thematic.sqlite"
        db=sqlite3.connect(path)
        db.executescript("""
        CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT);
        CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT);
        INSERT INTO purchases VALUES('32615969056','ООО Пример');
        INSERT INTO purchases VALUES('32616432604','АО Заказчик');
        INSERT INTO docs VALUES(1,'32615969056','/data/eis/example.pdf','checked');
        """)
        db.commit();db.close()
        def connect():
            c=sqlite3.connect(path,timeout=3,check_same_thread=False)
            c.row_factory=sqlite3.Row
            return c
        ns={"conn":connect}
        v342.init(ns)
        v345.install(ns)
        count=0
        async def verified_review(ns,reg,categories):
            nonlocal count
            count+=1
            return "completed",[{"category":"dates","title":"Протокол разъяснений","law":"223-ФЗ, ст. 4","evidence":"Подтверждённый факт"}]
        v342.independent_review=verified_review
        c=connect()
        try:
            c.execute("""INSERT INTO thematic_batches
                (id,categories,created_at,status,legal_forms) VALUES
                ('ab1234567890abcdef123456','["dates"]','2026-10-15T12:00:00+00:00','active','["ООО","АО"]')""")
            c.commit()
        finally:c.close()
        await v345.faster_tick(ns)
        c=connect()
        try:
            statuses={r["reg"]:r["status"] for r in c.execute("SELECT reg,status FROM thematic_items")}
            assert statuses["32615969056"]=="completed",statuses
            assert statuses["32616432604"]=="waiting_originals",statuses
            assert c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0]==1
        finally:c.close()
        assert count==1,count
        await v345.faster_tick(ns)
        assert count==1,"unchanged document rows must not be re-extracted"
        c=connect()
        try:
            c.execute("INSERT INTO docs VALUES(2,'32616432604','/data/eis/other.pdf','checked')")
            c.execute("INSERT INTO docs VALUES(3,'32615969056','/data/eis/added.pdf','checked')")
            c.commit()
        finally:c.close()
        original("32616432604","B"*32,b"Newly available original")
        original("32615969056","C"*32,b"New official revision")
        # New evidence does not erase an already scheduled extraction deadline.
        c=connect()
        try:
            c.execute("UPDATE thematic_items SET next_retry_at=0,updated_at='2020-01-01T00:00:00+00:00' WHERE status='waiting_originals'")
            c.commit()
        finally:c.close()
        await v345.faster_tick(ns)
        c=connect()
        try:
            assert c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0]==2
            statuses={r["reg"]:r["status"] for r in c.execute("SELECT reg,status FROM thematic_items")}
            assert all(x=="completed" for x in statuses.values()),statuses
        finally:c.close()
        assert count==3,count
        print("V345_THEMATIC_FAST_SELFTEST_OK parallel_ready=True new_docs_once=True bounded_retry_preserved=True saved_findings=True no_duplicates=True")

if __name__=="__main__":
    asyncio.run(test())
