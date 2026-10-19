"""V372 automatic protocol/regulation tests: all work from saved EIS originals.

Run during the image build; tests never call Railway, EIS or a real DB.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from v372_auto_regulation_protocols import (
    evaluate,install,LAW_REG,LAW_PROT,LAW_4,
    _reg_doc,_protocol_doc
)

EIS="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32
P={"reg":"32616412317","method":"Открытый конкурс",
   "published":"2026-02-12","title":"Поставка приборов"}
def official(name,body,original=True):
    return {"name":name,"status":"checked","text":body,
      "meta":{"source":"official_eis_original" if original else "unknown",
              "sha256":"a"*64,"source_url":EIS},
      "url":EIS}

PROTOCOL_HEAD=("Протокол рассмотрения заявок. По итогам конкурентной закупки "
               "комиссия рассмотрела документы участников. ")
def test_explicit():
    text=PROTOCOL_HEAD+(
      "По заявке № 12 комиссия приняла решение: отклонена заявка № 12, "
      "основания отклонения не указаны. Итоги рассмотрения зафиксированы.")
    doc=official("Протокол рассмотрения заявок.pdf",text)
    snapshot=deepcopy(doc)
    finds,checks=evaluate(P,[doc])
    assert len(finds)==1,finds
    result=finds[0]
    assert result["kind"]=="violation"
    assert result["code"]=="v372-protocol-rejected-bid-no-reasons"
    assert "ст. 3.2 ч. 13" in result["law"]
    assert result["law_url"]==LAW_PROT
    assert result["source_url"]==EIS
    assert result["document"]==doc["name"]
    assert result["evidence"] and "заявка № 12" in result["evidence"]
    assert snapshot==doc,"Do not change original payload"
    assert len(checks)<=2
    # A verified EIS source is mandatory even when the text admits the error.
    assert evaluate(P,[official(doc["name"],text,False)])==([],[])
    assert evaluate({"method":"Закупка у единственного поставщика"},[doc])==([],[])
    assert evaluate(P,[{"name":doc["name"],"text":text,"status":"checked"}])==([],[])
    # Quotes of hypothetical violations in templates cannot be findings.
    assert evaluate(P,[official(doc["name"],"Образец протокола. "+text)])[0]==[]

def test_protocol_review():
    text=PROTOCOL_HEAD+"Отклонена заявка № 24 в связи с несоответствием требованиям, заявка не допущена. "
    findings,checks=evaluate(P,[official("Итоговый протокол.pdf",text)])
    assert not findings
    assert any(c["field_path"]=="v372-protocol-rejected-bid-document-clause" for c in checks)
    assert all(c["status"]=="check" for c in checks)
    assert all(c["law_url"]==LAW_PROT for c in checks)
    grounded=PROTOCOL_HEAD+"Отклонена заявка № 24 в связи с несоответствием пункту 4.3 документации. "
    finds,checks=evaluate(P,[official("Итоговый протокол.pdf",grounded)])
    assert not finds
    assert not any(x["field_path"]=="v372-protocol-rejected-bid-document-clause" for x in checks)
    # Annexes, scanned or unpublished documents must never yield fake findings.
    assert evaluate(P,[official("Протокол отмены разъяснений.pdf",text)])==([],[])
    assert evaluate(P,[official("Документация.docx",text)])==([],[])

def test_regulation():
    reg_text=(
      "Положение о закупке товаров и услуг. "
      "Дата утверждения положения о закупке: 01.02.2026. "
      "Дата размещения положения о закупке в ЕИС: 01.03.2026. "
      "Способы закупки: аукцион, запрос котировок. "
    )+"Общие положения закупок и договоров. "*25
    original=official("Положение о закупке.docx",reg_text)
    found,checks=evaluate(P,[original])
    assert not found,"A date declaration in one file is not independent proof of breach"
    paths={x["field_path"] for x in checks}
    assert "v372-regulation-publication-timing" in paths,checks
    assert "v372-regulation-purchase-method" in paths,checks
    for c in checks:
      assert c["status"]=="check"
      assert c["law_url"] in (LAW_REG,LAW_4)
      assert c["evidence"] and c["document"]==original["name"]
    finds,review=evaluate(P,[official("Документация конкурентной закупки.docx",reg_text)])
    assert not finds
    assert not any(x["field_path"].startswith("v372-regulation") for x in review)
    assert evaluate(P,[official("Положение о закупке.docx",reg_text,False)])==([],[])
    # Explicit later approval cannot establish that earlier rules were invalid.
    p={**P,"published":"2026-01-03"}
    _,review=evaluate(p,[original])
    assert any(c["field_path"]=="v372-regulation-version" for c in review)
    assert all(c["status"]=="check" for c in review)

def test_integrated_auto():
    proto=official("Протокол рассмотрения заявок.pdf",
        PROTOCOL_HEAD+"По заявке № 18 отклонена заявка № 18, основания отклонения не указаны.")
    ns={"analyze":lambda p,docs:[{"kind":"violation","code":"old",
                                  "title":"Существующее нарушение"}],
        "build_checks":lambda p,detail,card,docs:[
            {"status":"check","field_path":"prior-review","item":"Старое замечание"}]}
    install(ns)
    assert getattr(ns["analyze"],"__v372__",False)
    finds=ns["analyze"](P,[proto])
    assert len(finds)==2 and finds[0]["code"]=="old" and finds[1]["code"].startswith("v372-")
    checks=ns["build_checks"](P,{}, {},[proto])
    assert checks[0]["field_path"]=="prior-review"
    assert all(x["status"]=="check" for x in checks[1:])
    install(ns)
    assert len(ns["analyze"](P,[proto]))==2,"Second startup must not wrap rules twice"
    # Active entrypoint (not an unused parallel demo or a manual button)
    entry=Path(__file__).with_name("railway_start_v282.py").read_text(encoding="utf-8")
    assert "v372_auto_regulation_protocols.install(vars(entry))" in entry
    assert entry.index("v348_cross_document_legal.install(vars(entry))")<entry.index(
        "v372_auto_regulation_protocols.install(vars(entry))")<entry.index(
        "v342_thematic_queue.install(vars(entry))")
    from v342_thematic_queue import independent_review
    assert 'ns.get("analyze")' in __import__("inspect").getsource(independent_review)


def test_revision_requeue():
    """A new legal revision reopens old results once, preserving evidence.

    Keep the fixture here: this Docker gate runs before tests/ is copied.
    A revision's spelling cannot prove that the actual startup queue works.
    """
    import v332_all_procurements_audit as audit
    with tempfile.TemporaryDirectory(prefix="v372-requeue-") as temporary, patch.dict(
            os.environ,{"EIS223_DATA_DIR":temporary}):
        directory=Path(temporary)
        db_path=directory/"audit.sqlite"
        legacy,current="32619000101","32619000102"
        def connect():
            connection=sqlite3.connect(db_path)
            connection.row_factory=sqlite3.Row
            return connection
        def snapshot(table):
            connection=connect()
            try:
                return [tuple(row) for row in connection.execute("SELECT * FROM "+table+" ORDER BY 1")]
            finally:connection.close()
        ns={"conn":connect,"_verified_set235":lambda:[legacy,current]}
        connection=connect()
        try:
            connection.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,last_seen TEXT,
                    documents_checked INTEGER,violations INTEGER);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,url TEXT,
                    local_path TEXT,status TEXT,metadata_json TEXT);
                CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,evidence TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,evidence TEXT);
            """)
            for reg in (legacy,current):
                data="<document>Сохранённый оригинал ЕИС</document>".encode("utf-8")
                digest=hashlib.sha256(data).hexdigest()
                folder=directory/"eis223_official_originals"/reg/("A"*32)
                folder.mkdir(parents=True)
                original=folder/(digest+".xml")
                original.write_bytes(data)
                (folder/"receipt.json").write_text(json.dumps({
                    "reg_number":reg,"uid":"A"*32,"sha256":digest,
                    "extension":".xml","bytes":len(data)}),encoding="utf-8")
                manifest=directory/"eis223_public_browser_intake"/reg/"manifest.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(json.dumps({"reg_number":reg,"source":"public_eis_223_browser",
                    "documents":[{"name":"Оригинал.xml","url":EIS}]}),encoding="utf-8")
                connection.execute("INSERT INTO purchases VALUES(?,'ООО Тест','2026-10-19',1,1)",(reg,))
                connection.execute("INSERT INTO docs(reg_number,name,url,local_path,status,metadata_json) "
                    "VALUES(?,'Оригинал.xml',?,?,'checked',?)",(reg,EIS,str(original),json.dumps({
                    "source":"official_eis_original","source_url":EIS,"sha256":digest})))
                connection.execute("INSERT INTO findings(reg_number,code,evidence) VALUES(?,'prior-rule','Сохранённое доказательство')",(reg,))
                connection.execute("INSERT INTO checks(reg_number,status,evidence) VALUES(?,'check','Сохранённое замечание')",(reg,))
            connection.commit()
        finally:connection.close()
        retained={table:snapshot(table) for table in ("purchases","docs","findings","checks")}
        original_files={path:path.read_bytes() for path in directory.rglob("*") if path.is_file() and path!=db_path}
        audit.initialize(ns)
        for reg in (legacy,current):
            claim=audit.claim_next(ns)
            assert claim==reg
            watermark=audit.review_watermark(ns,claim)
            audit.mark(ns,claim,"completed",{**watermark,"documents_found":1,"documents_read":1,
                "verified_original_documents":1,"original_expected":1,"original_received":1,
                "new_findings":1,"new_checks":1})
        connection=connect()
        try:
            # Only the rule version changes: source identity and doc ids are
            # the exact values recorded by the real completion path above.
            connection.execute("UPDATE v332_audit_queue SET audit_revision=?,last_engine_signature=? WHERE reg=?",
                               ("selftest-previous-legal-revision","selftest-previous-engine",legacy))
            connection.commit()
        finally:connection.close()
        assert audit.initialize(ns)["states"]=={"completed":2}, "Startup defers catch-up to bounded sweeps"
        assert audit.refresh_changed(ns)==1, "A bounded sweep must admit the stale legal engine"
        assert audit.progress(ns)["states"]=={"pending":1,"completed":1}
        queued=snapshot("v332_audit_queue")
        audit.initialize(ns)
        assert snapshot("v332_audit_queue")==queued,"Repeated startup must not reset queued work"
        assert audit.refresh_changed(ns)==0
        claim=audit.claim_next(ns)
        assert claim==legacy,"Only the outdated completed purchase should be claimed"
        assert audit.claim_next(ns) is None,"A running recheck must not be claimed twice"
        audit.mark(ns,claim,"completed",audit.review_watermark(ns,claim))
        completed=snapshot("v332_audit_queue")
        assert audit.initialize(ns)["states"]=={"completed":2}
        assert snapshot("v332_audit_queue")==completed,"Current completed results must remain completed"
        assert audit.refresh_changed(ns)==0
        assert audit.claim_next(ns) is None
        assert audit.progress(ns)["new_evidence_based_findings"]==2,"Requeue must preserve recorded counters"
        assert {table:snapshot(table) for table in retained}==retained,\
            "A rule revision must preserve historical findings, checks and document rows"
        assert {path:path.read_bytes() for path in original_files}==original_files,\
            "A rule revision must preserve original files and receipts"


if __name__=="__main__":
    test_explicit()
    test_protocol_review()
    test_regulation()
    test_integrated_auto()
    test_revision_requeue()
    print("V372_AUTO_LEGAL_SELFTEST_OK real_protocols=True regulations=True "
          "no_absence_inferred_violation=True receipt_only=True "
          "generic_and_thematic=True automatic_reaudit=True idempotent_install=True "
          "evidence_unmodified=True",flush=True)
