"""Account read regression fixtures; no production credentials or writes."""
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from fastapi import Response
from scripts import v366_account_manual as manual
from scripts import v375_staff_center as center
from scripts import v368_manager_evidence as manager


class ManualReadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'fixture.sqlite')
        self.statements=[]
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,documents_found INTEGER,
                    documents_checked INTEGER,customer_name TEXT,customer_inn TEXT);
                CREATE TABLE findings(reg_number TEXT,title TEXT,law TEXT,evidence TEXT,
                    document TEXT,source_url TEXT,code TEXT,kind TEXT);
                CREATE TABLE checks(reg_number TEXT,item TEXT,law TEXT,evidence TEXT,
                    document TEXT,source_url TEXT,status TEXT);
                CREATE TABLE v301_search_jobs(id TEXT PRIMARY KEY,target TEXT,kind TEXT,
                    status TEXT,detail TEXT,created_at TEXT);
                CREATE TABLE v332_audit_queue(reg TEXT,state TEXT,documents_read INTEGER,updated_at TEXT);
            ''')
            for n in range(360):
                reg=str(32600000000+n)
                db.execute('INSERT INTO purchases VALUES(?,?,?,?,?)',(reg,3,2,'Заказчик '+str(n),'1234567890'))
                db.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)',(reg,'Срок','223-ФЗ, ст. 4','Доказательство '+str(n),'Оригинал','https://zakupki.gov.ru/','test','violation'))
                db.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?)',(reg,'Контроль','223-ФЗ','Текст '+str(n),'Оригинал','https://zakupki.gov.ru/','violation'))
                db.execute('INSERT INTO v301_search_jobs VALUES(?,?,?,?,?,?)',(str(n),reg,'reg','completed','Загружено','2026-10-01'))
                db.execute('INSERT INTO v332_audit_queue VALUES(?,?,?,?)',(reg,'completed',2,'2026-10-01'))
        self.entries=[{'reg_number':str(32600000000+n),'job_id':str(n),
                       'last_started_at':datetime(2026,10,1,tzinfo=timezone.utc)} for n in range(360)]

    def tearDown(self):self.temp.cleanup()

    def connect(self):
        db=sqlite3.connect(self.path)
        db.set_trace_callback(self.statements.append)
        return db

    def read(self,entries):return manual._read_only_status({'conn':self.connect},entries)

    def test_bounded_queries_across_batch_boundary(self):
        rows=self.read(self.entries)
        self.assertEqual(len(rows),360)
        self.assertLessEqual(len(self.statements),20,self.statements)
        self.assertEqual(rows[-1]['customer'],'Заказчик 359')
        self.assertEqual(rows[0]['violations'],2)
        self.assertEqual(rows[0]['violation_types'],['Контроль','Срок'])
        self.assertTrue(rows[0]['legal_review_completed'])

    def test_job_target_and_kind_are_not_crossed(self):
        entries=[dict(self.entries[0],job_id='1')]
        self.assertEqual(self.read(entries)[0]['job_status'],'unknown')
        with sqlite3.connect(self.path) as db:db.execute("UPDATE v301_search_jobs SET kind='other' WHERE id='0'")
        self.assertEqual(self.read(self.entries[:1])[0]['job_status'],'unknown')

    def test_latest_audit_and_empty_audit_not_download_completion(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO v332_audit_queue VALUES(?,?,?,?)",(self.entries[0]['reg_number'],'running',0,'2026-10-18'))
        row=self.read(self.entries[:1])[0]
        self.assertEqual(row['job_status'],'completed')
        self.assertFalse(row['legal_review_completed'])
        self.assertEqual(row['legal_review_state'],'running')

    def test_duplicate_regs_keep_individual_jobs_and_order(self):
        rows=self.read([self.entries[1],dict(self.entries[1],job_id='missing'),self.entries[0]])
        self.assertEqual([x['job_status'] for x in rows],['completed','unknown','completed'])
        self.assertEqual([x['customer'] for x in rows],['Заказчик 1','Заказчик 1','Заказчик 0'])

    def test_missing_optional_tables_preserves_unknown_states(self):
        with sqlite3.connect(self.path) as db:
            for table in ('purchases','v301_search_jobs','v332_audit_queue'):
                db.execute('DROP TABLE '+table)
        row=self.read(self.entries[:1])[0]
        self.assertFalse(row['card_available'])
        self.assertFalse(row['legal_review_completed'])
        self.assertEqual(row['customer'],'')
        self.assertEqual(row['documents_checked'],0)
        self.assertEqual(row['job_status'],'unknown')
        self.assertEqual(row['violations'],2)

    def test_label_cap_and_customer_legacy_column(self):
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE purchases RENAME COLUMN customer_name TO customer')
            for n in range(50):
                db.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)',
                           (self.entries[0]['reg_number'],f'Type {n:02d}','','','','','','нарушение'))
        row=self.read(self.entries[:1])[0]
        self.assertEqual(len(row['violation_types']),40)
        self.assertEqual(row['customer'],'Заказчик 0')
        self.assertEqual(row['violations'],2)

    def test_empty_input_does_not_connect(self):
        self.assertEqual(self.read([]),[])
        self.assertEqual(self.statements,[])

    def test_read_only_connection(self):
        self.read(self.entries[:1])
        self.assertIn('PRAGMA query_only=ON',self.statements)
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for s in self.statements))


class Cursor:
    def __init__(self,rows):self.rows=rows
    def fetchall(self):return self.rows
    def fetchone(self):return self.rows[0] if self.rows else None

class FixtureDB:
    def __init__(self,results):self.results=iter(results);self.queries=[]
    def execute(self,sql,args=()):
        self.queries.append((sql,args));return Cursor(next(self.results))
    def __enter__(self):return self
    def __exit__(self,*args):pass

class DashboardTests(unittest.TestCase):
    def test_staff_counts_exceed_visible_list_limits(self):
        now=datetime.now(timezone.utc)
        db=FixtureDB([[('Иванов','Иван',None)],[(1,'32600000000','Задание','assigned',None,now,125)],
                      [(2,'Кампания','active',5,None,'batch',now,95)],[(0,)],[],[],[(0,)]])
        response=Response()
        with patch.object(center,'own',return_value='owner'),patch.object(center,'connect',return_value=db):
            result=center.dashboard(SimpleNamespace(),response)
        self.assertEqual((result['task_active'],result['campaign_active']),(125,95))
        self.assertEqual(len(result['tasks']),1)
        self.assertEqual(len(db.queries),7)
        self.assertTrue(all(args==('owner',) for _,args in db.queries))
        self.assertIn('OVER ()',db.queries[1][0])
        self.assertIn('no-store',response.headers['cache-control'])

    def test_manager_discloses_capped_manual_evidence_metrics(self):
        now=datetime.now(timezone.utc)
        db=FixtureDB([[('worker',)],[],[],[],[('worker','32600000000','job',now,now,351)],[]])
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        with patch.object(manager,'supervisor',return_value=('boss','manager')),patch.object(manager,'connect',return_value=db):
            result=manager.dashboard(request,Response())
        self.assertTrue(result['manual_checks_truncated'])
        self.assertIn('350',result['metric_note'])
        self.assertIsNone(result['real_confirmed_findings'])
        self.assertEqual(db.queries[4][1],('worker',))

    def test_manager_counts_are_aggregated_and_team_scoped(self):
        for role in ('manager','admin'):
            db=FixtureDB([[],[('assigned',450,25),('submitted',75,5),('completed',1200,0)],[],[],[]])
            with patch.object(manager,'supervisor',return_value=('boss',role)),patch.object(manager,'connect',return_value=db):
                result=manager.dashboard(SimpleNamespace(),Response())
            self.assertEqual(result['tasks_total'],1725)
            self.assertEqual(result['tasks_overdue'],30)
            self.assertEqual(result['tasks_pending'],75)
            sql,args=db.queries[1]
            self.assertNotIn('LIMIT',sql)
            self.assertIn('GROUP BY t.status',sql)
            if role=='manager':
                self.assertIn('t.manager_id=%s AND mt.manager_id=%s',sql)
                self.assertEqual(args[1:],('boss','boss'))
            else:self.assertEqual(len(args),1)

if __name__=='__main__':unittest.main()
