"""Unchanged legal facts are read-only; genuine repairs keep lease fencing."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import v341_eis_facts as facts
from audit_write_guard import background_write_guard

REG = '32616434134'


class FactPersistenceContention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fact-write-safety-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fixture.sqlite'
        self.statements = []
        self.ns = {'conn': self.connect}
        with closing(self.connect()) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,
                method TEXT,submission_end TEXT,deadline TEXT,price TEXT,card_fields_json TEXT);
                CREATE TABLE findings(reg TEXT,body TEXT);
                INSERT INTO findings VALUES('32616434134','preserved evidence');''')
            c.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?)', (REG, '', '', '', '', '{"other":{"keep":1}}'))
            c.commit()
        self.purchase = {'reg': REG, 'method': 'Электронный конкурс', 'fact_evidence':
                         {'method': {'source': 'official_eis_card', 'value': 'Электронный конкурс'}}}

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.03)
        c.row_factory = sqlite3.Row
        c.set_trace_callback(self.statements.append)
        return c

    def row(self):
        with closing(self.connect()) as c:
            return dict(c.execute('SELECT * FROM purchases WHERE reg_number=?', (REG,)).fetchone())

    def test_repeated_equal_evidence_does_not_acquire_writer_under_real_contention(self):
        facts.persist_facts(self.ns, self.purchase)
        before = self.row()
        with closing(self.connect()) as owner:
            owner.execute('BEGIN IMMEDIATE')
            owner.execute("INSERT INTO findings VALUES('other','in-flight')")
            self.statements.clear()
            facts.persist_facts(self.ns, self.purchase)
            observed = list(self.statements)
            owner.rollback()
        self.assertFalse(any('BEGIN IMMEDIATE' in sql or sql.startswith('UPDATE') for sql in observed), observed)
        self.assertEqual(self.row(), before)
        with closing(self.connect()) as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM findings')], [(REG, 'preserved evidence')])

    def test_equal_json_formatting_does_not_create_a_writer(self):
        facts.persist_facts(self.ns, self.purchase)
        with closing(self.connect()) as c:
            card = json.loads(self.row()['card_fields_json'])
            c.execute('UPDATE purchases SET card_fields_json=?', (json.dumps(card, sort_keys=True, indent=4),))
            c.commit()
        before = self.row()
        self.statements.clear()
        facts.persist_facts(self.ns, self.purchase)
        self.assertFalse(any('BEGIN IMMEDIATE' in sql for sql in self.statements))
        self.assertEqual(self.row(), before)

    def test_plan_runs_outside_writer_and_concurrent_correction_is_not_lost(self):
        original = facts._facts_plan
        count = []
        def racing(row, purchase, evidence):
            count.append(1)
            if len(count) == 1:
                with closing(self.connect()) as c:
                    c.execute('BEGIN IMMEDIATE')
                    card = json.loads(row['card_fields_json'])
                    card['new_external_field'] = 'preserve concurrent import'
                    c.execute('UPDATE purchases SET card_fields_json=?', (json.dumps(card),))
                    c.commit()
            return original(row, purchase, evidence)
        with patch.object(facts, '_facts_plan', side_effect=racing):
            facts.persist_facts(self.ns, self.purchase)
        self.assertEqual(len(count), 2)
        row = self.row()
        self.assertEqual(row['method'], 'Электронный конкурс')
        self.assertEqual(json.loads(row['card_fields_json'])['new_external_field'], 'preserve concurrent import')

    def test_stale_lease_fails_before_mutation(self):
        before = self.row()
        def denied(c):
            self.assertTrue(c.in_transaction)
            raise LookupError('stale attempt')
        with background_write_guard(sql_guard=denied):
            with self.assertRaisesRegex(LookupError, 'stale attempt'):
                facts.persist_facts(self.ns, self.purchase)
        self.assertEqual(self.row(), before)

    def test_xml_cache_never_overwrites_authoritative_purchase_scalar(self):
        p = {**self.purchase, 'fact_evidence': {'method':
             {'source': 'official_eis_xml', 'value': 'XML конкурс'}}}
        facts.persist_facts(self.ns, p)
        self.assertEqual(self.row()['method'], '')
        self.assertEqual(json.loads(self.row()['card_fields_json'])['fact_evidence'], p['fact_evidence'])

    def test_actual_deadline_repair_preserves_unrelated_data_and_is_idempotent(self):
        p = {'reg': REG, 'submission_end': '2026-10-30', 'fact_evidence':
             {'submission_end': {'source': 'official_eis_card', 'value': '2026-10-30'}}}
        facts.persist_facts(self.ns, p)
        row = self.row()
        self.assertEqual((row['submission_end'], row['deadline']), ('2026-10-30', '2026-10-30'))
        self.assertEqual(json.loads(row['card_fields_json'])['other'], {'keep': 1})
        facts.persist_facts(self.ns, p)
        self.assertEqual(self.row(), row)

    def test_deadline_alias_is_repaired_even_when_submission_end_is_unchanged(self):
        with closing(self.connect()) as c:
            c.execute("UPDATE purchases SET submission_end='2026-10-30',deadline='2026-10-20'")
            c.commit()
        facts.persist_facts(self.ns, {'reg': REG, 'submission_end': '2026-10-30', 'fact_evidence':
            {'submission_end': {'source': 'official_eis_card', 'value': '2026-10-30'}}})
        self.assertEqual(self.row()['deadline'], '2026-10-30')

    def test_hot_row_rebase_is_bounded_and_never_commits_stale_plan(self):
        original = facts._facts_plan
        count = []
        def racing(row, purchase, evidence):
            count.append(1)
            with closing(self.connect()) as c:
                c.execute('UPDATE purchases SET price=?', (str(len(count)),))
                c.commit()
            return original(row, purchase, evidence)
        with patch.object(facts, '_facts_plan', side_effect=racing):
            with self.assertRaises(sqlite3.OperationalError):
                facts.persist_facts(self.ns, self.purchase)
        self.assertEqual(len(count), 4)
        self.assertEqual(self.row()['method'], '')
        self.assertEqual(self.row()['price'], '4')

    def test_malformed_optional_cache_is_repaired_without_overwriting_other_scalars(self):
        with closing(self.connect()) as c:
            c.execute('UPDATE purchases SET card_fields_json=?,price=?', ('{"summary":[],"fact_evidence":null}', '100'))
            c.commit()
        facts.persist_facts(self.ns, self.purchase)
        row = self.row()
        self.assertEqual(row['price'], '100')
        self.assertEqual(json.loads(row['card_fields_json'])['summary']['method'], self.purchase['method'])


if __name__ == '__main__':
    unittest.main()
