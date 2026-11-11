"""Exact thematic source variants remain readable without inheriting decisions."""
from contextlib import contextmanager
import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import payment_finding_reassessment as assessment
import v342_thematic_queue as queue
import v384_thematic_findings as sources
import thematic_proof_variants as variants
import test_legal_finding_reassessment as legal_fixture
from test_payment_finding_reassessment import BATCH, REG
from scripts import v385_msp_reassessment as msp


class ThematicVariantReaders(unittest.TestCase):
    def setUp(self):
        self.case = legal_fixture.LegalFindingReassessmentTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.ns = self.case.ns
        self.old = dict(self.case.legal)
        self.fresh = {**self.old, 'document': 'Актуальный протокол.pdf',
            'source_url': self.old['source_url'] + '&revision=2'}
        with self.case.connection() as db:
            db.execute('UPDATE thematic_batches SET categories=? WHERE id=?',
                       (json.dumps(['protocols']), BATCH))
            variants.initialize(db)
        self.base_history = self.case.retained()

    def seed(self, *items):
        with self.case.connection() as db:
            for item in items:
                variants.insert_variant(db, BATCH, REG, item)

    def current(self, *items):
        self.case.record(list(items), 'thematic')

    def read(self):
        with self.case.connection() as db:
            return sources.read_thematic_items(db, BATCH, REG)

    def test_fresh_variant_is_current_and_old_source_history_stays_review(self):
        self.seed(self.fresh)
        self.current(self.fresh)
        actual = self.read()
        self.assertEqual(2, len(actual))
        by_name = {item['document']: item for item in actual}
        self.assertEqual('confirmed', by_name[self.fresh['document']]['effective_status'])
        self.assertEqual('requires_review', by_name[self.old['document']]['effective_status'])
        self.assertEqual(1, sum(item['effective_status'] == 'confirmed' for item in actual))
        self.assertEqual(self.base_history, self.case.retained())

    def test_current_copies_count_once_but_each_exact_key_remains_addressable(self):
        self.seed(self.fresh)
        self.current(self.old, self.fresh)
        self.assertEqual(1, len(self.read()))
        keys = {sources.canonical_item(REG, item)['finding_key'] for item in (self.old, self.fresh)}
        with self.case.connection() as db:
            exact = sources.read_thematic_exact_items(db, BATCH, REG, keys)
        self.assertEqual(keys, {item['finding_key'] for item in exact})
        self.assertTrue(all(item['effective_status'] == 'confirmed' for item in exact))
        self.assertEqual(self.base_history, self.case.retained())

    def test_exact_key_does_not_cross_purchase_batch_or_selected_category(self):
        self.seed(self.fresh, {**self.fresh, 'category': 'national', 'document': 'Исключённый.pdf'})
        self.current(self.old, self.fresh)
        excluded = sources.canonical_item(REG, {**self.fresh, 'category': 'national',
                                               'document': 'Исключённый.pdf'})['finding_key']
        with self.case.connection() as db:
            self.assertEqual([], sources.read_thematic_exact_items(db, BATCH, REG, [excluded]))
            self.assertEqual([], sources.read_thematic_exact_items(db, BATCH, REG, ['f' * 64]))
            with self.assertRaises(HTTPException) as failure:
                sources.read_thematic_exact_items(db, 'b' * 24, REG)
            self.assertEqual(404, failure.exception.status_code)
            with self.assertRaises(HTTPException) as failure:
                sources.read_thematic_exact_items(db, BATCH, '32611111111')
            self.assertEqual(404, failure.exception.status_code)

    def test_read_only_legacy_database_needs_no_variant_schema_migration(self):
        self.current(self.old)
        expected = sources.canonical_item(REG, self.old)['finding_key']
        with self.case.connection() as db:
            db.execute('DROP TABLE ' + variants.TABLE)
            db.execute('PRAGMA query_only=ON')
            result = sources.read_thematic_items(db, BATCH, REG)
            exact = sources.read_thematic_exact_items(db, BATCH, REG, [expected])
            self.assertEqual([expected], [item['finding_key'] for item in result])
            self.assertEqual([expected], [item['finding_key'] for item in exact])
            self.assertFalse(db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (variants.TABLE,)).fetchone())

    def test_history_cache_and_count_follow_a_new_variant_without_new_base_row(self):
        self.current(self.old)
        before = queue.thematic_history_records(self.ns)['batches'][0]
        self.assertEqual(1, before['violations'])
        self.seed(self.fresh)
        self.current(self.fresh)
        after = queue.thematic_history_records(self.ns)['batches'][0]
        self.assertEqual(1, after['violations'])
        self.assertEqual(1, after['reassessed_findings'])
        self.assertEqual(2, after['stored_rows'])
        self.assertEqual(self.base_history, self.case.retained())

    def test_history_cache_invalidates_when_only_variant_sources_change(self):
        self.seed(self.fresh)
        self.current(self.fresh)
        self.assertEqual(1, queue.thematic_history_records(self.ns)['batches'][0]['violations'])
        with self.case.connection() as db:
            db.execute('DELETE FROM thematic_findings')
            db.execute("UPDATE docs SET metadata_json='changed current original' WHERE reg_number=?", (REG,))
        self.assertEqual(0, queue.thematic_history_records(self.ns)['batches'][0]['violations'])

    def test_changed_source_metadata_never_keeps_variant_confirmed(self):
        self.seed(self.fresh)
        self.current(self.fresh)
        with self.case.connection() as db:
            db.execute("UPDATE docs SET metadata_json='changed current original' WHERE reg_number=?", (REG,))
        self.assertTrue(all(item['effective_status'] == 'requires_review' for item in self.read()))

    def test_exact_lookup_applies_the_same_current_overlays_as_public_cards(self):
        self.seed(self.fresh)
        self.current(self.fresh)
        fresh_key = sources.canonical_item(REG, self.fresh)['finding_key']
        old_key = sources.canonical_item(REG, self.old)['finding_key']
        exact = sources.thematic_exact_items(self.ns, BATCH, REG, [old_key, fresh_key])
        by_key = {item['finding_key']: item for item in exact}
        self.assertEqual('requires_review', by_key[old_key]['effective_status'])
        self.assertEqual('confirmed', by_key[fresh_key]['effective_status'])
        self.assertNotEqual(old_key, fresh_key)

    def security_sources(self, *, same_url=False):
        from test_bid_security_requirement_recall import original, PURCHASE
        import v336_legal_matrix as rules
        p = {**PURCHASE, 'reg': REG, 'price_num': None,
             'method': 'Аукцион в электронной форме, участниками которого могут быть только субъекты малого и среднего предпринимательства'}
        body = 'Лот №1. НМЦД: 10 000 000 рублей. Обеспечение заявки: 3% НМЦД.'
        self.old = rules.security_findings(p, [original(body, 'Извещение.docx', 11, lot_number='1')])[0][0]
        self.fresh = rules.security_findings(p, [original(body + '\nНовая страница оригинала.',
            'Извещение.docx', 11 if same_url else 12, lot_number='1')])[0][0]
        for item in (self.old, self.fresh):
            item['category'] = 'sme_bid_security'
        with self.case.connection() as db:
            db.execute('UPDATE thematic_batches SET categories=? WHERE id=?', ('["sme_bid_security"]', BATCH))
            db.execute('DELETE FROM thematic_findings')
            db.execute('INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) '
                'VALUES(?,?,?,?,?,?,?,?,?)', (BATCH, REG, *(self.old[key] for key in
                ('category', 'title', 'law', 'evidence', 'code', 'document', 'source_url'))))

    def msp_event(self, item, token):
        canonical = sources.canonical_item(REG, item)
        with self.case.connection() as db:
            msp.initialize(db)
            db.execute('INSERT INTO thematic_finding_reassessments('
                'actor_id,batch_id,reg,finding_key,effective_status,source_fingerprint,source_token,'
                'detector_revision,finding_snapshot,scope_json,sources_json,reason,created_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', ('owner', BATCH, REG, canonical['finding_key'],
                'superseded', 'verified-scope-recheck', token, msp.REVISION, json.dumps(canonical),
                '{"value":false,"state":"no"}', '{}', 'Участие не ограничено МСП', '2026-11-11'))

    def test_stale_msp_source_does_not_hide_a_fresh_independent_variant(self):
        self.security_sources()
        self.seed(self.fresh)
        self.current(self.old, self.fresh)
        self.msp_event(self.old, 'older-source')
        with patch.object(msp, 'current_source_token', return_value='current-source'):
            rows = self.read()
        by_url = {item['source_url']: item for item in rows}
        self.assertEqual('requires_review', by_url[self.old['source_url']]['effective_status'])
        self.assertEqual('confirmed', by_url[self.fresh['source_url']]['effective_status'])
        self.assertEqual(1, sum(item['effective_status'] == 'confirmed' for item in rows))

    def test_stale_msp_hash_is_not_carried_to_changed_bytes_under_the_same_url(self):
        self.security_sources(same_url=True)
        self.seed(self.fresh)
        self.current(self.old, self.fresh)
        self.msp_event(self.old, 'older-source')
        with patch.object(msp, 'current_source_token', return_value='current-source'):
            rows = self.read()
        fresh_key = sources.canonical_item(REG, self.fresh)['finding_key']
        self.assertEqual('confirmed', next(item for item in rows if item['finding_key'] == fresh_key)['effective_status'])
        self.assertEqual(1, sum(item['effective_status'] == 'confirmed' for item in rows))

    def test_current_msp_correction_still_limits_equivalent_representations(self):
        self.security_sources()
        self.seed(self.fresh)
        self.current(self.old, self.fresh)
        self.msp_event(self.old, 'current-source')
        with patch.object(msp, 'current_source_token', return_value='current-source'):
            rows = self.read()
        self.assertTrue(rows)
        self.assertTrue(all(item['effective_status'] == 'superseded' for item in rows))

    def test_msp_recheck_target_remains_exact_when_another_source_is_the_public_representative(self):
        from security_episode_identity import PREFIX
        self.security_sources()
        self.old['evidence'] = self.old['evidence'].split(PREFIX, 1)[0].strip()
        with self.case.connection() as db:
            db.execute('UPDATE thematic_findings SET evidence=?', (self.old['evidence'],))
        self.seed(self.fresh)
        self.current(self.old, self.fresh)
        public = self.read()
        self.assertEqual(1, len(public))
        old_key = sources.canonical_item(REG, self.old)['finding_key']
        self.assertNotEqual(old_key, public[0]['finding_key'])
        with self.case.connection() as db:
            exact = msp._target(db, BATCH, REG, old_key)
        self.assertEqual(old_key, exact['finding_key'])
        self.assertEqual(self.old['source_url'], exact['source_url'])

    def internal_code_variants(self, old_code):
        fresh_code = self.old['code']
        self.old['code'] = old_code
        with self.case.connection() as db:
            db.execute('UPDATE thematic_findings SET code=?', (old_code,))
        self.fresh = {**self.old, 'code': fresh_code}
        self.seed(self.fresh)
        return sources.canonical_item(REG, self.old)['finding_key']

    def test_same_material_claim_uses_current_registered_code_without_changing_public_key(self):
        key = self.internal_code_variants('protocol')
        before = self.case.retained()
        self.current(self.fresh)
        public = self.read()
        with self.case.connection() as db:
            exact = sources.read_thematic_exact_items(db, BATCH, REG, [key])
            self.assertEqual(2, len(variants.raw_rows(db, BATCH, REG)))
        self.assertEqual([key], [item['finding_key'] for item in public])
        self.assertEqual([key], [item['finding_key'] for item in exact])
        self.assertEqual(self.fresh['code'], public[0]['code'])
        self.assertEqual('confirmed', public[0]['effective_status'])
        self.assertEqual('confirmed', exact[0]['effective_status'])
        self.assertEqual(before, self.case.retained())

    def test_unassessed_legacy_default_cannot_override_current_review_of_the_same_claim(self):
        key = self.internal_code_variants('unregistered-legacy-rule')
        self.current()
        public = self.read()
        with self.case.connection() as db:
            exact = sources.read_thematic_exact_items(db, BATCH, REG, [key])
        self.assertEqual(1, len(public))
        self.assertEqual('requires_review', public[0]['effective_status'])
        self.assertEqual('requires_review', exact[0]['effective_status'])
        self.assertEqual(self.fresh['code'], public[0]['code'])

    def test_current_confirmation_wins_over_an_unassessed_alias_of_the_same_claim(self):
        key = self.internal_code_variants('unregistered-legacy-rule')
        self.current(self.fresh)
        public = self.read()
        with self.case.connection() as db:
            exact = sources.read_thematic_exact_items(db, BATCH, REG, [key])
        self.assertEqual(1, len(public))
        self.assertEqual('confirmed', public[0]['effective_status'])
        self.assertEqual('confirmed', exact[0]['effective_status'])
        self.assertEqual(self.fresh['code'], public[0]['code'])


class ExactVariantRoutes(unittest.TestCase):
    def setUp(self):
        import test_thematic_finding_submissions as thematic_fixture
        import test_v382_finding_submissions as identity_fixture
        self.identity = identity_fixture
        self.case = thematic_fixture.ThematicFindingTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.batch = thematic_fixture.BATCH_A
        self.other_batch = thematic_fixture.BATCH_B
        self.reg = identity_fixture.REG_A
        with sqlite3.connect(self.case.f.evidence_path) as db:
            db.row_factory = sqlite3.Row
            variants.initialize(db)
            self.old = dict(db.execute('SELECT * FROM thematic_findings WHERE batch_id=? AND category="protocols"',
                                      (self.batch,)).fetchone())
        self.old_key = sources.canonical_item(self.reg, self.old)['finding_key']
        # Whitespace is the same substantive event, but its quoted source and
        # full saved identity are distinct; the public representative prefers B.
        self.fresh = {**self.old, 'document': 'Текущий протокол.pdf',
            'source_url': self.old['source_url'] + '?edition=2',
            'evidence': self.old['evidence'].replace('подписан', 'подписан' + ' ' * 80)}

    def seed_fresh(self):
        with sqlite3.connect(self.case.f.evidence_path) as db:
            variants.insert_variant(db, self.batch, self.reg, self.fresh)

    def test_approved_exact_source_remains_addressable_when_public_grouping_prefers_another(self):
        self.assertEqual(200, self.case.submit(self.old_key).status_code)
        self.assertEqual(200, self.case.f.decision(key=self.old_key, version=1).status_code)
        approvals = self.case.f.rows('account_manager_finding_reviews')
        self.seed_fresh()
        public = self.case.get().json()['findings']
        self.assertEqual(1, len(public))
        self.assertNotEqual(self.old_key, public[0]['finding_key'])
        self.assertIsNone(public[0]['review'])
        self.assertIsNone(public[0]['submission'])
        old = self.case.f.manager_finding()
        self.assertEqual(self.old_key, old['finding_key'])
        self.assertEqual(self.old['document'], old['document'])
        self.assertEqual('approved', old['submission']['status'])
        self.assertEqual('approved', self.case.submit(self.old_key).json()['status'])
        self.assertEqual(approvals, self.case.f.rows('account_manager_finding_reviews'))

    def test_owner_scope_precedes_exact_key_lookup_and_authorized_delete_cleans_only_that_batch(self):
        self.seed_fresh()
        fresh_key = sources.canonical_item(self.reg, self.fresh)['finding_key']
        self.assertEqual(404, self.case.submit(fresh_key, self.other_batch).status_code)
        headers = self.case.f.headers(self.identity.EMPLOYEE_A)
        response = self.case.f.client.delete('/api/thematic/' + self.other_batch, headers=headers)
        self.assertEqual(404, response.status_code)
        with sqlite3.connect(self.case.f.evidence_path) as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM ' + variants.TABLE + ' WHERE batch_id=?',
                                         (self.batch,)).fetchone()[0])
            variants.insert_variant(db, self.other_batch, self.reg, self.fresh)
        stopped = self.case.f.client.post('/api/thematic/' + self.batch + '/stop', headers=headers)
        self.assertEqual(200, stopped.status_code, stopped.text)
        with sqlite3.connect(self.case.f.evidence_path) as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM ' + variants.TABLE + ' WHERE batch_id=?',
                                         (self.batch,)).fetchone()[0])
        response = self.case.f.client.delete('/api/thematic/' + self.batch, headers=headers)
        self.assertEqual(200, response.status_code, response.text)
        with sqlite3.connect(self.case.f.evidence_path) as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM ' + variants.TABLE + ' WHERE batch_id=?',
                                         (self.batch,)).fetchone()[0])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM ' + variants.TABLE + ' WHERE batch_id=?',
                                         (self.other_batch,)).fetchone()[0])

    def test_failed_authorized_deletion_rolls_back_variants_base_and_campaign_together(self):
        self.seed_fresh()
        with sqlite3.connect(self.case.f.evidence_path) as db:
            db.execute("CREATE TRIGGER refuse_variant_delete BEFORE DELETE ON thematic_findings "
                       "BEGIN SELECT RAISE(ABORT,'retained evidence'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.case.f.client.delete('/api/thematic/' + self.batch,
                                      headers=self.case.f.headers(self.identity.EMPLOYEE_A))
        with sqlite3.connect(self.case.f.evidence_path) as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM ' + variants.TABLE + ' WHERE batch_id=?',
                                         (self.batch,)).fetchone()[0])
            self.assertIsNotNone(db.execute('SELECT 1 FROM thematic_findings WHERE batch_id=?',
                                           (self.batch,)).fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM thematic_batches WHERE id=?',
                                           (self.batch,)).fetchone())


if __name__ == '__main__':
    unittest.main()
