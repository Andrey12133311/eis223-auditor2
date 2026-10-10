from __future__ import annotations
import contextlib
import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from patch_v258 import CLAIM_NEW as BASE_CLAIM
from patch_v261 import ORDER_OLD,ORDER_NEW
from patch_v260 import NEW as BASE_RECOVERY
from patch_v262 import CLAIM_OLD,CLAIM_NEW,RECOVERY_OLD,RECOVERY_NEW,patch_text

class V262Tests(unittest.TestCase):
    def test_versioned_source_anchors(self):
        self.assertEqual(ORDER_NEW.count(CLAIM_OLD),1)
        self.assertEqual(BASE_RECOVERY.count(RECOVERY_OLD),1)
        self.assertIn("AND attempts<CASE",CLAIM_NEW)
        self.assertIn("r.tries,0)=0",RECOVERY_NEW)
        with self.assertRaisesRegex(RuntimeError,'source mismatch'):
            patch_text("print('unexpected code')\n")

    def test_auto_skip_failed_and_no_source_but_manual_still_allowed(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row
        con.execute('''CREATE TABLE document_jobs_v204(
            reg TEXT,identity TEXT,lane TEXT,status TEXT,source_host TEXT,
            has_alternative INTEGER,local_path TEXT,payload TEXT,due REAL,
            priority INTEGER,attempts INTEGER,PRIMARY KEY(reg,identity))''')
        for reg,lane,host,alt,local,tries in [
          ('new','auto','zakupki.gov.ru',0,'',0),
          ('already-failed-twice','auto','zakupki.gov.ru',0,'',2),
          ('empty-source','auto','',0,'',0),
          ('alt-retry','auto','zakupki.gov.ru',1,'',2),
          ('alt-exhausted','auto','zakupki.gov.ru',1,'',3),
          ('cached','auto','zakupki.gov.ru',0,'/data/test/123.pdf',2),
          ('manual','manual','zakupki.gov.ru',0,'',9),
        ]:
            con.execute('INSERT INTO document_jobs_v204 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (reg,reg,lane,'waiting',host,alt,local,'{}',0,0,tries))
        con.commit()
        @contextlib.contextmanager
        def db():
            with con: yield con
        source=BASE_CLAIM.replace(ORDER_OLD,ORDER_NEW,1).replace(CLAIM_OLD,CLAIM_NEW,1)
        self.assertNotIn(CLAIM_OLD,source)
        ns={'time':time,'db':db,'host_health':{}}
        exec('def build():\n'+source+'\n    return claim\n',ns)
        take=ns['build']()
        selected=[]
        while True:
            item=take('auto')
            if item is None:break
            selected.append(item['reg'])
        self.assertEqual(selected,['cached','alt-retry','new'])
        self.assertNotIn('already-failed-twice',selected)
        self.assertNotIn('empty-source',selected)
        self.assertNotIn('alt-exhausted',selected)
        self.assertEqual(take('manual')['reg'],'manual')
        self.assertEqual(con.execute('SELECT COUNT(*) FROM document_jobs_v204').fetchone()[0],7)
        self.assertEqual(con.execute("SELECT status FROM document_jobs_v204 WHERE reg='already-failed-twice'").fetchone()[0],'waiting')

    def test_existing_main_page_total_uses_exact_filtered_list(self):
        src=Path(__file__).resolve().parents[1]/'scripts'/'v257_features.py'
        content=src.read_text(encoding='utf-8')
        self.assertIn("SELECT COUNT(*) purchases",content)
        self.assertIn("records=c.execute('SELECT p.*'+sql",content)
        self.assertIn("'stats':dict(result)",content)
        self.assertIn("body=body.replace('<small>Закупок по фильтру</small>','<small>Закупки</small>')",content)
        self.assertNotIn('v262-progress',content)

    def test_no_delete_mark_checked_or_change_existing_stats(self):
        s=CLAIM_NEW+RECOVERY_NEW
        for forbidden in ('DELETE FROM','status=\\'checked\\'','UPDATE purchases','result[\\'purchases\\']'):
            self.assertNotIn(forbidden,s)
        self.assertIn('if lane==\\'auto\\':',s)
        self.assertIn('if not force:',s)

if __name__=='__main__':unittest.main()
