"""Real temporary SQLite databases: no production data or remote requests."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import full_recheck_request as ledger
import full_recheck_runtime as runtime
import v314_eis_metadata as metadata
from test_v314_eis_metadata import database, URL
from audit_write_guard import background_write_guard

REG = '32616467075'
EVIDENCE = {'customer':'Государственное автономное учреждение «Агентство»',
            'customer_inn':'6501309746','title':'Оказание услуг по организации мероприятий','source_url':URL}

class MetadataNoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ns = database(Path(self.tmp.name)); self.trace = []
        original = self.ns['conn']
        def conn():
            db = original(); db.execute('PRAGMA busy_timeout=30'); db.set_trace_callback(self.trace.append); return db
        self.ns['conn'] = conn

    def writes(self):
        return [s for s in self.trace if s.upper().startswith(('BEGIN IMMEDIATE', 'INSERT', 'UPDATE', 'DELETE', 'CREATE'))]

    def test_repeated_metadata_remains_read_only_under_another_writer(self):
        self.assertTrue(metadata.apply_metadata(self.ns, REG, EVIDENCE))
        with closing(self.ns['conn']()) as writer:
            writer.execute('BEGIN IMMEDIATE'); self.trace.clear()
            self.assertFalse(metadata.apply_metadata(self.ns, REG, EVIDENCE))
            self.assertEqual(self.writes(), [])
            writer.rollback()

    def test_noop_keeps_capture_timestamp_and_findings(self):
        metadata.apply_metadata(self.ns, REG, EVIDENCE)
        with closing(self.ns['conn']()) as db:
            before = tuple(db.execute('SELECT * FROM v314_eis_metadata').fetchone())
        metadata.apply_metadata(self.ns, REG, dict(reversed(list(EVIDENCE.items()))))
        with closing(self.ns['conn']()) as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM v314_eis_metadata').fetchone()), before)
            self.assertEqual(db.execute('SELECT evidence FROM findings').fetchone()[0], 'existing finding')

    def test_new_evidence_is_saved_without_counting_as_row_repair(self):
        metadata.apply_metadata(self.ns, REG, EVIDENCE)
        changed = {**EVIDENCE, 'document':'another verified original'}
        self.assertFalse(metadata.apply_metadata(self.ns, REG, changed))
        with closing(self.ns['conn']()) as db:
            self.assertEqual(json.loads(db.execute('SELECT metadata_json FROM v314_eis_metadata').fetchone()[0]), changed)

    def test_planning_releases_reader_and_does_not_hold_writer(self):
        original = metadata._metadata_plan
        def plan(*args):
            with closing(self.ns['conn']()) as db:
                db.execute('UPDATE findings SET evidence=evidence'); db.commit()
            return original(*args)
        with patch.object(metadata, '_metadata_plan', side_effect=plan):
            self.assertTrue(metadata.apply_metadata(self.ns, REG, EVIDENCE))

    def test_racing_authoritative_correction_is_not_overwritten(self):
        original = metadata._metadata_plan; changed = []
        def plan(*args):
            if not changed:
                changed.append(True)
                with closing(self.ns['conn']()) as db:
                    db.execute('UPDATE purchases SET customer=?,customer_inn=?,title=?', ('ООО «Другой»','7707083893','Поставка реагентов')); db.commit()
            return original(*args)
        with patch.object(metadata, '_metadata_plan', side_effect=plan):
            self.assertFalse(metadata.apply_metadata(self.ns, REG, EVIDENCE))
        with closing(self.ns['conn']()) as db:
            self.assertEqual(tuple(db.execute('SELECT customer,customer_inn,title FROM purchases').fetchone()), ('ООО «Другой»','7707083893','Поставка реагентов'))

    def test_active_write_fence_is_not_bypassed(self):
        def stale(_): raise LookupError('superseded')
        with background_write_guard(stale), self.assertRaises(LookupError):
            metadata.apply_metadata(self.ns, REG, EVIDENCE)
        with closing(self.ns['conn']()) as db:
            self.assertIsNone(db.execute('SELECT customer FROM purchases').fetchone()[0])

    def test_real_change_under_busy_writer_is_not_false_success(self):
        with closing(self.ns['conn']()) as holder:
            holder.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):
                metadata.apply_metadata(self.ns, REG, EVIDENCE)
            holder.rollback()
        self.assertTrue(metadata.apply_metadata(self.ns, REG, EVIDENCE))

    def test_missing_registration_never_reserves_writer(self):
        self.assertFalse(metadata.apply_metadata(self.ns, '32619999999', EVIDENCE))
        self.assertEqual(self.writes(), [])

class AllSavedAndLease(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'all.sqlite'; self.trace=[]
        with closing(self.conn()) as db:
            db.execute('CREATE TABLE purchases(reg_number TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO purchases VALUES(?)', [(REG,), ('32610000001',), ('manual-no-eis-number',)])
            db.commit()
        self.ns={'conn': self.conn}
        self.epoch=runtime.ALL_SAVED_EPOCH
        with closing(self.conn()) as db:
            self.summary=ledger.create_request(db,self.epoch,None,all_saved=True,expected_count=None)
            db.execute('BEGIN IMMEDIATE')
            self.claim=ledger.claim_member(db,self.epoch,REG,now=1000,lease_seconds=300)
            db.commit()
        self.reg=SimpleNamespace(full_recheck_claim=self.claim)
        self.trace.clear()

    def conn(self):
        db=sqlite3.connect(self.path,timeout=.03);db.row_factory=sqlite3.Row;db.set_trace_callback(self.trace.append);return db

    def member(self):
        with closing(self.conn()) as db:return dict(db.execute('SELECT * FROM full_recheck_members WHERE reg=?',(REG,)).fetchone())

    def test_all_saved_has_no_dashboard_dependency_and_blocks_invalid_reg(self):
        self.assertEqual(self.summary['actual_count'],3)
        self.assertEqual(self.summary['expected_count'],3)
        self.assertEqual(self.summary['states']['pending'],2)
        self.assertEqual(self.summary['states']['blocked'],1)
        self.assertEqual(self.summary['manual_excluded_count'],0)

    def test_replay_does_not_extend_snapshot_or_reset_running_member(self):
        before=self.member()
        with closing(self.conn()) as db:
            db.execute('INSERT INTO purchases VALUES(?)',('32610000002',));db.commit()
            replay=ledger.create_request(db,self.epoch,None,all_saved=True,expected_count=None)
        self.assertEqual(replay['actual_count'],3)
        self.assertEqual(self.member(),before)

    def test_scope_includes_manual_entries_and_saved_rows_outside_old_queue(self):
        with closing(self.conn()) as db:
            db.execute('CREATE TABLE v319_manual_inputs(reg TEXT)');db.execute('INSERT INTO v319_manual_inputs VALUES(?)',(REG,));db.commit()
            getter=Mock(side_effect=AssertionError('dashboard must not be called'))
            result=ledger.create_request(db,self.epoch+'-2',getter,all_saved=True,expected_count=None)
        getter.assert_not_called()
        self.assertEqual(result['actual_count'],3);self.assertEqual(result['manual_excluded_count'],0)

    def test_budget_failure_never_creates_partial_request(self):
        with closing(self.conn()) as db:
            with self.assertRaises(ledger.SnapshotError):
                ledger.create_request(db,self.epoch+'-small',None,all_saved=True,expected_count=None,max_rows=2)
            self.assertIsNone(ledger.inventory(db,self.epoch+'-small'))

    def test_healthy_lease_tick_does_not_need_writer(self):
        before=self.member()
        with closing(self.conn()) as holder:
            holder.execute('BEGIN IMMEDIATE');self.trace.clear()
            with patch.object(runtime.time,'time',return_value=1030):
                self.assertFalse(runtime._renew_if_due(self.ns,self.reg))
            self.assertFalse(any(s.startswith('BEGIN IMMEDIATE') for s in self.trace))
            holder.rollback()
        self.assertEqual(before,self.member())

    def test_due_lease_extends_same_owned_attempt(self):
        with patch.object(runtime.time,'time',return_value=1160):
            self.assertTrue(runtime._renew_if_due(self.ns,self.reg))
        row=self.member();self.assertEqual(row['lease_until'],1460)
        self.assertEqual(row['lease_token'],self.claim.lease_token);self.assertEqual(row['attempt_count'],1)

    def test_expired_lease_is_not_revived(self):
        with patch.object(runtime.time,'time',return_value=1301), self.assertRaises(ledger.StaleClaim):
            runtime._renew_if_due(self.ns,self.reg)
        self.assertEqual(self.member()['lease_until'],1300)

    def test_superseded_lease_is_never_renewed(self):
        with closing(self.conn()) as db:
            db.execute('UPDATE full_recheck_members SET lease_token=? WHERE reg=?',('different-owner',REG));db.commit()
        with patch.object(runtime.time,'time',return_value=1160), self.assertRaises(ledger.StaleClaim):
            runtime._renew_if_due(self.ns,self.reg)
        self.assertEqual(self.member()['lease_until'],1300)

if __name__=='__main__': unittest.main()
