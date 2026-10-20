"""No production I/O: actual SQLite contention and frozen request regressions."""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import v314_eis_metadata as metadata
import v341_eis_facts as facts
import v332_all_procurements_audit as general
import v345_thematic_accelerator as thematic
import full_recheck_runtime as runtime
import full_recheck_request as ledger
from audit_write_guard import background_write_guard
import test_full_recheck_request as request_fixtures
A, B = request_fixtures.A, request_fixtures.B

REG = '32616422163'
NAME = 'Общество с ограниченной ответственностью «Тест»'
INN = '7707083893'


class FactMetadataContention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.trace = []
        with closing(self.connect()) as c:
            c.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,customer_inn TEXT,
                org_form TEXT,title TEXT,price TEXT,price_num REAL,published_at TEXT,
                submission_end TEXT,deadline TEXT,card_fields_json TEXT);
                CREATE TABLE v311_verified_buyers(reg TEXT PRIMARY KEY,name TEXT,inn TEXT,
                evidence_page TEXT,evidence_source TEXT,captured_at TEXT);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,body TEXT);
                INSERT INTO findings VALUES(1,'preserve legal evidence');''')
            c.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (REG,NAME,INN,'ООО','Поставка оборудования','','','','','','{}'))
            c.commit()
        self.ns = {'conn': self.connect}
        self.metadata = {'customer':NAME,'customer_inn':INN,'source_url':'https://zakupki.gov.ru/223/'}
        self.purchase = {'reg':REG,'submission_end':'2026-11-01','fact_evidence': {
            'submission_end': {'source':'official_eis_card','value':'2026-11-01','source_url':'https://zakupki.gov.ru/223/'}}}

    def connect(self):
        c = sqlite3.connect(self.path,timeout=.02)
        c.row_factory=sqlite3.Row
        c.set_trace_callback(self.trace.append)
        return c

    def row(self):
        with closing(self.connect()) as c:
            return dict(c.execute('SELECT * FROM purchases').fetchone())

    def hold(self):
        c=self.connect(); c.execute('BEGIN IMMEDIATE'); return c

    def test_identical_metadata_uses_no_writer_even_during_another_write(self):
        metadata.apply_metadata(self.ns,REG,self.metadata)
        with closing(self.hold()) as holder:
            self.trace.clear()
            self.assertFalse(metadata.apply_metadata(self.ns,REG,self.metadata))
            self.assertFalse(any(sql.startswith(('BEGIN IMMEDIATE','UPDATE','INSERT','CREATE')) for sql in self.trace))
            holder.rollback()

    def test_same_fact_cache_uses_no_writer_even_during_another_write(self):
        facts.persist_facts(self.ns,self.purchase)
        with closing(self.hold()) as holder:
            self.trace.clear()
            facts.persist_facts(self.ns,self.purchase)
            self.assertFalse(any(sql.startswith(('BEGIN IMMEDIATE','UPDATE','INSERT','CREATE')) for sql in self.trace))
            holder.rollback()

    def test_new_facts_preserve_busy_failure_and_old_data(self):
        before=self.row()
        with closing(self.hold()) as holder:
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                facts.persist_facts(self.ns,self.purchase)
            holder.rollback()
        self.assertEqual(self.row(),before)
        facts.persist_facts(self.ns,self.purchase)
        self.assertEqual(self.row()['deadline'],'2026-11-01')

    def test_new_metadata_is_not_reported_saved_while_locked(self):
        with closing(self.hold()) as holder:
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                metadata.apply_metadata(self.ns,REG,self.metadata)
            holder.rollback()
        with closing(self.connect()) as c:
            self.assertEqual(c.execute('SELECT body FROM findings').fetchone()[0],'preserve legal evidence')

    def test_stale_lease_still_rejects_actual_metadata_and_facts_writes(self):
        def reject(_c): raise RuntimeError('superseded attempt')
        with background_write_guard(reject):
            for operation,args in ((metadata.apply_metadata,(self.ns,REG,self.metadata)),
                                   (facts.persist_facts,(self.ns,self.purchase))):
                with self.subTest(operation=operation.__name__),self.assertRaisesRegex(RuntimeError,'superseded'):
                    operation(*args)
        self.assertEqual(self.row()['deadline'],'')

    def test_malformed_derived_cache_does_not_lose_valid_date_evidence(self):
        with closing(self.connect()) as c:
            c.execute('UPDATE purchases SET card_fields_json=?',(json.dumps({'summary':[], 'fact_evidence':None, 'other':'keep'}),)); c.commit()
        facts.persist_facts(self.ns,self.purchase)
        card=json.loads(self.row()['card_fields_json'])
        self.assertEqual(card['summary']['submission_end'],'2026-11-01')
        self.assertEqual(card['fact_evidence'],self.purchase['fact_evidence'])
        self.assertEqual(card['other'],'keep')

    def test_facts_computation_does_not_hold_writer_and_preserves_concurrent_update(self):
        original=facts._fact_updates
        def concurrent(*args):
            with closing(self.connect()) as c:
                c.execute("UPDATE purchases SET deadline='2026-12-01',submission_end='2026-12-01'")
                c.commit()
            return original(*args)
        with patch.object(facts,'_fact_updates',side_effect=concurrent):
            facts.persist_facts(self.ns,self.purchase)
        self.assertEqual(self.row()['deadline'],'2026-12-01')

    def test_official_xml_does_not_mutate_its_own_semantic_source_fields(self):
        self.purchase['fact_evidence']['submission_end']['source']='official_eis_xml'
        facts.persist_facts(self.ns,self.purchase)
        self.assertEqual(self.row()['submission_end'],'')
        self.assertEqual(json.loads(self.row()['card_fields_json'])['fact_evidence'],self.purchase['fact_evidence'])

    def test_metadata_rebases_on_concurrent_identity_without_overwriting_new_buyer(self):
        original=metadata._metadata_plan; calls=[]
        def concurrent(*args):
            calls.append(1)
            if len(calls)==1:
                with closing(self.connect()) as c:
                    c.execute("UPDATE purchases SET customer='ООО «Другая компания»'"); c.commit()
            return original(*args)
        with patch.object(metadata,'_metadata_plan',side_effect=concurrent):
            metadata.apply_metadata(self.ns,REG,self.metadata)
        self.assertEqual(self.row()['customer'],'ООО «Другая компания»')
        self.assertEqual(len(calls),2)


class SharedWriterAdmission(unittest.IsolatedAsyncioTestCase):
    async def test_general_and_thematic_writes_do_not_overlap(self):
        guard=threading.Lock(); state={'active':0,'maximum':0,'count':0}
        def write():
            with guard:
                state['active']+=1; state['maximum']=max(state['maximum'],state['active'])
            time.sleep(.01)
            with guard:
                state['active']-=1; state['count']+=1
        await asyncio.gather(*(general.db_stage({},'full_request_ack',write) if i%2 else thematic._retry_db(write) for i in range(12)))
        self.assertEqual(state,{'active':0,'maximum':1,'count':12})

    async def test_cancelled_write_keeps_gate_until_physical_thread_finishes(self):
        entered=threading.Event(); release=threading.Event(); next_started=threading.Event()
        def first():
            entered.set()
            if not release.wait(3): raise RuntimeError('test synchronization timeout')
        a=asyncio.create_task(general.db_stage({},'full_request_ack',first))
        self.assertTrue(await asyncio.to_thread(entered.wait,2))
        a.cancel()
        b=asyncio.create_task(thematic._retry_db(next_started.set))
        try:
            await asyncio.sleep(.03)
            self.assertFalse(next_started.is_set())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError): await a
        await b
        self.assertTrue(next_started.is_set())

    async def test_read_stages_remain_available_while_writer_is_busy(self):
        entered=threading.Event(); release=threading.Event()
        def first(): entered.set(); release.wait(3)
        a=asyncio.create_task(general.db_stage({},'full_request_ack',first))
        self.assertTrue(await asyncio.to_thread(entered.wait,2))
        try:
            self.assertEqual(await asyncio.wait_for(general.db_stage({},'existing',lambda:17),1),17)
        finally:
            release.set(); await a


class NewFrozenRequest(unittest.TestCase):
    setUp=request_fixtures.FullRecheckRequestTests.setUp
    def test_4007_expected_is_not_a_fabricated_population_limit(self):
        old=ledger.create_request(self.c,runtime.AUTHORIZED_EPOCH,lambda:[A],expected_count=3058)
        many=[str(32618000000+i) for i in range(4007)]
        self.c.execute('BEGIN IMMEDIATE')
        self.c.executemany('INSERT INTO purchases VALUES(?,?,?)',[(reg,'Unknown customer','Synthetic') for reg in many])
        self.c.commit()
        new=ledger.create_request(self.c,runtime.DASHBOARD_EPOCH,lambda:many,expected_count=4007,source_seconds=60,max_seconds=10)
        self.assertEqual(new['actual_count'],4010) # 4007 source + 3 existing dashboard queue members
        self.assertEqual(new['expected_count'],4007)
        self.assertFalse(new['expected_matches_actual'])
        self.assertEqual(ledger.inventory(self.c,runtime.AUTHORIZED_EPOCH),old)
        def must_not_run(): raise AssertionError('repeated request enumerated again')
        self.assertEqual(ledger.create_request(self.c,runtime.DASHBOARD_EPOCH,must_not_run),new)

    def test_slow_source_capture_has_own_budget_without_long_writer_deadline(self):
        clock=[100.0]
        def source(): clock[0]+=30; return [A]
        with patch.object(ledger.time,'monotonic',side_effect=lambda:clock[0]):
            new=ledger.create_request(self.c,runtime.DASHBOARD_EPOCH,source,expected_count=4007,max_seconds=5,source_seconds=60)
        self.assertEqual(new['actual_count'],3)

    def test_new_authorized_epoch_initializes_with_actual_count_and_is_idempotent(self):
        ns={'conn':lambda:sqlite3.connect(self.path),'_verified_set235':lambda:[A,B]}
        with patch.dict(os.environ,{runtime.REQUEST_ENV:runtime.DASHBOARD_EPOCH}), \
                patch('full_recheck_inventory.collect',return_value={'status':'test'}), \
                patch.object(general,'engine_signature',return_value='synthetic-generation-ten'):
            runtime.initialize(ns)
            self.assertEqual(runtime.request_id(ns),runtime.DASHBOARD_EPOCH)
            new=ledger.inventory(self.c,runtime.DASHBOARD_EPOCH)
            self.assertEqual((new['actual_count'],new['expected_count'],new['target_generation']),(3,4007,10))
            runtime.initialize(ns)
            self.assertEqual(ledger.inventory(self.c,runtime.DASHBOARD_EPOCH),new)

    def test_source_budget_exhaustion_never_publishes_partial_membership(self):
        clock=[100.0]
        def source(): clock[0]+=61; return [A]
        with patch.object(ledger.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(ledger.SnapshotError):
                ledger.create_request(self.c,runtime.DASHBOARD_EPOCH,source,source_seconds=60)
        self.assertIsNone(ledger.inventory(self.c,runtime.DASHBOARD_EPOCH))

    def test_old_expired_claims_are_visible_to_new_epoch_recovery(self):
        ledger.create_request(self.c,runtime.AUTHORIZED_EPOCH,lambda:[A])
        self.c.execute('BEGIN IMMEDIATE')
        claim=ledger.claim_member(self.c,runtime.AUTHORIZED_EPOCH,A,now=1,lease_seconds=30)
        self.c.commit()
        ledger.create_request(self.c,runtime.DASHBOARD_EPOCH,lambda:[A,B],expected_count=4007)
        ns={'conn':lambda:sqlite3.connect(self.path),'_full_recheck':{'request_id':runtime.DASHBOARD_EPOCH}}
        expired=runtime._expired(ns)
        self.assertEqual(len(expired),1)
        self.assertEqual(expired[0].request_id,runtime.AUTHORIZED_EPOCH)
        self.c.execute('BEGIN IMMEDIATE')
        self.assertIsNone(ledger.claim_member(self.c,runtime.DASHBOARD_EPOCH,A))
        self.c.rollback()


if __name__=='__main__': unittest.main()
