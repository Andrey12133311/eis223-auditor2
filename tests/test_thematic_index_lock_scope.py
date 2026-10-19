"""Recurring raw-page indexing releases readers/writer during scope checks."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import thematic_period as period
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast

BATCH='a'*24

class ThematicIndexLockScope(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'index.sqlite'
        self.statements=[]
        self.ns={'conn':self.connect}
        with self.connection() as c:
            c.executescript('''CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,published_at TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT);
                CREATE TABLE intake(id INTEGER PRIMARY KEY);''')
        thematic.init(self.ns)
        with self.connection() as c:
            fast.ensure_scan_cursors(c)
            c.execute("ALTER TABLE thematic_items ADD COLUMN reservation_token TEXT NOT NULL DEFAULT ''")
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,legal_forms,date_from,date_to) "
                "VALUES(?,?,'2026-10-19',?,'2026-10-01','2026-10-19')",
                (BATCH,'["dates"]','["ООО"]'))
        self.restore_state=dict(thematic.WORKER_STATE)
        self.restore_tick=thematic.tick
        self.addCleanup(self.restore)

    def restore(self):
        thematic.WORKER_STATE.clear();thematic.WORKER_STATE.update(self.restore_state)
        thematic.tick=self.restore_tick

    def connect(self):
        c=sqlite3.connect(self.path,timeout=.05)
        c.row_factory=sqlite3.Row
        c.set_trace_callback(self.statements.append)
        return c

    @contextmanager
    def connection(self):
        c=self.connect()
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:c.close()

    def purchases(self,count=1,published='2026-10-19'):
        regs=[str(32610000000+i) for i in range(count)]
        with self.connection() as c:
            c.executemany('INSERT INTO purchases VALUES(?,?,?)',[(r,'ООО Пример',published) for r in regs])
        return regs

    def cursor(self):
        with self.connection() as c:return fast.scan_cursor(c,'thematic-index:'+BATCH)

    def queued(self):
        with self.connection() as c:return [r[0] for r in c.execute('SELECT reg FROM thematic_items ORDER BY reg')]

    def test_other_writer_commits_during_publication_and_customer_classification(self):
        self.purchases(3)
        for journal in ('WAL','DELETE'):
            with self.connection() as c:c.execute('PRAGMA journal_mode='+journal)
            for module,field in ((period,'publication_day'),(thematic,'customer_form')):
                actual=getattr(module,field)
                called=[]
                def classify(*args):
                    first=not called
                    called.append(1)
                    if first:
                        with self.connection() as c:c.execute('INSERT INTO intake DEFAULT VALUES')
                    return actual(*args)
                with self.subTest(journal=journal,classification=field),patch.object(module,field,side_effect=classify):
                    self.assertTrue(fast._index_batch(self.ns,BATCH))
                self.assertTrue(called)
        with self.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM intake').fetchone()[0],4)

    def test_sparse_period_scans_raw_pages_and_eventually_reaches_late_match(self):
        regs=self.purchases(1025,'2025-01-01')
        with self.connection() as c:c.execute('UPDATE purchases SET published_at=? WHERE reg_number=?',('2026-10-19',regs[-1]))
        actual=period.publication_day
        for page in range(9):
            self.statements.clear()
            with patch.object(period,'publication_day',wraps=actual) as parse:
                result=fast._index_batch(self.ns,BATCH)
            self.assertLessEqual(parse.call_count,fast.SCAN_PAGE_SIZE)
            self.assertTrue(result)
            reads=[s for s in self.statements if s.startswith('SELECT reg_number,customer,')]
            self.assertEqual(len(reads),2,'one bounded snapshot and one bounded CAS reread, no N+1')
            if page==0:
                self.assertEqual(self.cursor(),regs[127]);self.assertEqual(self.queued(),[])
        self.assertEqual(self.queued(),[regs[-1]])
        self.assertEqual(self.cursor(),'')

    def test_non_registry_rows_count_toward_the_raw_page_budget(self):
        reg=self.purchases()[0]
        with self.connection() as c:
            c.executemany('INSERT INTO purchases VALUES(?,?,?)',
                [(str(10000000000+i),'ООО Пример','2026-10-19') for i in range(256)])
        for _ in range(2):
            with patch.object(period,'publication_day',wraps=period.publication_day) as parse:
                self.assertTrue(fast._index_batch(self.ns,BATCH))
                self.assertEqual(parse.call_count,0)
            self.assertEqual(self.queued(),[])
        self.assertTrue(fast._index_batch(self.ns,BATCH));self.assertEqual(self.queued(),[reg])

    def test_customer_or_publication_change_retries_page_without_advancing(self):
        regs=self.purchases(2)
        for column,value in (('customer','АО Другая форма'),('published_at','2025-01-01')):
            with self.connection() as c:
                c.execute("DELETE FROM thematic_items")
                c.execute("UPDATE purchases SET customer='ООО Пример',published_at='2026-10-19'")
            actual=period.publication_day;changed=[]
            def classify(*args):
                if not changed:
                    with self.connection() as c:c.execute('UPDATE purchases SET '+column+'=? WHERE reg_number=?',(value,regs[0]))
                    changed.append(1)
                return actual(*args)
            with self.subTest(column=column),patch.object(period,'publication_day',side_effect=classify):
                self.assertFalse(fast._index_batch(self.ns,BATCH))
            self.assertEqual(self.cursor(),'');self.assertEqual(self.queued(),[])
            self.assertTrue(fast._index_batch(self.ns,BATCH))
            self.assertEqual(self.queued(),[regs[1]])

    def test_excluded_row_becoming_eligible_does_not_get_skipped(self):
        regs=self.purchases(2,'2025-01-01')
        actual=period.publication_day;changed=[]
        def classify(*args):
            if not changed:
                with self.connection() as c:c.execute("UPDATE purchases SET published_at='2026-10-19' WHERE reg_number=?",(regs[0],))
                changed.append(1)
            return actual(*args)
        with patch.object(period,'publication_day',side_effect=classify):
            self.assertFalse(fast._index_batch(self.ns,BATCH))
        self.assertEqual(self.queued(),[]);self.assertEqual(self.cursor(),'')
        self.assertTrue(fast._index_batch(self.ns,BATCH));self.assertEqual(self.queued(),[regs[0]])

    def test_stopped_or_changed_campaign_and_competing_cursor_are_not_overwritten(self):
        self.purchases(2)
        for sql,args in (("UPDATE thematic_batches SET status='stopped' WHERE id=?",(BATCH,)),
                         ("UPDATE thematic_batches SET legal_forms=? WHERE id=?",('["АО"]',BATCH)),
                         ("INSERT INTO v382_audit_scan_cursors VALUES(?,?)",('thematic-index:'+BATCH,'32619999999'))):
            with self.connection() as c:
                c.execute("UPDATE thematic_batches SET status='active',legal_forms=?",('["ООО"]',))
                c.execute('DELETE FROM v382_audit_scan_cursors')
            actual=period.publication_day;changed=[]
            def classify(*values):
                if not changed:
                    with self.connection() as c:c.execute(sql,args)
                    changed.append(1)
                return actual(*values)
            with patch.object(period,'publication_day',side_effect=classify):
                self.assertFalse(fast._index_batch(self.ns,BATCH))
            self.assertEqual(self.queued(),[])
            with self.connection() as c:
                self.assertEqual(fast.scan_cursor(c,'thematic-index-at:'+BATCH),'')
            self.assertEqual(self.cursor(),'32619999999' if sql.startswith('INSERT') else '')

    def test_insert_behind_cursor_is_found_after_wrap(self):
        regs=self.purchases(129)
        self.assertTrue(fast._index_batch(self.ns,BATCH));self.assertEqual(self.cursor(),regs[127])
        behind='32609999999'
        with self.connection() as c:c.execute('INSERT INTO purchases VALUES(?,?,?)',(behind,'ООО Пример','2026-10-19'))
        self.assertTrue(fast._index_batch(self.ns,BATCH));self.assertEqual(self.cursor(),'')
        self.assertNotIn(behind,self.queued())
        self.assertTrue(fast._index_batch(self.ns,BATCH));self.assertIn(behind,self.queued())
        self.assertEqual(len(self.queued()),130)

    def test_campaign_rotation_keeps_eight_campaign_limit(self):
        self.purchases(300,'2025-01-01')
        fast.install(self.ns)
        with self.connection() as c:
            for i in range(9):
                c.execute("INSERT INTO thematic_batches(id,categories,created_at,legal_forms,date_from,date_to) "
                    "VALUES(?,?,'2026-10-19',?,'2026-10-01','2026-10-19')",
                    (format(i,'024x'),'["dates"]','["ООО"]'))
        thematic.WORKER_STATE['index_at']=0
        actual=fast._index_batch
        with patch.object(fast,'_index_batch',wraps=actual) as index:
            self.assertEqual(fast._prepare(self.ns),[])
            first=[call.args[1] for call in index.call_args_list]
            index.reset_mock()
            self.assertEqual(fast._prepare(self.ns),[])
            second=[call.args[1] for call in index.call_args_list]
        self.assertEqual(len(first),8);self.assertEqual(len(second),2)
        self.assertFalse(set(first)&set(second))
        self.assertEqual(len(set(first+second)),10)

    def test_existing_history_leases_and_retry_state_are_preserved(self):
        regs=self.purchases(2)
        with self.connection() as c:
            # Same schema upgrades as production, without changing global tick.
            columns={row[1] for row in c.execute('PRAGMA table_info(thematic_items)')}
            for name,definition in (('reservation_token',"TEXT NOT NULL DEFAULT ''"),('retry_attempts','INTEGER NOT NULL DEFAULT 0'),('next_retry_at','REAL NOT NULL DEFAULT 0')):
                if name not in columns:c.execute('ALTER TABLE thematic_items ADD COLUMN '+name+' '+definition)
            c.execute("INSERT INTO thematic_items(batch_id,reg,status,updated_at,reservation_token,retry_attempts,next_retry_at) VALUES(?,?,'running','saved','lease',3,9999999999)",(BATCH,regs[0]))
            c.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,evidence) VALUES(?,?,'dates','История','Исходное доказательство')",(BATCH,regs[0]))
            before=[tuple(row) for row in c.execute('SELECT * FROM thematic_items')]
        self.assertTrue(fast._index_batch(self.ns,BATCH))
        with self.connection() as c:
            self.assertEqual(tuple(c.execute('SELECT * FROM thematic_items WHERE reg=?',(regs[0],)).fetchone()),before[0])
            self.assertEqual(c.execute('SELECT evidence FROM thematic_findings').fetchone()[0],'Исходное доказательство')

if __name__=='__main__':unittest.main()
