"""Bounded automatic legal catch-up, using temporary SQLite and local originals."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from test_general_audit_recovery import DatabaseFixture, LATE_REPLY
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
from audit_source_version import SemanticSnapshotError


class GeneralScheduler(DatabaseFixture, unittest.TestCase):
    def finish(self, reg, state='completed'):
        self.assertEqual(general.claim_next(self.ns), reg)
        watermark = general.review_watermark(self.ns, reg)
        general.mark(self.ns, reg, state, watermark)
        return watermark

    def test_real_source_prefix_resumes_before_general_queue_acknowledges(self):
        import audit_source_version as source
        source.clear_semantic_cache()
        self.addCleanup(source.clear_semantic_cache)
        self.assertEqual(source.SEMANTIC_MAX_BYTES, 256 * 1024 * 1024)
        self.assertEqual(source.SEMANTIC_MAX_FILES, 12000)
        self.assertEqual(source.SEMANTIC_MAX_SECONDS, 20)
        reg = self.purchase()
        general.initialize(self.ns)
        previous = self.finish(reg)
        before = self.row(reg)
        self.document(reg, 'Новый оригинал 2 ' + LATE_REPLY)
        self.document(reg, 'Новый оригинал 3 ' + LATE_REPLY)
        sizes = [len(value) for value in self.contents.values()]
        self.assertEqual(len(sizes), 3)
        self.assertLess(max(sizes), 50000)
        self.assertGreater(min(sizes) * 2, 50000)
        source.clear_semantic_cache()
        with patch.object(source, 'SEMANTIC_MAX_BYTES', 50000):
            for _ in range(2):
                self.assertEqual(general.refresh_changed(self.ns), 0)
                self.assertEqual(self.row(reg), before)
            self.assertEqual(general.refresh_changed(self.ns), 1)
            admitted = self.row(reg)
            self.assertEqual(admitted['state'], 'pending')
            self.assertEqual(admitted['last_semantic_version'], previous['last_semantic_version'])
            self.assertEqual(admitted['last_engine_signature'], previous['last_engine_signature'])
            current = self.finish(reg)
            self.assertNotEqual(current['last_semantic_version'], previous['last_semantic_version'])
            self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertEqual(source.SEMANTIC_MAX_BYTES, 256 * 1024 * 1024)

    def test_new_original_once_and_same_bytes_metadata_fixed_point(self):
        reg = self.purchase()
        general.initialize(self.ns)
        first = self.finish(reg)
        self.assertEqual(general.refresh_changed(self.ns), 0)
        path = next(iter(self.contents))
        os.utime(path, None)
        with self.connect() as con:
            con.execute("UPDATE docs SET status='downloaded_no_text',text_chars=text_chars+100")
            con.execute("UPDATE purchases SET violations=20,documents_checked=90")
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.document(reg, 'Новый официальный оригинал: ' + LATE_REPLY)
        self.assertEqual(general.refresh_changed(self.ns), 1)
        second = self.finish(reg)
        self.assertNotEqual(first['last_semantic_version'], second['last_semantic_version'])
        self.assertEqual(general.refresh_changed(self.ns), 0)

    def test_rule_only_change_unknown_migration_and_restart_no_seeding(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.finish(reg)
        before = self.row(reg)
        with patch.object(general, 'engine_signature', return_value='changed-engine'):
            self.assertEqual(general.refresh_changed(self.ns), 1)
            self.finish(reg)
            self.assertEqual(general.refresh_changed(self.ns), 0)
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET last_semantic_version='',last_engine_signature='' WHERE reg=?", (reg,))
        general.initialize(self.ns)
        self.assertEqual(self.row(reg)['state'], 'completed')
        self.assertEqual(self.row(reg)['last_semantic_version'], '')
        self.assertEqual(general.refresh_changed(self.ns), 1)
        self.assertEqual(self.row(reg)['new_findings'], before['new_findings'])

    def test_arrival_during_running_reservation_gets_exactly_one_followup(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.assertEqual(general.claim_next(self.ns), reg)
        before = general.review_watermark(self.ns, reg)
        self.document(reg, 'Новая редакция ' + LATE_REPLY)
        self.assertEqual(general.refresh_changed(self.ns), 0)
        general.mark(self.ns, reg, 'completed', before)
        self.assertEqual(general.refresh_changed(self.ns), 1)
        self.finish(reg)
        self.assertEqual(general.refresh_changed(self.ns), 0)

    def test_future_retry_and_running_reservations_survive_source_and_rule_changes(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.assertEqual(general.claim_next(self.ns), reg)
        watermark = general.review_watermark(self.ns, reg)
        general.defer_retry(self.ns, reg, watermark, 'partial')
        before = self.row(reg)
        self.document(reg, 'Пришёл ещё один оригинал ' + LATE_REPLY)
        with patch.object(general, 'engine_signature', return_value='changed-engine'):
            self.assertEqual(general.refresh_changed(self.ns), 0)
        after = self.row(reg)
        self.assertEqual((after['next_retry_at'], after['retry_count'], after['reservation_token']),
                         (before['next_retry_at'], before['retry_count'], before['reservation_token']))
        self.assertIsNone(general.claim_next(self.ns))

    def test_scan_is_bounded_persistent_fair_and_does_not_count_missing_docs(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.finish(reg)
        with self.connect() as con:
            for number in range(2, 301):
                later = str(32619000000 + number)
                con.execute("INSERT INTO purchases(reg_number,customer) VALUES(?,'ООО Тест')", (later,))
                con.execute("INSERT INTO docs(reg_number,local_path,status) VALUES(?,'fixture','checked')", (later,))
                con.execute("INSERT INTO v332_audit_queue(campaign,reg,state,last_semantic_version,last_engine_signature) VALUES(?,?,'completed','same','engine')", (general.CAMPAIGN, later))
            con.execute("UPDATE v332_audit_queue SET last_semantic_version='same',last_engine_signature='engine'")
            con.execute("UPDATE v332_audit_queue SET last_semantic_version='old' WHERE reg='32619000300'")
            # A large missing-original backlog must not block ready catch-up.
            for number in range(301, 340):
                con.execute("INSERT INTO v332_audit_queue(campaign,reg) VALUES(?,?)", (general.CAMPAIGN, str(32619000000 + number)))
        seen = []
        def semantic(con, number):
            self.assertFalse(con.in_transaction)
            seen.append(number)
            return 'same'
        with patch.object(general, 'semantic_source_version', side_effect=semantic), patch.object(general, 'engine_signature', return_value='engine'):
            self.assertEqual(general.refresh_changed(self.ns), 0)
            self.assertEqual(len(seen), 128)
            # New namespace/process keeps the durable keyset cursor.
            self.assertEqual(general.refresh_changed(dict(self.ns)), 0)
            self.assertEqual(len(seen), 256)
            self.assertEqual(general.refresh_changed(dict(self.ns)), 1)
            self.assertEqual(len(seen), 300)
        self.assertEqual(self.row('32619000300')['state'], 'pending')

    def test_admissions_highwater_and_concurrent_cas_are_bounded(self):
        regs = [self.purchase(i) for i in range(1, 25)]
        general.initialize(self.ns)
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET state='completed'")
        with patch.object(general, 'semantic_source_version', return_value='source'), patch.object(general, 'engine_signature', return_value='engine'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                counts = list(pool.map(lambda _: general.refresh_changed(self.ns), range(2)))
            self.assertTrue(all(count <= 8 for count in counts))
            self.assertLessEqual(sum(counts), 16)
            while general.refresh_changed(self.ns):
                pass
            self.assertEqual(sum(self.row(reg)['state'] == 'pending' for reg in regs), 16)

    def test_temporary_hash_error_and_racing_reservation_never_stamp_current(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.finish(reg)
        before = self.row(reg)
        with patch.object(general, 'semantic_source_version', side_effect=SemanticSnapshotError('fixture')):
            self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertEqual(self.row(reg), before)
        def racing(con, number):
            self.assertFalse(con.in_transaction)
            with self.connect() as writer:
                writer.execute("UPDATE v332_audit_queue SET state='running',reservation_token='new-owner',updated_at='changed' WHERE reg=?", (reg,))
            return 'new-source'
        with patch.object(general, 'semantic_source_version', side_effect=racing):
            self.assertEqual(general.refresh_changed(self.ns), 0)
        general.mark(self.ns, reg, 'completed', {'reservation_token': before['reservation_token'],
                     'last_semantic_version': 'late', 'last_engine_signature': 'late'})
        self.assertEqual(self.row(reg)['reservation_token'], 'new-owner')
        self.assertEqual(self.row(reg)['state'], 'running')

    def test_snapshot_sweep_diagnostic_keeps_reason_without_acknowledging_input(self):
        reg = self.purchase()
        general.initialize(self.ns)
        self.finish(reg)
        before = self.row(reg)
        with patch.object(general, 'semantic_source_version', side_effect=SemanticSnapshotError('byte_budget', '/private/path')):
            self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertEqual(self.row(reg), before)
        self.assertIn('reason=source_snapshot_byte_budget', self.output.getvalue())
        self.assertNotIn('/private/path', self.output.getvalue())

    def test_ineligible_company_backlog_does_not_consume_ready_capacity(self):
        stale = self.purchase(1)
        excluded = [self.purchase(i) for i in range(2, 19)]
        general.initialize(self.ns)
        self.finish(stale)
        with self.connect() as con:
            con.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number<>?", (stale,))
            con.execute("UPDATE v332_audit_queue SET last_engine_signature='old'")
        self.assertEqual(general.refresh_changed(self.ns), 1)
        self.assertEqual(self.row(stale)['state'], 'pending')
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET state='completed' WHERE reg<>?", (stale,))
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertTrue(all(self.row(reg)['state'] == 'completed' for reg in excluded))

    def test_old_worker_cannot_persist_mark_defer_or_release_new_claim(self):
        reg = self.purchase()
        general.initialize(self.ns)
        old = general.claim_next(self.ns)
        general.initialize(self.ns)
        new = general.claim_next(self.ns)
        before = self.row(reg)
        finding = {'kind': 'violation', 'code': 'late', 'title': 'Stale finding'}
        with self.assertRaisesRegex(LookupError, 'superseded'):
            general.persist(self.ns, old, [finding], [], 'old-result')
        general.mark(self.ns, old, 'completed', {'new_findings': 1, 'result_token': 'old-result'})
        general.defer_retry(self.ns, old, {}, 'old retry')
        general.return_running(self.ns, old)
        self.assertEqual(self.row(reg), before)
        self.assertEqual(self.findings(reg), 0)
        self.assertNotEqual(old.reservation_token, new.reservation_token)

    def test_wall_budget_advances_only_examined_rows(self):
        regs = [self.purchase(i) for i in range(1, 4)]
        general.initialize(self.ns)
        for reg in regs:self.finish(reg)
        calls = []
        real = general.semantic_source_version
        def slow(con, reg):
            calls.append(reg)
            time.sleep(.02)
            return real(con, reg)
        with patch.object(general, 'SCAN_BUDGET_SECONDS', .001), patch.object(general, 'semantic_source_version', side_effect=slow):
            self.assertEqual(general.refresh_changed(self.ns), 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(general.refresh_changed(dict(self.ns)), 0)
            self.assertEqual(calls, regs[:2])


class ThematicScheduler(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        previous_state, previous_tick = dict(thematic.WORKER_STATE), thematic.tick
        def restore():
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(previous_state)
            thematic.tick = previous_tick
        self.addCleanup(restore)
        thematic.WORKER_STATE.clear()
        thematic.init(self.ns)
        fast.install(self.ns)
        self.stack.enter_context(patch.object(thematic, 'storage_health', return_value={'blocked': False}))
        self.stack.enter_context(patch.object(fast, '_request_missing_originals', return_value=0))
        with self.connect() as con:
            con.execute("INSERT INTO thematic_batches(id,categories,created_at,legal_forms) VALUES('batch','[\"dates\"]','2026-10-19','[\"ООО\"]')")

    def item(self, reg):
        with self.connect() as con:
            row = con.execute("SELECT * FROM thematic_items WHERE batch_id='batch' AND reg=?", (reg,)).fetchone()
            return dict(row) if row else None

    async def test_real_source_prefix_resumes_before_thematic_queue_acknowledges(self):
        import audit_source_version as source
        source.clear_semantic_cache()
        self.addCleanup(source.clear_semantic_cache)
        self.assertEqual(source.SEMANTIC_MAX_BYTES, 256 * 1024 * 1024)
        self.assertEqual(source.SEMANTIC_MAX_FILES, 12000)
        self.assertEqual(source.SEMANTIC_MAX_SECONDS, 20)
        reg = self.purchase()
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        before = self.item(reg)
        self.assertEqual(before['status'], 'completed')
        self.document(reg, 'Новый оригинал 2 ' + LATE_REPLY)
        self.document(reg, 'Новый оригинал 3 ' + LATE_REPLY)
        sizes = [len(value) for value in self.contents.values()]
        self.assertEqual(len(sizes), 3)
        self.assertLess(max(sizes), 50000)
        self.assertGreater(min(sizes) * 2, 50000)
        source.clear_semantic_cache()
        with patch.object(source, 'SEMANTIC_MAX_BYTES', 50000):
            for _ in range(2):
                self.assertEqual(fast._prepare(self.ns), [])
                self.assertEqual(self.item(reg), before)
            items = fast._prepare(self.ns)
            self.assertEqual(len(items), 1)
            admitted = self.item(reg)
            self.assertEqual(admitted['status'], 'queued')
            self.assertTrue(admitted['reservation_token'])
            self.assertEqual(admitted['last_semantic_version'], before['last_semantic_version'])
            self.assertEqual(admitted['last_engine_signature'], before['last_engine_signature'])
            result = await fast._one(self.ns, items[0], asyncio.Semaphore(1))
            self.assertEqual(result[1], 'completed')
            fast._persist(self.ns, [result])
            current = self.item(reg)
            self.assertEqual(current['status'], 'completed')
            self.assertNotEqual(current['last_semantic_version'], before['last_semantic_version'])
            self.assertEqual(fast._prepare(self.ns), [])
        self.assertEqual(source.SEMANTIC_MAX_BYTES, 256 * 1024 * 1024)

    async def test_real_review_fixed_point_and_preserved_partial_deadline(self):
        reg = self.purchase()
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(self.item(reg)['status'], 'completed')
        self.assertTrue(self.item(reg)['last_semantic_version'])
        self.assertEqual(fast._prepare(self.ns), [])
        with self.connect() as con:
            con.execute("UPDATE docs SET text_chars=text_chars+1,status='downloaded_no_text'")
        self.assertEqual(fast._prepare(self.ns), [])
        self.document(reg, 'Ещё один документ ' + LATE_REPLY)
        async def partial(*args):return 'retry', []
        with patch.object(thematic, 'independent_review', side_effect=partial):
            self.assertEqual(await fast.faster_tick(self.ns), 1)
        before = self.item(reg)
        self.document(reg, 'Последний оригинал ' + LATE_REPLY)
        self.assertEqual(fast._prepare(self.ns), [])
        self.assertEqual(self.item(reg), before)

    async def test_snapshot_prepare_diagnostic_does_not_reserve_or_lose_old_result(self):
        reg = self.purchase()
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        before = self.item(reg)
        with patch.object(fast, 'semantic_source_version', side_effect=SemanticSnapshotError('resume_capacity')):
            self.assertEqual(fast._prepare(self.ns), [])
        self.assertEqual(self.item(reg), before)
        self.assertIn('reason=source_snapshot_resume_capacity', self.output.getvalue())

    async def test_snapshot_failure_after_reservation_preserves_watermark_and_skips_engine(self):
        reg = self.purchase()
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        before = self.item(reg)
        self.document(reg, 'Новый оригинал ' + LATE_REPLY)
        item = fast._prepare(self.ns)[0]
        engine = AsyncMock()
        with patch.object(fast, '_watermark', side_effect=SemanticSnapshotError('byte_budget')), \
             patch.object(thematic, 'independent_review', engine):
            result = await fast._one(self.ns, item, asyncio.Semaphore(1))
        self.assertEqual(result[1:], ('retry', [], 'source_snapshot_byte_budget'))
        engine.assert_not_awaited()
        fast._persist(self.ns, [result])
        after = self.item(reg)
        self.assertEqual(after['status'], 'retry')
        self.assertEqual(after['last_semantic_version'], before['last_semantic_version'])
        self.assertEqual(after['last_engine_signature'], before['last_engine_signature'])
        self.assertGreater(after['next_retry_at'], time.time())
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_running_status_concurrent_selection_and_arrival_followup(self):
        reg = self.purchase()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def review(*args):
            calls.append(args[1])
            entered.set()
            await release.wait()
            return 'completed', []
        with patch.object(thematic, 'independent_review', side_effect=review):
            task = asyncio.create_task(fast.faster_tick(self.ns))
            await asyncio.wait_for(entered.wait(), 5)
            self.assertEqual(self.item(reg)['status'], 'running')
            self.assertEqual(await asyncio.to_thread(fast._prepare, self.ns), [])
            self.document(reg, 'Новый документ во время работы ' + LATE_REPLY)
            release.set()
            self.assertEqual(await task, 1)
            self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(calls, [reg, reg])
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_stopped_campaign_rejects_late_findings(self):
        reg = self.purchase()
        items = fast._prepare(self.ns)
        self.assertEqual(len(items), 1)
        self.assertTrue(fast._start_item(self.ns, items[0]))
        with self.connect() as con:
            con.execute("UPDATE thematic_batches SET status='stopped'")
        before = self.item(reg)
        fast._persist(self.ns, [(items[0], 'completed', [{'category': 'dates', 'title': 'No late finding', 'law': 'Law', 'evidence': 'Evidence'}], 'done')])
        self.assertEqual(self.item(reg), before)
        with self.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM thematic_findings').fetchone()[0], 0)

    async def test_restart_recovers_only_owned_reservations_without_stamping(self):
        reg = self.purchase()
        item = fast._prepare(self.ns)[0]
        fast._start_item(self.ns, item)
        fast.install(self.ns)
        row = self.item(reg)
        self.assertEqual((row['status'], row['reservation_token'], row['last_semantic_version']), ('queued', '', ''))
        async def review(*args):return 'completed', []
        with patch.object(thematic, 'independent_review', side_effect=review):
            self.assertEqual(await fast.faster_tick(self.ns), 1)
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_hash_io_runs_off_loop_under_physical_gate_without_writer(self):
        reg = self.purchase()
        main_thread = threading.get_ident()
        real = fast.semantic_source_version
        def checked(con, number):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertFalse(con.in_transaction)
            with self.connect() as writer:
                writer.execute("UPDATE purchases SET violations=violations WHERE reg_number=?", (number,))
            return real(con, number)
        with patch.object(fast, 'semantic_source_version', side_effect=checked):
            self.assertEqual(await fast.faster_tick(self.ns), 1)

    async def test_outside_period_transition_clears_token_and_can_reenter_scope(self):
        reg = self.purchase()
        with self.connect() as con:
            con.execute("UPDATE thematic_batches SET date_from='2026-10-01',date_to='2026-10-01'")
        item = fast._prepare(self.ns)[0]
        self.assertTrue(fast._start_item(self.ns, item))
        with self.connect() as con:
            con.execute("UPDATE purchases SET published_at='2026-10-02' WHERE reg_number=?", (reg,))
        fast._persist(self.ns, [(item, 'completed', [], 'done')])
        self.assertEqual((self.item(reg)['status'], self.item(reg)['reservation_token']), ('outside_period', ''))
        with self.connect() as con:
            con.execute("UPDATE purchases SET published_at='2026-10-01' WHERE reg_number=?", (reg,))
        thematic.WORKER_STATE['index_at'] = 0
        selected = fast._prepare(self.ns)
        self.assertEqual([row['reg'] for row in selected], [reg])
        self.assertNotEqual(selected[0]['reservation_token'], item['reservation_token'])

    async def test_old_general_analyzer_cannot_overwrite_new_card_facts(self):
        from v341_eis_facts import persist_facts
        reg = self.purchase()
        general.initialize(self.ns)
        old = general.claim_next(self.ns)
        entered, release = threading.Event(), threading.Event()
        def analyze(purchase, docs):
            entered.set()
            if not release.wait(5):raise RuntimeError('Fixture release timeout')
            persist_facts(self.ns, {'reg': reg, 'method': 'Old method', 'fact_evidence':
                {'method': {'source': 'official_eis_card', 'value': 'Old method'}}})
            return []
        self.ns['analyze'] = analyze
        task = asyncio.create_task(general.inspect_one(self.ns, old))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            general.initialize(self.ns)
            new = general.claim_next(self.ns)
            self.assertNotEqual(old.reservation_token, new.reservation_token)
            with self.connect() as con:
                con.execute("UPDATE purchases SET method='New authoritative method' WHERE reg_number=?", (reg,))
            release.set()
            with self.assertRaisesRegex(LookupError, 'superseded'):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        with self.connect() as con:
            self.assertEqual(con.execute('SELECT method FROM purchases WHERE reg_number=?', (reg,)).fetchone()[0],
                             'New authoritative method')
        self.assertEqual(self.row(reg)['reservation_token'], new.reservation_token)

    async def test_stopped_thematic_analyzer_cannot_write_source_facts(self):
        from v341_eis_facts import persist_facts
        reg = self.purchase()
        entered, release = threading.Event(), threading.Event()
        def analyze(purchase, docs):
            entered.set()
            if not release.wait(5):raise RuntimeError('Fixture release timeout')
            persist_facts(self.ns, {'reg': reg, 'method': 'Old method', 'fact_evidence':
                {'method': {'source': 'official_eis_card', 'value': 'Old method'}}})
            return []
        self.ns['analyze'] = analyze
        task = asyncio.create_task(fast.faster_tick(self.ns))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            with self.connect() as con:
                con.execute("UPDATE thematic_batches SET status='stopped'")
                con.execute("UPDATE purchases SET method='New authoritative method' WHERE reg_number=?", (reg,))
            release.set()
            self.assertEqual(await task, 0)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        with self.connect() as con:
            self.assertEqual(con.execute('SELECT method FROM purchases WHERE reg_number=?', (reg,)).fetchone()[0],
                             'New authoritative method')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM thematic_findings').fetchone()[0], 0)

    async def test_metadata_writer_guard_runs_inside_transaction(self):
        from audit_write_guard import background_write_guard
        from v314_eis_metadata import apply_metadata
        reg = self.purchase()
        general.initialize(self.ns)
        old = general.claim_next(self.ns)
        general.initialize(self.ns)
        general.claim_next(self.ns)
        observed = []
        def guard(con):
            observed.append(con.in_transaction)
            general.require_reservation(con, old)
        with background_write_guard(guard), self.assertRaisesRegex(LookupError, 'superseded'):
            await asyncio.to_thread(apply_metadata, self.ns, reg, {'title': 'Stale metadata'})
        self.assertEqual(observed, [True])
        with self.connect() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v314_eis_metadata'").fetchone())




class SnapshotWorkerDiagnostics(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_error_is_not_database_busy_and_success_clears_attributed_error(self):
        reg = self.purchase()
        general.initialize(self.ns)
        claimed = general.claim_next(self.ns)
        before = general.review_watermark(self.ns, claimed)
        general.mark(self.ns, claimed, 'completed', before)
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET state='pending' WHERE reg=?", (reg,))
        real_watermark = general.review_watermark
        errors = True
        def watermark(ns, number):
            if errors:raise SemanticSnapshotError('byte_budget')
            return real_watermark(ns, number)
        engine = AsyncMock(return_value=('completed', {
            'documents_found': 1, 'documents_read': 1, 'documents_unreadable': 0,
            'verified_original_documents': 1, 'original_expected': 1,
            'original_received': 1, 'new_findings': 0, 'new_checks': 0}))
        real_sleep = asyncio.sleep
        async def sleep(seconds):await real_sleep(0 if seconds == 14 else seconds)
        async def wait_for(predicate):
            while not predicate():await real_sleep(.005)
        with patch.object(general, 'GENERAL_WORKERS', 1), \
             patch.object(general, 'review_watermark', side_effect=watermark), \
             patch.object(general, 'inspect_one', engine), \
             patch.object(general, 'refresh_changed', return_value=0), \
             patch.object(general, 'add_new_verified', return_value=0), \
             patch.object(general.asyncio, 'sleep', side_effect=sleep):
            general.install(self.ns)
            await self.ns['app'].startups[0]()
            try:
                await asyncio.wait_for(wait_for(lambda:self.row(reg)['retry_count'] == 1), 5)
                row = self.row(reg)
                self.assertEqual(row['state'], 'pending')
                self.assertEqual(row['last_semantic_version'], before['last_semantic_version'])
                self.assertEqual(row['last_engine_signature'], before['last_engine_signature'])
                self.assertGreater(row['next_retry_at'], time.time())
                self.assertLessEqual(row['next_retry_at'], time.time() + general.BUSY_RETRY_BASE_SECONDS)
                self.assertIn('source_snapshot_byte_budget', row['error'])
                self.assertNotIn('блокировка БД', row['error'])
                engine.assert_not_awaited()
                slot = self.ns['_v332_runtime']['workers']['1']
                self.assertEqual(slot['last_error_reg'], reg)
                self.assertEqual(slot['last_error_phase'], 'source')
                self.assertTrue(slot['last_error_at'])
                self.assertIn('database_busy=False reason=source_snapshot_byte_budget', self.output.getvalue())
                errors = False
                with self.connect() as con:
                    con.execute("UPDATE v332_audit_queue SET next_retry_at=0 WHERE reg=?", (reg,))
                general.audit_signals(self.ns).notify('general')
                await asyncio.wait_for(wait_for(lambda:self.row(reg)['state'] == 'completed'), 5)
                await asyncio.wait_for(wait_for(lambda:slot.get('last_error') is None), 5)
                engine.assert_awaited_once()
                self.assertEqual(self.row(reg)['retry_count'], 0)
                self.assertIsNone(slot['last_error_reg'])
                self.assertIsNone(slot['last_error_at'])
            finally:
                self.ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(self.ns['_v332_campaign_worker'], return_exceptions=True)


if __name__ == '__main__':unittest.main()
