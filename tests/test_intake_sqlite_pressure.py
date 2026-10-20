"""Synthetic concurrency regressions; no live EIS or production DB is used."""
import asyncio
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import eis223_soap_xml as xml_reader
import v294_eis_bridge as bridge
import v298_eis_only as offline
import v334_findings_ledger as ledger
import v357_storage_guard as storage
import test_eis223_soap_archive as archive_fixture
fixture,archive,REG,TOKEN=(archive_fixture.fixture,archive_fixture.archive,
                          archive_fixture.REG,archive_fixture.TOKEN)


class MetadataPressureTests(unittest.TestCase):
    setUp=archive_fixture.OfficialSoapArchiveTests.setUp
    upload=archive_fixture.OfficialSoapArchiveTests.upload

    def connect(self):
        db=sqlite3.connect(self.dbfile,timeout=.04)
        db.row_factory=sqlite3.Row
        return db

    def test_unchanged_soap_revision_needs_no_write_lock(self):
        self.assertEqual(self.upload(archive(fixture())).status_code,200)
        with closing(self.connect()) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO findings VALUES(?,?)",(REG,"unrelated in-flight evidence"))
            traces=[]
            def traced():
                db=self.connect();db.set_trace_callback(traces.append);return db
            result=offline.import_soap_metadata({"conn":traced},REG)
            self.assertEqual(result,{"stored":True,"changed":False,"reg_number":REG})
            self.assertFalse(any(sql.startswith(("BEGIN","UPDATE","INSERT","DELETE","COMMIT")) for sql in traces),traces)
            writer.rollback()

    def test_changed_revision_does_not_falsely_acknowledge_busy_write(self):
        self.assertEqual(self.upload(archive(fixture(version=1))).status_code,200)
        with patch.dict(self.ns,{"_v379_import_soap_metadata":None}):
            self.assertEqual(self.upload(archive(fixture(version=2))).status_code,200)
        with closing(self.connect()) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                offline.import_soap_metadata(self.ns,REG)
            self.assertIn("редакция 1",writer.execute("SELECT title FROM purchases").fetchone()[0])
            writer.rollback()
        self.assertTrue(offline.import_soap_metadata(self.ns,REG)["changed"])

    def test_rechecks_newer_revision_after_acquiring_writer(self):
        self.upload(archive(fixture(version=1)))
        with patch.dict(self.ns,{"_v379_import_soap_metadata":None}):
            self.upload(archive(fixture(version=2)))
        newer=xml_reader.parse_document(fixture(version=3)[1],fixture(version=3)[0])[0]
        calls=[]
        def conn():
            if calls:
                with closing(self.connect()) as other:
                    card=json.loads(other.execute("SELECT card_fields_json FROM purchases").fetchone()[0])
                    card["soap_metadata"]=newer
                    other.execute("UPDATE purchases SET title=?,card_fields_json=? WHERE reg_number=?",
                                  ("newer committed title",json.dumps(card),REG))
                    other.commit()
            db=self.connect();calls.append(db);return db
        result=offline.import_soap_metadata({"conn":conn},REG)
        self.assertFalse(result["changed"])
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT title FROM purchases").fetchone()[0],"newer committed title")
        for db in calls:
            with self.assertRaises(sqlite3.ProgrammingError):db.execute("SELECT 1")

    def test_noop_does_not_change_originals_findings_or_manual_membership(self):
        self.upload(archive(fixture()))
        with closing(self.connect()) as db:
            db.execute("UPDATE purchases SET violations=7,documents_checked=9,manual=1")
            db.execute("INSERT INTO findings VALUES(?,?)",(REG,"saved evidence"));db.commit()
        files={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob("*.zip")}
        for _ in range(3):self.assertFalse(offline.import_soap_metadata(self.ns,REG)["changed"])
        with closing(self.connect()) as db:
            self.assertEqual(tuple(db.execute("SELECT violations,documents_checked,manual FROM purchases").fetchone()),(7,9,1))
            self.assertEqual(db.execute("SELECT evidence FROM findings").fetchone()[0],"saved evidence")
        self.assertEqual(files,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob("*.zip")})


class LedgerPressureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"test.db";self.trace=[]
        with closing(self.connect()) as db:
            db.executescript("CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,kind TEXT,title TEXT);"
                             "CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,item TEXT);"
                             "CREATE TABLE unrelated(id INTEGER);")
            db.executescript(ledger.SCHEMA)
            db.execute("INSERT INTO findings VALUES(1,?,'violation','saved evidence')",(REG,))
            db.commit()
        self.trace.clear()

    def connect(self):
        db=sqlite3.connect(self.path,timeout=.04);db.row_factory=sqlite3.Row
        db.set_trace_callback(self.trace.append);return db

    def test_noop_snapshot_works_during_unrelated_write(self):
        before=ledger.snapshot(self.connect,REG)
        with closing(self.connect()) as writer:
            writer.execute("BEGIN IMMEDIATE");writer.execute("INSERT INTO unrelated VALUES(1)")
            self.trace.clear()
            self.assertEqual(ledger.snapshot(self.connect,REG),before)
            self.assertFalse(any(sql.startswith(("BEGIN","INSERT","UPDATE","DELETE","COMMIT")) for sql in self.trace),self.trace)
            writer.rollback()

    def test_hashing_never_holds_writer(self):
        real=ledger._encode_records
        def encode(rows):
            with closing(self.connect()) as other:
                other.execute("INSERT INTO unrelated VALUES(1)");other.commit()
            return real(rows)
        with patch.object(ledger,"_encode_records",side_effect=encode):
            self.assertEqual(len(ledger.snapshot(self.connect,REG)),1)

    def test_changed_source_rebases_before_archiving(self):
        real=ledger._encode_records;calls=[]
        def encode(rows):
            if not calls:
                with closing(self.connect()) as other:
                    other.execute("UPDATE findings SET title='concurrent correction'");other.commit()
            calls.append(1)
            return real(rows)
        with patch.object(ledger,"_encode_records",side_effect=encode):
            result=ledger.snapshot(self.connect,REG)
        self.assertEqual(len(calls),2)
        with closing(self.connect()) as db:
            stored=db.execute("SELECT payload_json FROM v334_violation_history").fetchall()
            self.assertEqual(len(stored),1)
            self.assertEqual(json.loads(stored[0][0])["title"],"concurrent correction")
            self.assertEqual(result,{(r[1],r[2]) for r in ledger._records(db,REG)})

    def test_busy_real_write_is_not_silently_lost(self):
        with closing(self.connect()) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):ledger.snapshot(self.connect,REG)
            writer.rollback()
        self.assertEqual(len(ledger.snapshot(self.connect,REG)),1)

    def test_append_only_history_and_empty_snapshot(self):
        first=ledger.snapshot(self.connect)
        with closing(self.connect()) as db:
            db.execute("UPDATE findings SET title='revised evidence'")
            db.execute("INSERT INTO checks VALUES(1,?,'violation','a different act')",(REG,));db.commit()
        second=ledger.snapshot(self.connect)
        self.assertNotEqual(first,second)
        self.assertEqual(second,ledger.snapshot(self.connect))
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v334_violation_history").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM findings").fetchone()[0],1)
        self.trace.clear()
        self.assertEqual(ledger.snapshot(self.connect,"32616233662"),set())
        self.assertFalse(any(sql.startswith("BEGIN") for sql in self.trace))


class AsyncIntakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{"EIS223_DATA_DIR":self.tmp.name,"EIS223_UPLOAD_TOKEN":TOKEN})
        env.start();self.addCleanup(env.stop)
        self.app=FastAPI();bridge.install({"app":self.app})

    async def test_parse_and_fsync_are_off_http_thread(self):
        loop_thread=threading.get_ident();parse=bridge.parse_notices;write=bridge._atomic
        calls=[]
        def outside(fn,*args):
            self.assertNotEqual(threading.get_ident(),loop_thread)
            calls.append(fn.__name__);return fn(*args)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test") as client:
            with patch.object(bridge,"parse_notices",side_effect=lambda *a:outside(parse,*a)), \
                 patch.object(bridge,"_atomic",side_effect=lambda *a:outside(write,*a)):
                response=await client.post("/api/v294/eis223/archive",content=archive(fixture()),
                                           headers={"x-eis223-upload-token":TOKEN})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(calls,["parse_notices","_atomic","_atomic"])

    async def test_heartbeat_is_responsive_while_archive_is_parsed(self):
        entered,release=threading.Event(),threading.Event()
        real=bridge.parse_notices
        def slow(raw):
            entered.set()
            if not release.wait(3):raise RuntimeError("test parser wait expired")
            return real(raw)
        @self.app.post("/heartbeat-probe")
        async def heartbeat():return {"ok":True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test") as client:
            with patch.object(bridge,"parse_notices",side_effect=slow):
                task=asyncio.create_task(client.post("/api/v294/eis223/archive",content=archive(fixture()),
                                                   headers={"x-eis223-upload-token":TOKEN}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,2))
                    response=await asyncio.wait_for(client.post("/heartbeat-probe"),1)
                    self.assertEqual(response.status_code,200)
                    self.assertFalse(task.done())
                finally:
                    release.set()
                self.assertEqual((await task).status_code,200)

    async def test_concurrent_archive_replay_does_not_double_credit(self):
        raw=archive(fixture())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test") as client:
            async def upload():
                return await client.post("/api/v294/eis223/archive",content=raw,headers={"x-eis223-upload-token":TOKEN})
            responses=await asyncio.gather(upload(),upload(),upload())
        self.assertEqual([x.status_code for x in responses],[200]*3)
        self.assertEqual(sorted(x.json()["already_received"] for x in responses),[False,True,True])
        self.assertTrue(all(x.json()["audited_documents_increment"]==0 for x in responses))
        saved=list(Path(self.tmp.name).rglob("*.zip"))
        self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_bytes(),raw)

    async def test_identity_mismatch_still_rejected_with_safe_diagnostics(self):
        name,raw=fixture()
        raw=raw.replace(f"<registrationNumber>{REG}</registrationNumber>".encode(),b"<registrationNumber>32616233662</registrationNumber>")
        logs=io.StringIO()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test") as client:
            with redirect_stdout(logs):
                response=await client.post("/api/v294/eis223/archive",content=archive((name,raw)),headers={"x-eis223-upload-token":TOKEN})
        self.assertEqual(response.status_code,422)
        self.assertIn('"filename_reg": "'+REG+'"',logs.getvalue())
        self.assertIn('"own_reg": "32616233662"',logs.getvalue())
        self.assertNotIn(TOKEN,logs.getvalue());self.assertNotIn("https://",logs.getvalue())
        self.assertFalse(list(Path(self.tmp.name).rglob("*.zip")))

    def test_untrusted_identity_text_is_never_logged(self):
        secret="SECRET-token-url-never-log"
        error=xml_reader.IdentityMismatch(secret,secret,secret,secret,secret)
        self.assertNotIn(secret,json.dumps(error.diagnostic))
        self.assertEqual(set(error.diagnostic.values()),{"unknown","unsupported","invalid"})


class StorageDiagnosticTests(unittest.TestCase):
    def test_sqlite_diagnostic_does_not_change_journal_or_data(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"test.db"
            with closing(sqlite3.connect(path)) as db:db.execute("CREATE TABLE kept(id)")
            trace=[]
            def conn():
                db=sqlite3.connect(path);db.set_trace_callback(trace.append);return db
            stream=io.StringIO()
            with redirect_stdout(stream):storage.install({"app":FastAPI(),"conn":conn})
            self.assertIn("read_only_diagnostic=True",stream.getvalue())
            self.assertNotIn(str(path),stream.getvalue())
            self.assertFalse(any("=" in q or q.startswith(("BEGIN","UPDATE","INSERT","DELETE")) for q in trace),trace)


if __name__=="__main__":unittest.main()
