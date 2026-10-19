"""Retry only DB stages, preserving legal evidence and recoverable queue work."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v332_all_procurements_audit as general
import v314_eis_metadata as metadata
import v341_precise_legal as legal
from audit_db_retry import run_db_sync
from test_general_thematic_parity import P, REG, docs
import test_general_company_claim as company_fixture
import test_general_audit_counters as counter_fixture


class StageRetries(unittest.IsolatedAsyncioTestCase):
    async def test_existing_and_metadata_retry_without_reextracting_or_reanalyzing(self):
        busy = sqlite3.OperationalError('database is locked')
        analyzer = Mock(return_value=[])
        checker = Mock(return_value=[])
        ns = {'analyze': analyzer, 'build_checks': checker}
        with ExitStack() as stack:
            existing = stack.enter_context(patch.object(general, 'existing', side_effect=[busy, (P, [])]))
            extraction = stack.enter_context(patch.object(general, 'stored_docs', return_value=(docs, 0)))
            stack.enter_context(patch.object(general, 'allowed_documents', return_value={}))
            stack.enter_context(patch.object(metadata, 'from_documents', return_value={'title': 'Поставка материалов'}))
            saved_metadata = stack.enter_context(patch.object(metadata, 'apply_metadata', side_effect=[busy, True]))
            persisted = stack.enter_context(patch.object(general, 'persist', return_value=(0, 0)))
            state, _ = await general.inspect_one(ns, REG)
        self.assertEqual(state, 'completed')
        self.assertEqual(existing.call_count, 2)
        self.assertEqual(saved_metadata.call_count, 2)
        self.assertEqual(extraction.call_count, 1)
        self.assertEqual(analyzer.call_count, 1)
        self.assertEqual(checker.call_count, 1)
        self.assertEqual(persisted.call_count, 1)

    async def test_busy_failure_remains_pending_and_other_ready_work_continues(self):
        fixture = company_fixture.GeneralCompanyClaim()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first = fixture.add(1, 'ООО Первый')
        second = fixture.add(2, 'ООО Второй')
        general.add_new_verified(fixture.ns)
        c = fixture.connect()
        c.execute("ALTER TABLE v332_audit_queue ADD COLUMN error TEXT")
        c.execute("ALTER TABLE v332_audit_queue ADD COLUMN audit_revision TEXT DEFAULT ''")
        for column in ('documents_found','documents_read','documents_unreadable',
                       'verified_original_documents','original_expected','original_received',
                       'new_findings','new_checks'):
            c.execute("ALTER TABLE v332_audit_queue ADD COLUMN " + column + " INTEGER DEFAULT 0")
        c.commit(); c.close()
        class App:
            def __init__(self): self.startups = []
            def get(self, *a, **kw): return lambda fn: fn
            def on_event(self, event):
                def register(fn): self.startups.append(fn); return fn
                return register
        ns = {**fixture.ns, 'app': App()}
        analyzed = []
        async def inspect(ns, reg):
            analyzed.append(reg)
            if reg == first: raise sqlite3.OperationalError('database is locked')
            return 'completed', {'documents_found':1,'documents_read':1,
                'documents_unreadable':0,'verified_original_documents':1,
                'original_expected':1,'original_received':1,'new_findings':0,'new_checks':0}
        real_sleep = asyncio.sleep
        async def sleep(seconds): await real_sleep(0 if seconds == 14 else seconds)
        with patch.object(general, 'initialize', return_value={}), \
             patch.object(general, 'inspect_one', side_effect=inspect), \
             patch.object(general, 'add_new_verified', return_value=0), \
             patch.object(general, 'refresh_changed', return_value=0), \
             patch.object(general.asyncio, 'sleep', side_effect=sleep):
            general.install(ns); await ns['app'].startups[0]()
            try:
                async def settled():
                    while True:
                        c = fixture.connect()
                        rows = {r['reg']: dict(r) for r in c.execute('SELECT * FROM v332_audit_queue')}
                        c.close()
                        if rows[first]['state'] == 'pending' and rows[first]['error'] and rows[second]['state'] == 'completed':
                            return rows
                        await real_sleep(.01)
                rows = await asyncio.wait_for(settled(), 2)
                self.assertEqual(rows[first]['audit_revision'], '')
                self.assertEqual(rows[first]['retry_count'], 1)
                self.assertGreater(rows[first]['next_retry_at'], time.time())
                self.assertLessEqual(rows[first]['next_retry_at'], time.time() + general.BUSY_RETRY_BASE_SECONDS)
                self.assertEqual(rows[second]['audit_revision'], general.RULE_REVISION)
                self.assertFalse(ns['_v332_campaign_worker'].done())
                self.assertEqual(general.runtime_snapshot(ns)['live_workers'], 4)
                self.assertEqual(analyzed.count(first), 1)
                self.assertEqual(analyzed.count(second), 1)
                self.assertIsNone(general.claim_next(ns))
                # A new process namespace still observes the durable cooldown.
                fresh_ns = {'conn':fixture.connect}
                self.assertIsNone(general.claim_next(fresh_ns))
                # Finish the old process before testing its replacement. The
                # four live workers can correctly win the same atomic claim
                # once this fixture manually expires the cooldown below.
                ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(ns['_v332_campaign_worker'], return_exceptions=True)
                self.assertIsNone(general.claim_next(fresh_ns))
                c = fixture.connect()
                c.execute("UPDATE v332_audit_queue SET next_retry_at=0 WHERE reg=?", (first,))
                c.commit(); c.close()
                self.assertEqual(general.claim_next(fresh_ns), first)
            finally:
                ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(ns['_v332_campaign_worker'], return_exceptions=True)

    async def test_exhausted_result_mark_preserves_committed_token_and_counter(self):
        fixture = counter_fixture.GeneralAuditCounters()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        reg = counter_fixture.REG
        general.return_running(fixture.ns, reg)
        class App:
            def __init__(self): self.startups = []
            def get(self, *a, **kw): return lambda fn: fn
            def on_event(self, event):
                def register(fn): self.startups.append(fn); return fn
                return register
        ns = {**fixture.ns, 'app':App()}
        tokens = []; attempted_results = []; busy = True
        original_mark = general.mark
        async def inspect(ns, reg):
            token = 'save-budget-' + str(len(tokens) + 1)
            tokens.append(token)
            new_f, new_c = await general.thread_call(
                general.persist, ns, reg, [fixture.finding()], [], token)
            return 'completed', fixture.result(token, new_f, new_c)
        def mark(ns, reg, state, detail):
            if state == 'completed' and busy:
                attempted_results.append(dict(detail))
                raise sqlite3.OperationalError('database is locked')
            return original_mark(ns, reg, state, detail)
        def row():
            with fixture.connection() as db:
                return dict(db.execute('SELECT * FROM v332_audit_queue WHERE reg=?', (reg,)).fetchone())
        real_sleep = asyncio.sleep
        async def sleep(seconds): await real_sleep(0 if seconds == 14 else seconds)
        async def wait_for_row(predicate):
            while True:
                value = row()
                if predicate(value): return value
                await real_sleep(.005)
        with patch.object(general, 'inspect_one', side_effect=inspect), \
             patch.object(general, 'mark', side_effect=mark), \
             patch.object(general, 'DB_STAGE_RETRY_SECONDS', .025), \
             patch.object(general, 'RETRY_BASE_SECONDS', .01), \
             patch.object(general, 'RETRY_MAX_SECONDS', .01), \
             patch.object(general.asyncio, 'sleep', side_effect=sleep):
            general.install(ns); await ns['app'].startups[0]()
            try:
                deferred = await asyncio.wait_for(wait_for_row(
                    lambda value:value['state']=='pending' and value['retry_count']==1), 2)
                self.assertFalse(ns['_v332_campaign_worker'].done())
                self.assertEqual(general.runtime_snapshot(ns)['live_workers'], 4)
                self.assertEqual(tokens, ['save-budget-1'])
                self.assertTrue(attempted_results)
                self.assertTrue(all(value['result_token']==tokens[0] for value in attempted_results))
                self.assertEqual(deferred['last_counted_token'], tokens[0])
                self.assertEqual(deferred['last_result_token'], tokens[0])
                self.assertEqual(deferred['audit_revision'], '')
                self.assertGreater(deferred['next_retry_at'], time.time())
                fixture.assert_counts(1)
                fresh_ns = {'conn':fixture.connect, '_verified_set235':lambda:[reg]}
                general.initialize(fresh_ns)
                self.assertIsNone(general.claim_next(fresh_ns))
                self.assertEqual(general.progress(fresh_ns)['new_evidence_based_findings'], 1)
                busy = False
                with fixture.connection() as db:
                    db.execute('UPDATE v332_audit_queue SET next_retry_at=0 WHERE reg=?', (reg,))
                general.audit_signals(ns).notify('general')
                completed = await asyncio.wait_for(wait_for_row(lambda value:value['state']=='completed'), 2)
                self.assertEqual(tokens, ['save-budget-1', 'save-budget-2'])
                self.assertEqual(completed['retry_count'], 0)
                self.assertEqual(completed['next_retry_at'], 0)
                fixture.assert_counts(1)
                # A delayed failure for an older result cannot undo completion.
                general.defer_retry(fresh_ns, reg, {'result_token':'stale-result'},
                                    'late busy report', general.BUSY_RETRY_BASE_SECONDS)
                self.assertEqual(row(), completed)
            finally:
                ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(ns['_v332_campaign_worker'], return_exceptions=True)


class SynchronousStageRetries(unittest.TestCase):
    def test_fact_persistence_retries_without_rerunning_enrichment_or_rules(self):
        base = Mock(return_value=[])
        ns = {'analyze': base, 'build_checks': lambda *args: []}
        legal.install(ns)
        with patch.object(legal, 'enrich') as enriched, \
             patch.object(legal, 'persist_facts', side_effect=[sqlite3.OperationalError('database is locked'), None]) as saved, \
             patch.object(legal, 'shared_evaluate', return_value=([], [])):
            self.assertEqual(ns['analyze']({'reg': REG}, docs), [])
        self.assertEqual(enriched.call_count, 1)
        self.assertEqual(saved.call_count, 2)
        self.assertEqual(base.call_count, 1)

    def test_other_sql_errors_are_not_retried_and_busy_budget_expires(self):
        for message in ('no such table: evidence', 'database is locked'):
            op = Mock(side_effect=sqlite3.OperationalError(message))
            with self.assertRaises(sqlite3.OperationalError):
                run_db_sync(op, retry_seconds=0)
            self.assertEqual(op.call_count, 1)


if __name__ == '__main__': unittest.main()
