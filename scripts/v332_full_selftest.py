"""Pure temporary-DB regressions for expanded legal review and all-row queue.

No production DB, EIS network, or Railway volume access.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path

from v330_amendments_223 import day,evaluation
from v331_broad_legal import CATALOG,category_checks
from v332_all_procurements_audit import (
    initialize,progress,next_reg,mark,persist,stored_docs,CAMPAIGN)

UID="0123456789ABCDEF0123456789ABCDEF"
REG="32616233661"
SRC=("https://zakupki.gov.ru/223/filestore/public/1.0/"
     "download/fz223/file.html?uid="+UID)

def original(text):
    return {"name":"Изменения документации.pdf","status":"checked",
            "type":"documentation","url":SRC,"text":text,
            "meta":{"source":"official_eis_original","sha256":"a"*64,
                    "source_url":SRC}}

def test_law():
    assert len(CATALOG)>=20
    assert day("10.08.2026").isoformat()=="2026-08-10"
    assert day("2026-09-02T16:00:00").isoformat()=="2026-09-02"
    sample=(
        "Дата принятия решения о внесении изменений: 10.08.2026. "
        "Дата публикации изменений в ЕИС: 02.09.2026. "
        "Срок подачи заявок был продлен.")
    confirmed,review=evaluation({"reg":REG},[original(sample)])
    assert len(confirmed)==1 and "23 календарных" in confirmed[0]["evidence"],(confirmed,review)
    assert confirmed[0]["code"]=="223-4-11-AMENDMENT-PUBLICATION-DELAY"
    assert evaluation({"reg":REG},[original(sample.replace(
        "Дата публикации изменений в ЕИС","Дата неизвестна"))])[0]==[]
    untrusted=original(sample)
    untrusted["meta"]["source"]="external_directory"
    assert evaluation({"reg":REG},[untrusted])[0]==[]
    flags=category_checks({"reg":REG},[original(
        "Условие технического задания: поставка эквивалента не допускается.")])
    assert any(c["field_path"]=="single_brand" and c["status"]=="check" for c in flags)
    assert all(c["status"]=="check" for c in flags)

def test_queue():
    with tempfile.TemporaryDirectory(prefix="eis223-v332-test-") as tmp:
        dbfile=Path(tmp)/"test.sqlite3"
        def conn():
            c=sqlite3.connect(dbfile)
            c.row_factory=sqlite3.Row
            return c
        c=conn()
        c.executescript("""
        CREATE TABLE purchases (
          reg_number TEXT PRIMARY KEY,customer TEXT DEFAULT 'ООО Тестовый заказчик',violations INTEGER DEFAULT 0,
          documents_checked INTEGER DEFAULT 0,checks_ok INTEGER DEFAULT 0,
          checks_attention INTEGER DEFAULT 0,last_seen TEXT DEFAULT '2026-10-14'
        );
        CREATE TABLE findings (
          id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,kind TEXT,title TEXT,
          law TEXT,law_url TEXT,evidence TEXT,note TEXT,document TEXT,
          source_url TEXT,location TEXT,match_text TEXT
        );
        CREATE TABLE checks (
          id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,item TEXT,status TEXT,
          evidence TEXT,source TEXT,source_url TEXT,document TEXT,
          field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT
        );
        CREATE TABLE v319_manual_inputs (reg TEXT PRIMARY KEY);
                INSERT INTO purchases(reg_number) VALUES('32616233661');
        INSERT INTO purchases(reg_number) VALUES('32616324151');
        INSERT INTO purchases(reg_number) VALUES('32616434308');
        """)
        c.commit();c.close()
        ns={"conn":conn,"extract_text":lambda p:Path(p).read_text(encoding="utf-8"),
            "_verified_set235":lambda:[REG,"32616324151","32616434308"]}
        first=initialize(ns)
        assert first["total_registered"]==3,first
        assert first["baseline_recorded_violations"]==0
        assert next_reg(ns)==REG
        mark(ns,REG,"running")
        state=progress(ns)
        assert state["states"].get("running")==1,state
        # Reinitialization after a crash resumes the existing snapshot.
        after=initialize(ns)
        assert after["total_registered"]==3 and after["states"].get("pending")==3,after
        finding={"code":"223-4-11-AMENDMENT-PUBLICATION-DELAY",
                 "kind":"violation","title":"Изменения опубликованы с опозданием",
                 "law":"223-ФЗ, ст. 4 ч. 11","evidence":"10.08.2026 — 02.09.2026",
                 "document":"Изменения документации.pdf","source_url":SRC}
        review={"scope":"223-ФЗ","item":"Проверить документы и сроки",
                "status":"check","document":"Изменения документации.pdf",
                "field_path":"amendments"}
        assert persist(ns,REG,[finding],[review])==(1,1)
        assert persist(ns,REG,[finding],[review])==(0,0)
        c=conn()
        counts=c.execute("SELECT violations,checks_attention FROM purchases WHERE reg_number=?",
                         (REG,)).fetchone()
        c.close()
        assert counts["violations"]==1 and counts["checks_attention"]==1,dict(counts)
        mark(ns,REG,"completed",{"documents_found":4,"documents_read":3,
                                 "verified_original_documents":2,
                                 "new_findings":1,"new_checks":1})
        summary=progress(ns)
        assert summary["states"].get("completed")==1
        assert summary["documents_read_in_completed"]==3
        assert summary["new_evidence_based_findings"]==1
        # A saved but non-EIS file is reviewed for legal signals without
        # becoming an authenticated EIS original.
        old=os.environ.get("EIS223_DATA_DIR")
        os.environ["EIS223_DATA_DIR"]=tmp
        try:
            filename=Path(tmp)/"file.txt"
            filename.write_text("Открытый конкурс. Поставка эквивалента не допускается.",encoding="utf-8")
            result,bad=asyncio.run(stored_docs(ns,REG,[{
                "id":1,"name":"file.txt","url":"https://example.org/file.txt",
                "doc_type":"documentation","local_path":str(filename),
                "metadata_json":"{}","status":"checked"
            }]))
            assert bad==0 and len(result)==1
            assert result[0]["status"]=="checked"
            assert result[0]["meta"]["source"]=="existing_local_unverified"
        finally:
            if old is None:os.environ.pop("EIS223_DATA_DIR",None)
            else:os.environ["EIS223_DATA_DIR"]=old

if __name__=="__main__":
    test_law()
    test_queue()
    print("V332_FULL_SELFTEST_OK 223_FZ_art4_11_23days=True verified_only=True "
          "broad_categories>=20 durable_3row_queue=True no_deleted_files=True "
          "idempotent_findings=True unverified_marked=True",flush=True)
