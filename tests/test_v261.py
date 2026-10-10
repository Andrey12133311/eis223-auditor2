"""Regression tests for the V261 source selection and UI invariance."""
from __future__ import annotations
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from patch_v258 import CLAIM_NEW,ENQUEUE_NEW
from patch_v261 import RULES,ORDER_OLD,ORDER_NEW,ALTERNATIVE_NEW,patch,patch_text

class QueueFairnessTests(unittest.TestCase):
    def test_exact_inspected_source_anchors(self):
        self.assertIn(ORDER_OLD,CLAIM_NEW)
        self.assertIn(RULES[1][0],ENQUEUE_NEW)

    def test_alternative_first_and_cached_first(self):
        con=sqlite3.connect(':memory:')
        con.row_factory=sqlite3.Row
        con.execute('''CREATE TABLE document_jobs_v204(
            reg TEXT,identity TEXT,lane TEXT,status TEXT,source_host TEXT,
            has_alternative INTEGER,local_path TEXT,payload TEXT,due REAL,
            priority INTEGER,attempts INTEGER, PRIMARY KEY(reg,identity))''')
        for reg,host,alt,local,due in (
            ('primary-new','zakupki.gov.ru',0,'',0),
            ('primary-repeat','zakupki.gov.ru',0,'',0),
            ('alternative','zakupki.gov.ru',1,'',0),
            ('cached','zakupki.gov.ru',0,'/data/verified/cached.pdf',0),
            ('delayed-alt','zakupki.gov.ru',1,'',time.time()+3600),
        ):
            con.execute('INSERT INTO document_jobs_v204 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (reg,reg,'auto','waiting',host,alt,local,'{}',due,0,0))
        con.execute('UPDATE document_jobs_v204 SET attempts=2 WHERE reg=?',('primary-repeat',))
        con.commit()

        @contextmanager
        def db():
            with con:
                yield con

        claim_new=CLAIM_NEW.replace(ORDER_OLD,ORDER_NEW,1)
        ns={'db':db,'time':time,'host_health':{}}
        exec('def build():\n'+claim_new+'\n    return claim\n',ns)
        take=ns['build']()
        self.assertEqual(take('auto')['reg'],'cached')
        self.assertEqual(take('auto')['reg'],'alternative')
        self.assertEqual(take('auto')['reg'],'primary-new')
        self.assertEqual(take('auto')['reg'],'primary-repeat')
        self.assertIsNone(take('auto'))
        self.assertEqual(con.execute("SELECT status FROM document_jobs_v204 WHERE reg='delayed-alt'").fetchone()[0],'waiting')
        self.assertEqual(con.execute('SELECT COUNT(*) FROM document_jobs_v204').fetchone()[0],5)
        con.close()

    def test_blocked_host_still_defers_unmirrored_jobs(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row
        con.execute('''CREATE TABLE document_jobs_v204(reg TEXT,identity TEXT,lane TEXT,status TEXT,source_host TEXT,
            has_alternative INT,local_path TEXT,payload TEXT,due REAL,priority INT,attempts INT,PRIMARY KEY(reg,identity))''')
        for reg,alt in [('not-reachable',0),('mirror',1)]:
            con.execute('INSERT INTO document_jobs_v204 VALUES(?,?,?,?,?,?,?,?,?,?,?)',(reg,reg,'auto','waiting','zakupki.gov.ru',alt,'','{}',0,0,0))
        con.commit()
        @contextmanager
        def db():
            with con:yield con
        body=CLAIM_NEW.replace(ORDER_OLD,ORDER_NEW,1)
        ns={'db':db,'time':time,'host_health':{('auto','zakupki.gov.ru'):{'until':time.time()+3600}}}
        exec('def build():\n'+body+'\n    return claim\n',ns)
        self.assertEqual(ns['build']()('auto')['reg'],'mirror')
        self.assertIsNone(ns['build']()('auto'))
        con.close()

    def test_only_validated_alternative_hosts_get_priority(self):
        ns={'urlparse':urlparse,'ns':{'safe_host':lambda u:u.startswith('https://roseltorg.ru/')}}
        exec('def classify(urls,host):\n    alternative=False\n    for u in urls:\n        if not isinstance(u,str) or not u.startswith((\\'https://\\',\\'http://\\')):continue\n'+ALTERNATIVE_NEW+'\n    return alternative\n',ns)
        check=ns['classify']
        self.assertTrue(check(['https://roseltorg.ru/document.pdf'],'zakupki.gov.ru'))
        self.assertFalse(check(['https://unsafe.example.org/document.pdf'],'zakupki.gov.ru'))
        self.assertFalse(check(['https://[malformed'],'zakupki.gov.ru'))
        self.assertFalse(check(['https://roseltorg.ru/document.pdf'],'roseltorg.ru'))

    def test_no_counters_or_persistent_snapshot_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'entry.py'
            f.write_text('pass\n',encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'unexpected source anchor'):
                patch(f)
            self.assertEqual(f.read_text(encoding='utf-8'),'pass\n')
        for _,after in RULES:
            self.assertNotIn('DELETE FROM',after)
            self.assertNotIn('UPDATE purchases',after)
            self.assertNotIn('stats.purchases',after)
            self.assertNotIn('V244 COUNTERS',after)

if __name__=='__main__':unittest.main()
