"""A short database lock must not discard completed analyses or stop workers."""
import asyncio
from collections import Counter
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_db_retry import run_db
import v345_thematic_accelerator as fast
import v342_thematic_queue as thematic
import v332_all_procurements_audit as general


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_retries_same_result_without_reanalysis(self):
        items=[{'reg':reg,'batch':'b','ready':1,'doc_id':1,'categories':['dates']} for reg in ('one','two')]
        analyzed=Counter();saved=[];attempts=0
        async def review(ns,reg,categories):
            analyzed[reg]+=1
            return 'completed',[]
        def persist(ns,results):
            nonlocal attempts
            attempts+=1
            if attempts==1:raise sqlite3.OperationalError('database is locked')
            saved.extend(r[0]['reg'] for r in results)
        with patch.object(thematic,'storage_health',return_value={'blocked':False}), \
             patch.object(thematic,'independent_review',side_effect=review), \
             patch.object(fast,'_start_item',return_value=True), \
             patch.object(fast,'_watermark',return_value=('fixture','engine')), \
             patch.object(fast,'_release_items',return_value=None), \
             patch.object(fast,'_prepare',return_value=items), \
             patch.object(fast,'_persist',side_effect=persist), \
             patch.object(fast,'_request_missing_originals',return_value=0):
            self.assertEqual(await fast.faster_tick({}),2)
        self.assertEqual(analyzed,{'one':1,'two':1})
        self.assertCountEqual(saved,['one','two'])
        self.assertEqual(attempts,3)

    async def test_non_busy_sql_error_is_not_retried(self):
        calls=0
        def fail():
            nonlocal calls
            calls+=1
            raise sqlite3.OperationalError('no such table: evidence')
        with self.assertRaises(sqlite3.OperationalError):await run_db(fail)
        self.assertEqual(calls,1)

    async def test_exhausted_busy_retry_is_visible_and_bounded(self):
        def fail():raise sqlite3.OperationalError('database is locked')
        with self.assertRaises(sqlite3.OperationalError):
            await run_db(fail,retry_seconds=0,stage='test')

    async def test_busy_claim_cannot_cancel_all_general_workers(self):
        import threading
        class App:
            def __init__(self):self.startups=[]
            def get(self,*a,**kw):return lambda fn:fn
            def on_event(self,event):
                def register(fn):self.startups.append(fn);return fn
                return register
        app=App();ns={'app':app};reached=asyncio.Event();calls=0
        guard=threading.Lock()
        real_sleep=asyncio.sleep
        async def sleep(seconds):await real_sleep(0 if seconds==14 else seconds)
        def flaky_claim(ns):
            nonlocal calls
            with guard:
                calls+=1
                attempt=calls
            if attempt==1:raise sqlite3.OperationalError('database is locked')
            return 'reg' if attempt==2 else None
        async def inspect(ns,reg):
            reached.set()
            return 'completed',{'documents_found':1,'documents_read':1,
                'documents_unreadable':0,'verified_original_documents':1,
                'original_received':1,'original_expected':1,
                'new_findings':0,'new_checks':0,'error':None}
        with patch.object(general,'initialize',return_value={}), \
             patch.object(general,'claim_next',side_effect=flaky_claim), \
             patch.object(general,'inspect_one',side_effect=inspect), \
             patch.object(general,'review_watermark',return_value={'last_semantic_version':'fixture','last_engine_signature':'engine'}), \
             patch.object(general,'mark'), \
             patch.object(general,'return_running'), \
             patch.object(general,'add_new_verified',return_value=0), \
             patch.object(general,'refresh_changed',return_value=0), \
             patch.object(general,'RETRY_BASE_SECONDS',.01), \
             patch.object(general,'RETRY_MAX_SECONDS',.01), \
             patch.object(general.asyncio,'sleep',side_effect=sleep):
            general.install(ns);await app.startups[0]()
            try:
                await asyncio.wait_for(reached.wait(),1)
                self.assertFalse(ns['_v332_campaign_worker'].done())
                self.assertEqual(general.runtime_snapshot(ns)['live_workers'],4)
            finally:
                ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(ns['_v332_campaign_worker'],return_exceptions=True)

    async def test_busy_mark_preserves_result_and_other_workers(self):
        import threading
        class App:
            def __init__(self):self.startups=[]
            def get(self,*a,**kw):return lambda fn:fn
            def on_event(self,event):
                def register(fn):self.startups.append(fn);return fn
                return register
        app=App();ns={'app':app};reached=asyncio.Event();calls=0;marks=0;inspections=0
        loop=asyncio.get_running_loop();guard=threading.Lock();results=[]
        real_sleep=asyncio.sleep
        async def sleep(seconds):await real_sleep(0 if seconds==14 else seconds)
        def claim(ns):
            nonlocal calls
            with guard:
                calls+=1
                return 'reg' if calls==1 else None
        def flaky_mark(ns,reg,state,detail):
            nonlocal marks
            with guard:
                marks+=1
                attempt=marks
                results.append((reg,state,dict(detail)))
            if attempt==1:raise sqlite3.OperationalError('database is locked')
            loop.call_soon_threadsafe(reached.set)
        async def inspect(ns,reg):
            nonlocal inspections
            inspections+=1
            return 'completed',{'documents_found':1,'documents_read':1,
                'documents_unreadable':0,'verified_original_documents':1,
                'original_received':1,'original_expected':1,
                'new_findings':1,'new_checks':0,'error':None}
        with patch.object(general,'initialize',return_value={}), \
             patch.object(general,'claim_next',side_effect=claim), \
             patch.object(general,'inspect_one',side_effect=inspect), \
             patch.object(general,'review_watermark',return_value={'last_semantic_version':'fixture','last_engine_signature':'engine'}), \
             patch.object(general,'mark',side_effect=flaky_mark), \
             patch.object(general,'return_running'), \
             patch.object(general,'add_new_verified',return_value=0), \
             patch.object(general,'refresh_changed',return_value=0), \
             patch.object(general,'RETRY_BASE_SECONDS',.01), \
             patch.object(general,'RETRY_MAX_SECONDS',.01), \
             patch.object(general.asyncio,'sleep',side_effect=sleep):
            general.install(ns);await app.startups[0]()
            try:
                await asyncio.wait_for(reached.wait(),1)
                self.assertFalse(ns['_v332_campaign_worker'].done())
                self.assertEqual(general.runtime_snapshot(ns)['live_workers'],4)
                self.assertEqual(inspections,1)
                self.assertEqual(marks,2)
                self.assertEqual(results[0],results[1])
                self.assertEqual(results[1][2]['new_findings'],1)
                self.assertTrue(results[1][2]['result_token'])
            finally:
                ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(ns['_v332_campaign_worker'],return_exceptions=True)

if __name__=='__main__':unittest.main()
