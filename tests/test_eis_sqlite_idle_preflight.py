"""Regression coverage for idle full-recheck SQLite writer reservations.

Execute the production preflight/transaction functions against a temporary WAL
SQLite database. The ledger double checks transactional revalidation and failure
handling; the existing full-recheck suite covers the real ledger and fencing.
"""
import ast
from contextlib import contextmanager, closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

FUNCTIONS={'_catch_up','transaction','request_id','_target_args'}
SOURCE=Path(__file__).resolve().parents[1] / 'scripts' / 'full_recheck_runtime.py'
tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in FUNCTIONS]
if {n.name for n in nodes} != FUNCTIONS:raise RuntimeError('Expected production function definitions not found')
R={'contextmanager':contextmanager,'CATCHUP_PAGE_SIZE':32}
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),R)

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'test.sqlite'
        self.trace=[]
        self.opened=[]
        self.connection_hook=None
        c=sqlite3.connect(self.db)
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE full_recheck_members(request_id TEXT, reg TEXT,
            status TEXT, result_engine_signature TEXT,
            receipt TEXT DEFAULT 'retained', new_findings INTEGER DEFAULT 7,
            PRIMARY KEY(request_id,reg));
        CREATE TABLE test_target(request_id TEXT PRIMARY KEY, signature TEXT, generation INTEGER);
        INSERT INTO test_target VALUES('request-1','engine-new',6);
        CREATE TABLE original_evidence(uid TEXT PRIMARY KEY, body BLOB);
        INSERT INTO original_evidence VALUES('keep-me',X'504446');
        ''')
        c.commit();c.close()
        self.ns={'conn':self.connect,'_full_recheck':{'request_id':'request-1','engine_signature':'engine-new','target_generation':6}}
        self.callback=Mock(side_effect=self.ledger_double)
        self.ledger=types.ModuleType('full_recheck_request')
        self.ledger.reopen_completed=self.callback
        self.patch=patch.dict(sys.modules,{'full_recheck_request':self.ledger})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        for c in self.opened:
            try:c.close()
            except Exception:pass
        self.tmp.cleanup()

    def connect(self):
        if self.connection_hook:self.connection_hook()
        c=sqlite3.connect(self.db,timeout=.02)
        c.set_trace_callback(self.trace.append)
        self.opened.append(c)
        return c

    def put(self,reg='32616485753',status='completed',engine='engine-old',request='request-1'):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO full_recheck_members(request_id,reg,status,result_engine_signature) VALUES(?,?,?,?)',(request,reg,status,engine))

    def rows(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            return c.execute('SELECT * FROM full_recheck_members ORDER BY request_id,reg').fetchall()

    def no_writer(self):
        self.assertFalse(any('BEGIN IMMEDIATE' in s.upper() for s in self.trace),self.trace)

    def ledger_double(self,c,request,*,limit,engine_signature,generation):
        self.assertTrue(c.in_transaction,'Ledger must remain inside a write transaction')
        self.assertEqual(limit,32)
        # Mimic only the existing ledger's *recheck*, not an alternative ledger.
        target=c.execute('SELECT signature,generation FROM test_target WHERE request_id=?',(request,)).fetchone()
        if target != (engine_signature,generation):return 0
        regs=c.execute("SELECT reg FROM full_recheck_members WHERE request_id=? AND status='completed' AND result_engine_signature<>? ORDER BY reg LIMIT ?",(request,engine_signature,limit)).fetchall()
        for (reg,) in regs:
            c.execute("UPDATE full_recheck_members SET status='retry' WHERE request_id=? AND reg=? AND status='completed' AND result_engine_signature<>?",(request,reg,engine_signature))
        return len(regs)

    def run_catchup(self):return R['_catch_up'](self.ns)

    def test_empty_population_does_not_take_writer(self):
        self.assertEqual(self.run_catchup(),0);self.no_writer();self.callback.assert_not_called()

    def test_current_completed_does_not_take_writer(self):
        self.put(engine='engine-new');before=self.rows()
        self.assertEqual(self.run_catchup(),0);self.no_writer();self.assertEqual(self.rows(),before)

    def test_pending_running_and_blocked_members_are_untouched(self):
        for i,state in enumerate(('pending','running','retry','waiting_originals','blocked')):
            self.put(reg=str(32616485753+i),status=state)
        before=self.rows();self.assertEqual(self.run_catchup(),0);self.assertEqual(self.rows(),before);self.no_writer()

    def test_other_request_is_not_reopened(self):
        self.put(request='other-request');before=self.rows()
        self.assertEqual(self.run_catchup(),0);self.assertEqual(self.rows(),before);self.no_writer()

    def test_stale_member_still_uses_authoritative_writer_and_preserves_receipts(self):
        self.put();self.assertEqual(self.run_catchup(),1)
        self.callback.assert_called_once()
        row=self.rows()[0]
        self.assertEqual(row[2],'retry');self.assertEqual(row[4:6],('retained',7))
        with closing(sqlite3.connect(self.db)) as c, c:
            self.assertEqual(c.execute('SELECT body FROM original_evidence').fetchone()[0],b'PDF')

    def test_read_connection_closed_before_writer_acquisition(self):
        self.put();self.assertEqual(self.run_catchup(),1)
        self.assertEqual(len(self.opened),2)
        with self.assertRaises(sqlite3.ProgrammingError):self.opened[0].execute('SELECT 1')

    def test_idle_turn_succeeds_while_another_writer_holds_wal_lock(self):
        self.put(engine='engine-new')
        blocker=sqlite3.connect(self.db);blocker.execute('BEGIN IMMEDIATE')
        try:self.assertEqual(self.run_catchup(),0);self.no_writer()
        finally:blocker.rollback();blocker.close()

    def test_database_read_failure_is_not_swallowed(self):
        with closing(sqlite3.connect(self.db)) as c, c:c.execute('DROP TABLE full_recheck_members')
        with self.assertRaises(sqlite3.OperationalError):self.run_catchup()
        self.callback.assert_not_called();self.no_writer()

    def test_callback_failure_rolls_back_not_success(self):
        self.put();before=self.rows()
        def fail(c,*args,**kwargs):
            c.execute("UPDATE full_recheck_members SET status='retry'")
            raise RuntimeError('test write failure')
        self.callback.side_effect=fail
        with self.assertRaisesRegex(RuntimeError,'test write failure'):self.run_catchup()
        self.assertEqual(self.rows(),before)

    def test_changed_target_is_revalidated_in_existing_ledger(self):
        self.put();before=self.rows()
        with closing(sqlite3.connect(self.db)) as c, c:c.execute("UPDATE test_target SET signature='engine-future',generation=7")
        self.assertEqual(self.run_catchup(),0);self.callback.assert_called_once();self.assertEqual(self.rows(),before)

    def test_completion_after_negative_read_is_processed_next_turn(self):
        self.put(status='running');self.assertEqual(self.run_catchup(),0)
        with closing(sqlite3.connect(self.db)) as c, c:c.execute("UPDATE full_recheck_members SET status='completed'")
        self.assertEqual(self.run_catchup(),1);self.callback.assert_called_once()

    def test_completion_corrected_before_writer_is_not_reopened(self):
        self.put();calls=0
        def hook():
            nonlocal calls
            calls+=1
            if calls==2:
                with closing(sqlite3.connect(self.db)) as c, c:c.execute("UPDATE full_recheck_members SET result_engine_signature='engine-new'")
        self.connection_hook=hook
        self.assertEqual(self.run_catchup(),0)
        self.callback.assert_called_once();self.assertEqual(self.rows()[0][2],'completed')

    def test_missing_signature_keeps_existing_validation_path(self):
        self.ns['_full_recheck'].pop('engine_signature')
        self.callback.side_effect=ValueError('existing target validation')
        with self.assertRaisesRegex(ValueError,'existing target validation'):self.run_catchup()
        self.callback.assert_called_once()

if __name__=='__main__':unittest.main(verbosity=2)
