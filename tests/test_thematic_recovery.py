"""Recovery against real SQLite locks and synthetic receipt-verified originals.

No live data or remote requests. The legal rules, original authentication,
thematic selection, source versions and commits are the production functions.
"""
import asyncio
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import v341_precise_legal as legal
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast


URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
TEXT_A = ("Дата поступления запроса разъяснения: 01.10.2026. "
          "Дата публикации ответа в ЕИС: 07.10.2026.")
TEXT_B = ("Дата поступления запроса разъяснения: 08.10.2026. "
          "Дата публикации ответа в ЕИС: 14.10.2026.")


class ThematicRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "audit.sqlite"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"EIS223_DATA_DIR": str(self.root)}))
        self.stack.enter_context(patch.object(thematic, "storage_health", return_value={"blocked": False}))
        self.stack.enter_context(patch.object(fast, "_request_missing_originals", return_value=0))
        state, tick = dict(thematic.WORKER_STATE), thematic.tick
        history = dict(thematic._HISTORY_COUNT_CACHE)

        def restore():
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(state)
            thematic._HISTORY_COUNT_CACHE.clear()
            thematic._HISTORY_COUNT_CACHE.update(history)
            thematic.tick = tick

        self.addCleanup(restore)
        thematic.WORKER_STATE.clear()
        thematic._HISTORY_COUNT_CACHE.clear()
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,
                    title TEXT,method TEXT,published_at TEXT,submission_end TEXT,
                    card_fields_json TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,
                    url TEXT,doc_type TEXT,published_at TEXT,local_path TEXT,status TEXT,
                    metadata_json TEXT,final_url TEXT,text_chars INTEGER);
                CREATE INDEX docs_by_reg ON docs(reg_number,id);
            """)
        self.calls = []
        self.ns = {"conn": self.connect, "extract_text": self.extract,
                   "analyze": self.analyze, "build_checks": self.checklist}
        thematic.init(self.ns)
        fast.install(self.ns)
        self.batch = "a" * 24
        self.add_batch(self.batch)
        self.next_uid = 1

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.1, check_same_thread=False)
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

    def add_batch(self, batch, *, date_from="", date_to="", status="active"):
        with self.connection() as c:
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms,date_from,date_to) "
                      "VALUES(?,? ,?,?,?, ?,?)",
                      (batch, '["dates"]', thematic.now(), status, '["ООО"]', date_from, date_to))

    def purchase(self, suffix=1):
        reg = f"326100{suffix:05d}"
        with self.connection() as c:
            c.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?)",
                      (reg, "ООО Тест", "Тестовые материалы", "Открытый конкурс",
                       "2026-10-19", "2026-10-30", '{"status":"partial","pages":{}}'))
        return reg

    def document(self, reg, text=TEXT_A, *, doc_id=None, trusted=True):
        if doc_id is None:
            uid = f"{self.next_uid:032X}"
            self.next_uid += 1
        else:
            with self.connection() as c:
                prior = c.execute("SELECT metadata_json FROM docs WHERE id=?", (doc_id,)).fetchone()
            uid = json.loads(prior[0])["uid"]
        document = ElementTree.Element("document")
        document.text = text
        data = ElementTree.tostring(document, encoding="utf-8", xml_declaration=True)
        digest = hashlib.sha256(data).hexdigest()
        folder = self.root / "eis223_official_originals" / reg / uid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + ".xml")
        path.write_bytes(data)
        receipt = {"reg_number": reg, "uid": uid, "extension": ".xml",
                   "sha256": digest, "bytes": len(data)}
        (folder / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        metadata = {"uid": uid, "source": "official_eis_original" if trusted else "unverified_fixture",
                    "source_url": URL + uid, "sha256": digest}
        fields = (reg, "Разъяснения-" + uid + ".xml", URL + uid, "clarification",
                  "2026-10-19", str(path), "checked", json.dumps(metadata), URL + uid, len(text))
        with self.connection() as c:
            if doc_id is None:
                doc_id = c.execute("INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,"
                                   "status,metadata_json,final_url,text_chars) VALUES(?,?,?,?,?,?,?,?,?,?)", fields).lastrowid
            else:
                c.execute("UPDATE docs SET reg_number=?,name=?,url=?,doc_type=?,published_at=?,local_path=?,"
                          "status=?,metadata_json=?,final_url=?,text_chars=? WHERE id=?", (*fields, doc_id))
        return doc_id, path

    def extract(self, path):
        return "\n".join(ElementTree.parse(path).getroot().itertext())

    def analyze(self, purchase, docs):
        self.calls.append(("analyze", purchase["reg"], id(purchase), id(docs)))
        return legal.evaluate(purchase, docs)[0]

    def checklist(self, purchase, detail, card, docs):
        self.calls.append(("checklist", purchase["reg"], id(purchase), id(docs)))
        findings, checks = legal.evaluate(purchase, docs)
        return checks + [{**f, "item": f["title"], "field_path": f["code"], "status": "violation"}
                         for f in findings]

    def item(self, reg, batch=None):
        with self.connection() as c:
            row = c.execute("SELECT * FROM thematic_items WHERE batch_id=? AND reg=?",
                            (batch or self.batch, reg)).fetchone()
            return dict(row) if row else None

    def findings(self, reg=None):
        with self.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM thematic_findings WHERE batch_id=? "
                                               + ("AND reg=? " if reg else "") + "ORDER BY rowid",
                                               (self.batch, reg) if reg else (self.batch,))]

    def age(self, reg, seconds):
        timestamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        with self.connection() as c:
            c.execute("UPDATE thematic_items SET updated_at=? WHERE batch_id=? AND reg=?",
                      (timestamp, self.batch, reg))

    def short_busy_timeout(self):
        def opened(ns):
            c = thematic.db(ns)
            c.execute("PRAGMA busy_timeout=20")
            return c
        return patch.object(fast, "_open", side_effect=opened)

    async def wait_event(self, event):
        deadline = time.monotonic() + 2
        while not event.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(.005)
        self.assertTrue(event.is_set(), "The real SQLite contention point was not reached")

    async def test_full_engine_preserves_checklist_only_violations_and_rejects_unproved_rows(self):
        reg = self.purchase()
        self.document(reg)
        self.document(reg, TEXT_B)
        self.document(reg, TEXT_A, trusted=False)
        base_analyze, base_check = self.analyze, self.checklist

        def analyzer(p, docs):
            findings = base_analyze(p, docs)
            first = findings[0]
            # The second independent event is emitted solely by build_checks.
            return [first, {**first, "kind": "risk"}, {**first, "evidence": ""},
                    {**first, "law": ""}, {**first, "document": "unknown", "source_url": "https://example.org"}]

        def checker(p, detail, card, docs):
            self.assertEqual(len(docs), 2, "Unverified originals reached legal analysis")
            valid = base_check(p, detail, card, docs)
            return valid + valid + [{**valid[0], "status": state} for state in ("check", "risk", "ok")]

        self.ns.update(analyze=analyzer, build_checks=checker)
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(self.item(reg)["status"], "completed")
        self.assertEqual(len(self.findings(reg)), 2)
        self.assertEqual(self.calls[0][2:], self.calls[1][2:])
        self.assertEqual(fast._prepare(self.ns), [])
        # Repeating the complete audit never creates another copy of either act.
        with self.connection() as c:
            c.execute("UPDATE thematic_items SET last_analysis_version=0")
        await fast.faster_tick(self.ns)
        self.assertEqual(len(self.findings(reg)), 2)

    async def test_new_revision_in_same_docs_row_is_reaudited_without_losing_history(self):
        reg = self.purchase()
        doc_id, original = self.document(reg)
        await fast.faster_tick(self.ns)
        saved = self.findings(reg)
        first_version = self.item(reg)["last_source_version"]
        self.document(reg, TEXT_B, doc_id=doc_id)
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(len(self.findings(reg)), 2)
        self.assertEqual(self.findings(reg)[0], saved[0])
        self.assertTrue(original.is_file())
        with self.connection() as c:
            self.assertEqual(tuple(c.execute("SELECT MAX(id),COUNT(*) FROM docs").fetchone()), (doc_id, 1))
        self.assertNotEqual(self.item(reg)["last_source_version"], first_version)
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_restored_original_at_same_path_and_id_releases_waiting_immediately(self):
        reg = self.purchase()
        doc_id, path = self.document(reg)
        await fast.faster_tick(self.ns)
        data = path.read_bytes()
        path.unlink()
        await fast.faster_tick(self.ns)
        self.assertEqual(self.item(reg)["status"], "waiting_originals")
        self.assertEqual(len(self.findings(reg)), 1)
        self.assertEqual(fast._prepare(self.ns), [])
        path.write_bytes(data)
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(self.item(reg)["status"], "completed")
        self.assertEqual(len(self.findings(reg)), 1)
        self.assertEqual(self.item(reg)["last_doc_id"], doc_id)

    async def test_arrival_during_analysis_does_not_advance_watermark_past_unread_evidence(self):
        reg = self.purchase()
        doc_id, _ = self.document(reg)
        original = self.analyze
        changed = False

        def analyzer(p, docs):
            nonlocal changed
            result = original(p, docs)
            if not changed:
                changed = True
                self.document(reg, TEXT_B, doc_id=doc_id)
            return result

        self.ns["analyze"] = analyzer
        await fast.faster_tick(self.ns)
        self.assertEqual(len(self.findings(reg)), 1)
        self.assertEqual([r["reg"] for r in fast._prepare(self.ns)], [reg])
        await fast.faster_tick(self.ns)
        self.assertEqual(len(self.findings(reg)), 2)
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_partial_read_failure_saves_proof_then_retries_without_hot_loop(self):
        reg = self.purchase()
        self.document(reg)
        failed_id, failed_path = self.document(reg, TEXT_B)
        with self.connection() as c:
            c.execute("UPDATE docs SET status='downloaded_no_text',text_chars=0 WHERE id=?", (failed_id,))
        failing = True

        def extract(path):
            if failing and Path(path) == failed_path:
                raise OSError("Temporary fixture extraction failure")
            return self.extract(path)

        self.ns["extract_text"] = extract
        await fast.faster_tick(self.ns)
        self.assertEqual((self.item(reg)["status"], self.item(reg)["retry_attempts"]), ("retry", 1))
        self.assertEqual(len(self.findings(reg)), 1)
        self.assertEqual(fast._prepare(self.ns), [])
        self.age(reg, 100)
        await fast.faster_tick(self.ns)
        self.assertEqual(self.item(reg)["retry_attempts"], 2)
        self.age(reg, 100)
        self.assertEqual(fast._prepare(self.ns), [], "An unchanged damaged file must back off")
        failing = False  # Same rows, paths and bytes; only the temporary read error recovers.
        self.age(reg, 200)
        await fast.faster_tick(self.ns)
        self.assertEqual((self.item(reg)["status"], self.item(reg)["retry_attempts"]), ("completed", 0))
        self.assertEqual(len(self.findings(reg)), 2)
        self.assertEqual(fast._prepare(self.ns), [])
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT status FROM docs WHERE id=?", (failed_id,)).fetchone()[0],
                             "downloaded_no_text", "The audit must not fabricate an intake checked status")

    async def test_saved_no_text_status_is_retried_after_extractor_recovers_without_source_change(self):
        reg = self.purchase()
        doc_id, path = self.document(reg)
        with self.connection() as c:
            c.execute("UPDATE docs SET status='downloaded_no_text',text_chars=0 WHERE id=?", (doc_id,))
            before = tuple(c.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone())
        saved_bytes = path.read_bytes()
        self.ns["extract_text"] = lambda path: ""
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        failed = self.item(reg)
        self.assertEqual((failed["status"], failed["retry_attempts"]), ("retry", 1))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.findings(reg), [])
        self.assertEqual(fast._prepare(self.ns), [], "The unchanged failed extraction must wait")
        self.ns["extract_text"] = self.extract
        self.age(reg, 100)
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(self.item(reg)["status"], "completed")
        self.assertEqual(self.item(reg)["last_source_version"], failed["last_source_version"])
        self.assertEqual(len(self.findings(reg)), 1)
        with self.connection() as c:
            self.assertEqual(tuple(c.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()), before)
        self.assertEqual(path.read_bytes(), saved_bytes)
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_item_deadline_preserves_fast_sibling_and_retries_without_late_thread_result(self):
        slow, quick = self.purchase(1), self.purchase(2)
        self.document(slow)
        self.document(quick)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        quick_saved = threading.Event()
        analyze, persist = self.analyze, fast._persist

        def analyzer(purchase, docs):
            if purchase["reg"] != slow:
                return analyze(purchase, docs)
            entered.set()
            try:
                if not release.wait(5):
                    raise RuntimeError("Fixture failed to release its read-only analyzer")
                return analyze(purchase, docs)
            finally:
                finished.set()

        def save(ns, results):
            result = persist(ns, results)
            if results[0][0]["reg"] == quick:
                quick_saved.set()
            return result

        self.ns["analyze"] = analyzer
        task = None
        try:
            with patch.object(fast, "ITEM_TIMEOUT_SECONDS", 1), patch.object(fast, "_persist", new=save):
                task = asyncio.create_task(fast.faster_tick(self.ns))
                await self.wait_event(entered)
                await self.wait_event(quick_saved)
                self.assertFalse(task.done(), "The fast result must commit before the blocked review finishes")
                self.assertEqual(self.item(quick)["status"], "completed")
                self.assertEqual(self.item(slow)["status"], "queued")
                self.assertEqual(await asyncio.wait_for(task, 3), 2)
                self.assertFalse(finished.is_set(), "The physical analyzer thread should still be blocked")
                self.assertEqual((self.item(slow)["status"], self.item(slow)["retry_attempts"]), ("retry", 1))
                self.assertEqual(self.item(slow)["last_analysis_version"], 0)
                self.assertEqual(self.findings(slow), [])
                self.assertEqual(fast._prepare(self.ns), [])
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await self.wait_event(finished)
        self.assertEqual(self.item(slow)["status"], "retry")
        self.assertEqual(self.findings(slow), [], "A late detached analyzer may not save a completed result")
        self.assertFalse(any(call[0] == "checklist" and call[1] == slow for call in self.calls))
        self.age(slow, 100)
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(self.item(slow)["status"], "completed")
        self.assertEqual(len(self.findings(slow)), 1)
        self.assertEqual(len(self.findings(quick)), 1)

    async def test_no_usable_text_is_retry_and_new_evidence_bypasses_backoff(self):
        reg = self.purchase()
        doc_id, _ = self.document(reg)
        self.ns["extract_text"] = lambda path: ""
        await fast.faster_tick(self.ns)
        self.assertEqual(self.item(reg)["status"], "retry")
        self.assertEqual(self.findings(reg), [])
        self.assertEqual(self.calls, [])
        with self.connection() as c:
            c.execute("UPDATE thematic_items SET retry_attempts=20")
        self.assertEqual(fast._prepare(self.ns), [])
        self.document(reg, TEXT_B, doc_id=doc_id)
        self.ns["extract_text"] = self.extract
        await fast.faster_tick(self.ns)
        self.assertEqual(self.item(reg)["status"], "completed")
        self.assertEqual(len(self.findings(reg)), 1)

    async def test_checklist_error_does_not_complete_analyzer_only_review(self):
        reg = self.purchase()
        self.document(reg)
        calls = 0

        def checker(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("Temporary fixture checklist failure")
            return self.checklist(*args)

        self.ns["build_checks"] = checker
        await fast.faster_tick(self.ns)
        self.assertEqual(self.item(reg)["status"], "retry")
        self.assertEqual(self.item(reg)["last_analysis_version"], 0)
        self.assertEqual(self.findings(reg), [])
        self.age(reg, 100)
        await fast.faster_tick(self.ns)
        self.assertEqual(calls, 2)
        self.assertEqual(self.item(reg)["last_analysis_version"], fast.ANALYSIS_VERSION)
        self.assertEqual(len(self.findings(reg)), 1)

    async def test_real_sqlite_busy_retries_commit_without_premature_completed(self):
        reg = self.purchase()
        self.document(reg)
        items = fast._prepare(self.ns)
        blocker = self.connect()
        blocker.execute("BEGIN IMMEDIATE")
        blocked = threading.Event()
        actual = fast._persist
        errors = []

        def persist(ns, results):
            try:
                return actual(ns, results)
            except sqlite3.OperationalError as exc:
                errors.append(exc.sqlite_errorcode)
                blocked.set()
                raise

        try:
            with self.short_busy_timeout(), patch.object(fast, "_prepare", return_value=items), \
                    patch.object(fast, "_persist", new=persist):
                task = asyncio.create_task(fast.faster_tick(self.ns))
                await self.wait_event(blocked)
                self.assertEqual(self.item(reg)["status"], "queued")
                self.assertEqual(self.item(reg)["last_source_version"], "")
                self.assertEqual(self.findings(reg), [])
                blocker.rollback()
                self.assertEqual(await asyncio.wait_for(task, 3), 1)
        finally:
            blocker.close()
        self.assertTrue(errors and all(code & 255 == sqlite3.SQLITE_BUSY for code in errors))
        self.assertEqual(self.item(reg)["status"], "completed")
        self.assertEqual(len(self.findings(reg)), 1)

    async def test_exhausted_busy_for_one_result_does_not_cancel_other_items(self):
        first, second = self.purchase(1), self.purchase(2)
        self.document(first)
        self.document(second)
        items = fast._prepare(self.ns)
        blocker = self.connect()
        blocker.execute("BEGIN IMMEDIATE")
        ready = asyncio.Event()
        loop = asyncio.get_running_loop()
        actual_persist, actual_review = fast._persist, thematic.independent_review
        failures = 0

        async def review(ns, reg, categories):
            if reg == second:
                await ready.wait()
            return await actual_review(ns, reg, categories)

        def persist(ns, results):
            nonlocal failures
            try:
                return actual_persist(ns, results)
            except sqlite3.OperationalError:
                failures += 1
                if failures == 3:
                    blocker.rollback()
                    loop.call_soon_threadsafe(ready.set)
                raise

        try:
            with self.short_busy_timeout(), patch.object(fast, "DB_RETRY_DELAYS", (.01, .01)), \
                    patch.object(fast, "_prepare", return_value=items), \
                    patch.object(fast, "_persist", new=persist), \
                    patch.object(thematic, "independent_review", side_effect=review):
                self.assertEqual(await asyncio.wait_for(fast.faster_tick(self.ns), 3), 1)
        finally:
            blocker.close()
        self.assertEqual(failures, 3)
        self.assertEqual(self.item(first)["status"], "queued")
        self.assertEqual(self.item(first)["last_source_version"], "")
        self.assertEqual(self.item(second)["status"], "completed")
        self.assertEqual([row["reg"] for row in self.findings()], [second])
        await fast.faster_tick(self.ns)
        self.assertEqual(self.item(first)["status"], "completed")
        self.assertEqual(len(self.findings()), 2)

    async def test_failure_after_finding_insert_rolls_back_result_and_continues_batch(self):
        first, second = self.purchase(1), self.purchase(2)
        self.document(first)
        self.document(second)
        with self.connection() as c:
            c.execute("CREATE TRIGGER fixture_fail_update BEFORE UPDATE ON thematic_items "
                      "WHEN NEW.status='completed' AND NEW.reg='" + first + "' "
                      "BEGIN SELECT RAISE(ABORT,'fixture commit-stage error'); END")
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(self.item(first)["status"], "queued")
        self.assertEqual(self.item(first)["last_source_version"], "")
        self.assertEqual(self.findings(first), [])
        self.assertEqual(len(self.findings(second)), 1)
        with self.connection() as c:
            c.execute("DROP TRIGGER fixture_fail_update")
        await fast.faster_tick(self.ns)
        self.assertEqual(len(self.findings()), 2)

    async def test_history_lock_retries_fresh_result_with_same_owner_filter(self):
        reg = self.purchase()
        self.document(reg)
        foreign = "b" * 24
        self.add_batch(foreign)
        await fast.faster_tick(self.ns)
        cached = thematic.thematic_history_records(self.ns, [self.batch])
        self.assertEqual(cached["batches"][0]["violations"], 1)
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=DELETE")
        blocker = self.connect()
        blocker.execute("BEGIN EXCLUSIVE")
        actual = thematic._thematic_history_records
        failures = []

        def snapshot(ns, permitted_ids=None):
            try:
                return actual(ns, permitted_ids)
            except sqlite3.OperationalError as exc:
                failures.append(exc.sqlite_errorcode)
                blocker.rollback()
                # A fresh result must include a concurrent newly saved event,
                # and must not fall back to an unfiltered cached history.
                prior = self.findings(reg)[0]
                with self.connection() as c:
                    c.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence) "
                              "VALUES(?,?,?,?,?,?)", (self.batch, reg, prior["category"], prior["title"],
                               prior["law"], prior["evidence"].replace("2026-10-01", "2026-10-08")))
                raise

        try:
            with patch.object(thematic, "_thematic_history_records", side_effect=snapshot):
                result = await asyncio.to_thread(thematic.thematic_history_records, self.ns, [self.batch])
        finally:
            blocker.close()
        self.assertEqual(len(failures), 1)
        self.assertEqual([row["id"] for row in result["batches"]], [self.batch])
        self.assertEqual(result["batches"][0]["violations"], 2)
        self.assertEqual(thematic.thematic_history_records(self.ns, []), {"batches": []})

    async def test_history_persistent_lock_is_bounded_and_does_not_return_stale_success(self):
        reg = self.purchase()
        self.document(reg)
        await fast.faster_tick(self.ns)
        thematic.thematic_history_records(self.ns, [self.batch])
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=DELETE")
        blocker = self.connect()
        blocker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        actual = thematic._thematic_history_records
        try:
            with patch.object(thematic, "_thematic_history_records", wraps=actual) as attempts:
                with self.assertRaises(thematic.HTTPException) as error:
                    # An outer saved-view wrapper must not multiply the three
                    # attempts already owned by the public history function.
                    await asyncio.to_thread(thematic.saved_read, thematic.thematic_history_records,
                                            self.ns, [self.batch])
                self.assertEqual(attempts.call_count, 3)
                self.assertEqual(error.exception.status_code, 503)
                self.assertEqual(error.exception.headers, {"Retry-After": "2"})
        finally:
            blocker.close()
        self.assertLess(time.monotonic() - started, 4.5)

    async def test_partial_results_respect_current_period_and_stopped_campaign(self):
        reg = self.purchase()
        self.document(reg)
        await fast.faster_tick(self.ns)
        historical = self.findings(reg)
        with self.connection() as c:
            c.execute("UPDATE thematic_batches SET date_from='2026-10-19',date_to='2026-10-19'")
            c.execute("UPDATE thematic_items SET last_analysis_version=0")
        item = fast._prepare(self.ns)[0]
        state, findings = await thematic.independent_review(self.ns, reg, ["dates"])
        self.assertEqual(state, "completed")
        with self.connection() as c:
            c.execute("UPDATE purchases SET published_at='2026-10-20' WHERE reg_number=?", (reg,))
        fast._persist(self.ns, [(item, "retry", findings, "Partial extraction")])
        self.assertEqual(self.item(reg)["status"], "outside_period")
        self.assertEqual(self.findings(reg), historical)
        with self.connection() as c:
            c.execute("UPDATE thematic_batches SET status='stopped'")
            c.execute("UPDATE purchases SET published_at='2026-10-19' WHERE reg_number=?", (reg,))
        fast._persist(self.ns, [(item, "retry", findings, "Partial extraction")])
        self.assertEqual(self.item(reg)["status"], "outside_period")
        self.assertEqual(self.findings(reg), historical)
        self.assertEqual(fast._prepare(self.ns), [])


if __name__ == "__main__":
    unittest.main()
