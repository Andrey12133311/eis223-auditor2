"""Bounded, local-only intake revisits independent of general/thematic campaigns."""
import asyncio
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
import zipfile
from unittest.mock import patch

from fastapi import FastAPI
from starlette.requests import Request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v295_eis_originals as originals

REG="32616233661"
UID="00000000000000000000000000000001"


class IntakeAutomaticRechecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        env=patch.dict(os.environ,{"EIS223_DATA_DIR":str(self.root)})
        env.start();self.addCleanup(env.stop)
        self.db_path=self.root/"test.db"
        def connect():
            con=sqlite3.connect(self.db_path,timeout=.1)
            con.row_factory=sqlite3.Row
            return con
        self.connect=connect
        con=connect()
        con.executescript('''CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT DEFAULT 'ООО Тест',
        published_at TEXT DEFAULT '2026-10-19',documents_found INTEGER DEFAULT 0,documents_checked INTEGER DEFAULT 0);
        CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,url TEXT,local_path TEXT,status TEXT,metadata_json TEXT);
        CREATE TABLE protected_findings(reg TEXT PRIMARY KEY,evidence TEXT);''')
        con.close()
        self.calls=[];self.engine="engine-a";self.card={};self.block=None;self.error=None;self.checked=True
        self.snapshot_error=None;self.apps=[]
        def versions(ns,reg):
            if self.snapshot_error:raise self.snapshot_error
            return {"source_version":originals._review_source_version(reg),
                    "semantic_version":hashlib.sha256((originals._review_source_version(reg)+self.card.get(reg,"")).encode()).hexdigest(),
                    "engine_signature":self.engine}
        mocked=patch.object(originals,"_review_input_versions",side_effect=versions)
        mocked.start();self.addCleanup(mocked.stop)
        self.snapshot_patch=mocked
        self.ns=self.install()
        self.prepare(REG)

    def install(self):
        async def audit(reg):
            self.calls.append((reg,self.engine,self.card.get(reg,"")))
            if self.block is not None:await self.block.wait()
            if self.error is not None:raise self.error
            con=self.connect()
            con.execute("UPDATE docs SET status=? WHERE reg_number=?",("checked" if self.checked else "downloaded_no_text",reg))
            con.commit();con.close()
            return {"checked_documents":int(self.checked),"findings":3}
        async def automatic(ns,reg):
            result=await audit(reg)
            with ns["_v295_persist_guard"](reg):pass
            return ("completed",{"documents_read":result["checked_documents"],
                "verified_original_documents":result["checked_documents"],"documents_unreadable":int(not self.checked),
                "original_received":1,"original_expected":1,"source_incomplete":not self.checked,"new_findings":0})
        ns={"app":FastAPI(),"conn":self.connect,"lock":asyncio.Lock(),"_v298_offline_audit":audit,
            "_v295_automatic_review":automatic}
        originals.install(ns);self.apps.append(ns["app"])
        return ns

    def prepare(self,reg):
        doc={"uid":UID,"name":"fixture.pdf","ext":".pdf",
             "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+UID}
        path=self.root/"eis223_public_browser_intake"/reg/"manifest.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({"source":"public_eis_223_browser","reg_number":reg,"documents":[doc]}))
        originals._store_original_version(reg,UID,doc,b"%PDF-1.7\nintake fixture")
        original=originals.saved_document(reg,UID)[0]
        con=self.connect()
        con.execute("INSERT OR IGNORE INTO purchases(reg_number) VALUES(?)",(reg,))
        con.execute("INSERT INTO docs(reg_number,name,url,local_path,status,metadata_json) VALUES(?,?,?,?,?,?)",
                    (reg,doc["name"],doc["url"],str(original),"checked","{}"))
        con.execute("INSERT OR IGNORE INTO protected_findings VALUES(?,?)",(reg,"previous complete evidence"))
        con.commit();con.close()
        return doc

    def state(self,reg=REG):
        path=self.root/"eis223_official_review_jobs"/(reg+".json")
        return json.loads(path.read_text()) if path.exists() else {}

    def store_state(self,data,reg=REG):
        path=self.root/"eis223_official_review_jobs"/(reg+".json");path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"reg_number":reg,**data}))

    async def settled(self,count=1,status="completed",reg=REG):
        for _ in range(200):
            if len(self.calls)>=count and self.state(reg).get("status")==status:
                await asyncio.sleep(.03)
                return
            await asyncio.sleep(.005)
        self.fail((self.calls,self.state(reg)))

    async def tick(self,source="purchases",ns=None,after=None):
        folder=self.root/"eis223_official_review_jobs";folder.mkdir(exist_ok=True)
        cursor_path=folder/".revisit-cursor.json"
        cursor=json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
        sources=("purchases","eis223_official_review_jobs","eis223_official_archive_inbox",
                 "eis223_public_browser_intake","eis223_official_originals")
        cursor["source_index"]=sources.index(source)
        if after is not None:cursor[source]=after
        cursor_path.write_text(json.dumps(cursor))
        return await (ns or self.ns)["_v295_review_tick"]()

    async def asyncTearDown(self):
        for app in self.apps:
            for handler in app.router.on_shutdown:await handler()
        await asyncio.sleep(.01)

    async def test_engine_change_rechecks_once_and_unchanged_replay_does_not(self):
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        self.engine="engine-b"
        self.assertEqual((await self.tick())["admitted"],1)
        await self.settled(2)
        self.assertEqual(self.state()["last_engine_signature"],"engine-b")
        for _ in range(3):await self.tick(after="")
        self.assertEqual(len(self.calls),2)

    async def test_card_change_rechecks_once_without_original_byte_change(self):
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        source=self.state()["last_source_version"]
        self.card[REG]="authoritative new deadline"
        await self.tick();await self.settled(2)
        self.assertEqual(self.state()["last_source_version"],source)
        await self.tick(after="");self.assertEqual(len(self.calls),2)

    async def test_engine_change_while_running_keeps_preanalysis_watermark_and_rechecks(self):
        self.block=asyncio.Event()
        self.ns["_v295_enqueue_review"](REG)
        while not self.calls:await asyncio.sleep(.005)
        self.assertNotIn("last_engine_signature",self.state())
        self.engine="engine-b";self.block.set();self.block=None
        await self.settled(2)
        self.assertEqual([entry[1] for entry in self.calls],["engine-a","engine-b"])
        self.assertEqual(self.state()["last_engine_signature"],"engine-b")

    async def test_card_change_while_running_is_not_acknowledged_as_already_checked(self):
        self.block=asyncio.Event();self.ns["_v295_enqueue_review"](REG)
        while not self.calls:await asyncio.sleep(.005)
        self.card[REG]="corrected card";self.block.set();self.block=None
        await self.settled(2)
        self.assertEqual([entry[2] for entry in self.calls],["","corrected card"])

    async def test_restart_keeps_success_and_resumes_interrupted_old_revision(self):
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        restarted=self.install();await self.tick(ns=restarted)
        self.assertEqual(len(self.calls),1)
        self.store_state({**self.state(),"status":"running","automatic":True})
        self.engine="engine-b";await self.tick(ns=restarted,after="");await self.settled(2)
        self.assertEqual(self.state()["last_engine_signature"],"engine-b")

    async def completion_window(self,*,restart=False,force=False):
        import v381_audit_scheduling as scheduling
        written=asyncio.Event();release=asyncio.Event();real=scheduling.run_file_io
        async def hold_completion(function,*args,**kwargs):
            value=await real(function,*args,**kwargs)
            if isinstance(value,dict) and value.get("status")=="completed" and not written.is_set():
                written.set();await release.wait()
            return value
        with patch.object(scheduling,"run_file_io",side_effect=hold_completion):
            try:
                self.ns["_v295_enqueue_review"](REG)
                await asyncio.wait_for(written.wait(),5)
                previous=self.state();token=previous["lease_token"]
                self.assertEqual(self.ns["_v295_enqueue_review"](REG)["status"],"completed")
                if not force:
                    doc={"uid":UID,"name":"fixture.pdf","ext":".pdf",
                         "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+UID}
                    originals._store_original_version(REG,UID,doc,b"%PDF-1.7\narrived during completion")
                queued=self.ns["_v295_enqueue_review"](REG,force=force)
                self.assertEqual(queued["status"],"queued")
                self.assertTrue(self.state()["pending_arrival"])
                self.assertEqual(self.state()["lease_token"],token)
                self.assertEqual(self.state()["last_source_version"],previous["last_source_version"])
                public=self.ns["_v295_review_state"](REG)
                self.assertEqual(public["status"],"queued")
                self.assertEqual(public["successful_coverage"],previous["successful_coverage"])
                restarted=self.install()
                self.assertEqual(restarted["_v295_enqueue_review"](REG,force=force)["status"],"queued")
                self.assertEqual((await self.tick(ns=restarted,after=""))["admitted"],0)
                self.assertEqual(len(self.calls),1)
                if restart:
                    for handler in self.ns["app"].router.on_shutdown:await handler()
                    self.assertTrue(self.state()["pending_arrival"])
                release.set()
                if restart:self.assertEqual((await self.tick(ns=restarted,after=""))["admitted"],1)
                await self.settled(2)
                self.assertFalse(self.state()["pending_arrival"])
                self.assertTrue(self.state()["automatic"])
                self.assertEqual(self.state()["last_source_version"],originals._review_source_version(REG))
                await self.tick(ns=restarted,after="")
                await asyncio.sleep(.05)
                self.assertEqual(len(self.calls),2)
            finally:release.set()

    async def test_changed_upload_during_completion_is_durably_queued_once(self):
        await self.completion_window()

    async def test_completion_arrival_survives_shutdown_before_finish_callback(self):
        await self.completion_window(restart=True)

    async def test_forced_repair_during_completion_is_not_lost_with_same_fingerprint(self):
        await self.completion_window(force=True)

    async def test_first_boot_does_not_launch_unknown_legacy_completion(self):
        self.store_state({"status":"completed","originals_received":1})
        for handler in self.ns["app"].router.on_startup:await handler()
        await asyncio.sleep(.03)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.ns["_v295_enqueue_review"](REG)["status"],"completed")
        await self.tick();await self.settled()
        self.assertEqual(self.state()["last_engine_signature"],"engine-a")

    async def test_later_database_page_past_500_and_cursor_restart(self):
        later="99999999999";self.prepare(later)
        con=self.connect()
        con.executemany("INSERT OR IGNORE INTO purchases(reg_number) VALUES(?)",
                        ((str(10000000000+i),) for i in range(530)))
        con.commit();con.close()
        with patch.object(originals,"REVIEW_SCAN_PAGE",31):
            for _ in range(10):await self.tick()
            cursor_path=self.root/"eis223_official_review_jobs"/".revisit-cursor.json"
            before=json.loads(cursor_path.read_text())["purchases"]
            self.assertTrue(before)
            restarted=self.install()
            for _ in range(10):await self.tick(ns=restarted)
        await self.settled(2,reg=later)
        self.assertIn(later,[entry[0] for entry in self.calls])

    async def test_orphan_saved_receipts_beyond_500_are_not_truncated(self):
        for i in range(520):(self.root/"eis223_public_browser_intake"/str(10000000000+i)).mkdir()
        later="99999999999";self.prepare(later)
        con=self.connect();con.execute("DELETE FROM purchases WHERE reg_number=?",(later,));con.commit();con.close()
        # A slow host may exhaust a sweep before all 32 rows are examined.
        # Control only this module's clocks, leaving asyncio and SQLite real.
        elapsed=[0.0];wall=[originals.time.time()];seen=[]
        clock=SimpleNamespace(monotonic=lambda:elapsed[0],time=lambda:wall[0],
                              strftime=originals.time.strftime,gmtime=originals.time.gmtime)
        has_intake=originals._has_review_intake
        def budgeted_intake(reg):
            seen.append(reg);elapsed[0]+=.25
            return has_intake(reg)

        # Reproduce a real slow SQLite commit's exclusive lock at the tail
        # page: the orphan is adopted while the other review opens its DB.
        # The old eager two-calls assertion failed here despite the orphan
        # succeeding and the other procurement retaining durable retry state.
        locked=threading.Event();read_failed=threading.Event();errors=[]
        class SlowCommit(sqlite3.Connection):
            def execute(connection,sql,parameters=()):
                if sql=="BEGIN IMMEDIATE" and not locked.is_set():
                    result=super().execute("BEGIN EXCLUSIVE")
                    locked.set()
                    if not read_failed.wait(5):
                        raise AssertionError("The competing review did not encounter the held SQLite lock")
                    return result
                return super().execute(sql,parameters)
        def slow_connect():
            db=sqlite3.connect(self.db_path,timeout=.1,factory=SlowCommit)
            db.row_factory=sqlite3.Row
            return db
        restore=originals._restore_intake_rows;initial_repairs=[0]
        def concurrent_restore(ns,reg):
            if reg==REG:
                initial_repairs[0]+=1
                if initial_repairs[0]==2:  # Worker, after the scan's pointer check.
                    if not locked.wait(5):raise AssertionError("Orphan adoption did not acquire its SQLite lock")
                    try:return restore(ns,reg)
                    except sqlite3.OperationalError as exc:
                        errors.append(str(exc));read_failed.set();raise
            return restore(ns,reg)

        source="eis223_public_browser_intake"
        with patch.object(originals,"time",clock),patch.object(originals,"_has_review_intake",side_effect=budgeted_intake),\
                patch.object(originals,"_restore_intake_rows",side_effect=concurrent_restore):
            ns=self.ns;ns["conn"]=slow_connect
            # 522 names at 20 examined rows per five-second sweep: exactly 27
            # bounded passes, with the persisted cursor surviving a restart.
            for index in range(27):
                if index==10:
                    ns=self.install();ns["conn"]=slow_connect
                result=await self.tick(source=source,ns=ns)
                self.assertEqual(result["visited"],20 if index<26 else 2)
                self.assertLessEqual(result["admitted"],originals.MAX_REVIEW_ADMISSIONS)
                cursor=json.loads((self.root/"eis223_official_review_jobs"/".revisit-cursor.json").read_text())
                self.assertEqual(cursor[source],seen[-1] if index<26 else "")
            self.assertEqual(seen,sorted([str(10000000000+i) for i in range(520)]+[REG,later]))
            await self.settled(1,reg=later)
            self.assertEqual(errors,["database is locked"])
            self.assertEqual([entry[0] for entry in self.calls],[later])
            failed=self.state(REG)
            self.assertEqual((failed["status"],failed["reason"],failed["attempts"]),("failed","OperationalError",1))
            self.assertGreater(failed["next_retry_at"],wall[0])
            self.assertNotIn("last_semantic_version",failed)

            # Retry survives a second restart, cannot run early, then resumes
            # at its actual persisted deadline. No state/backoff is rewritten.
            restarted=self.install()
            self.assertEqual((await self.tick(source="eis223_official_review_jobs",ns=restarted,after=""))["admitted"],0)
            self.assertEqual(self.state(REG)["next_retry_at"],failed["next_retry_at"])
            wall[0]=failed["next_retry_at"]
            self.assertEqual((await self.tick(source="eis223_official_review_jobs",ns=restarted,after=""))["admitted"],1)
            await self.settled(2)
            self.assertCountEqual([entry[0] for entry in self.calls],[REG,later])
            for reg in (REG,later):
                self.assertEqual(self.state(reg)["status"],"completed")
                self.assertEqual(self.state(reg)["next_retry_at"],0)
            for _ in range(3):
                self.assertEqual((await self.tick(source=source,ns=restarted,after="10000000519"))["admitted"],0)
            self.assertEqual(len(self.calls),2)

    async def test_admissions_and_live_tasks_are_bounded_and_excess_uploads_durable(self):
        regs=[REG]+[str(32616233662+i) for i in range(9)]
        for reg in regs[1:]:self.prepare(reg)
        self.block=asyncio.Event()
        with patch.object(originals,"MAX_REVIEW_TASKS",3),patch.object(originals,"MAX_REVIEW_ADMISSIONS",2):
            self.assertEqual((await self.tick())["admitted"],2)
            for reg in regs:self.ns["_v295_enqueue_review"](reg)
            await asyncio.sleep(.03)
            self.assertEqual(len(self.calls),1)  # Existing shared single-review gate.
            self.assertEqual(sum(self.state(reg).get("status")=="queued" for reg in regs),9)
            self.assertEqual((await self.tick(after=""))["admitted"],0)
            self.block.set();self.block=None
            await self.settled(3)
            await self.tick(source="eis223_official_review_jobs",after="")
            for _ in range(6):
                await self.tick(source="eis223_official_review_jobs")
                await asyncio.sleep(.025)
                if len(self.calls)>=5:break
            await self.settled(5,reg=regs[3])

    async def test_explicit_retry_queued_at_capacity_resets_exhausted_backoff(self):
        self.store_state({"status":"failed","originals_received":1,"attempts":4,
                          "retry_exhausted":True,"next_retry_at":originals.time.time()+3600})
        with patch.object(originals,"MAX_REVIEW_TASKS",0):
            queued=self.ns["_v295_enqueue_review"](REG,force=True)
        self.assertEqual(queued["status"],"queued")
        self.assertFalse(queued["retry_exhausted"])
        self.assertEqual(queued["next_retry_at"],0)
        await self.tick();await self.settled()

    async def test_saved_history_explicit_retry_at_capacity_resets_exhausted_backoff(self):
        self.store_state({"status":"failed","originals_received":1,"attempts":4,
                          "successful_coverage":{"checked_documents":1},
                          "retry_exhausted":True,"next_retry_at":originals.time.time()+3600})
        with patch.object(originals,"MAX_REVIEW_TASKS",0):
            queued=self.ns["_v295_enqueue_review"](REG,force=True)
        self.assertEqual(queued["status"],"queued")
        self.assertTrue(queued["automatic"])
        self.assertFalse(queued["retry_exhausted"])
        self.assertEqual(queued["next_retry_at"],0)
        self.assertEqual(queued["attempts"],0)
        await self.tick();await self.settled()

    async def test_failed_attempt_backoff_survives_restart_and_preserves_completed_watermark(self):
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        old=self.state();self.engine="engine-b";self.error=OSError("transient")
        await self.tick();await self.settled(2,status="failed")
        self.assertEqual(self.state()["last_engine_signature"],old["last_engine_signature"])
        self.assertEqual(self.state()["findings"],3)
        self.assertGreater(self.state()["next_retry_at"],originals.time.time())
        restarted=self.install();await self.tick(ns=restarted,after="")
        self.assertEqual(len(self.calls),2)
        self.error=None;self.store_state({**self.state(),"next_retry_at":0})
        await self.tick(ns=restarted,after="");await self.settled(3)
        self.assertEqual(self.state()["last_engine_signature"],"engine-b")
        con=self.connect();self.assertEqual(con.execute("SELECT evidence FROM protected_findings").fetchone()[0],"previous complete evidence");con.close()

    async def test_unchanged_partial_stops_after_bounded_attempts_new_evidence_resets(self):
        self.checked=False
        with patch.object(originals,"MAX_AUTOMATIC_ATTEMPTS",2):
            await self.tick();await self.settled(status="partial")
            self.store_state({**self.state(),"next_retry_at":0})
            await self.tick(after="");await self.settled(2,status="partial")
            self.assertTrue(self.state()["retry_exhausted"])
            self.store_state({**self.state(),"next_retry_at":0})
            for _ in range(3):await self.tick(after="")
            self.assertEqual(len(self.calls),2)
            self.checked=True;self.engine="engine-b"
            await self.tick(after="");await self.settled(3)
        self.assertEqual(self.state()["attempts"],0)

    async def test_snapshot_error_is_backed_off_without_running_engine_or_marking_success(self):
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        old=self.state();self.snapshot_error=OSError("snapshot unavailable")
        await self.tick()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.state()["last_semantic_version"],old["last_semantic_version"])
        await self.tick(after="")
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.state()["attempts"],1)

    async def test_general_and_active_thematic_owned_records_are_skipped(self):
        from v332_all_procurements_audit import CAMPAIGN
        thematic="32616233662";self.prepare(thematic)
        con=self.connect()
        con.executescript('''CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT);
        CREATE TABLE thematic_batches(id TEXT,status TEXT,legal_forms TEXT,date_from TEXT,date_to TEXT);
        CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT);''')
        con.execute("INSERT INTO v332_audit_queue VALUES(?,?,?)",(CAMPAIGN,REG,"completed"))
        con.execute("INSERT INTO thematic_batches VALUES('batch','active',?,'2026-10-01','2026-10-31')",(json.dumps(["ООО"]),))
        con.execute("INSERT INTO thematic_items VALUES('batch',?,'running')",(thematic,))
        con.commit();con.close()
        self.assertEqual((await self.tick())["admitted"],0)
        self.assertEqual(self.calls,[])

    async def test_stale_thematic_scope_does_not_block_changed_engine(self):
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        con=self.connect()
        con.executescript('''CREATE TABLE thematic_batches(id TEXT,status TEXT,legal_forms TEXT,date_from TEXT,date_to TEXT);
        CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT);''')
        con.execute("INSERT INTO thematic_batches VALUES('batch','active',?,'2026-10-01','2026-10-31')",(json.dumps(["ООО"]),))
        con.execute("INSERT INTO thematic_items VALUES('batch',?,'completed')",(REG,))
        con.commit();con.close()
        calls=1
        # Both current eligibility and stale item statuses matter: a status flag
        # alone must neither claim an excluded purchase nor release a valid one.
        cases=[("2026-01-01","ООО Тест","outside_period",False),
               ("2026-01-01","ООО Тест","completed",False),
               ("2026-10-19","АО Тест","excluded_company",False),
               ("2026-10-19","АО Тест","completed",False),
               ("2026-10-01T00:00:00+03:00","ООО Тест","outside_period",True),
               ("31.10.2026 23:59:59","ООО Тест","completed",True),
               ("2026-10-19","ООО Тест","running",True)]
        for index,(published,customer,status,owned) in enumerate(cases):
            with self.subTest(published=published,customer=customer,status=status):
                con=self.connect()
                con.execute("UPDATE purchases SET published_at=?,customer=? WHERE reg_number=?",(published,customer,REG))
                con.execute("UPDATE thematic_items SET status=? WHERE reg=?",(status,REG))
                con.commit();con.close()
                self.engine="scope-engine-"+str(index)
                self.assertEqual(originals._review_owned_elsewhere(self.ns,REG),owned)
                self.assertEqual((await self.tick(after=""))["admitted"],0 if owned else 1)
                if not owned:
                    calls+=1;await self.settled(calls)
                    self.assertEqual(self.state()["last_engine_signature"],self.engine)
                    self.assertEqual((await self.tick(after=""))["admitted"],0)
                self.assertEqual(len(self.calls),calls)
        con=self.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM thematic_items").fetchone()[0],1)
        self.assertEqual(con.execute("SELECT evidence FROM protected_findings").fetchone()[0],"previous complete evidence")
        con.close()

    async def test_excluded_general_company_does_not_block_changed_engine(self):
        from v332_all_procurements_audit import CAMPAIGN
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        con=self.connect()
        con.execute("CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT)")
        con.execute("INSERT INTO v332_audit_queue VALUES(?,?,'completed')",(CAMPAIGN,REG))
        con.execute("UPDATE purchases SET customer='ГБУ Тест' WHERE reg_number=?",(REG,))
        con.commit();con.close()
        self.engine="engine-b"
        self.assertFalse(originals._review_owned_elsewhere(self.ns,REG))
        self.assertEqual((await self.tick())["admitted"],1)
        await self.settled(2)
        self.assertEqual(self.state()["last_engine_signature"],"engine-b")
        con=self.connect()
        self.assertEqual(con.execute("SELECT state FROM v332_audit_queue").fetchone()[0],"completed")
        con.execute("UPDATE purchases SET customer='ООО Тест' WHERE reg_number=?",(REG,))
        con.commit();con.close()
        self.engine="engine-c"
        self.assertTrue(originals._review_owned_elsewhere(self.ns,REG))
        self.assertEqual((await self.tick(after=""))["admitted"],0)
        self.assertEqual(len(self.calls),2)

    async def test_automatic_path_never_calls_network_capable_legacy_process(self):
        self.ns.pop("_v298_offline_audit")
        self.ns.pop("_v295_automatic_review")
        async def forbidden(*args):raise AssertionError("Network-capable process was called")
        self.ns["process_purchase"]=forbidden
        await self.tick()
        for _ in range(50):
            if self.state().get("status")=="failed":break
            await asyncio.sleep(.005)
        self.assertNotEqual(self.state()["reason"],"AssertionError")
        self.assertEqual(self.calls,[])

    async def test_live_restart_respects_lease_and_old_completion_cannot_overwrite_new_owner(self):
        self.block=asyncio.Event();await self.tick()
        while not self.calls:await asyncio.sleep(.005)
        old_token=self.state()["lease_token"]
        restarted=self.install()
        self.assertEqual((await self.tick(ns=restarted,after=""))["admitted"],0)
        self.assertEqual(self.state()["lease_token"],old_token)
        # Simulate a process that exhausted its lease and a new reservation.
        # The old analysis may finish, but its guarded commit must now fail.
        replacement={**self.state(),"status":"completed","lease_token":"new-owner",
                     "lease_until":0,"last_engine_signature":"new-owner-engine",
                     "last_semantic_version":"new-owner-source"}
        self.store_state(replacement)
        self.block.set();self.block=None
        await asyncio.sleep(.1)
        self.assertEqual(self.state()["last_engine_signature"],"new-owner-engine")
        self.assertEqual(self.state()["last_semantic_version"],"new-owner-source")
        self.assertEqual(self.state()["status"],"completed")
        self.assertEqual(len(self.calls),1)

    async def test_old_shutdown_cancellation_cannot_reset_new_completed_result(self):
        self.block=asyncio.Event();await self.tick()
        while not self.calls:await asyncio.sleep(.005)
        self.store_state({**self.state(),"status":"completed","lease_token":"new-owner","lease_until":0,
                          "last_engine_signature":"new-engine"})
        for handler in self.ns["app"].router.on_shutdown:await handler()
        self.assertEqual(self.state()["status"],"completed")
        self.assertEqual(self.state()["last_engine_signature"],"new-engine")

    async def test_expired_clock_lease_does_not_replace_a_live_physical_execution(self):
        self.block=asyncio.Event();await self.tick()
        while not self.calls:await asyncio.sleep(.005)
        old_token=self.state()["lease_token"]
        self.store_state({**self.state(),"lease_until":1})
        restarted=self.install()
        self.assertEqual((await self.tick(ns=restarted,after=""))["admitted"],0)
        self.assertEqual(self.state()["lease_token"],old_token)
        self.block.set();self.block=None
        await self.settled()
        self.assertEqual(len(self.calls),1)

    async def test_expired_running_lease_is_recovered_with_fresh_token(self):
        self.store_state({"status":"running","automatic":True,"lease_token":"dead-process","lease_until":1})
        await self.tick();await self.settled()
        self.assertNotEqual(self.state()["lease_token"],"dead-process")
        self.assertEqual(self.state()["last_engine_signature"],"engine-a")

    async def test_recurring_counts_and_state_writes_stay_off_event_loop(self):
        import threading
        actual=originals.save_atomic
        def guarded(path,data):
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            return actual(path,data)
        with patch.object(originals,"save_atomic",side_effect=guarded):
            await self.tick();await self.settled()

    async def test_async_intake_admission_keeps_slow_receipts_off_event_loop(self):
        actual = originals.allowed_documents
        started, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        def slow_counts(reg):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            started.set()
            self.assertTrue(release.wait(2), "event loop did not resume while receipts were read")
            return actual(reg)
        async def release_from_loop():
            for _ in range(100):
                if started.is_set():
                    release.set()
                    return
                await asyncio.sleep(.005)
            self.fail("receipt read did not start")
        tick = asyncio.create_task(release_from_loop())
        try:
            with patch.object(originals,"allowed_documents",side_effect=slow_counts):
                result = await self.ns["_v295_enqueue_review_async"](REG)
                await tick
                await self.settled()
            self.assertEqual(result["status"],"queued")
            self.assertEqual(len(self.calls),1)
        finally:
            release.set()
            tick.cancel()
            await asyncio.gather(tick,return_exceptions=True)

    async def test_parallel_async_intake_admissions_keep_one_physical_review(self):
        self.block = asyncio.Event()
        results = await asyncio.gather(*(
            self.ns["_v295_enqueue_review_async"](REG) for _ in range(8)))
        # Contending non-owners may all hand off to one durable queued state;
        # the normal bounded scan also handles that tokenless admission.
        await self.tick(source="eis223_official_review_jobs")
        for _ in range(100):
            if self.calls:break
            await asyncio.sleep(.005)
        self.assertEqual(len(self.calls),1,{"state":self.state(),"results":results})
        # A non-owner can observe the tiny interval after the execution flock
        # was acquired but before its first durable queued state was written.
        self.assertTrue(all(result["status"] in ("not_queued","queued","running") for result in results))
        self.assertTrue(any(result["status"] in ("queued","running") for result in results))
        self.block.set();self.block=None
        await self.settled()
        replay = await self.ns["_v295_enqueue_review_async"](REG)
        self.assertEqual(replay["status"],"completed")
        self.assertEqual(len(self.calls),1)
        admission=inspect.getclosurevars(self.ns["_v295_enqueue_review_async"]).nonlocals["enqueue_automatic"]
        self.assertEqual(inspect.getclosurevars(admission).nonlocals["admission_locks"],{})

    async def test_http_and_periodic_admission_share_one_registry_handoff(self):
        self.block=asyncio.Event()
        actual=originals.save_atomic
        entered,release=threading.Event(),threading.Event()
        def slow_reservation(path,data):
            if Path(path).name==REG+".json" and json.loads(data).get("status")=="queued":
                entered.set()
                self.assertTrue(release.wait(2),"reservation fixture was not released")
            return actual(path,data)
        with patch.object(originals,"save_atomic",side_effect=slow_reservation):
            request=asyncio.create_task(self.ns["_v295_enqueue_review_async"](REG))
            periodic=None
            try:
                for _ in range(100):
                    if entered.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                periodic=asyncio.create_task(self.tick())
                await asyncio.sleep(.03)
                self.assertFalse(periodic.done())
            finally:
                release.set()
                queued=await request
                if periodic is not None:await periodic
        for _ in range(100):
            if self.calls:break
            await asyncio.sleep(.005)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.state()["lease_token"],queued["lease_token"])
        self.assertFalse(self.state()["automatic"])
        self.block.set();self.block=None
        await self.settled()
        self.assertEqual(len(self.calls),1)

    async def test_sibling_prepare_cannot_steal_new_reviewers_execution_flock(self):
        self.block=asyncio.Event()
        actual_try,actual_release=originals._try_execution_lock,originals._release_execution_lock
        first_released,release_first,release_sibling=threading.Event(),threading.Event(),threading.Event()
        reviewer_attempted=threading.Event()
        reviewer_locks=[]
        def preparing():
            return any(frame.function=="prepare_review" for frame in inspect.stack(context=0))
        def acquire(reg):
            in_prepare=preparing()
            handle=actual_try(reg)
            if in_prepare and first_released.is_set() and handle is not None:
                # Without serialization, the sibling holds this flock while
                # the first preparation returns and schedules its reviewer.
                release_sibling.wait(2)
            elif not in_prepare:
                reviewer_locks.append(handle is not None)
                reviewer_attempted.set()
            return handle
        def unlock(handle):
            actual_release(handle)
            if preparing() and not first_released.is_set():
                first_released.set()
                release_first.wait(2)
        with patch.object(originals,"_try_execution_lock",side_effect=acquire), \
                patch.object(originals,"_release_execution_lock",side_effect=unlock):
            first=asyncio.create_task(self.ns["_v295_enqueue_review_async"](REG))
            sibling=None
            try:
                for _ in range(200):
                    if first_released.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(first_released.is_set())
                sibling=asyncio.create_task(self.ns["_v295_enqueue_review_async"](REG))
                await asyncio.sleep(.05)
                release_first.set()
                for _ in range(200):
                    if reviewer_attempted.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(reviewer_attempted.is_set())
            finally:
                release_first.set();release_sibling.set()
                await first
                if sibling is not None:await sibling
        self.assertEqual(reviewer_locks,[True],"a sibling preparation stranded the new review lease")
        self.block.set();self.block=None
        await self.settled()
        self.assertEqual(len(self.calls),1)

    async def test_sync_upload_callback_during_async_admission_releases_before_reviewer(self):
        self.block=asyncio.Event()
        actual_try,actual_release=originals._try_execution_lock,originals._release_execution_lock
        first_released,release_first=threading.Event(),threading.Event()
        loop_thread=threading.get_ident()
        reviewer_locks=[]
        sync_owned=[]
        def preparing():
            return any(frame.function=="prepare_review" for frame in inspect.stack(context=0))
        def acquire(reg):
            in_prepare=preparing()
            handle=actual_try(reg)
            if in_prepare and threading.get_ident()==loop_thread:
                sync_owned.append(handle is not None)
                release_first.set()
                # The background preparation returns during this callback;
                # its reviewer still cannot execute before the callback exits.
                originals.time.sleep(.05)
            elif not in_prepare:
                reviewer_locks.append(handle is not None)
            return handle
        def unlock(handle):
            actual_release(handle)
            if preparing() and threading.get_ident()!=loop_thread and not first_released.is_set():
                first_released.set()
                release_first.wait(2)
        with patch.object(originals,"_try_execution_lock",side_effect=acquire), \
                patch.object(originals,"_release_execution_lock",side_effect=unlock):
            first=asyncio.create_task(self.ns["_v295_enqueue_review_async"](REG))
            try:
                for _ in range(200):
                    if first_released.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(first_released.is_set())
                sync_result=self.ns["_v295_enqueue_review"](REG)
            finally:
                release_first.set()
                async_result=await first
            for _ in range(200):
                if self.calls:break
                await asyncio.sleep(.005)
        self.assertEqual(sync_owned,[True])
        self.assertEqual(reviewer_locks,[True])
        self.assertEqual(sync_result["lease_token"],async_result["lease_token"])
        self.assertEqual(len(self.calls),1)
        self.block.set();self.block=None
        await self.settled()

    async def test_async_intake_admission_respects_existing_retry_deadline(self):
        self.store_state({"status":"partial","source_version":originals._review_source_version(REG),
                          "next_retry_at":4102444800,"attempts":3,"automatic":True})
        result = await self.ns["_v295_enqueue_review_async"](REG)
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["next_retry_at"],4102444800)
        self.assertEqual(self.calls,[])

    async def test_async_explicit_force_and_automatic_flags_match_sync_defaults(self):
        self.store_state({"status":"failed","source_version":originals._review_source_version(REG),
                          "originals_received":1,"attempts":4,"retry_exhausted":True,
                          "next_retry_at":4102444800})
        with patch.object(originals,"MAX_REVIEW_TASKS",0):
            skipped=await self.ns["_v295_enqueue_review_async"](REG,force=False,automatic=True)
            self.assertEqual(skipped["status"],"failed")
            queued=await self.ns["_v295_enqueue_review_async"](REG,force=True,automatic=False)
        self.assertEqual(queued["status"],"queued")
        self.assertFalse(queued["retry_exhausted"])
        self.assertEqual(queued["next_retry_at"],0)
        self.assertEqual(queued["attempts"],0)
        await self.tick();await self.settled()

    async def test_async_manual_repeat_promotes_existing_task_without_restart(self):
        self.block=asyncio.Event()
        await self.ns["_v295_enqueue_review_async"](REG,automatic=True)
        while not self.calls:await asyncio.sleep(.005)
        token=self.state()["lease_token"]
        import v381_audit_scheduling as scheduling
        loop_thread=threading.get_ident()
        def manual(ns,reg):
            self.assertNotEqual(threading.get_ident(),loop_thread)
            return True
        with patch.object(scheduling,"is_manual_review",side_effect=manual), \
                patch.object(scheduling,"promote_review") as promote:
            await self.ns["_v295_enqueue_review_async"](REG)
            promote.assert_called_once()
            await self.ns["_v295_enqueue_review_async"](REG,automatic=True)
            promote.assert_called_once()
        self.assertEqual(self.state()["lease_token"],token)
        self.assertEqual(len(self.calls),1)
        self.block.set();self.block=None
        await self.settled()

    def install_report_delivery(self):
        import eis_server_worker as worker
        import v301_search_jobs as queue
        from v294_eis_bridge import parse_notices
        key="local-fixture-report-key-long-enough"
        env=patch.dict(os.environ,{"EIS223_UPLOAD_TOKEN":key})
        env.start();self.addCleanup(env.stop)
        queue.install(self.ns);worker.install(self.ns)
        with self.connect() as db:db.execute("DELETE FROM v301_search_jobs")
        identity=str(uuid.uuid4())
        queue._enqueue(self.ns,"reg",REG,1,9,"worker-bootstrap",False)
        job=worker.claim(self.ns,identity,1)[0]
        url="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+UID
        xml=(f'<purchaseNoticeKESMBO xmlns="http://zakupki.gov.ru/223fz/purchase/1">'
             f'<body><item><purchaseNoticeKESMBOData><registrationNumber>{REG}</registrationNumber>'
             f'<version>1</version><attachments><document><guid>fixture</guid><fileName>fixture.pdf</fileName>'
             f'<url>{url}</url></document></attachments></purchaseNoticeKESMBOData></item></body></purchaseNoticeKESMBO>').encode()
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,"w") as archive:archive.writestr("purchaseNoticeKESMBO_"+REG+"_fixture.xml",xml)
        raw=buffer.getvalue();digest=hashlib.sha256(raw).hexdigest()
        folder=self.root/"eis223_official_archive_inbox"/REG;folder.mkdir(parents=True)
        (folder/(digest+".zip")).write_bytes(raw)
        (folder/(digest+".json")).write_text(json.dumps({"source":"official_eis_soap_archive",
            "reg_number":REG,"archive_sha256":digest,"notices":parse_notices(raw)}))
        endpoint=next(route.endpoint for route in self.ns["app"].routes
                      if route.path=="/api/eis-worker/jobs/{identifier}/report")
        payload=json.dumps({"worker_id":identity,"reg_number":REG,"lease_token":job["lease_token"],
                            "status":"completed","archive_sha256s":[digest]}).encode()
        async def deliver():
            async def receive():return {"type":"http.request","body":payload,"more_body":False}
            request=Request({"type":"http","headers":[(b"x-eis223-upload-token",key.encode())]},receive)
            return await endpoint(job["id"],request)
        return deliver

    async def test_committed_report_admission_failure_replay_recovers_from_durable_intake(self):
        deliver=self.install_report_delivery()
        async def unavailable(reg):raise OSError("temporary admission fixture")
        actual=self.ns["_v295_enqueue_review_async"]
        self.ns["_v295_enqueue_review_async"]=unavailable
        result=await deliver()
        self.assertTrue(result["download_complete"])
        self.assertEqual(result["review_status"],"pending_retry")
        self.assertTrue((await deliver())["replayed"])
        self.assertEqual(self.calls,[])
        self.ns["_v295_enqueue_review_async"]=actual
        self.assertEqual((await self.tick(source="eis223_official_originals"))["admitted"],1)
        await self.settled()
        self.assertEqual(len(self.calls),1)

    async def test_committed_report_admission_cancellation_replay_recovers_from_durable_intake(self):
        deliver=self.install_report_delivery()
        entered=asyncio.Event()
        async def canceled_admission(reg):
            entered.set()
            await asyncio.Event().wait()
        actual=self.ns["_v295_enqueue_review_async"]
        self.ns["_v295_enqueue_review_async"]=canceled_admission
        request=asyncio.create_task(deliver())
        await asyncio.wait_for(entered.wait(),2)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):await request
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM eis_server_reports").fetchone()[0],1)
        self.assertTrue((await deliver())["replayed"])
        self.assertEqual(self.calls,[])
        self.ns["_v295_enqueue_review_async"]=actual
        self.assertEqual((await self.tick(source="eis223_official_originals"))["admitted"],1)
        await self.settled()
        self.assertEqual(len(self.calls),1)

    async def test_canceled_async_admission_settles_lease_without_freeing_task_capacity(self):
        other="32616233662"
        self.prepare(other)
        self.block=asyncio.Event()
        actual=originals.save_atomic
        entered,release=threading.Event(),threading.Event()
        def slow_reservation(path,data):
            if Path(path).name==REG+".json" and json.loads(data).get("status")=="queued":
                entered.set()
                self.assertTrue(release.wait(2),"reservation fixture was not released")
            return actual(path,data)
        with patch.object(originals,"MAX_REVIEW_TASKS",1),patch.object(originals,"save_atomic",side_effect=slow_reservation):
            admission=asyncio.create_task(self.ns["_v295_enqueue_review_async"](REG))
            try:
                for _ in range(100):
                    if entered.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                admission.cancel();await asyncio.sleep(.005)
                admission.cancel();await asyncio.sleep(.005)
                self.assertFalse(admission.done())
                queued=await self.ns["_v295_enqueue_review_async"](other)
                self.assertEqual(queued["status"],"queued")
                self.assertFalse(queued["lease_token"])
                self.assertEqual(queued["lease_until"],0)
                self.assertEqual(self.calls,[])
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):await admission
            for _ in range(100):
                if self.calls:break
                await asyncio.sleep(.005)
            self.assertEqual([call[0] for call in self.calls],[REG])
            self.assertTrue(self.state()["lease_token"])
            self.block.set();self.block=None
            await self.settled()
            self.assertEqual((await self.tick(source="eis223_official_review_jobs",after=""))["admitted"],1)
            await self.settled(2,reg=other)
            self.assertCountEqual([call[0] for call in self.calls],[REG,other])

    async def test_canceled_periodic_admission_keeps_http_handoff_serialized(self):
        self.block=asyncio.Event()
        actual=originals.save_atomic
        entered,release=threading.Event(),threading.Event()
        def slow_reservation(path,data):
            if Path(path).name==REG+".json" and json.loads(data).get("status")=="queued":
                entered.set()
                self.assertTrue(release.wait(2),"periodic reservation fixture was not released")
            return actual(path,data)
        with patch.object(originals,"save_atomic",side_effect=slow_reservation):
            periodic=asyncio.create_task(self.tick())
            request=None
            try:
                for _ in range(200):
                    if entered.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                request=asyncio.create_task(self.ns["_v295_enqueue_review_async"](REG))
                periodic.cancel();await asyncio.sleep(.005)
                periodic.cancel();await asyncio.sleep(.005)
                self.assertFalse(periodic.done())
                self.assertFalse(request.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):await periodic
                if request is not None:await request
        self.block.set();self.block=None
        await self.settled()
        self.assertEqual(len(self.calls),1)

    async def test_shutdown_settles_periodic_admission_as_restartable_without_late_worker(self):
        actual=originals.save_atomic
        entered,release=threading.Event(),threading.Event()
        def slow_reservation(path,data):
            fields=json.loads(data)
            if Path(path).name==REG+".json" and fields.get("status")=="queued" and fields.get("lease_token"):
                entered.set()
                self.assertTrue(release.wait(2),"shutdown reservation fixture was not released")
            return actual(path,data)
        with patch.object(originals,"REVIEW_SCAN_INTERVAL",.01),patch.object(originals,"save_atomic",side_effect=slow_reservation):
            for handler in self.ns["app"].router.on_startup:await handler()
            for _ in range(200):
                if entered.is_set():break
                await asyncio.sleep(.005)
            self.assertTrue(entered.is_set())
            async def stop():
                for handler in self.ns["app"].router.on_shutdown:await handler()
            shutdown=asyncio.create_task(stop())
            try:
                await asyncio.sleep(.01)
                self.assertFalse(shutdown.done())
                self.assertEqual(self.calls,[])
            finally:
                release.set()
                await shutdown
        self.assertEqual(self.state()["status"],"queued")
        self.assertFalse(self.state()["lease_token"])
        self.assertEqual(self.state()["lease_until"],0)
        self.assertEqual(self.calls,[])
        restarted=self.install()
        self.assertEqual((await self.tick(ns=restarted,source="eis223_official_review_jobs"))["admitted"],1)
        await self.settled()
        self.assertEqual(len(self.calls),1)

    async def test_real_semantic_card_facts_reopen_once_and_hash_outside_sql_writer(self):
        import threading
        self.snapshot_patch.stop()
        con=self.connect()
        con.execute("CREATE TABLE v341_card_facts(reg TEXT PRIMARY KEY,source_page TEXT,payload TEXT)")
        url="https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+REG
        con.execute("INSERT INTO v341_card_facts VALUES(?,?,?)",(REG,url,json.dumps({"fields":{"submission_end":"2026-11-01"}})))
        con.commit();con.close()
        original=originals._review_input_versions
        snapshots=[]
        def guarded(ns,reg):
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            con=self.connect()
            con.execute("UPDATE protected_findings SET evidence=evidence WHERE reg=?",(reg,));con.commit();con.close()
            value=original(ns,reg);snapshots.append(value)
            return value
        with patch.object(originals,"_review_input_versions",side_effect=guarded):
            self.ns["_v295_enqueue_review"](REG);await self.settled()
            before=self.state()["last_semantic_version"]
            con=self.connect();con.execute("UPDATE v341_card_facts SET payload=? WHERE reg=?",(json.dumps({"fields":{"submission_end":"2026-11-02"}}),REG));con.commit();con.close()
            await self.tick();await self.settled(2)
            self.assertNotEqual(self.state()["last_semantic_version"],before)
            await self.tick(after="");self.assertEqual(len(self.calls),2)
            self.assertGreaterEqual(len(snapshots),5)

    async def test_ui_callback_is_read_only_and_keeps_prior_successful_coverage(self):
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        state=self.ns["_v295_review_state"](REG)
        self.assertEqual(state["successful_coverage"]["checked_documents"],1)
        self.assertNotIn("lease_token",state)
        self.engine="engine-b";self.error=OSError("temporary")
        await self.tick();await self.settled(2,status="failed")
        failed=self.ns["_v295_review_state"](REG)
        self.assertEqual(failed["successful_coverage"],state["successful_coverage"])
        self.assertEqual(failed["status"],"failed")
        count=len(self.calls)
        for _ in range(5):self.ns["_v295_review_state"](REG)
        self.assertEqual(len(self.calls),count)

    async def test_completed_card_replay_does_not_modify_originals(self):
        path=originals.saved_document(REG,UID)[0];before=path.read_bytes()
        self.ns["_v295_enqueue_review"](REG);await self.settled()
        await self.tick();await self.tick(after="")
        self.assertEqual(len(self.calls),1);self.assertEqual(path.read_bytes(),before)




# Exercise the actual installed local inspection and append-only transaction,
# rather than merely a fake offline callback's reported coverage.
from test_general_audit_recovery import DatabaseFixture
import v332_all_procurements_audit as general


class IntakePersistenceIntegration(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns["app"]=FastAPI();self.ns["lock"]=asyncio.Lock()
        self.ns["save"]=lambda *args:(_ for _ in ()).throw(AssertionError("Destructive legacy save called"))
        originals.install(self.ns)

    async def asyncTearDown(self):
        for handler in self.ns["app"].router.on_shutdown:await handler()

    def state_path(self,reg):return self.folder/"eis223_official_review_jobs"/(reg+".json")

    def state(self,reg):
        path=self.state_path(reg)
        return json.loads(path.read_text()) if path.exists() else {}

    def write_state(self,reg,value):
        path=self.state_path(reg);path.parent.mkdir(exist_ok=True)
        originals.save_atomic(path,json.dumps({"reg_number":reg,**value}).encode())

    async def tick(self):
        path=self.folder/"eis223_official_review_jobs"/".revisit-cursor.json"
        path.parent.mkdir(exist_ok=True);path.write_text('{}')
        return await self.ns["_v295_review_tick"]()

    async def settled(self,reg,status):
        for _ in range(300):
            if self.state(reg).get("status")==status:
                await asyncio.sleep(.04);return
            await asyncio.sleep(.005)
        self.fail((self.state(reg),self.output.getvalue()))

    def install_real_legacy_save(self):
        import ast
        import v298_eis_only as intake
        import v334_findings_ledger as ledger
        with self.connect() as con:
            for table,names in (("purchases",("org_form","price","deadline","url","source","clarifications",
                    "protocols","cancellations","risks","submission_start","price_num","card_fields_json",
                    "card_source_status","documents_found")),("docs",("annotated_path","error","checked_at"))):
                columns={row[1] for row in con.execute("PRAGMA table_info("+table+")")}
                for name in names:
                    if name not in columns:con.execute("ALTER TABLE "+table+" ADD COLUMN "+name+" TEXT")
            # Same saved manager-decision fixture as test_automatic_legal_outcomes.
            con.execute("CREATE TABLE manager_decisions(id INTEGER PRIMARY KEY,reg TEXT,decision TEXT)")
        path=Path(__file__).resolve().parents[1]/"app.py"
        source=ast.parse(path.read_text())
        save=next(node for node in source.body if isinstance(node,ast.FunctionDef) and node.name=="save")
        scope={"conn":self.connect,"org_form":lambda name:"ООО","now":lambda:"2026-10-19",
               "json":json,"clean":str,"render_report":lambda *args:None}
        exec(compile(ast.Module(body=[save],type_ignores=[]),str(path),"exec"),scope)
        self.ns["save"]=scope["save"]
        intake.install(self.ns);ledger.install(self.ns)

    async def test_real_changed_upload_preserves_active_findings_checks_and_approval(self):
        self.install_real_legacy_save()
        for index,status in enumerate(("completed","partial","failed",None,"checks_only","orphan"),1):
            with self.subTest(previous=status):
                reg=self.purchase(index)
                with self.connect() as con:
                    doc=dict(con.execute("SELECT * FROM docs WHERE reg_number=?",(reg,)).fetchone())
                    if status!="checks_only":
                        con.execute("INSERT INTO findings(id,reg_number,code,kind,title,law,evidence,document,source_url) "
                            "VALUES(?,?,'prior-proven','violation','Previously approved episode','223-ФЗ',"
                            "'Retained signed evidence',?,?)",(10000*index,reg,doc["name"],doc["url"]))
                    con.execute("INSERT INTO checks(id,reg_number,scope,item,status,evidence) "
                        "VALUES(?,?,'Earlier review','Earlier check','ok','Retained check evidence')",(20000*index,reg))
                    con.execute("INSERT INTO manager_decisions VALUES(?,?,'approved')",(index,reg))
                    previous_finding=con.execute("SELECT * FROM findings WHERE id=?",(10000*index,)).fetchone()
                    previous_check=tuple(con.execute("SELECT * FROM checks WHERE id=?",(20000*index,)).fetchone())
                if status in ("completed","partial","failed"):
                    saved={"status":status,"originals_received":1,"checked_documents":1,
                           "source_version":originals._review_source_version(reg)}
                    if status=="completed":saved["successful_coverage"]={"checked_documents":1}
                    self.write_state(reg,saved)
                new_path=self.document(reg,"Дополнительная документация. Предмет закупки: поставка материалов.")
                with self.connect() as con:
                    con.execute("DELETE FROM docs WHERE local_path=?",(str(new_path),))
                    if status=="orphan":con.execute("DELETE FROM purchases WHERE reg_number=?",(reg,))
                queued=self.ns["_v295_enqueue_review"](reg)
                self.assertEqual(queued["status"],"queued")
                self.assertTrue(queued["automatic"])
                await self.settled(reg,"completed")
                self.assertEqual(self.state(reg)["checked_documents"],2)
                with self.connect() as con:
                    if previous_finding:
                        self.assertEqual(tuple(con.execute("SELECT * FROM findings WHERE id=?",(10000*index,)).fetchone()),tuple(previous_finding))
                    self.assertEqual(tuple(con.execute("SELECT * FROM checks WHERE id=?",(20000*index,)).fetchone()),previous_check)
                    self.assertEqual(dict(con.execute("SELECT * FROM docs WHERE id=?",(doc["id"],)).fetchone()),doc)
                    self.assertEqual(con.execute("SELECT decision FROM manager_decisions WHERE id=?",(index,)).fetchone()[0],"approved")
                    self.assertEqual(con.execute("SELECT status FROM docs WHERE local_path=?",(str(new_path),)).fetchone()[0],"downloaded_no_text")
                    self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='v332_audit_queue'").fetchone())
                self.assertEqual(self.ns["_v295_enqueue_review"](reg)["status"],"completed")

    async def test_changed_upload_owned_by_campaign_never_uses_destructive_save(self):
        self.install_real_legacy_save()
        for index,owner in enumerate(("general","thematic"),1):
            with self.subTest(owner=owner):
                reg=self.purchase(index)
                with self.connect() as con:
                    con.execute("INSERT INTO findings(id,reg_number,code,kind,title,evidence) "
                        "VALUES(?,?,'prior-proven','violation','Earlier signed finding','Retained evidence')",(900+index,reg))
                    con.execute("INSERT INTO manager_decisions VALUES(?,?,'approved')",(index,reg))
                    if owner=="general":
                        con.execute("CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT)")
                        con.execute("INSERT INTO v332_audit_queue VALUES(?,?,'completed')",(general.CAMPAIGN,reg))
                    else:
                        con.executescript("CREATE TABLE thematic_batches(id TEXT,status TEXT,legal_forms TEXT,date_from TEXT,date_to TEXT);"
                                          "CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT);")
                        con.execute("INSERT INTO thematic_batches VALUES('batch','active',?,'2026-10-01','2026-10-31')",(json.dumps(["ООО"]),))
                        con.execute("INSERT INTO thematic_items VALUES('batch',?,'completed')",(reg,))
                self.write_state(reg,{"status":"completed","originals_received":1,
                    "source_version":originals._review_source_version(reg),"successful_coverage":{"checked_documents":1}})
                new_path=self.document(reg,"Дополнительная документация для существующей проверки.")
                with self.connect() as con:con.execute("DELETE FROM docs WHERE local_path=?",(str(new_path),))
                self.assertTrue(self.ns["_v295_enqueue_review"](reg)["automatic"])
                await self.settled(reg,"deferred")
                with self.connect() as con:
                    self.assertIsNotNone(con.execute("SELECT 1 FROM findings WHERE id=?",(900+index,)).fetchone())
                    self.assertEqual(con.execute("SELECT decision FROM manager_decisions WHERE id=?",(index,)).fetchone()[0],"approved")
                    self.assertEqual(con.execute("SELECT status FROM docs WHERE local_path=?",(str(new_path),)).fetchone()[0],"downloaded_no_text")
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM v332_audit_queue").fetchone()[0],1)

    async def test_real_first_intake_still_initializes_through_legacy_save(self):
        self.install_real_legacy_save()
        reg=self.purchase()
        with self.connect() as con:
            con.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
            con.execute("DELETE FROM purchases WHERE reg_number=?",(reg,))
        self.assertFalse(self.ns["_v295_enqueue_review"](reg)["automatic"])
        await self.settled(reg,"completed")
        with self.connect() as con:
            self.assertEqual(con.execute("SELECT documents_checked FROM purchases WHERE reg_number=?",(reg,)).fetchone()[0],1)
            self.assertEqual(con.execute("SELECT status FROM docs WHERE reg_number=?",(reg,)).fetchone()[0],"checked")
        self.assertEqual(self.state(reg)["successful_coverage"]["checked_documents"],1)

    async def test_eligible_campaign_owns_first_review_before_any_saved_legal_history(self):
        self.install_real_legacy_save()
        # Initial ingestion can publish new source-labelled card/document
        # identity once. Its append-only catch-up uses the real queue schema.
        general.initialize(self.ns)
        analyses={}
        analyze=self.ns["analyze"]
        def counted(purchase,documents):
            number=purchase.get("reg") or purchase.get("reg_number")
            analyses[number]=analyses.get(number,0)+1
            return analyze(purchase,documents)
        self.ns["analyze"]=counted
        with self.connect() as con:
            con.executescript("CREATE TABLE thematic_batches(id TEXT,status TEXT,legal_forms TEXT,date_from TEXT,date_to TEXT);"
                              "CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT);")
        cases=(("general","ООО Тест","2026-10-19",True),
               ("thematic","ООО Тест","2026-10-19",True),
               ("thematic","ООО Тест","2026-01-01",False),
               ("thematic","АО Тест","2026-10-19",False),
               ("general","ГБУ Тест","2026-10-19",False))
        for index,(owner,customer,published,eligible) in enumerate(cases,1):
            with self.subTest(owner=owner,customer=customer,published=published):
                reg=self.purchase(index)
                with self.connect() as con:
                    con.execute("UPDATE purchases SET customer=?,published_at=? WHERE reg_number=?",(customer,published,reg))
                    con.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
                    if owner=="general":
                        con.execute("INSERT INTO v332_audit_queue(campaign,reg,state) VALUES(?,?,'pending')",(general.CAMPAIGN,reg))
                    else:
                        con.execute("INSERT INTO thematic_batches VALUES(?,'active',?,'2026-10-01','2026-10-31')",(reg,json.dumps(["ООО"])))
                        con.execute("INSERT INTO thematic_items VALUES(?,?,'queued')",(reg,reg))
                    self.assertEqual(con.execute("SELECT documents_checked FROM purchases WHERE reg_number=?",(reg,)).fetchone()[0],0)
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM findings WHERE reg_number=?",(reg,)).fetchone()[0],0)
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM checks WHERE reg_number=?",(reg,)).fetchone()[0],0)
                    membership_count=con.execute("SELECT COUNT(*) FROM v332_audit_queue").fetchone()[0]
                self.assertEqual(self.state(reg),{})
                self.assertEqual(originals._review_owned_elsewhere(self.ns,reg),eligible)
                queued=self.ns["_v295_enqueue_review"](reg)
                self.assertEqual(queued["automatic"],eligible)
                await self.settled(reg,"deferred" if eligible else "completed")
                if eligible:
                    self.assertEqual(analyses.get(reg,0),0)
                else:
                    from v381_audit_scheduling import run_file_io
                    for _ in range(300):
                        current=await run_file_io(originals._review_input_versions,self.ns,reg)
                        saved=self.state(reg)
                        if (saved.get("status")=="completed" and analyses.get(reg)==2
                                and saved.get("last_semantic_version")==current["semantic_version"]):break
                        await asyncio.sleep(.005)
                    else:self.fail((self.state(reg),analyses,self.output.getvalue()))
                    self.assertTrue(saved["automatic"])
                    # The initial source adoption causes exactly one catch-up,
                    # never an endless unchanged-input or completion loop.
                    for _ in range(3):
                        self.assertEqual(self.ns["_v295_enqueue_review"](reg)["status"],"completed")
                        await asyncio.sleep(.025)
                    self.assertEqual(analyses[reg],2)
                    self.assertEqual(self.state(reg)["status"],"completed")
                with self.connect() as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM v332_audit_queue").fetchone()[0],membership_count)
                    self.assertEqual(con.execute("SELECT status FROM docs WHERE reg_number=?",(reg,)).fetchone()[0],
                                     "downloaded_no_text" if eligible else "checked")
                    if eligible:
                        self.assertEqual(con.execute("SELECT documents_checked FROM purchases WHERE reg_number=?",(reg,)).fetchone()[0],0)
                        self.assertEqual(con.execute("SELECT COUNT(*) FROM findings WHERE reg_number=?",(reg,)).fetchone()[0],0)
                        self.assertEqual(con.execute("SELECT COUNT(*) FROM checks WHERE reg_number=?",(reg,)).fetchone()[0],0)

    async def test_actual_automatic_inspection_retains_prior_findings_docs_and_campaign_scope(self):
        reg=self.purchase()
        with self.connect() as con:
            con.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) VALUES(?, 'prior-proven','violation','Earlier proven episode','Retained documentary evidence')",(reg,))
            before=[tuple(row) for row in con.execute("SELECT * FROM docs")]
        self.write_state(reg,{"status":"completed","originals_received":1})
        await self.tick();await self.settled(reg,"completed")
        self.assertEqual(self.findings(reg),2)
        with self.connect() as con:
            self.assertEqual([tuple(row) for row in con.execute("SELECT * FROM docs")],before)
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='v332_audit_queue'").fetchone())
            self.assertEqual(con.execute("SELECT COUNT(*) FROM findings WHERE code='prior-proven'").fetchone()[0],1)
        self.assertTrue(self.state(reg)["last_engine_signature"])
        # Force another semantic/rule catch-up: existing facts remain idempotent.
        self.write_state(reg,{**self.state(reg),"last_engine_signature":"older-engine"})
        await self.tick();await self.settled(reg,"completed")
        self.assertEqual(self.findings(reg),2)

    async def test_actual_partial_extraction_does_not_replace_prior_complete_rows(self):
        reg=self.purchase()
        await self.tick();await self.settled(reg,"completed")
        self.assertEqual(self.findings(reg),1)
        previous=self.state(reg)
        self.write_state(reg,{**previous,"last_engine_signature":"older-engine"})
        with patch.object(general,"document_text",return_value=""):
            await self.tick();await self.settled(reg,"partial")
        self.assertEqual(self.findings(reg),1)
        self.assertEqual(self.state(reg)["last_engine_signature"],"older-engine")
        self.assertEqual(self.state(reg)["last_semantic_version"],previous["last_semantic_version"])
        with self.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM docs").fetchone()[0],"checked")

    async def test_real_append_commit_is_fenced_against_late_old_process(self):
        reg=self.purchase()
        real=general.persist
        def replaced_owner(ns,number,*args,**kwargs):
            self.write_state(reg,{**self.state(reg),"status":"completed","lease_token":"replacement",
                "lease_until":0,"last_engine_signature":"new-owner-engine","last_semantic_version":"new-owner-source"})
            return real(ns,number,*args,**kwargs)
        with patch.object(general,"persist",side_effect=replaced_owner):
            await self.tick();await self.settled(reg,"completed")
        self.assertEqual(self.findings(reg),0)
        self.assertEqual(self.state(reg)["last_engine_signature"],"new-owner-engine")
        self.assertEqual(self.state(reg)["last_semantic_version"],"new-owner-source")

    async def test_real_orphan_receipts_restore_only_unchecked_pointers_then_review(self):
        reg=self.purchase()
        with self.connect() as con:
            con.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
            con.execute("DELETE FROM purchases WHERE reg_number=?",(reg,))
        path=self.folder/"eis223_official_review_jobs"/".revisit-cursor.json"
        path.parent.mkdir(exist_ok=True);path.write_text('{"source_index":3}')
        result=await self.ns["_v295_review_tick"]()
        self.assertEqual(result["restored"],1)
        await self.settled(reg,"completed")
        with self.connect() as con:
            self.assertIsNotNone(con.execute("SELECT 1 FROM purchases WHERE reg_number=?",(reg,)).fetchone())
            self.assertEqual(con.execute("SELECT status FROM docs WHERE reg_number=?",(reg,)).fetchone()[0],"downloaded_no_text")
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='v332_audit_queue'").fetchone())
        self.assertEqual(self.state(reg)["checked_documents"],1)

    async def test_general_owned_crash_gets_missing_pointers_without_duplicate_intake_run(self):
        reg=self.purchase()
        general.initialize(self.ns)
        general.mark(self.ns,reg,"completed")
        with self.connect() as con:con.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
        result=await self.tick()
        self.assertEqual(result["restored"],1);self.assertEqual(result["admitted"],0)
        with self.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM docs WHERE reg_number=?",(reg,)).fetchone()[0],"downloaded_no_text")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM v332_audit_queue").fetchone()[0],1)
        self.assertEqual(general.refresh_changed(self.ns),1)
        self.assertEqual(str(general.claim_next(self.ns)),reg)

    async def test_failed_adoption_candidates_are_bounded_and_later_receipts_get_a_turn(self):
        import v298_eis_only
        reg=self.purchase()
        for index in range(11):self.document(reg,"Later original "+str(index))
        for file in list(self.contents)[:9]:file.write_bytes(b"!"*len(self.contents[file]))
        with self.connect() as con:con.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
        actual=v298_eis_only._read_original
        with patch.object(v298_eis_only,"_read_original",wraps=actual) as read:
            self.assertEqual(await asyncio.to_thread(originals._restore_intake_rows,self.ns,reg),0)
            self.assertLessEqual(read.call_count,8)
            read.reset_mock()
            self.assertEqual(await asyncio.to_thread(originals._restore_intake_rows,self.ns,reg),3)
            self.assertLessEqual(read.call_count,8)
        self.assertEqual(await asyncio.to_thread(originals._restore_intake_rows,self.ns,reg),0)
        with self.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],3)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM docs WHERE status='checked'").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT documents_checked FROM purchases").fetchone()[0],0)

    async def test_receipt_pointer_race_cannot_adopt_unverified_initial_path(self):
        import v298_eis_only
        reg=self.purchase()
        with self.connect() as con:con.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
        verified=originals.saved_document(reg,"00000000000000000000000000000001")
        changed=(self.folder/"different-original.docx",{**verified[1],"sha256":"b"*64},"b"*64)
        with patch.object(v298_eis_only,"_read_original",return_value=changed):
            self.assertEqual(await asyncio.to_thread(originals._restore_intake_rows,self.ns,reg),0)
        with self.connect() as con:self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],0)

    async def test_corrupt_orphan_bytes_are_not_adopted_or_counted_checked(self):
        reg=self.purchase()
        for file in self.contents:file.write_bytes(b"!"*len(self.contents[file]))
        with self.connect() as con:
            con.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
            con.execute("DELETE FROM purchases WHERE reg_number=?",(reg,))
        self.assertEqual(await asyncio.to_thread(originals._restore_intake_rows,self.ns,reg),0)
        with self.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM purchases").fetchone()[0],0)


if __name__=="__main__":unittest.main()
