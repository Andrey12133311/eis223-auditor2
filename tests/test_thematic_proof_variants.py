"""A source-exact current proof must survive a historical display UNIQUE key."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_legal_finding_reassessment as fixture
import payment_finding_reassessment as assessment
import v345_thematic_accelerator as fast
import v384_thematic_findings as thematic

REG, BATCH = fixture.REG, fixture.BATCH


class ThematicProofVariants(unittest.TestCase):
    connect = fixture.LegalFindingReassessmentTests.connect
    connection = fixture.LegalFindingReassessmentTests.connection
    retained = fixture.LegalFindingReassessmentTests.retained
    event = fixture.LegalFindingReassessmentTests.event
    record = fixture.LegalFindingReassessmentTests.record
    item = fixture.LegalFindingReassessmentTests.item

    def setUp(self):
        fixture.LegalFindingReassessmentTests.setUp(self)
        self.legal['category'] = 'protocol_compliance'
        with self.connection() as db:
            db.execute('UPDATE thematic_findings SET category=?', (self.legal['category'],))
            db.execute('UPDATE thematic_batches SET categories=?',
                       (json.dumps([self.legal['category']]),))
        self.fresh = {**self.legal, 'document': 'Действующий протокол.pdf',
            'source_url': self.legal['source_url'].replace('1'*32, '2'*32)}

    def save(self, findings, state='completed'):
        return fast._persist(self.ns, [(self.item(), state, findings, 'source-exact refresh')])

    def test_changed_source_keeps_current_proof_and_old_history(self):
        before = self.retained()
        self.assertNotEqual(assessment.evidence_key(self.legal), assessment.evidence_key(self.fresh))
        self.assertEqual(1, self.save([self.fresh]))
        with self.connection() as db:
            rows = thematic.read_thematic_items(db, BATCH, REG)
            confirmed = [row for row in rows if row['effective_status'] == 'confirmed']
            self.assertEqual([self.fresh['document']], [row['document'] for row in confirmed])
            self.assertEqual([self.legal['document']], [row['document'] for row in rows
                             if row['effective_status'] == 'requires_review'])
        self.assertEqual(before, self.retained())

    def test_repeat_does_not_append_another_representation(self):
        import thematic_proof_variants as variants
        self.assertEqual(1, self.save([self.fresh, deepcopy(self.fresh)]))
        with self.connection() as db:
            once = [dict(row) for row in db.execute('SELECT * FROM '+variants.TABLE)]
        self.assertEqual(len(once), 1)
        self.assertEqual(1, self.save([self.fresh]))
        with self.connection() as db:
            self.assertEqual(once, [dict(row) for row in db.execute('SELECT * FROM '+variants.TABLE)])

    def test_two_current_representations_still_count_as_one_legal_act(self):
        self.assertEqual(1, self.save([self.legal, self.fresh]))
        with self.connection() as db:
            rows = thematic.read_thematic_items(db, BATCH, REG)
            self.assertEqual(sum(row['effective_status'] == 'confirmed' for row in rows), 1)
            self.assertEqual(rows[0]['source_representations'], 2)

    def test_same_quote_does_not_discard_a_changed_legal_representation(self):
        import thematic_proof_variants as variants
        alternate = {**self.legal, 'title': 'Уточнённая квалификация того же факта',
                     'law': '223-ФЗ, ст. 3.2 ч. 13'}
        self.assertEqual(1, self.save([self.legal, alternate]))
        with self.connection() as db:
            rows = variants.raw_rows(db, BATCH, REG)
            self.assertEqual({row['title'] for row in rows},
                             {self.legal['title'], alternate['title']})
            self.assertEqual({row['law'] for row in rows}, {self.legal['law'], alternate['law']})

    def test_failure_rolls_back_variant_journal_and_result_marker(self):
        import thematic_proof_variants as variants
        old = self.retained()
        item = self.item()
        with patch.object(assessment, 'record_assessment', side_effect=sqlite3.OperationalError('fixture abort')):
            with self.assertRaises(sqlite3.OperationalError):
                fast._persist(self.ns, [(item, 'completed', [self.fresh], 'must roll back')])
        with self.connection() as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM '+variants.TABLE).fetchone()[0])
            marker = db.execute('SELECT status,reservation_token FROM thematic_items WHERE batch_id=? AND reg=?',
                                (BATCH, REG)).fetchone()
            self.assertEqual(tuple(marker), ('running', 'lease-a'))
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM '+assessment.TABLE).fetchone()[0])
        self.assertEqual(old, self.retained())

    def test_changed_reservation_cannot_append_an_orphan_variant(self):
        import thematic_proof_variants as variants
        item = self.item()
        with self.connection() as db:
            db.execute("UPDATE thematic_items SET reservation_token='new-owner' WHERE batch_id=? AND reg=?",
                       (BATCH, REG))
        self.assertEqual(0, fast._persist(self.ns, [(item, 'completed', [self.fresh], 'stale worker')]))
        with self.connection() as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM '+variants.TABLE).fetchone()[0])

    def test_source_variant_change_rebases_the_snapshot_before_writing(self):
        import thematic_proof_variants as variants
        concurrent = {**self.fresh, 'document': 'Второй действующий оригинал.pdf',
            'source_url': self.fresh['source_url'].replace('2'*32, '3'*32)}
        original_plan = fast._plan_thematic_results
        calls = []

        def interleave(*args, **kwargs):
            planned = original_plan(*args, **kwargs)
            calls.append(True)
            if len(calls) == 1:
                with self.connection() as db:
                    self.assertTrue(variants.insert_variant(db, BATCH, REG, concurrent))
            return planned

        with patch.object(fast, '_plan_thematic_results', side_effect=interleave):
            self.assertEqual(1, self.save([self.fresh, concurrent]))
        self.assertGreaterEqual(len(calls), 2)
        with self.connection() as db:
            stored = [dict(row) for row in db.execute('SELECT * FROM '+variants.TABLE)]
        self.assertEqual({row['document'] for row in stored},
                         {self.fresh['document'], concurrent['document']})

    def test_unregistered_copy_keeps_existing_semantic_deduplication(self):
        import thematic_proof_variants as variants
        with self.connection() as db:
            db.execute("UPDATE thematic_findings SET code='external-unregistered-rule'")
        fresh = {**self.fresh, 'code': 'external-unregistered-rule'}
        self.assertEqual(1, self.save([fresh]))
        with self.connection() as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM '+variants.TABLE).fetchone()[0])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM thematic_findings').fetchone()[0])

    def test_partial_pass_cannot_confirm_an_appended_current_representation(self):
        self.assertEqual(1, self.save([self.fresh], state='retry'))
        with self.connection() as db:
            rows = thematic.read_thematic_items(db, BATCH, REG)
        self.assertEqual(0, sum(row['effective_status'] == 'confirmed' for row in rows))

    def test_missing_startup_store_is_not_created_inside_the_writer(self):
        import thematic_proof_variants as variants
        old = self.retained()
        with self.connection() as db:
            db.execute('DROP TABLE '+variants.TABLE)
        with self.assertRaises(sqlite3.OperationalError):
            self.save([self.fresh])
        with self.connection() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name=?", (variants.TABLE,)).fetchone())
        self.assertEqual(old, self.retained())

    def test_writer_does_not_perform_schema_changes(self):
        traced = []
        connection = self.connect

        def trace_connection():
            db = connection()
            db.set_trace_callback(traced.append)
            return db

        with patch.dict(self.ns, {'conn': trace_connection}):
            self.assertEqual(1, self.save([self.fresh]))
        ddl = [sql for sql in traced if sql.lstrip().split(None, 1)[0].upper() in
               {'CREATE', 'DROP', 'ALTER', 'REINDEX'}]
        self.assertEqual([], ddl)


class ProofVariantStorage(unittest.TestCase):
    def test_read_only_legacy_database_does_not_need_a_new_table(self):
        import thematic_proof_variants as variants
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE thematic_findings(batch_id TEXT,reg TEXT,'+
                       ','.join(field+' TEXT' for field in variants.FIELDS)+')')
            db.execute('INSERT INTO thematic_findings(batch_id,reg,title) VALUES(?,?,?)',
                       (BATCH, REG, 'Историческое доказательство'))
            db.commit()
            db.execute('PRAGMA query_only=ON')
            rows = variants.raw_rows(db, BATCH, REG)
            self.assertEqual([row['title'] for row in rows], ['Историческое доказательство'])
            self.assertEqual(variants.revision(db, BATCH), (1, 1, 0, 0))
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name=?", (variants.TABLE,)).fetchone())

    def test_representation_key_keeps_legal_and_source_fields_but_ignores_sql_metadata(self):
        import thematic_proof_variants as variants
        base = fixture.PAYMENT
        expected = variants.storage_key(base)
        for changed in (
            {**base, 'title': 'Другое установленное условие'},
            {**base, 'law': '223-ФЗ, ст. 3.2 ч. 14'},
            {**base, 'document': 'Другой оригинал.pdf'},
            {**base, 'source_url': base['source_url']+'&source=2'},
            {**base, 'category': 'payment_223'},
        ):
            self.assertNotEqual(expected, variants.storage_key(changed))
        self.assertEqual(expected, variants.storage_key({**base, '_source_rowid': 100,
                                                       '_source_storage': 'variant'}))

    def test_removing_a_queue_item_does_not_cascade_delete_proof_history(self):
        import thematic_proof_variants as variants
        with sqlite3.connect(':memory:') as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,PRIMARY KEY(batch_id,reg))')
            variants.initialize(db)
            db.execute('INSERT INTO thematic_items VALUES(?,?)', (BATCH, REG))
            self.assertTrue(variants.insert_variant(db, BATCH, REG, fixture.PAYMENT))
            db.execute('DELETE FROM thematic_items WHERE batch_id=? AND reg=?', (BATCH, REG))
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM '+variants.TABLE).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
