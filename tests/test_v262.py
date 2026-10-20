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
        # V352 replaced the historical inline count SQL. Verify the public
        # behavior across full-filter counts and pages instead of obsolete
        # source text anchors. Synthetic saved values must stay untouched.
        from v352_dashboard_truth import dashboard_counts
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row
        self.addCleanup(con.close)
        con.executescript("""
          CREATE TABLE purchases(reg_number TEXT,customer TEXT,last_seen TEXT,
            violations INTEGER,risks INTEGER,documents_found INTEGER,documents_checked INTEGER);
          CREATE TABLE findings(reg_number TEXT,kind TEXT,code TEXT,title TEXT,
            law TEXT,evidence TEXT,document TEXT,source_url TEXT);
          CREATE TABLE checks(reg_number TEXT,status TEXT,field_path TEXT,item TEXT,
            law TEXT,evidence TEXT,document TEXT,source_url TEXT);
        """)
        for i in range(3):
            con.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?,?)',
                        (str(i),'АО Тест',str(i),7,0,2,2))
            proof=('Порядок оплаты: расчет производится в течение 15 рабочих дней '
                   'с даты подписания документов о приемке продукции заказчиком.')
            con.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)',
                        (str(i),'violation','1352-28','Срок оплаты МСП превышен',
                         'ПП РФ № 1352 п. 28',proof,'Договор.pdf','source'))
            con.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?,?)',
                        (str(i),'violation','1352-28','Срок оплаты МСП превышен',
                         'ПП РФ № 1352 п. 28',proof,'Договор.pdf','source'))
        con.commit();changes=con.total_changes
        stats,first=dashboard_counts(con,' FROM purchases p WHERE 1=1',[],0,1)
        second_stats,second=dashboard_counts(con,' FROM purchases p WHERE 1=1',[],1,1)
        self.assertEqual((stats['purchases'],stats['violations']),(3,3))
        self.assertEqual(stats,second_stats)
        self.assertEqual((len(first),len(second)),(1,1))
        self.assertNotEqual(first[0]['reg_number'],second[0]['reg_number'])
        filtered,_=dashboard_counts(con,' FROM purchases p WHERE p.reg_number=?',['0'],0,1)
        self.assertEqual((filtered['purchases'],filtered['violations']),(1,1))
        self.assertEqual(con.total_changes,changes)
        self.assertEqual(con.execute('SELECT SUM(violations) FROM purchases').fetchone()[0],21)
        self.assertIn("body=body.replace('<small>Закупок по фильтру</small>','<small>Закупки</small>')",content)
        self.assertNotIn('v262-progress',content)

    def test_no_delete_mark_checked_or_change_existing_stats(self):
        s=CLAIM_NEW+RECOVERY_NEW
        for forbidden in ("DELETE FROM", "status='checked'", "UPDATE purchases", "result['purchases']"):
            self.assertNotIn(forbidden,s)
        self.assertIn("if lane=='auto':",s)
        self.assertIn('if not force:',s)

if __name__=='__main__':unittest.main()
