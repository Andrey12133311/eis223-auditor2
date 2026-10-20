"""Read-only dashboard recovery; all source/SQLite fixtures are isolated."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import asyncio
import io
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import dashboard_read_cache as read_cache
from dashboard_read_cache import MembershipCache, MembershipUnavailable, ensure_read_indexes
import dashboard_load_ui as ui
import v257_features as features
import v270_count_display as retired
import v298_eis_only as eis_only
import test_full_recheck_cohort_contract as fixtures


class MembershipTests(unittest.TestCase):
    def test_empty_success_is_different_from_missing_source(self):
        rows, meta = MembershipCache(lambda: set()).snapshot()
        self.assertEqual(rows, frozenset())
        self.assertFalse(meta['membership_stale'])
        for bad in (None, '123', b'123', {}, 42):
            with self.subTest(bad=bad), self.assertRaises(MembershipUnavailable):
                MembershipCache(lambda: bad).snapshot()

    def test_many_timed_out_waiters_keep_one_physical_scan(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def scan():
            calls.append(1); entered.set(); release.wait(5)
            return {'32616448060'}
        cache = MembershipCache(scan)
        def read(_):
            with self.assertRaises(MembershipUnavailable):
                cache.snapshot(wait_seconds=.02)
        try:
            with ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(read, range(32)))
            self.assertTrue(entered.is_set())
            self.assertEqual(len(calls), 1)
            first = cache.refresh()
            self.assertIs(cache.refresh(), first)
            release.set(); self.assertTrue(first.result(1))
            self.assertEqual(cache.snapshot()[0], {'32616448060'})
            self.assertEqual(len(calls), 1)
        finally:
            release.set()

    def test_old_real_snapshot_is_immediate_marked_and_refresh_is_singleflight(self):
        now = [10.0]; gate = threading.Event(); calls = []
        def scan():
            calls.append(1)
            if len(calls) > 1: gate.wait(3)
            return {'old'} if len(calls) == 1 else {'new'}
        cache = MembershipCache(scan, clock=lambda: now[0])
        self.assertEqual(cache.snapshot()[0], {'old'})
        now[0] += 31
        try:
            rows, meta = cache.snapshot(wait_seconds=0)
            self.assertEqual(rows, {'old'})
            self.assertTrue(meta['membership_stale'])
            self.assertTrue(meta['membership_refreshing'])
            self.assertEqual(meta['membership_age_seconds'], 31)
            future = cache.refresh(); gate.set(); future.result(1)
            self.assertEqual(cache.snapshot()[0], {'new'})
        finally: gate.set()

    def test_failed_refresh_keeps_real_value_but_does_not_serve_it_forever(self):
        now = [10.0]; calls = []
        def scan():
            calls.append(1)
            if len(calls) > 1: raise OSError('fixture failed')
            return {'saved'}
        cache = MembershipCache(scan, clock=lambda: now[0])
        cache.snapshot(); now[0] += 31
        self.assertFalse(cache.refresh().result(1))
        self.assertEqual(cache.snapshot()[0], {'saved'})
        self.assertEqual(len(calls), 2)
        now[0] += 300
        with self.assertRaises(MembershipUnavailable): cache.snapshot()
        self.assertEqual(len(calls), 3)

    def test_invalid_or_over_budget_data_never_becomes_a_successful_zero(self):
        with patch.object(read_cache, 'MAX_MEMBERS', 2):
            with self.assertRaises(MembershipUnavailable):
                MembershipCache(lambda: iter(('one', 'two', 'three'))).snapshot()

    def test_snapshot_is_immutable_and_separate_for_each_app(self):
        source = {'one'}; a = MembershipCache(lambda: source)
        rows, meta = a.snapshot(); source.add('two')
        self.assertEqual(rows, {'one'})
        with self.assertRaises(AttributeError): rows.add('foreign')
        meta['membership_age_seconds'] = -99
        self.assertGreaterEqual(a.snapshot()[1]['membership_age_seconds'], 0)
        b = MembershipCache(lambda: {'private-other-app'})
        self.assertEqual(b.snapshot()[0], {'private-other-app'})
        self.assertEqual(a.snapshot()[0], {'one'})

    def test_failure_retry_is_bounded_and_logs_no_source_content(self):
        calls = []
        def bad(): calls.append(1); raise RuntimeError('private-token-DO-NOT-LOG')
        lines = []
        with patch.object(read_cache, '_emit', side_effect=lines.append):
            cache = MembershipCache(bad)
            for _ in range(3):
                with self.assertRaises(MembershipUnavailable): cache.snapshot()
        self.assertEqual(len(calls), 1)
        self.assertIn('DEFERRED type=RuntimeError', '\n'.join(lines))
        self.assertNotIn('private-token', '\n'.join(lines))


class DashboardIntegration(unittest.TestCase):
    setUp = fixtures.CohortContract.setUp
    connect = fixtures.CohortContract.connect
    database = fixtures.CohortContract.database
    purchase = fixtures.CohortContract.purchase

    def get(self, path='/api/v257/dashboard'):
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns['app']), base_url='http://test') as client:
                return await client.get(path)
        return asyncio.run(request())

    def test_repeated_filters_do_not_rescan_and_totals_still_cover_all_rows(self):
        for i in range(10): self.purchase(i, 'АО Тест')
        calls = []
        self.ns['_verified_set235'] = lambda: calls.append(1) or self.verified
        with self.database() as c:
            before = c.execute('SELECT COUNT(*) FROM purchases').fetchone()[0]
        first = self.get('/api/v257/dashboard?limit=3').json()
        second = self.get('/api/v257/dashboard?limit=3&offset=3').json()
        self.assertEqual(first['stats']['purchases'], 10)
        self.assertEqual(second['stats']['purchases'], 10)
        self.assertEqual(len(second['purchases']), 3)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(first['purchases'][0], second['purchases'][0])
        with self.database() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM purchases').fetchone()[0], before)

    def test_cold_failure_is_503_retry_after_not_fake_empty_success(self):
        self.purchase(1, 'АО Тест')
        def bad(): raise OSError('private fixture')
        self.ns['_verified_set235'] = bad
        r = self.get()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.headers['retry-after'], '10')
        self.assertEqual(r.headers['x-eis-dashboard-state'], 'warming')
        self.assertNotIn('stats', r.json())
        self.assertNotIn('private fixture', r.text)

    def test_invalid_inn_is_rejected_without_scanning_source(self):
        calls = []
        self.ns['_verified_set235'] = lambda: calls.append(1) or set()
        self.assertEqual(self.get('/api/v257/dashboard?inn=xxx').status_code, 400)
        self.assertEqual(calls, [])

    def test_cached_membership_does_not_cache_legal_edits_or_manual_ownership(self):
        reg = self.purchase(1, 'АО Тест')
        with self.database() as c:
            c.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)',
                (reg, 'violation', 'test', 'Ошибка', '223-ФЗ', 'Доказательство', 'Документ', 'source'))
        self.assertEqual(self.get().json()['stats']['violations'], 1)
        with self.database() as c: c.execute("UPDATE findings SET kind='check'")
        self.assertEqual(self.get().json()['stats']['violations'], 0)
        with self.database() as c:
            c.execute('INSERT INTO v319_manual_inputs VALUES(?,?,?,?)', (reg, '2026-10-20', 'number_field', ''))
        self.assertEqual(self.get().json()['stats']['purchases'], 0)

    def test_legacy_hardcoded_count_override_cannot_suppress_a_genuine_second_event(self):
        reg = '32616448060'
        with self.database() as c:
            c.execute('INSERT INTO purchases(reg_number,customer,last_seen) VALUES(?,?,?)', (reg,'АО Тест','2026-10-20'))
            for days in (15, 25):
                c.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)',
                    (reg, 'violation', '1352-28', 'Срок оплаты МСП превышен', 'ПП РФ № 1352 п. 28',
                     'Порядок оплаты: расчет производится в течение '+str(days)+' рабочих дней с даты подписания документов о приемке продукции заказчиком.',
                     'Договор.pdf','source'))
        self.verified.add(reg)
        retired.install(self.ns)
        data = self.get().json()
        self.assertEqual(data['stats']['violations'], 2)
        self.assertEqual(data['purchases'][0]['violations'], 2)
        with self.database() as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM findings').fetchone()[0], 2)

    def test_additive_indexes_preserve_evidence_and_are_idempotent(self):
        self.purchase(1, 'АО Тест')
        with self.database() as c:
            c.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)', ('1','violation','x','title','law','proof','doc','source'))
            before = c.execute('SELECT * FROM findings').fetchall()
        self.assertTrue(ensure_read_indexes(self.connect))
        self.assertTrue(ensure_read_indexes(self.connect))
        with self.database() as c:
            self.assertEqual(c.execute('SELECT * FROM findings').fetchall(), before)
            plan = str([tuple(r) for r in c.execute("EXPLAIN QUERY PLAN SELECT * FROM findings WHERE reg_number=? AND kind='violation'", ('1',))])
            self.assertIn('dashboard_findings_violations_reg', plan)

    def test_read_snapshot_is_not_blocked_by_other_wal_writer(self):
        self.purchase(1, 'АО Тест')
        with closing(self.connect()) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE purchases SET customer='АО Не завершено'")
            try:
                r = self.get()
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()['purchases'][0]['customer'], 'АО Тест')
            finally: writer.rollback()

    def test_index_connection_failure_is_observable_not_fatal(self):
        def unavailable(): raise sqlite3.OperationalError('locked')
        self.assertFalse(ensure_read_indexes(unavailable))

    def test_eis_only_keeps_safe_warm_hook_but_does_not_restart_legacy_collectors(self):
        import os
        async def forbidden_legacy_collector():
            raise AssertionError('legacy collector called')
        self.ns['app'].router.on_startup.append(forbidden_legacy_collector)
        with patch.dict(os.environ, {'EIS223_SOURCE_MODE': 'OFFICIAL_EIS_VPS_ONLY'}):
            eis_only.install(self.ns)
        names = [f.__name__ for f in self.ns['app'].router.on_startup]
        self.assertIn('warm_dashboard_reads', names)
        self.assertNotIn('on_start', names)
        self.assertNotIn('forbidden_legacy_collector', names)

    def test_load_state_installed_before_original_script_and_only_once(self):
        original = '<html><head><script>fetch("/api/v257/dashboard")</script></head><body></body></html>'
        result = features._html_patch(original)
        self.assertLess(result.index('id="eis-dashboard-load-state"'), result.index('fetch("/api/v257/dashboard")'))
        self.assertEqual(features._html_patch(result).count('id="eis-dashboard-load-state"'), 1)


if __name__ == '__main__': unittest.main()
