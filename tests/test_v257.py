"""Route and persistence smoke tests using synthetic procurement data."""
from __future__ import annotations
import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
import httpx
from fastapi import FastAPI
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import v257_features as features
from patch_v257 import EXACT

class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db_file=str(Path(self.temp.name)/'audit.sqlite')
        def conn():
            c=sqlite3.connect(self.db_file,timeout=30)
            c.row_factory=sqlite3.Row
            return c
        with conn() as c:
            c.executescript("""CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,title TEXT,source TEXT,customer_inn TEXT,org_form TEXT,price_num REAL,method TEXT,published_at TEXT,card_source_status TEXT,last_seen TEXT,submission_start TEXT,submission_end TEXT,documents_found INT,documents_checked INT,violations INT,risks INT,clarifications INT,protocols INT,cancellations INT);
            CREATE TABLE findings(reg_number TEXT,kind TEXT,code TEXT,title TEXT);
            CREATE TABLE requests_v233(reg TEXT PRIMARY KEY,lane TEXT,status TEXT,requested_at TEXT);
            CREATE TABLE document_priorities_v233(reg TEXT PRIMARY KEY,lane TEXT,requested_at TEXT);
            CREATE TABLE customer_purchases_v233(inn TEXT,reg TEXT);""")
            c.execute("""INSERT INTO purchases(reg_number,customer,title,source,customer_inn,org_form,price_num,last_seen,documents_found,documents_checked,violations,risks)
              VALUES('32610000001','Auto','Automatic','api','1234567890','ООО',5000,'2026',2,2,1,0)""")
            c.execute("""INSERT INTO purchases(reg_number,customer,title,source,customer_inn,org_form,price_num,last_seen,documents_found,documents_checked,violations,risks)
              VALUES('32610000002','Manual','Manual','manual','1234567890','ООО',100,'2026',0,0,0,0)""")
            c.execute("INSERT INTO findings VALUES('32610000001','violation','CLAR-LATE','Нарушение срока разъяснения')")
            c.execute("INSERT INTO requests_v233 VALUES('32610000002','manual','pending','2026-10-10')")
        self.app=FastAPI()
        features.install({'app':self.app,'conn':conn,'_verified_set235':lambda:{'32610000001','32610000002'}})
        for fn in self.app.router.on_startup:asyncio.run(fn())
    def tearDown(self):self.temp.cleanup()
    def test_dashboard_manual_isolation_and_findings(self):
        async def task():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test") as c:
                r=await c.get('/api/v257/dashboard?violation_type=CLAR-LATE&inn=1234567890')
                self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()['stats']['purchases'],1)
                self.assertEqual(r.json()['purchases'][0]['reg_number'],'32610000001')
                m=await c.get('/api/v257/manual')
                self.assertEqual(m.status_code,200)
                self.assertEqual(m.json()['total'],1)
                self.assertEqual(m.json()['items'][0]['reg_number'],'32610000002')
                types=await c.get('/api/v257/violation-types')
                self.assertEqual(types.json()['items'][0]['code'],'CLAR-LATE')
                self.assertEqual((await c.get('/manual-purchases')).status_code,200)
                self.assertEqual((await c.get('/api/v257/purchase-extra/32610000001')).status_code,200)
        asyncio.run(task())
    def test_invalid_inn_and_manual_gate(self):
        async def task():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://test") as c:
                self.assertEqual((await c.get('/api/v257/dashboard?inn=123')).status_code,400)
                self.assertEqual((await c.post('/api/v257/manual/32610000003')).status_code,409)
        asyncio.run(task())
    def test_html_upgrade_is_safe_and_idempotent(self):
        html='<html><body><script>'+features.OLD_FILTER+';'+features.OLD_DASHBOARD+'</script></body></html>'
        updated=features._html_patch(html)
        self.assertIn(features.NEW_FILTER,updated)
        self.assertIn(features.NEW_DASHBOARD,updated)
        self.assertEqual(updated.count('id="v257-ui"'),1)
        self.assertEqual(features._html_patch(updated).count('id="v257-ui"'),1)
    def test_private_snapshot_safe(self):
        from patch_v257 import patch
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'entry.py'
            text='import os\n'
            for old,new in EXACT:text+=old+'\n'
            file.write_text(text)
            with self.assertRaises(SyntaxError):
                patch(file)
            self.assertNotIn('v257_features',file.read_text())

if __name__=='__main__':unittest.main()
