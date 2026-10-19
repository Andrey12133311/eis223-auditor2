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
        class App:
            def __init__(self):self.startups=[]
            def get(self,*a,**kw):return lambda fn:fn
            def on_event(self,event):
                def register(fn):self.startups.append(fn);return fn
                return register
        app=App();ns={'app':app};reached=asyncio.Event();calls=0
        real_sleep=asyncio.sleep
        async def sleep(seconds):await real_sleep(0 if seconds in (14,2) else seconds)
        async def flaky(operation,*args,**kwargs):
            if operation is general.mark:return None
            nonlocal calls
            calls+=1
            if calls==1:raise sqlite3.OperationalError('database is locked')
            return 'reg' if calls==2 else None
        async def inspect(ns,reg):
            reached.set()
            return 'no_readable_documents',{}
        with patch.object(general,'initialize',return_value={}), \
             patch.object(general,'run_db',side_effect=flaky), \
             patch.object(general,'inspect_one',side_effect=inspect), \
             patch.object(general,'mark'), \
             patch.object(general,'add_new_verified',return_value=0), \
             patch.object(general,'refresh_changed',return_value=0), \
             patch.object(general.asyncio,'sleep',side_effect=sleep):
            general.install(ns);await app.startups[0]()
            try:
                await asyncio.wait_for(reached.wait(),1)
                self.assertFalse(ns['_v332_campaign_worker'].done())
            finally:
                ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(ns['_v332_campaign_worker'],return_exceptions=True)

    async def test_busy_mark_preserves_result_and_other_workers(self):
        class App:
            def __init__(self):self.startups=[]
            def get(self,*a,**kw):return lambda fn:fn
            def on_event(self,event):
                def register(fn):self.startups.append(fn);return fn
                return register
        app=App();ns={'app':app};reached=asyncio.Event();calls=0;marks=0;inspections=0
        real_sleep=asyncio.sleep
        async def sleep(seconds):await real_sleep(0 if seconds in (14,2) else seconds)
        async def flaky(operation,*args,**kwargs):
            nonlocal calls,marks
            if operation is general.mark:
                marks+=1
                if marks==1:raise sqlite3.OperationalError('database is locked')
                reached.set()
                return None
            calls+=1
            return 'reg' if calls==1 else None
        async def inspect(ns,reg):
            nonlocal inspections
            inspections+=1
            return 'no_readable_documents',{}
        with patch.object(general,'initialize',return_value={}), \
             patch.object(general,'run_db',side_effect=flaky), \
             patch.object(general,'inspect_one',side_effect=inspect), \
             patch.object(general,'mark'), \
             patch.object(general,'add_new_verified',return_value=0), \
             patch.object(general,'refresh_changed',return_value=0), \
             patch.object(general.asyncio,'sleep',side_effect=sleep):
            general.install(ns);await app.startups[0]()
            try:
                await asyncio.wait_for(reached.wait(),1)
                self.assertFalse(ns['_v332_campaign_worker'].done())
                self.assertEqual(inspections,1)
                self.assertEqual(marks,2)
            finally:
                ns['_v332_campaign_worker'].cancel()
                await asyncio.gather(ns['_v332_campaign_worker'],return_exceptions=True)

if __name__=='__main__':unittest.main()
