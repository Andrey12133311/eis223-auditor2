"""Explicit 4007 request: freeze all actual members, not a hard numeric quota."""
from contextlib import closing
import os
import sqlite3
import time
import unittest
from unittest.mock import patch
import full_recheck_request as ledger
import full_recheck_runtime as runtime
import full_recheck_inventory as inventory
import test_full_recheck_request as fixtures


class Dashboard4007Request(unittest.TestCase):
    setUp = fixtures.FullRecheckRequestTests.setUp
    write = fixtures.FullRecheckRequestTests.write

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.05)
        c.row_factory = sqlite3.Row
        return c

    def test_actual_4014_members_not_truncated_to_observed_4007_and_old_ledger_preserved(self):
        old = ledger.create_request(self.c, runtime.AUTHORIZED_EPOCH, lambda: [fixtures.A])
        prior_queue = [tuple(r) for r in self.c.execute('SELECT * FROM v332_audit_queue ORDER BY campaign,reg')]
        regs = ['32617' + str(i).zfill(6) for i in range(4011)]
        self.c.executemany('INSERT INTO purchases VALUES(?,?,?)', ((reg, 'АО Тест', 'Поставка') for reg in regs))
        self.c.execute("INSERT INTO findings(reg,body) VALUES(?,?)", (fixtures.A, 'preserved original evidence'))
        result = ledger.create_request(self.c, runtime.DASHBOARD_EPOCH, lambda: regs, expected_count=4007, source_seconds=60)
        self.assertEqual(result['actual_count'], 4014)  # 4011 new + three ordinary dashboard members.
        self.assertEqual(result['expected_count'], 4007)
        self.assertFalse(result['expected_matches_actual'])
        self.assertEqual(ledger.inventory(self.c, runtime.AUTHORIZED_EPOCH), old)
        self.assertEqual([tuple(r) for r in self.c.execute('SELECT * FROM v332_audit_queue ORDER BY campaign,reg')], prior_queue)
        self.assertEqual(self.c.execute('SELECT body FROM findings').fetchone()[0], 'preserved original evidence')
        repeat = ledger.create_request(self.c, runtime.DASHBOARD_EPOCH,
            lambda: (_ for _ in ()).throw(AssertionError('must not enumerate twice')), expected_count=4007)
        self.assertEqual(result, repeat)

    def test_slow_bounded_getter_does_not_consume_sql_writer_budget(self):
        clock = [0.0]
        def getter():
            self.assertFalse(self.c.in_transaction)
            with closing(sqlite3.connect(self.path, timeout=.05)) as other:
                other.execute('BEGIN IMMEDIATE'); other.rollback()
            clock[0] = 25.0
            return [fixtures.A]
        with patch.object(ledger.time, 'monotonic', side_effect=lambda: clock[0]):
            report = ledger.create_request(self.c, runtime.DASHBOARD_EPOCH, getter,
                                           expected_count=4007, max_seconds=5, source_seconds=60)
        self.assertEqual(report['actual_count'], 3)

    def test_overlong_source_capture_still_fails_closed_without_partial_membership(self):
        clock = [0.0]
        def getter():
            clock[0] = 61.0
            return [fixtures.A]
        with patch.object(ledger.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(ledger.SnapshotError):
                ledger.create_request(self.c, runtime.DASHBOARD_EPOCH, getter, source_seconds=60)
        self.assertIsNone(ledger.inventory(self.c, runtime.DASHBOARD_EPOCH))
        self.assertFalse(self.c.in_transaction)

    def test_capture_budget_configuration_cannot_be_unbounded(self):
        for value in (True, 0, -1, 61, float('inf'), float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ledger.create_request(self.c, 'invalid-budget', lambda: [fixtures.A], source_seconds=value)

    def test_new_request_recovers_expired_prior_epoch_but_never_steals_live_claim(self):
        ledger.create_request(self.c, runtime.AUTHORIZED_EPOCH, lambda: [fixtures.A])
        ledger.create_request(self.c, runtime.DASHBOARD_EPOCH, lambda: [fixtures.A], expected_count=4007)
        stamp = time.time()
        with self.write():
            old = ledger.claim_member(self.c, runtime.AUTHORIZED_EPOCH, fixtures.A, now=stamp-20, lease_seconds=1)
            live = ledger.claim_member(self.c, runtime.AUTHORIZED_EPOCH, fixtures.B, now=stamp, lease_seconds=300)
        ns = {'conn': self.connect, '_full_recheck': {'request_id': runtime.DASHBOARD_EPOCH}}
        expired = runtime._expired(ns)
        self.assertEqual([tuple((str(item), item.request_id, item.lease_token)) for item in expired],
                         [(fixtures.A, runtime.AUTHORIZED_EPOCH, old.lease_token)])
        with self.write():
            self.assertIsNone(ledger.claim_member(self.c, runtime.DASHBOARD_EPOCH, fixtures.A))
        runtime._recover(ns, expired[0])  # Production caller also proves the existing physical lock.
        with self.write():
            fresh = ledger.claim_member(self.c, runtime.DASHBOARD_EPOCH, fixtures.A)
            self.assertIsNotNone(fresh)
            self.assertIsNone(ledger.claim_member(self.c, runtime.DASHBOARD_EPOCH, fixtures.B))
        self.assertEqual(ledger.require_claim(self.c, live)['status'], 'running')

    def test_new_request_is_enabled_only_by_exact_authorized_epoch_and_reports_expected(self):
        ns = {'conn': self.connect, '_verified_set235': lambda: [fixtures.A]}
        with patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.DASHBOARD_EPOCH}):
            runtime.initialize(ns)
        self.assertEqual(runtime.request_id(ns), runtime.DASHBOARD_EPOCH)
        with closing(self.connect()) as c:
            current = ledger.inventory(c, runtime.DASHBOARD_EPOCH)
        self.assertEqual(current['expected_count'], 4007)
        self.assertEqual(current['actual_count'], 3)
        with patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.DASHBOARD_EPOCH}):
            report = inventory.collect(ns)
        self.assertEqual(report['expected_requested_count'], 4007)
        self.assertEqual(report['counts']['dashboard_expected_difference'], 3-4007)
        with patch.dict(os.environ, {runtime.REQUEST_ENV: '2026-unknown-request'}):
            with self.assertRaises(ValueError):
                runtime.initialize({'conn': self.connect, '_verified_set235': lambda: [fixtures.A]})


if __name__ == '__main__':
    unittest.main()
