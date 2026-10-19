"""Retry only DB stages, preserving legal evidence and recoverable queue work."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
import sqlite3
import sys
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
            return 'no_readable_documents', {}
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
                        if rows[first]['state'] == 'pending' and rows[first]['error'] and rows[second]['state'] == 'no_readable_documents':
                            return rows
                        await real_sleep(.01)
                rows = await asyncio.wait_for(settled(), 2)
                self.assertEqual(rows[first]['audit_revision'], '')
                self.assertFalse(ns['_v332_campaign_worker'].done())
                self.assertEqual(analyzed.count(first), 1)
                self.assertIsNone(general.claim_next(ns))
                ns['_v332_busy_until'][first] = 0
                self.assertEqual(general.claim_next(ns), first)
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
