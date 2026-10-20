"""Real SQLite writer contention: repeated evidence stays read-only, repairs fence."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import v314_eis_metadata as metadata
import v298_eis_only as soap
import full_recheck_runtime as runtime
from audit_write_guard import background_write_guard
from test_eis223_soap_archive import fixture, REG
from eis223_soap_xml import parse_document

NAME = 'Общество с ограниченной ответственностью «Тест»'
INN = '7707083893'
SOURCE = 'https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='+'A'*32


class MetadataContention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'fixture.sqlite'
        self.trace, self.opened = [], []
        self.ns = {'conn': self.connect, 'org_form': lambda name: 'ООО'}
        self.evidence = {'customer': NAME, 'customer_inn': INN, 'source_url': SOURCE,
                         'title': 'Поставка тестовых материалов'}
        with closing(self.connect()) as c:
            c.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, title TEXT,customer TEXT,
                customer_inn TEXT,org_form TEXT,card_fields_json TEXT,price TEXT,price_num REAL,
                method TEXT,published_at TEXT,submission_start TEXT,submission_end TEXT,deadline TEXT);
            CREATE TABLE v311_verified_buyers(reg TEXT PRIMARY KEY,name TEXT,inn TEXT,
                evidence_page TEXT,evidence_source TEXT,captured_at TEXT);
            CREATE TABLE findings(reg_number TEXT,evidence TEXT);
            CREATE TABLE intake(id INTEGER);
            INSERT INTO findings VALUES('32616233661','existing legal finding');''')
            c.execute('INSERT INTO purchases(reg_number,title,customer,customer_inn,org_form,card_fields_json) '
                      'VALUES(?,?,?,?,?,?)', (REG, self.evidence['title'], NAME, INN, 'ООО', '{"keep":"evidence"}'))
            c.commit()
        self.trace.clear()

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.04); c.row_factory = sqlite3.Row
        c.set_trace_callback(self.trace.append); self.opened.append(c)
        return c

    def snapshot(self):
        with closing(self.connect()) as c:
            return list(c.iterdump())

    def assert_no_write(self):
        self.assertFalse(any(s.startswith(('BEGIN IMMEDIATE', 'UPDATE', 'INSERT', 'CREATE', 'DELETE'))
                             for s in self.trace), self.trace)

    def assert_all_closed(self):
        for c in self.opened:
            with self.assertRaises(sqlite3.ProgrammingError): c.execute('SELECT 1')

    def test_duplicate_evidence_is_read_only_even_with_another_writer(self):
        self.assertFalse(metadata.apply_metadata(self.ns, REG, self.evidence))
        before = self.snapshot()
        with closing(self.connect()) as holder:
            holder.execute('BEGIN IMMEDIATE'); holder.execute('INSERT INTO intake VALUES(1)')
            self.trace.clear()
            self.assertFalse(metadata.apply_metadata(self.ns, REG, dict(reversed(list(self.evidence.items())))))
            self.assert_no_write()
            holder.rollback()
        self.assertEqual(self.snapshot(), before)
        self.assert_all_closed()

    def test_missing_purchase_does_not_create_evidence_table_or_reserve_writer(self):
        self.trace.clear()
        self.assertFalse(metadata.apply_metadata(self.ns, '32616233662', self.evidence))
        self.assert_no_write(); self.assert_all_closed()

    def test_changed_evidence_is_saved_without_inventing_a_purchase_repair(self):
        self.assertFalse(metadata.apply_metadata(self.ns, REG, self.evidence))
        new = {**self.evidence, 'document': 'Updated source.docx'}
        self.assertFalse(metadata.apply_metadata(self.ns, REG, new))
        with closing(self.connect()) as c:
            self.assertEqual(json.loads(c.execute('SELECT metadata_json FROM v314_eis_metadata').fetchone()[0]), new)
            self.assertEqual(c.execute('SELECT evidence FROM findings').fetchone()[0], 'existing legal finding')
        self.assert_all_closed()

    def test_real_write_still_fails_busy_without_claiming_success(self):
        metadata.apply_metadata(self.ns, REG, self.evidence)
        before = self.snapshot()
        with closing(self.connect()) as holder:
            holder.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                metadata.apply_metadata(self.ns, REG, {**self.evidence, 'document': 'New.docx'})
            holder.rollback()
        self.assertEqual(self.snapshot(), before)
        self.assert_all_closed()

    def test_planning_does_not_hold_a_reader_or_writer_and_rebases_racing_intake(self):
        with closing(self.connect()) as c:
            c.execute("UPDATE purchases SET customer='',customer_inn='',org_form='' ")
            c.commit()
        real = metadata._metadata_plan
        calls = []
        def plan(*args):
            calls.append(True)
            # This independent writer must succeed before any repair acquires
            # SQLite. Preserve the concurrent verified correction on rebase.
            if len(calls) == 1:
                with closing(self.connect()) as c:
                    c.execute('UPDATE purchases SET customer=?,customer_inn=?,org_form=?',
                              ('ООО «Другой»', '6501309746', 'ООО'))
                    c.commit()
            return real(*args)
        with patch.object(metadata, '_metadata_plan', side_effect=plan):
            self.assertFalse(metadata.apply_metadata(self.ns, REG, self.evidence))
        self.assertEqual(len(calls), 2)
        with closing(self.connect()) as c:
            self.assertEqual(tuple(c.execute('SELECT customer,customer_inn FROM purchases').fetchone()),
                             ('ООО «Другой»', '6501309746'))
        self.assert_all_closed()

    def test_changed_buyer_provenance_between_phases_is_not_overwritten(self):
        metadata.apply_metadata(self.ns, REG, self.evidence)
        with closing(self.connect()) as c:
            c.execute("UPDATE purchases SET customer_inn='' ")
            c.execute("UPDATE v311_verified_buyers SET inn='' "); c.commit()
        real = metadata._metadata_plan
        once = []
        def race(*args):
            if not once:
                once.append(True)
                with closing(self.connect()) as c:
                    c.execute('UPDATE v311_verified_buyers SET inn=?,evidence_source=?',
                              ('6501309746', 'official_eis_card'))
                    c.commit()
            return real(*args)
        with patch.object(metadata, '_metadata_plan', side_effect=race):
            metadata.apply_metadata(self.ns, REG, self.evidence)
        with closing(self.connect()) as c:
            self.assertEqual(tuple(c.execute('SELECT inn,evidence_source FROM v311_verified_buyers').fetchone()),
                             ('6501309746', 'official_eis_card'))
        self.assert_all_closed()

    def test_stale_attempt_fails_before_any_actual_mutation(self):
        before = self.snapshot()
        seen = []
        def fence(c):
            self.assertTrue(c.in_transaction); seen.append(True)
            raise LookupError('stale lease')
        with background_write_guard(sql_guard=fence):
            with self.assertRaisesRegex(LookupError, 'stale lease'):
                metadata.apply_metadata(self.ns, REG, self.evidence)
        self.assertEqual(seen, [True]); self.assertEqual(self.snapshot(), before)
        self.assert_all_closed()

    def test_concurrent_deletion_does_not_recreate_a_purchase(self):
        real = metadata._metadata_plan
        def plan(*args):
            with closing(self.connect()) as c:
                c.execute('DELETE FROM purchases'); c.commit()
            return real(*args)
        with patch.object(metadata, '_metadata_plan', side_effect=plan):
            self.assertFalse(metadata.apply_metadata(self.ns, REG, self.evidence))
        with closing(self.connect()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM purchases').fetchone()[0], 0)
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='v314_eis_metadata'").fetchone())
        self.assert_all_closed()

    def test_continuously_changed_rows_exhaust_bounded_rebase_without_stale_write(self):
        real = metadata._metadata_plan
        calls = []
        def race(*args):
            calls.append(True)
            with closing(self.connect()) as c:
                c.execute('UPDATE purchases SET title=?', ('Поставка '+str(len(calls)),)); c.commit()
            return real(*args)
        with patch.object(metadata, '_metadata_plan', side_effect=race):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                metadata.apply_metadata(self.ns, REG, self.evidence)
        self.assertEqual(len(calls), 4)
        with closing(self.connect()) as c:
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='v314_eis_metadata'").fetchone())
        self.assert_all_closed()

    def soap_record(self, version):
        filename, raw = fixture(version=version)
        return parse_document(raw, filename)

    def test_duplicate_and_older_soap_revisions_need_no_writer(self):
        current = self.soap_record(3)
        with patch.object(soap, 'official_xml_documents', return_value=[current]):
            self.assertTrue(soap.import_soap_metadata(self.ns, REG)['changed'])
        before = self.snapshot()
        with closing(self.connect()) as holder:
            holder.execute('BEGIN IMMEDIATE')
            for record in (current, self.soap_record(2)):
                self.trace.clear()
                with patch.object(soap, 'official_xml_documents', return_value=[record]):
                    self.assertFalse(soap.import_soap_metadata(self.ns, REG)['changed'])
                self.assert_no_write()
            holder.rollback()
        self.assertEqual(self.snapshot(), before); self.assert_all_closed()

    def test_newer_soap_revision_still_waits_for_a_real_writer(self):
        with patch.object(soap, 'official_xml_documents', return_value=[self.soap_record(1)]):
            soap.import_soap_metadata(self.ns, REG)
        before = self.snapshot()
        with closing(self.connect()) as holder:
            holder.execute('BEGIN IMMEDIATE')
            with patch.object(soap, 'official_xml_documents', return_value=[self.soap_record(2)]):
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    soap.import_soap_metadata(self.ns, REG)
            holder.rollback()
        self.assertEqual(self.snapshot(), before)

    def test_racing_newer_soap_revision_wins_in_transaction(self):
        version3 = self.soap_record(3)[0]
        calls = []
        def conn():
            calls.append(True)
            if len(calls) == 2:
                with closing(self.connect()) as c:
                    c.execute('UPDATE purchases SET card_fields_json=?,title=?',
                              (json.dumps({'soap_metadata': version3}), 'Поставка новой редакции'))
                    c.commit()
            return self.connect()
        with patch.object(soap, 'official_xml_documents', return_value=[self.soap_record(2)]):
            self.assertFalse(soap.import_soap_metadata({**self.ns, 'conn': conn}, REG)['changed'])
        with closing(self.connect()) as c:
            self.assertEqual(c.execute('SELECT title FROM purchases').fetchone()[0], 'Поставка новой редакции')
        self.assert_all_closed()

    def test_empty_full_request_catchup_is_read_only_under_writer(self):
        with closing(self.connect()) as c:
            c.execute('CREATE TABLE full_recheck_members(request_id TEXT,status TEXT,result_engine_signature TEXT)')
            c.execute('INSERT INTO full_recheck_members VALUES(?,?,?)', ('request','completed','current'))
            c.commit()
        ns = {**self.ns, '_full_recheck': {'request_id':'request','engine_signature':'current','target_generation':6}}
        with closing(self.connect()) as holder:
            holder.execute('BEGIN IMMEDIATE'); self.trace.clear()
            self.assertEqual(runtime._catch_up(ns), 0); self.assert_no_write()
            holder.rollback()
        self.assert_all_closed()


if __name__ == '__main__': unittest.main()
