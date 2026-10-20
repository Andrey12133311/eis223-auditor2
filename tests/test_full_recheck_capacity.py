"""Deterministic bounded admission, cursor, cancellation and service-share proof."""
from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
import sqlite3
import sys
import threading
import time
import unittest
from unittest.mock import patch

from test_general_audit_recovery import App, DatabaseFixture
import full_recheck_request as ledger
import full_recheck_runtime as runtime
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
from v295_eis_originals import _try_execution_lock, _release_execution_lock


class RequestedCapacity(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def cohort(self, count):
        regs = [self.purchase(number) for number in range(1, count + 1)]
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение'")
        general.initialize(self.ns)
        self.stack.enter_context(patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.AUTHORIZED_EPOCH}))
        runtime.initialize(self.ns)
        return regs

    def member(self, reg):
        with self.connect() as c:
            return dict(c.execute("SELECT * FROM full_recheck_members WHERE reg=?", (reg,)).fetchone())

    async def stop(self, *tasks):
        for task in tasks:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)

    async def test_two_explicit_claims_leave_ordinary_and_thematic_work_available(self):
        regs = self.cohort(5)
        with self.connect() as c:
            for reg in regs[3:]:
                c.execute("UPDATE purchases SET customer='ООО Тест' WHERE reg_number=?", (reg,))
                c.execute("INSERT INTO v332_audit_queue(campaign,reg) VALUES(?,?)", (general.CAMPAIGN, reg))
        thematic.init(self.ns)
        fast.install(self.ns)
        with self.connect() as c:
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) "
                      "VALUES(?,?,'2026','active',?)", ('a' * 24, json.dumps(['dates']), json.dumps(['ООО'])))
            c.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,'2026')",
                      ('a' * 24, regs[4]))
        entered = asyncio.Queue()
        async def held(ns, reg):
            await entered.put(str(reg))
            await asyncio.Event().wait()
        with patch.object(general, 'inspect_one', side_effect=held):
            first = asyncio.create_task(runtime.run_one(self.ns, {}))
            second = asyncio.create_task(runtime.run_one(self.ns, {}))
            try:
                active = [await asyncio.wait_for(entered.get(), 5) for _ in range(2)]
                self.assertEqual(set(active), set(regs[:2]))
                self.assertFalse(await runtime.run_one(self.ns, {}))
                self.assertEqual(runtime._dispatch(self.ns)['active'], 2)
                for reg in active:
                    self.assertEqual(self.member(reg)['status'], 'running')
                    self.assertIsNone(_try_execution_lock(reg))
                ordinary = await general.claim_safely(self.ns)
                self.assertEqual(str(ordinary), regs[3])
                await runtime.attach_general(self.ns, ordinary)
                self.assertIsNotNone(ordinary.full_recheck_claim)
                # Attached receipts use an already-reserved ordinary worker.
                self.assertEqual(runtime._dispatch(self.ns)['active'], 2)
                selected = fast._prepare(self.ns)
                self.assertEqual([str(item['reg']) for item in selected], [regs[4]])
                fast._release_items(self.ns, selected)
                await runtime.release(self.ns, ordinary)
                await general.release_reservation(self.ns, ordinary)
            finally:
                await self.stop(first, second)
        self.assertEqual(runtime._dispatch(self.ns)['active'], 0)
        self.assertEqual(self.member(regs[2])['attempt_count'], 0)

    async def test_concurrent_due_retries_visit_all_members_before_cursor_wrap(self):
        regs = self.cohort(17)
        async def partial(ns, reg):
            await asyncio.sleep(0)
            return 'no_readable_documents', {'documents_found': 1, 'source_incomplete': True,
                                              'original_expected': 1, 'original_received': 1}
        stamp = time.time()
        with patch.object(general, 'inspect_one', side_effect=partial):
            for wave in range(9):
                # Every previous partial becomes due again before the next
                # pair. A shared unsynchronized page cursor can starve tails.
                with patch.object(ledger.time, 'time', return_value=stamp + 700 * wave):
                    self.assertEqual(await asyncio.gather(runtime.run_one(self.ns, {}),
                                                          runtime.run_one(self.ns, {})), [True, True])
                if wave == 7:
                    self.assertEqual([self.member(reg)['attempt_count'] for reg in regs], [1] * 16 + [0])
        self.assertEqual([self.member(reg)['attempt_count'] for reg in regs], [2] + [1] * 16)
        self.assertEqual(self.ns['_full_recheck']['cursor'], regs[0])
        self.assertEqual(runtime._dispatch(self.ns)['active'], 0)

    async def test_cancelled_late_claim_preserves_cancel_and_fence_on_unlock_failure(self):
        await self.claim_cancel_unlock_case(unlocked=False)

    async def test_cancelled_late_claim_preserves_cancel_after_unlock_error(self):
        await self.claim_cancel_unlock_case(unlocked=True)

    async def claim_cancel_unlock_case(self, *, unlocked):
        import v295_eis_originals as originals
        regs = self.cohort(2)
        committed, allow_claim = threading.Event(), threading.Event()
        cleanup_started, allow_cleanup = threading.Event(), threading.Event()
        claimed = []
        original_claim, original_release = runtime._claim, runtime._release_member
        def held_claim(ns, number):
            result = original_claim(ns, number)
            claimed.append(result)
            committed.set()
            if not allow_claim.wait(5): raise TimeoutError('claim fixture barrier')
            return result
        def held_cleanup(ns, reg):
            cleanup_started.set()
            if not allow_cleanup.wait(5): raise TimeoutError('cleanup fixture barrier')
            return original_release(ns, reg)
        def failed_unlock(handle):
            if unlocked:
                _release_execution_lock(handle)
            raise OSError('PRIVATE_UNLOCK_SENTINEL /tmp/private/source')
        task = None
        outcome = None
        cancelled = False
        with patch.object(runtime, '_claim', side_effect=held_claim), \
                patch.object(runtime, '_release_member', side_effect=held_cleanup), \
                patch.object(originals, '_release_execution_lock', side_effect=failed_unlock):
            try:
                task = asyncio.create_task(runtime.run_one(self.ns, {}))
                self.assertTrue(await asyncio.to_thread(committed.wait, 5))
                self.assertIsNotNone(claimed[0])
                before = self.member(regs[0])
                self.assertEqual((before['status'], before['attempt_count'], before['result_token']), ('running', 1, ''))
                task.cancel(); await asyncio.sleep(0); task.cancel(); await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertEqual(runtime._dispatch(self.ns)['active'], 1)
                self.assertTrue(runtime._dispatch(self.ns)['selector'].locked())
                self.assertIsNone(_try_execution_lock(regs[0]))
                allow_claim.set()
                self.assertTrue(await asyncio.to_thread(cleanup_started.wait, 5))
                task.cancel(); await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertEqual(self.member(regs[0])['lease_token'], before['lease_token'])
                self.assertIsNone(_try_execution_lock(regs[0]))
                allow_cleanup.set()
                try:
                    outcome = await asyncio.wait_for(task, 5)
                except asyncio.CancelledError:
                    cancelled = True
            finally:
                allow_claim.set(); allow_cleanup.set()
                if task is not None:
                    if not task.done(): task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        try:
            # The database cleanup committed; neither a result nor a receipt
            # was invented by an interrupted claim before analysis started.
            after = self.member(regs[0])
            self.assertEqual((after['status'], after['reason'], after['attempt_count']), ('retry', 'interrupted_attempt', 1))
            self.assertEqual((after['result_token'], after['total_new_findings'], self.findings(regs[0])), ('', 0, 0))
            with self.connect() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM full_recheck_receipts').fetchone()[0], 0)
            self.assertEqual(runtime._dispatch(self.ns)['active'], 0)
            self.assertFalse(runtime._dispatch(self.ns)['selector'].locked())
            handle = claimed[0].full_recheck_lock
            self.assertEqual(handle.closed, unlocked)
            lock = _try_execution_lock(regs[0])
            if unlocked:
                self.assertIsNotNone(lock)
                _release_execution_lock(lock)
            else:
                self.assertIsNone(lock)
            self.assertNotIn('PRIVATE_UNLOCK_SENTINEL', self.output.getvalue())
            self.assertTrue(cancelled, f'claim cancellation replaced by cleanup error; returned {outcome!r}')
            self.assertTrue(task.cancelled())
            self.assertIn('FULL_RECHECK_CLAIM cleanup_failed=True type=OSError', self.output.getvalue())
            runtime.initialize(self.ns)
            self.assertEqual(self.member(regs[0]), after)
            with patch.object(ledger.time, 'time', return_value=time.time() + 100):
                self.assertTrue(await runtime.run_one(self.ns, {}))
            if not unlocked:
                # A failure before real unlock stays fail-closed even when
                # the member becomes due. A different member can progress.
                self.assertEqual(self.member(regs[0]), after)
                self.assertEqual(self.member(regs[1])['status'], 'completed')
            else:
                self.assertEqual(self.member(regs[0])['status'], 'completed')
        finally:
            # Only the synthetic test owner closes this retained handle, after
            # both the real claim and cleanup threads and their task settled.
            for reg in claimed:
                handle = getattr(reg, 'full_recheck_lock', None)
                if handle is not None and not handle.closed:
                    _release_execution_lock(handle)
        with patch.object(ledger.time, 'time', return_value=time.time() + 100):
            self.assertTrue(await runtime.run_one(self.ns, {}))
        self.assertEqual([self.member(reg)['status'] for reg in regs], ['completed', 'completed'])
        self.assertEqual([self.member(reg)['attempt_count'] for reg in regs], [2, 1])
        self.assertEqual([self.member(reg)['total_new_findings'] for reg in regs], [1, 1])
        self.assertEqual([self.findings(reg) for reg in regs], [1, 1])

    async def test_cancelled_committed_claim_keeps_selector_and_permit_until_cleanup(self):
        regs = self.cohort(3)
        committed, allow_return = threading.Event(), threading.Event()
        inspecting = asyncio.Event()
        original = runtime._claim
        def delayed_claim(ns, number):
            reg = original(ns, number)
            if number == regs[0]:
                committed.set()
                if not allow_return.wait(5):
                    raise TimeoutError('fixture claim return')
            return reg
        async def held(ns, reg):
            inspecting.set()
            await asyncio.Event().wait()
        with patch.object(runtime, '_claim', side_effect=delayed_claim), patch.object(general, 'inspect_one', side_effect=held):
            first = asyncio.create_task(runtime.run_one(self.ns, {}))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(committed.wait, 5))
                second = asyncio.create_task(runtime.run_one(self.ns, {}))
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertEqual(runtime._dispatch(self.ns)['active'], 2)
                self.assertTrue(runtime._dispatch(self.ns)['selector'].locked())
                self.assertFalse(await runtime.run_one(self.ns, {}))
                self.assertIsNone(_try_execution_lock(regs[0]))
                self.assertEqual(self.member(regs[1])['attempt_count'], 0)
                allow_return.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(first, 5)
                await asyncio.wait_for(inspecting.wait(), 5)
                self.assertEqual(self.member(regs[0])['reason'], 'interrupted_attempt')
                self.assertEqual(self.member(regs[1])['status'], 'running')
                lock = _try_execution_lock(regs[0])
                self.assertIsNotNone(lock)
                _release_execution_lock(lock)
            finally:
                allow_return.set()
                await self.stop(*[task for task in (first, second) if task is not None])
        self.assertEqual(runtime._dispatch(self.ns)['active'], 0)
        self.assertFalse(runtime._dispatch(self.ns)['selector'].locked())
        self.assertTrue(await runtime.run_one(self.ns, {}))
        self.assertEqual(self.member(regs[2])['status'], 'completed')

    async def test_cancelled_cursor_reader_settles_before_selector_waiter_enters(self):
        regs = self.cohort(3)
        # Force the candidate reader to mutate the wrap cursor in its thread.
        self.ns['_full_recheck']['cursor'] = regs[-1]
        wrapped, allow_return = threading.Event(), threading.Event()
        original = runtime._candidates
        reads = []
        def delayed_candidates(ns):
            values = original(ns)
            reads.append(list(values))
            if len(reads) == 1:
                wrapped.set()
                if not allow_return.wait(5):
                    raise TimeoutError('fixture candidate read')
            return values
        with patch.object(runtime, '_candidates', side_effect=delayed_candidates):
            first = asyncio.create_task(runtime.run_one(self.ns, {}))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(wrapped.wait, 5))
                self.assertEqual(self.ns['_full_recheck']['cursor'], '')
                second = asyncio.create_task(runtime.run_one(self.ns, {}))
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertEqual(runtime._dispatch(self.ns)['active'], 2)
                self.assertEqual(len(reads), 1)
                allow_return.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(first, 5)
                self.assertTrue(await asyncio.wait_for(second, 5))
                self.assertEqual(len(reads), 2)
                self.assertEqual(self.member(regs[0])['attempt_count'], 1)
                self.assertEqual(self.member(regs[1])['attempt_count'], 0)
            finally:
                allow_return.set()
                await self.stop(*[task for task in (first, second) if task is not None])
        self.assertEqual(runtime._dispatch(self.ns)['active'], 0)
        self.assertFalse(runtime._dispatch(self.ns)['selector'].locked())

    async def test_cancelled_selector_waiter_returns_its_permit_without_a_claim(self):
        regs = self.cohort(2)
        dispatch = runtime._dispatch(self.ns)
        await dispatch['selector'].acquire()
        waiting = asyncio.create_task(runtime.run_one(self.ns, {}))
        try:
            await asyncio.sleep(0)
            self.assertEqual(dispatch['active'], 1)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            self.assertEqual(dispatch['active'], 0)
            self.assertTrue(dispatch['selector'].locked())
            self.assertEqual([self.member(reg)['attempt_count'] for reg in regs], [0, 0])
        finally:
            dispatch['selector'].release()
            await self.stop(waiting)
        self.assertTrue(await runtime.run_one(self.ns, {}))
        self.assertEqual(dispatch['active'], 0)

    async def test_restart_retains_frozen_membership_and_exact_active_lease(self):
        regs = self.cohort(3)
        with self.connect() as c:
            before = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
        entered = asyncio.Event()
        async def held(ns, reg):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(general, 'inspect_one', side_effect=held):
            old = asyncio.create_task(runtime.run_one(self.ns, {}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                active = self.member(regs[0])
                restarted = dict(self.ns)
                restarted.pop('_full_recheck_dispatch')
                restarted['_full_recheck'] = {}
                runtime.initialize(restarted)
                self.assertEqual(self.member(regs[0]), active)
                self.assertIsNone(_try_execution_lock(regs[0]))
                # New process selector may read from the start; leases and
                # physical locks, not the in-process cap, fence old ownership.
                self.assertEqual(runtime._candidates(restarted), regs[1:])
                self.assertEqual(runtime._dispatch(restarted)['active'], 0)
                self.assertEqual(runtime._dispatch(self.ns)['active'], 1)
            finally:
                await self.stop(old)
        self.assertTrue(await runtime.run_one(restarted, {}))
        with self.connect() as c:
            after = ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
        self.assertEqual((after['actual_count'], after['membership_digest'], after['db_snapshot_at']),
                         (before['actual_count'], before['membership_digest'], before['db_snapshot_at']))
        self.assertEqual(self.member(regs[0])['attempt_count'], 1)
        self.assertEqual(self.member(regs[1])['attempt_count'], 1)


    async def test_cancelled_real_analyzer_holds_cap_locks_and_fences_restart(self):
        regs=self.cohort(2)
        original=self.ns['analyze']; lock=threading.Lock(); entered=[]
        both=threading.Event(); allow=threading.Event(); claims=[]
        old_claim=runtime._claim
        def claim(*args):
            value=old_claim(*args)
            if value is not None:claims.append(value)
            return value
        def analyze(*args):
            with lock:
                entered.append(1)
                if len(entered)==2:both.set()
            if not allow.wait(5):raise AssertionError('controlled analyzers were not released')
            return original(*args)
        self.ns['analyze']=analyze
        with patch.object(runtime,'_claim',side_effect=claim):
            tasks=[asyncio.create_task(runtime.run_one(self.ns,{})) for _ in range(2)]
            await asyncio.wait_for(asyncio.to_thread(both.wait),3)
            for task in tasks:task.cancel()
            await asyncio.sleep(.02)
            for task in tasks:task.cancel()
            try:
                self.assertTrue(all(not task.done() for task in tasks))
                self.assertEqual(self.ns['_full_recheck_dispatch']['active'],2)
                self.assertFalse(await runtime.run_one(self.ns,{}))
                for reg in regs:self.assertIsNone(_try_execution_lock(reg))
            finally:allow.set()
            for task in tasks:
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,5)
        self.ns['analyze']=original
        self.assertEqual(self.ns['_full_recheck_dispatch']['active'],0)
        for reg in regs:
            physical=_try_execution_lock(reg); self.assertIsNotNone(physical); _release_execution_lock(physical)
            self.assertEqual(self.findings(reg),0)
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM full_recheck_members WHERE status='retry' AND reason='interrupted_attempt'").fetchone()[0],2)
            before=ledger.inventory(c,runtime.AUTHORIZED_EPOCH)
            c.execute('UPDATE full_recheck_members SET next_retry_at=0')
        runtime.initialize(self.ns)
        self.assertEqual(await asyncio.gather(*(runtime.run_one(self.ns,{}) for _ in range(2))),[True,True])
        with self.connect() as c:
            after=ledger.inventory(c,runtime.AUTHORIZED_EPOCH)
        self.assertEqual(after['membership_digest'],before['membership_digest'])
        self.assertEqual(after['current_completed'],2)
        for reg in regs:self.assertEqual(self.findings(reg),1)
        for old in claims:
            with self.assertRaises(ledger.StaleClaim):general.persist(self.ns,old,[],[],'stale-token')

class WorkerShare(unittest.IsolatedAsyncioTestCase):
    async def service_quanta(self, *, explicit=True, waves=4):
        """Drive the real installed worker loop with equal-cost service quanta.

        A barrier releases one wave of all four existing workers. No sleep
        duration or external throughput assumption affects the count proof.
        """
        ns = {'app': App()}
        admissions = asyncio.Queue()
        pending = []
        real_sleep = asyncio.sleep
        async def service(kind):
            future = asyncio.get_running_loop().create_future()
            pending.append(future)
            await admissions.put((kind, future))
            await future
        async def request(ns, slot):
            if not explicit:
                return False
            await service('explicit')
            return True
        async def ordinary(ns):
            await service('ordinary')
            return None
        async def skip_delay(*args, **kwargs):
            await real_sleep(0)
        class Signals:
            def version(self, topic): return 0
            def notify(self, *topics): pass
            async def wait(self, *args): await real_sleep(0)
        async def idle(*args, **kwargs): await asyncio.Event().wait()
        with ExitStack() as stack:
            stack.enter_context(patch.object(general.asyncio, 'sleep', side_effect=skip_delay))
            stack.enter_context(patch.object(general, 'initialize', return_value={}))
            stack.enter_context(patch.object(runtime, 'initialize', return_value=None))
            stack.enter_context(patch.object(runtime, 'heartbeat', return_value=None))
            stack.enter_context(patch.object(general, 'progress', return_value={}))
            stack.enter_context(patch.object(general, 'audit_signals', return_value=Signals()))
            stack.enter_context(patch.object(general, 'file_stage', side_effect=idle))
            stack.enter_context(patch.object(runtime, 'run_one', side_effect=request))
            stack.enter_context(patch.object(general, 'claim_safely', side_effect=ordinary))
            general.install(ns)
            await ns['app'].startups[0]()
            result = Counter()
            try:
                for wave in range(waves):
                    batch = [await asyncio.wait_for(admissions.get(), 5) for _ in range(4)]
                    result.update(kind for kind, _ in batch)
                    self.assertEqual(len(ns['_v332_runtime']['workers']), 4)
                    self.assertLessEqual(sum(kind == 'explicit' for kind, _ in batch), 2)
                    self.assertGreaterEqual(sum(kind == 'ordinary' for kind, _ in batch), 2)
                    if wave < waves - 1:
                        for _, future in batch:
                            future.set_result(None)
            finally:
                ns['_v332_campaign_worker'].cancel()
                await asyncio.wait_for(asyncio.gather(ns['_v332_campaign_worker'], return_exceptions=True), 5)
            return result

    async def test_two_of_four_slots_serve_request_without_starving_ordinary(self):
        self.assertEqual(await self.service_quanta(), {'explicit': 8, 'ordinary': 8})

    async def test_empty_explicit_request_returns_all_four_slots_to_ordinary(self):
        self.assertEqual(await self.service_quanta(explicit=False), {'ordinary': 16})


class Monitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.phase = None
        self.metrics = {}
        self.pending = self.holders = 0
    def begin(self, name):
        with self.lock:
            assert self.pending == self.holders == 0, (self.pending, self.holders)
            self.phase = name
            self.metrics[name] = Counter()
    def enter(self):
        with self.lock:
            phase = self.phase
            if phase:
                m = self.metrics[phase]
                m['attempts'] += 1
                self.pending += 1
                m['peak_waiting_or_entering_writers'] = max(m['peak_waiting_or_entering_writers'], self.pending)
            return phase
    def leave(self, phase, success, busy=False):
        with self.lock:
            if phase:
                self.pending -= 1
                m = self.metrics[phase]
                m['busy_errors'] += int(busy)
                if success:
                    self.holders += 1
                    m['successful_transactions'] += 1
                    m['peak_actual_writers'] = max(m['peak_actual_writers'], self.holders)
    def end(self):
        with self.lock:
            self.phase = None
    def snapshot(self):
        with self.lock:
            return {name: dict(values) for name, values in self.metrics.items()}


class FullRecheckContention(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.monitor = Monitor()
        super().setUp()
        state, tick = dict(thematic.WORKER_STATE), thematic.tick
        def restore():
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(state)
            thematic.tick = tick
        self.addCleanup(restore)

    def connect(self):
        monitor = self.monitor
        class Connection(sqlite3.Connection):
            tracked = False
            def execute(self, sql, parameters=()):
                statement = sql.lstrip().upper()
                write_start = not self.in_transaction and (
                    statement.startswith('BEGIN IMMEDIATE') or
                    statement.startswith(('INSERT ', 'UPDATE ', 'DELETE ')))
                phase = monitor.enter() if write_start else None
                try:
                    result = super().execute(sql, parameters)
                except sqlite3.OperationalError as exc:
                    if phase:
                        monitor.leave(phase, False, 'locked' in str(exc).lower())
                    raise
                if phase:
                    monitor.leave(phase, True)
                    self.tracked = True
                return result
            def settled(self, operation):
                with monitor.lock:
                    try:
                        return operation()
                    finally:
                        if self.tracked:
                            monitor.holders -= 1
                            self.tracked = False
            def commit(self): return self.settled(super().commit)
            def rollback(self): return self.settled(super().rollback)
            def close(self): return self.settled(super().close)
        con = sqlite3.connect(self.path, check_same_thread=False, factory=Connection)
        con.row_factory = sqlite3.Row
        return con
    async def until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('condition timeout; logs=' + self.output.getvalue()[-5000:])
            await asyncio.sleep(.005)
    def block(self):
        c = sqlite3.connect(self.path, check_same_thread=False)
        c.execute('BEGIN IMMEDIATE')
        return c
    def inventory(self):
        with self.connect() as c:
            return ledger.inventory(c, runtime.AUTHORIZED_EPOCH)
    def members(self):
        with self.connect() as c:
            return {r['reg']: dict(r) for r in c.execute('SELECT * FROM full_recheck_members')}

    async def test_contended_admission_then_real_renew_persist_release(self):
        await self.exercise_contention(admission_busy=True, executor_workers=6)

    async def test_saturated_explicit_slots_during_real_renew_persist_release(self):
        await self.exercise_contention(admission_busy=False, executor_workers=5)

    async def test_delayed_renewal_entry_preserves_all_four_real_claims(self):
        await self.exercise_contention(admission_busy=False, executor_workers=5, delayed_renewal=True)

    async def exercise_contention(self, *, admission_busy, executor_workers, delayed_renewal=False):
        # Test-local pool sizes reproduce small CI hosts; production allocation
        # is untouched. IsolatedAsyncioTestCase shuts this executor down.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=executor_workers))
        regs = [self.purchase(i) for i in range(1, 9)]
        with self.connect() as c:
            for reg in regs[:4]:
                c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        base = general.initialize(self.ns)
        import os
        self.stack.enter_context(patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.AUTHORIZED_EPOCH}))
        runtime.initialize(self.ns)
        before = self.inventory()
        # Independent thematic work is deliberately outside the frozen request.
        theme = self.purchase(9)
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='АО Отдельная Компания' WHERE reg_number=?", (theme,))
        thematic.init(self.ns)
        fast.install(self.ns)
        batch = 'd' * 24
        with self.connect() as c:
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) "
                      "VALUES(?,?,'2026','active',?)", (batch, json.dumps(['dates']), json.dumps(['АО'])))
            c.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,'2026')", (batch, theme))
        selected = fast._prepare(self.ns)
        self.assertEqual([item['reg'] for item in selected], [theme])

        observed = threading.Lock()
        claims, analyzed = {}, Counter()
        ready_for_analysis = set()
        first_analysis = asyncio.Event()
        allow_analysis = asyncio.Event()
        cleanup_arrived = set()
        allow_cleanup = asyncio.Event()
        original_analyze = self.ns['analyze']
        def analyze(p, docs):
            with observed:
                analyzed[p['reg']] += 1
            return original_analyze(p, docs)
        self.ns['analyze'] = analyze
        real_to_thread = asyncio.to_thread
        async def await_analysis(reg):
            ready_for_analysis.add(str(reg))
            if len(ready_for_analysis) == 5:
                first_analysis.set()
            # Hold actual claim ownership without occupying executor threads.
            # Five synchronous analyzer waits previously left only one free
            # thread on a six-thread CI pool, deadlocking the fixture itself.
            await allow_analysis.wait()
        async def park_analyzer(function, *args, **kwargs):
            # Both real analysis paths reach this after source preparation and
            # reservation checks. Do not park earlier preprocessing writes.
            if function is analyze:
                await await_analysis(args[0]['reg'])
            return await real_to_thread(function, *args, **kwargs)

        renewal_calls = Counter()
        renewed_busy = set()
        first_three_busy, all_four_busy = asyncio.Event(), asyncio.Event()
        delayed_entry, return_busy = asyncio.Event(), asyncio.Event()
        delayed_reg = None
        real_thread_call = general.thread_call
        async def controlled_thread_call(function, *args):
            if function is not runtime._renew:
                return await real_thread_call(function, *args)
            reg = str(args[1])
            first = renewal_calls[reg] == 0
            if first and reg == delayed_reg:
                await delayed_entry.wait()
            renewal_calls[reg] += 1
            try:
                return await real_thread_call(function, *args)
            except sqlite3.OperationalError as exc:
                if not first or not general.transient_sqlite(exc):
                    raise
                # This event proves this exact claim reached SQLite and got a
                # real BUSY response. Only the delayed-entry coverage case
                # parks the response; the coupled-pressure case leaves the
                # production retry/backoff entirely ungated while DB is busy.
                renewed_busy.add(reg)
                if len(renewed_busy) == 3:
                    first_three_busy.set()
                if len(renewed_busy) == 4:
                    all_four_busy.set()
                if delayed_renewal:
                    await return_busy.wait()
                raise
        real_claim, real_attach, real_release = runtime._claim, runtime.attach_general, runtime.release
        def claim(*args):
            result = real_claim(*args)
            if result is not None:
                with observed: claims[str(result)] = result
            return result
        async def attach(ns, reg):
            await real_attach(ns, reg)
            if getattr(reg, 'full_recheck_claim', None):
                with observed: claims[str(reg)] = reg
        async def release(ns, reg):
            if getattr(reg, 'full_recheck_claim', None) and not allow_cleanup.is_set():
                cleanup_arrived.add(str(reg))
                await allow_cleanup.wait()
            await real_release(ns, reg)
        real_sleep = asyncio.sleep
        async def start_now(seconds, *args, **kwargs):
            return await real_sleep(0 if seconds == 14 else seconds, *args, **kwargs)
        for name, value in (('initialize', lambda ns: base), ('refresh_changed', lambda ns: 0),
                            ('add_new_verified', lambda *args: 0)):
            self.stack.enter_context(patch.object(general, name, side_effect=value))
        self.stack.enter_context(patch.object(runtime, 'initialize', return_value=None))
        self.stack.enter_context(patch.object(runtime, '_claim', side_effect=claim))
        self.stack.enter_context(patch.object(runtime, 'attach_general', side_effect=attach))
        self.stack.enter_context(patch.object(runtime, 'release', side_effect=release))
        self.stack.enter_context(patch.object(general.asyncio, 'to_thread', side_effect=park_analyzer))
        self.stack.enter_context(patch.object(general, 'thread_call', side_effect=controlled_thread_call))
        self.stack.enter_context(patch.object(general.asyncio, 'sleep', side_effect=start_now))

        blocker = self.block() if admission_busy else None
        self.monitor.begin('admission_3s')
        general.install(self.ns)
        await self.ns['app'].startups[0]()
        campaign = self.ns['_v332_campaign_worker']
        async def theme_work():
            result = await fast._one(self.ns, selected[0], asyncio.Semaphore(1))
            await fast._retry_db(fast._persist, self.ns, [result])
            await fast._release_eventually(self.ns, selected)
            return result
        themed = asyncio.create_task(theme_work())
        renewals = []
        try:
            # Barrier identifies real blocked writers, then hold for exactly
            # three seconds: longer than the unchanged request busy timeout.
            if admission_busy:
                await self.until(lambda: self.monitor.pending >= 4)
                await real_sleep(3)
                during_admission = self.monitor.snapshot()['admission_3s']
                self.assertLessEqual(during_admission.get('busy_errors', 0), 3)
                self.assertEqual(self.inventory()['unique_attempted'], 0)
                blocker.rollback(); blocker.close(); blocker = None
            else:
                during_admission = {'unblocked_initial_wave': True}
            await asyncio.wait_for(first_analysis.wait(), 10)
            await self.until(lambda: len(claims) == 4)
            self.monitor.end()
            await self.until(lambda: self.monitor.pending == self.monitor.holders == 0)
            active_regs = set(claims)
            self.assertEqual(len(active_regs), 4)
            explicit_occupancy = sum(bool(getattr(claims[reg], 'full_recheck_only', False)) for reg in active_regs)
            if not admission_busy:
                self.assertEqual(explicit_occupancy, getattr(runtime, 'EXPLICIT_WORKERS', 1))
            self.assertEqual(len(self.ns['_v332_runtime']['workers']), 4)
            active_before = self.members()
            for reg in active_regs:
                self.assertEqual(active_before[reg]['status'], 'running')
                self.assertIsNone(_try_execution_lock(reg))

            # Exercise one real renewal tick for each of the four live claims.
            # This calls unchanged renewal+db_stage code, with no cadence,
            # lease, busy timeout or retry-delay modifications.
            blocker = self.block()
            self.monitor.begin('renewal_busy_barrier')
            if delayed_renewal:
                delayed_reg = max(active_regs)
            renewals = [asyncio.create_task(general.db_stage(
                self.ns, 'controlled_real_renew', runtime._renew, self.ns, claims[reg]))
                for reg in active_regs]
            if delayed_renewal:
                # Deliberately admit the fourth thread only after the first
                # three have already returned BUSY. Simultaneous pending==4
                # is impossible by construction, despite complete coverage.
                await asyncio.wait_for(first_three_busy.wait(), 15)
                self.assertEqual(renewed_busy, active_regs - {delayed_reg})
                self.assertEqual(renewal_calls[delayed_reg], 0)
                self.assertEqual(self.monitor.pending, 0)
                delayed_entry.set()
            else:
                # The async analyzer barrier leaves room for all four DB
                # threads even in the explicit six-thread test pool. Keep
                # this independent pressure measurement fully ungated.
                await self.until(lambda: self.monitor.pending == 4)
                await real_sleep(3)
            await asyncio.wait_for(all_four_busy.wait(), 15)
            self.assertEqual(renewed_busy, active_regs)
            self.assertEqual(dict(renewal_calls), dict.fromkeys(active_regs, 1))
            renewal_blocked = self.monitor.snapshot()['renewal_busy_barrier']
            self.assertEqual(renewal_blocked.get('attempts'), 4)
            self.assertEqual(renewal_blocked.get('busy_errors'), 4)
            self.assertLessEqual(renewal_blocked['peak_waiting_or_entering_writers'],
                                 3 if delayed_renewal else 4)
            during_renew = self.members()
            for reg in active_regs:
                self.assertEqual(during_renew[reg]['lease_token'], active_before[reg]['lease_token'])
                self.assertEqual(during_renew[reg]['attempt_count'], 1)
                self.assertIsNone(_try_execution_lock(reg))
            blocker.rollback(); blocker.close(); blocker = None
            return_busy.set()
            await asyncio.wait_for(asyncio.gather(*renewals), 6)
            self.assertEqual(dict(renewal_calls), dict.fromkeys(active_regs, 2))
            self.monitor.end()
            after_renew = self.members()
            for reg in active_regs:
                self.assertGreater(after_renew[reg]['lease_until'], active_before[reg]['lease_until'])
                self.assertEqual(after_renew[reg]['lease_token'], active_before[reg]['lease_token'])

            blocker = self.block()
            self.monitor.begin('persist_3s')
            allow_analysis.set()
            await self.until(lambda: self.monitor.pending >= 4)
            await real_sleep(3)
            persistence_blocked = self.monitor.snapshot()['persist_3s']
            self.assertLessEqual(persistence_blocked.get('busy_errors', 0), 2)
            self.assertEqual(self.inventory()['current_completed'], 0)
            for reg in active_regs:
                self.assertIsNone(_try_execution_lock(reg))
            blocker.rollback(); blocker.close(); blocker = None
            await self.until(lambda: len(cleanup_arrived) == 4)
            await asyncio.wait_for(themed, 6)
            self.monitor.end()
            await self.until(lambda: self.monitor.pending == self.monitor.holders == 0)

            blocker = self.block()
            self.monitor.begin('release_250ms')
            allow_cleanup.set()
            await self.until(lambda: self.monitor.pending == 4)
            await real_sleep(.25)
            for reg in active_regs:
                self.assertIsNone(_try_execution_lock(reg))
            self.assertEqual(self.monitor.snapshot()['release_250ms'].get('busy_errors', 0), 0)
            blocker.rollback(); blocker.close(); blocker = None
            await self.until(lambda: self.inventory()['current_completed'] == 8)
            # A committed receipt precedes the last lease/lock cleanup. Drain
            # the actual workers before teardown rather than cancel during
            # their successful-finally settlement (a pre-existing lifecycle
            # edge, separate from this occupied-writer proof).
            await self.until(lambda: all(
                slot['phase'] == 'waiting_for_documents' and slot['reg'] is None
                for slot in self.ns['_v332_runtime']['workers'].values())
                and (not hasattr(runtime, '_dispatch') or runtime._dispatch(self.ns)['active'] == 0)
                and self.monitor.pending == self.monitor.holders == 0)
            self.monitor.end()
            final = self.inventory()
            self.assertEqual((final['actual_count'], final['membership_digest'], final['db_snapshot_at']),
                             (before['actual_count'], before['membership_digest'], before['db_snapshot_at']))
            self.assertEqual((final['unique_attempted'], final['total_attempts'], final['committed_new_findings']), (8, 8, 8))
            self.assertEqual([self.findings(reg) for reg in regs], [1] * 8)
            self.assertEqual(analyzed, Counter({reg: 1 for reg in [*regs, theme]}))
            with self.connect() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM thematic_findings WHERE batch_id=?', (batch,)).fetchone()[0], 1)
                self.assertEqual(c.execute('SELECT status FROM thematic_items WHERE batch_id=? AND reg=?', (batch, theme)).fetchone()[0], 'completed')
                self.assertEqual(c.execute('SELECT COUNT(*) FROM full_recheck_receipts').fetchone()[0], 8)
            # Completed/stale tokens cannot insert duplicate credits or facts.
            for reg in active_regs:
                with self.assertRaises(ledger.StaleClaim):
                    general.persist(self.ns, claims[reg], [], [], 'stale-contention-token')
            self.assertFalse(await runtime.run_one(self.ns, {}))
            after = self.inventory()
            self.assertEqual((after['total_attempts'], after['committed_new_findings']), (8, 8))
            logs = self.output.getvalue()
            metrics = self.monitor.snapshot()
            for phase in metrics.values():
                self.assertLessEqual(phase.get('peak_actual_writers', 0), 1)
                self.assertLessEqual(phase.get('peak_waiting_or_entering_writers', 0), 5)
            print('CONTENTION_RESULT ' + json.dumps({
                'executor_workers': executor_workers, 'delayed_renewal': delayed_renewal,
                'retry_pressure_ungated': not delayed_renewal,
                'renewed_claims': len(renewed_busy),
                'admission_busy': admission_busy, 'first_wave_explicit_occupancy': explicit_occupancy,
                'explicit_limit': getattr(runtime, 'EXPLICIT_WORKERS', 1),
                'metrics': metrics,
                'blocked_admission': during_admission,
                'blocked_renewal': renewal_blocked,
                'blocked_persistence': persistence_blocked,
                'members_completed': after['current_completed'], 'attempts': after['total_attempts'],
                'new_findings': after['committed_new_findings'],
                'ordinary_completed': sum(self.row(reg)['state'] == 'completed' for reg in regs[4:]),
                'thematic_completed': 1, 'general_workers': general.GENERAL_WORKERS,
                'tracebacks': logs.count('Traceback'),
                'sqlite_retry_lines': logs.count('V332 SQLITE_RETRY phase='),
                'request_deferred_lines': logs.count('FULL_RECHECK_TURN deferred=True'),
                'thematic_db_retry_lines': logs.count('THEMATIC_DB_RETRY'),
            }, sort_keys=True), file=sys.__stdout__, flush=True)
        finally:
            if blocker is not None:
                blocker.rollback(); blocker.close()
            allow_analysis.set(); allow_cleanup.set(); return_busy.set(); delayed_entry.set()
            for task in [*renewals, themed, campaign]:
                task.cancel()
            await asyncio.wait_for(asyncio.gather(*renewals, themed, campaign, return_exceptions=True), 15)
            self.ns['analyze'] = original_analyze
            for reg in regs:
                physical = _try_execution_lock(reg)
                self.assertIsNotNone(physical)
                _release_execution_lock(physical)


if __name__ == '__main__':
    unittest.main()
