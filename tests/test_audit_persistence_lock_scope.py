"""Legal deduplication must not block intake, and concurrent saves stay unique."""
import asyncio
from contextlib import contextmanager, ExitStack
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import v333_dedup_ui as dedup
import v353_episode_guard as episodes


REG = "32616233661"
BATCH = "a" * 24


class DatabaseFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audit.sqlite"
        self.ns = {"conn": self.connect}
        with self.connection() as c:
            c.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,
                    published_at TEXT,violations INTEGER DEFAULT 0,
                    checks_ok INTEGER DEFAULT 0,checks_attention INTEGER DEFAULT 0);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,
                    local_path TEXT,status TEXT);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,
                    kind TEXT,title TEXT,law TEXT,law_url TEXT,evidence TEXT,note TEXT,
                    document TEXT,source_url TEXT,location TEXT,match_text TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,
                    item TEXT,status TEXT,evidence TEXT,source TEXT,source_url TEXT,
                    document TEXT,field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT);
                CREATE TABLE intake(id INTEGER PRIMARY KEY);
            """)
            c.execute("INSERT INTO purchases(reg_number,customer,published_at) "
                      "VALUES(?,'ООО Тест','2026-10-19')", (REG,))
        thematic.init(self.ns)
        prior_tick = thematic.tick
        prior_state = dict(thematic.WORKER_STATE)
        def restore():
            thematic.tick = prior_tick
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(prior_state)
        self.addCleanup(restore)
        fast.install(self.ns)
        with self.connection() as c:
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) "
                      "VALUES(?,'[\"dates\"]',?,'active','[\"ООО\"]')", (BATCH,thematic.now()))
            c.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?)",
                      (BATCH,REG,thematic.now()))
        self.item = {"batch":BATCH,"reg":REG,"doc_id":1,"ready":1,
                     "categories":["dates"],"source_version":"source-before",
                     "analysis_version":fast.ANALYSIS_VERSION}

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.05, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def connection(self):
        c = self.connect()
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def finding(self, *, later=False):
        dates = "10.09.2026 — 02.10.2026" if later else "10.08.2026 — 02.09.2026"
        return {"code":"223-4-11-AMENDMENT-PUBLICATION-DELAY","kind":"violation",
                "category":"dates","title":"Изменения опубликованы с опозданием",
                "law":"223-ФЗ, ст. 4 ч. 11",
                "evidence":"Дата решения и дата публикации в ЕИС: " + dates,
                "document":"Изменения документации.pdf",
                "source_url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/"
                             "fz223/file.html?uid=" + "A" * 32}

    def intake_write(self):
        with self.connection() as c:
            c.execute("INSERT INTO intake DEFAULT VALUES")


class AuditPersistenceLockScope(DatabaseFixture, unittest.TestCase):
    def test_general_other_writer_can_commit_during_event_comparison(self):
        first, later = self.finding(), self.finding(later=True)
        self.assertEqual(general.persist(self.ns, REG, [first], []), (1,0))
        actual = episodes.distinct_new
        def compare(*args):
            self.intake_write()
            return actual(*args)
        with patch.object(episodes, "distinct_new", side_effect=compare):
            self.assertEqual(general.persist(self.ns, REG, [later], []), (1,0))
        with self.connection() as c:
            self.assertGreater(c.execute("SELECT COUNT(*) FROM intake").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 2)

    def test_thematic_other_writer_can_commit_during_event_comparison(self):
        first, later = self.finding(), self.finding(later=True)
        fast._persist(self.ns, [(self.item,"completed",[first],"done")])
        actual = dedup._same_finding
        def compare(*args):
            self.intake_write()
            return actual(*args)
        with patch.object(dedup, "_same_finding", side_effect=compare):
            fast._persist(self.ns, [(self.item,"completed",[later],"done")])
        with self.connection() as c:
            self.assertGreater(c.execute("SELECT COUNT(*) FROM intake").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0], 2)

    def test_general_concurrent_save_rebases_and_does_not_duplicate_event(self):
        first,later=self.finding(),self.finding(later=True)
        general.persist(self.ns,REG,[first],[])
        actual=episodes.distinct_new
        changed=False
        def compare(*args):
            nonlocal changed
            if not changed:
                changed=True
                self.assertEqual(general.persist(self.ns,REG,[later],[]),(1,0))
            return actual(*args)
        with patch.object(episodes,"distinct_new",side_effect=compare):
            self.assertEqual(general.persist(self.ns,REG,[later],[]),(0,0))
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT violations FROM purchases").fetchone()[0],2)

    def test_general_concurrent_check_status_change_is_preserved(self):
        finding=self.finding()
        checklist={**finding,"item":finding["title"],"field_path":finding["code"],"status":"check"}
        general.persist(self.ns,REG,[],[checklist])
        actual=episodes.distinct_new
        changed=False
        def compare(*args):
            nonlocal changed
            if not changed:
                changed=True
                with self.connection() as c:
                    c.execute("UPDATE checks SET status='violation'")
                    c.execute("UPDATE purchases SET violations=1")
            return actual(*args)
        with patch.object(episodes,"distinct_new",side_effect=compare):
            self.assertEqual(general.persist(self.ns,REG,[finding],[]),(0,0))
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT status FROM checks").fetchone()[0],"violation")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0],0)

    def test_general_reservation_change_during_comparison_prevents_late_save(self):
        general.persist(self.ns,REG,[self.finding()],[])
        with self.connection() as c:
            c.execute("CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,reservation_token TEXT)")
            c.execute("INSERT INTO v332_audit_queue VALUES(?,?,'running','old-owner')",(general.CAMPAIGN,REG))
        old=general.AuditReservation(REG,"old-owner")
        actual=episodes.distinct_new
        changed=False
        def compare(*args):
            nonlocal changed
            if not changed:
                changed=True
                with self.connection() as c:
                    c.execute("UPDATE v332_audit_queue SET reservation_token='new-owner'")
            return actual(*args)
        with patch.object(episodes,"distinct_new",side_effect=compare):
            with self.assertRaisesRegex(LookupError,"superseded"):
                general.persist(self.ns,old,[self.finding(later=True)],[])
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT violations FROM purchases").fetchone()[0],1)
            self.assertEqual(tuple(c.execute("SELECT state,reservation_token FROM v332_audit_queue").fetchone()),
                             ("running","new-owner"))

    def test_thematic_concurrent_save_rebases_and_does_not_duplicate_event(self):
        first,later=self.finding(),self.finding(later=True)
        fast._persist(self.ns,[(self.item,"completed",[first],"done")])
        actual=dedup._same_finding
        changed=False
        def compare(*args):
            nonlocal changed
            if not changed:
                changed=True
                fast._persist(self.ns,[(self.item,"completed",[later],"done")])
            return actual(*args)
        with patch.object(dedup,"_same_finding",side_effect=compare):
            fast._persist(self.ns,[(self.item,"completed",[later],"done")])
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],2)

    def _thematic_concurrent_change(self,change):
        fast._persist(self.ns,[(self.item,"completed",[self.finding()],"done")])
        actual=dedup._same_finding
        changed=False
        def compare(*args):
            nonlocal changed
            if not changed:
                changed=True
                with self.connection() as c:
                    change(c)
            return actual(*args)
        with patch.object(dedup,"_same_finding",side_effect=compare):
            fast._persist(self.ns,[(self.item,"completed",[self.finding(later=True)],"later")])

    def test_thematic_stop_during_comparison_prevents_late_save(self):
        self._thematic_concurrent_change(lambda c:c.execute("UPDATE thematic_batches SET status='stopped'"))
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT detail FROM thematic_items").fetchone()[0],"done")

    def test_thematic_deleted_item_is_not_resurrected_by_late_save(self):
        self._thematic_concurrent_change(lambda c:c.execute("DELETE FROM thematic_items"))
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_items").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],1)

    def test_thematic_deleted_campaign_is_not_resurrected_by_late_save(self):
        def delete(c):
            c.execute("DELETE FROM thematic_findings")
            c.execute("DELETE FROM thematic_items")
            c.execute("DELETE FROM thematic_batches")
        self._thematic_concurrent_change(delete)
        with self.connection() as c:
            for table in ("thematic_batches","thematic_items","thematic_findings"):
                self.assertEqual(c.execute("SELECT COUNT(*) FROM "+table).fetchone()[0],0)

    def test_thematic_date_correction_during_comparison_preserves_scope(self):
        with self.connection() as c:
            c.execute("UPDATE thematic_batches SET date_from='2026-10-19',date_to='2026-10-19'")
        self._thematic_concurrent_change(lambda c:c.execute("UPDATE purchases SET published_at='2026-10-20'"))
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT status FROM thematic_items").fetchone()[0],"outside_period")


class PendingThematicResults(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(thematic,"storage_health",return_value={"blocked":False}))
        self.stack.enter_context(patch.object(fast,"_request_missing_originals",return_value=0))
        # Use the real scheduler and reservation lifecycle. Only the source
        # fingerprint and legal engine are fixed inputs to this SQLite fixture.
        with self.connection() as c:
            c.execute("INSERT INTO docs(id,reg_number,local_path,status) VALUES(1,?,'fixture','checked')",(REG,))
        self.stack.enter_context(patch.object(fast,"receipt_readiness",return_value=(False,None)))
        self.stack.enter_context(patch.object(fast,"semantic_source_version",
            side_effect=lambda c,reg:self.item["source_version"]))
        self.stack.enter_context(patch.object(fast,"engine_signature",
            side_effect=lambda:self.item.get("engine_signature","fixture-engine")))
        self.stack.enter_context(patch.object(fast,"DB_RETRY_DELAYS",(.005,.005)))
        actual_open=fast._open
        def short_open(ns):
            c=actual_open(ns)
            c.execute("PRAGMA busy_timeout=10")
            return c
        self.stack.enter_context(patch.object(fast,"_open",side_effect=short_open))
        self.reviews=0
        self.block_save=False
        self.blocker=None
        self.addCleanup(self.release_blocker)
        async def review(*args):
            self.reviews+=1
            with self.connection() as c:
                row=c.execute("SELECT status,reservation_token FROM thematic_items").fetchone()
                self.assertEqual(row["status"],"running")
                self.assertTrue(row["reservation_token"])
            if self.block_save:
                self.block_save=False
                # Hold the real competing writer only after selection, start,
                # fresh watermark and analysis have all finished successfully.
                self.blocker=self.connect()
                self.blocker.execute("BEGIN IMMEDIATE")
            return "completed",[self.finding()]
        self.stack.enter_context(patch.object(thematic,"independent_review",side_effect=review))

    def release_blocker(self):
        if self.blocker is not None:
            self.blocker.rollback()
            self.blocker.close()
            self.blocker=None

    def expire_cooldown(self):
        with self.connection() as c:
            c.execute("UPDATE thematic_items SET next_retry_at=0")

    async def block_first_save(self):
        self.block_save=True
        actual=fast._persist
        failures=0
        def persist(ns,results):
            nonlocal failures
            try:return actual(ns,results)
            except sqlite3.OperationalError as exc:
                self.assertTrue(thematic.sqlite_busy(exc))
                failures+=1
                if failures==len(fast.DB_RETRY_DELAYS)+1:
                    # Cleanup must now run through the production token-fenced
                    # release path, after the failed persistence transaction.
                    self.release_blocker()
                raise
        try:
            with patch.object(fast,"_persist",side_effect=persist):
                self.assertEqual(await fast.faster_tick(self.ns),0)
        finally:
            self.release_blocker()
        self.assertEqual(failures,len(fast.DB_RETRY_DELAYS)+1)
        with self.connection() as c:
            row=c.execute("SELECT status,last_source_version,reservation_token,next_retry_at FROM thematic_items").fetchone()
            self.assertEqual(tuple(row)[:3],("queued","",""))
            self.assertGreater(row["next_retry_at"],time.time())
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],0)
        self.assertEqual(self.reviews,1)
        self.assertEqual(fast._prepare(self.ns),[],"A live retry deadline must survive a new selection pass")

    async def test_busy_reuses_finished_evidence_without_second_legal_analysis(self):
        await self.block_first_save()
        old_result=self.ns["_thematic_pending_results"][(BATCH,REG)]["result"]
        old_token=old_result[0]["reservation_token"]
        self.assertTrue(old_token)
        self.expire_cooldown()
        actual=fast._persist
        saved_tokens=[]
        def persist(ns,results):
            current=results[0][0]
            saved_tokens.append(current["reservation_token"])
            self.assertNotEqual(current["reservation_token"],old_token)
            with self.connection() as c:
                before=dict(c.execute("SELECT * FROM thematic_items").fetchone())
            self.assertEqual(before["status"],"running")
            self.assertEqual(before["reservation_token"],current["reservation_token"])
            self.assertEqual(actual(ns,[old_result]),0,"A released token cannot write into a newly owned attempt")
            with self.connection() as c:
                self.assertEqual(dict(c.execute("SELECT * FROM thematic_items").fetchone()),before)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],0)
            return actual(ns,results)
        with patch.object(fast,"_persist",side_effect=persist):
            self.assertEqual(await fast.faster_tick(self.ns),1)
        self.assertEqual(len(saved_tokens),1)
        self.assertEqual(self.reviews,1)
        self.assertEqual(self.ns["_thematic_pending_results"],{})
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],1)
            self.assertEqual(tuple(c.execute("SELECT status,last_source_version,last_semantic_version,"
                                            "last_engine_signature,reservation_token FROM thematic_items").fetchone()),
                             ("completed","source-before","source-before","fixture-engine",""))

    async def test_new_original_invalidates_uncommitted_result_before_current_watermark(self):
        await self.block_first_save()
        self.item["source_version"]="source-after"
        self.expire_cooldown()
        self.assertEqual(await fast.faster_tick(self.ns),1)
        self.assertEqual(self.reviews,2)
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT last_source_version FROM thematic_items").fetchone()[0],"source-after")

    async def test_new_engine_invalidates_uncommitted_result_before_current_watermark(self):
        await self.block_first_save()
        self.item["engine_signature"]="engine-after"
        self.expire_cooldown()
        self.assertEqual(await fast.faster_tick(self.ns),1)
        self.assertEqual(self.reviews,2)
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT last_engine_signature FROM thematic_items").fetchone()[0],"engine-after")

    async def test_changed_categories_invalidate_uncommitted_result(self):
        await self.block_first_save()
        with self.connection() as c:
            c.execute("UPDATE thematic_batches SET categories='[\"security\"]'")
        self.expire_cooldown()
        self.assertEqual(await fast.faster_tick(self.ns),1)
        self.assertEqual(self.reviews,2)

    async def test_expired_result_is_reanalysed(self):
        await self.block_first_save()
        entry=self.ns["_thematic_pending_results"][(BATCH,REG)]
        entry["expires_at"]=time.monotonic()-1
        self.expire_cooldown()
        self.assertEqual(await fast.faster_tick(self.ns),1)
        self.assertEqual(self.reviews,2)

    async def test_pending_result_limits_bound_memory_and_reuse_count(self):
        pending={}
        result=(self.item,"completed",[self.finding()],"done")
        with patch.object(fast,"PENDING_RESULT_LIMIT",2):
            for index in range(3):
                item={**self.item,"reg":str(index)}
                fast._retain_pending_result(pending,(item,*result[1:]))
        self.assertEqual(len(pending),2)
        self.assertNotIn((BATCH,"0"),pending)
        fast._retain_pending_result(pending,({**self.item,"reg":"huge"},"completed",[],"X"*(fast.PENDING_RESULT_BYTES+1)))
        self.assertEqual(len(pending),2)
        for entry in pending.values():
            entry["reuses"]=fast.PENDING_RESULT_REUSES
        self.ns["_thematic_pending_results"]=pending
        self.assertEqual(fast._pending_results(self.ns),{})

    async def test_cancelled_persistence_waits_for_real_sqlite_commit(self):
        entered=threading.Event()
        release=threading.Event()
        finished=threading.Event()
        actual=fast._persist
        def persist(ns,results):
            entered.set()
            if not release.wait(3):raise RuntimeError("Fixture did not release persistence")
            try:return actual(ns,results)
            finally:finished.set()
        with patch.object(fast,"_persist",side_effect=persist):
            task=asyncio.create_task(fast.faster_tick(self.ns))
            try:
                deadline=time.monotonic()+1
                while not entered.is_set() and time.monotonic()<deadline:
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set())
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done(),"Cancellation abandoned a still-running SQLite writer")
                self.assertFalse(finished.is_set())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()
