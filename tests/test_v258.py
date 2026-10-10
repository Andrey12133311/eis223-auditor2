from __future__ import annotations
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from patch_v258 import CLAIM_NEW, ENQUEUE_NEW, RULES, patch, patch_text

class QueueTests(unittest.TestCase):
    def test_cache_and_alternate_sources_precede_blocked_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path=Path(tmp)/'jobs.db'
            con=sqlite3.connect(db_path)
            con.row_factory=sqlite3.Row
            con.execute('''CREATE TABLE document_jobs_v204(
                reg TEXT, identity TEXT, lane TEXT, source_host TEXT, has_alternative INTEGER,
                payload TEXT, local_path TEXT, status TEXT, due REAL, attempts INTEGER,
                priority INTEGER, PRIMARY KEY(reg,identity))''')
            for reg,host,alt,local,priority in [
                ('blocked','down.example',0,'',100),
                ('cached','down.example',0,'/tmp/original.docx',5),
                ('mirror','down.example',1,'',90),
                ('available','up.example',0,'',40)
            ]:
                con.execute("INSERT INTO document_jobs_v204 VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (reg,reg,'auto',host,alt,'{}',local,'pending',0,0,priority))
            con.commit()
            # sqlite's transaction context does not close the connection.
            # The worker contract is a context manager that COMMITs on exit.
            from contextlib import contextmanager
            @contextmanager
            def db():
                try:yield con
                finally:con.commit()
            namespace={'db':db,'time':time,
                       'host_health':{('auto','down.example'):{'until':time.time()+240}}}
            exec("def builder():\n"+CLAIM_NEW+"\n    return claim\n",namespace)
            claim=namespace['builder']()
            self.assertEqual(claim()['reg'],'cached')
            self.assertEqual(claim()['reg'],'mirror')
            self.assertEqual(claim()['reg'],'available')
            self.assertIsNone(claim())
            remaining=con.execute("SELECT attempts,status FROM document_jobs_v204 WHERE reg='blocked'").fetchone()
            self.assertEqual(tuple(remaining),(0,'pending'))
            con.close()

    def test_malformed_metadata_and_string_alternative(self):
        ns={'urlparse':urlparse}
        exec("def classify(d,host):\n"+ENQUEUE_NEW+"\n    return urls, alternative\n",ns)
        classify=ns['classify']
        urls,alt=classify({'meta':{'alt_urls':'https://mirror.example/d.pdf'}},'down.example')
        self.assertEqual(urls,['https://mirror.example/d.pdf'])
        self.assertTrue(alt)
        urls,alt=classify({'meta':{'alt_urls':['https://[broken','https://down.example/ok.pdf']}},'down.example')
        self.assertFalse(alt)
        urls,alt=classify({'meta':None},'down.example')
        self.assertFalse(alt)

    def test_no_new_ui_or_indicators(self):
        self.assertEqual(len(RULES),2)
        for before,after in RULES:
            self.assertNotIn('v258_features',after)
            self.assertNotIn('progress',after)
            self.assertNotIn('counter',after)
        self.assertNotIn('v258_features',patch_text.__code__.co_consts)

    def test_source_anchor_guard_and_no_original_modification(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'entry.py'
            src.write_text('pass\n',encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'patch anchor'):
                patch(src)
            self.assertEqual(src.read_text(),'pass\n')

if __name__=='__main__':unittest.main()
