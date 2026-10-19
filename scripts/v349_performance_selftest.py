"""Regression tests for legal-event evidence, OCR reuse and atomic audit claiming."""
from pathlib import Path
from unittest.mock import patch
import tempfile,sqlite3,hashlib,json
import v347_text_cache as cached
import v348_cross_document_legal as cross
import v341_precise_legal as legal
import v332_all_procurements_audit as audit

REG="32615969056"
URL="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32

def doc(name,body,trusted=True):
    import hashlib
    source=URL[:-32]+hashlib.md5(name.encode()).hexdigest().upper()
    return {"name":name,"text":body,"status":"checked",
      "url":source,"meta":{"source":"official_eis_original" if trusted else "unverified",
        "sha256":"a"*64,"source_url":source}}

def run():
    purchase={"reg":REG,"method":"Аукцион в электронной форме","submission_end":"2026-10-30"}
    request=doc("Запрос разъяснений.pdf","Дата поступления запроса на разъяснение: 01.10.2026")
    response=doc("Разъяснения 1.pdf","Дата размещения разъяснения в ЕИС: 12.10.2026")
    found=cross.cross_doc_clarifications(purchase,[request,response])
    assert len(found)==1,found
    assert found[0]["kind"]=="violation" and found[0]["law"]=="223-ФЗ, ст. 3.2 ч. 3"
    assert "2026-10-01" in found[0]["evidence"] and "2026-10-12" in found[0]["evidence"]
    assert not cross.cross_doc_clarifications(purchase,[request,doc(response["name"],response["text"],False)])
    assert len(cross.cross_doc_clarifications(purchase,[request,response,response]))==1
    assert not cross.cross_doc_clarifications(purchase,[request,response,doc("Разъяснения 2.pdf",response["text"])])
    assert not cross.cross_doc_clarifications(purchase,[request,doc(response["name"],"Дата размещения разъяснения в ЕИС: 05.10.2026")])
    assert not cross.cross_doc_clarifications({**purchase,"method":"Закупка у единственного поставщика"},[request,response])
    assert not cross.cross_doc_clarifications({**purchase,"submission_end":""},[request,response])

    with tempfile.TemporaryDirectory(prefix="eis-v349-") as tmp:
        root=Path(tmp)
        pdf=root/"original.pdf"
        pdf.write_bytes(b"original EIS bytes")
        count=[0]
        def extract(path):
            count[0]+=1
            return "Условия документа и дата окончания"
        ns={"extract_text":extract}
        with patch("v295_eis_originals.root",return_value=root):
            cached.install(ns)
        # Each selftest run gets its own cache even when bytes match an earlier run.
        with patch("v347_text_cache.tempfile.gettempdir",return_value=tmp):
            one=ns["extract_text"](pdf)
            two=ns["extract_text"](pdf)
            assert one==two and count[0]==1
            pdf.write_bytes(b"replaced bytes of EIS original")
            ns["extract_text"](pdf)
            assert count[0]==2,"Content change must invalidate OCR cache"
            assert cached.cache_stats()["hits"]>=1

        path=root/"audit.sqlite"
        def connection():
            db=sqlite3.connect(path,timeout=4,check_same_thread=False)
            db.row_factory=sqlite3.Row
            return db
        c=connection()
        c.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT);
          CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,local_path TEXT);
          CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,updated_at TEXT,
               last_doc_id INTEGER NOT NULL DEFAULT 0,audit_revision TEXT NOT NULL DEFAULT '',
               error TEXT,
               PRIMARY KEY(campaign,reg));
        """)
        ready="32615969056"; waiting="32616432604"
        c.executemany("INSERT INTO purchases VALUES(?,?)",[(ready,"ООО Готовые документы"),(waiting,"АО Нет документов")])
        c.execute("INSERT INTO docs VALUES(1,?,'checked','/data/eis/local.pdf')",(ready,))
        c.executemany("INSERT INTO v332_audit_queue(campaign,reg,state,updated_at) VALUES(?,?,'pending','')",
                      [(audit.CAMPAIGN,ready),(audit.CAMPAIGN,waiting)])
        c.commit();c.close()
        main_ns={"conn":connection}
        assert audit.claim_next(main_ns)==ready
        assert audit.claim_next(main_ns) is None, "No originals: don't waste a worker slot"
        c=connection()
        assert c.execute("SELECT COUNT(*) FROM v332_audit_queue WHERE state='running'").fetchone()[0]==1
        assert c.execute("SELECT state FROM v332_audit_queue WHERE reg=?",(waiting,)).fetchone()[0]=="pending"
        c.close()
        with patch("v295_eis_originals.root",return_value=root):
            watermark=audit.review_watermark(main_ns,ready)
        audit.mark(main_ns,ready,"completed",watermark)
        with patch("v295_eis_originals.root",return_value=root):
            assert audit.refresh_changed(main_ns)==0
        c=connection()
        c.execute("INSERT INTO docs VALUES(2,?,'checked','/data/eis/second.pdf')",(ready,))
        c.execute("INSERT INTO docs VALUES(3,?,'checked','/data/eis/new.pdf')",(waiting,))
        c.commit();c.close()
        # A real receipt-verified source revision, not a new fake local row,
        # invalidates the completed legal watermark.
        body=b"Additional official evidence";digest=hashlib.sha256(body).hexdigest()
        folder=root/"eis223_official_originals"/ready/("B"*32)
        folder.mkdir(parents=True)
        (folder/(digest+".xml")).write_bytes(body)
        (folder/"receipt.json").write_text(json.dumps({"reg_number":ready,"uid":"B"*32,
            "extension":".xml","sha256":digest,"bytes":len(body)}))
        with patch("v295_eis_originals.root",return_value=root):
            assert audit.refresh_changed(main_ns)==1
        assert audit.claim_next(main_ns)==ready
        assert audit.claim_next(main_ns)==waiting
        assert audit.claim_next(main_ns) is None
    original_eval=legal.evaluate
    count=[0]
    def evaluate_once(p,docs):
        count[0]+=1
        return [],[]
    try:
        legal.evaluate=evaluate_once
        p={"reg":REG}
        docs=[request]
        legal.shared_evaluate(p,docs)
        legal.shared_evaluate(p,docs,consume=True)
        assert count[0]==1,"Expensive date scan executed twice"
    finally:legal.evaluate=original_eval
    print("V349_AUDIT_PERFORMANCE_SELFTEST_OK saved_text_reused=True changed_hash_invalidated=True queued_once=True missing_originals_deferred=True cross_document_verified=True negatives_suppressed=True date_scan_once=True")

if __name__=="__main__":
    run()
