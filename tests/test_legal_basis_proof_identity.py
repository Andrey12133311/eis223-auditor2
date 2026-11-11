"""Current proof cannot endorse another retained legal ground for the same quote."""
import json
import unittest

import payment_finding_reassessment as assessment
import v384_thematic_findings as thematic
import test_legal_finding_reassessment as fixture

REG, BATCH = fixture.REG, fixture.BATCH


class LegalBasisProofIdentity(unittest.TestCase):
    connect = fixture.LegalFindingReassessmentTests.connect
    connection = fixture.LegalFindingReassessmentTests.connection
    retained = fixture.LegalFindingReassessmentTests.retained
    event = fixture.LegalFindingReassessmentTests.event
    record = fixture.LegalFindingReassessmentTests.record
    item = fixture.LegalFindingReassessmentTests.item

    def setUp(self):
        fixture.LegalFindingReassessmentTests.setUp(self)

    def replace_old_law(self, law):
        wrong = {**self.legal, 'law': law}
        with self.connection() as db:
            db.execute('UPDATE findings SET law=?', (law,))
            db.execute('UPDATE thematic_findings SET law=?', (law,))
            db.execute('UPDATE approved_decisions SET finding_key=?',
                       (thematic.canonical_item(REG, wrong)['finding_key'],))
        return wrong

    def test_distinct_legal_grounds_are_distinct_confirmed_proof_keys(self):
        for law in ('223-ФЗ, ст. 3.2 ч. 13', '223-ФЗ, ст. 3.2 ч. 27',
                    'ГК РФ, ст. 193', ''):
            with self.subTest(law=law):
                changed = {**self.legal, 'law': law}
                self.assertNotEqual(assessment.evidence_key(changed), assessment.evidence_key(self.legal))

    def test_display_heading_and_reference_link_do_not_change_the_same_proof(self):
        changed = {**self.legal, 'title': 'Уточнённый заголовок того же эпизода',
                   'law_url': 'https://www.consultant.ru/document/cons_doc_LAW_116964/'}
        self.assertEqual(assessment.evidence_key(changed), assessment.evidence_key(self.legal))

    def test_current_correct_ground_does_not_certify_old_wrong_general_ground(self):
        wrong = self.replace_old_law('223-ФЗ, ст. 3.2 ч. 27')
        before = self.retained()
        self.record([self.legal])
        with self.connection() as db:
            result = assessment.apply_general_reassessments(db, REG, [wrong, self.legal])
            self.assertEqual(['requires_review', 'confirmed'], [x['effective_status'] for x in result])
        self.assertEqual(before, self.retained())

    def test_current_correct_ground_does_not_certify_old_wrong_thematic_ground(self):
        wrong = self.replace_old_law('223-ФЗ, ст. 3.2 ч. 27')
        before = self.retained()
        self.record([self.legal], 'thematic')
        with self.connection() as db:
            result = assessment.apply_thematic_reassessments(db, BATCH, REG, [wrong, self.legal])
            self.assertEqual(['requires_review', 'confirmed'], [x['effective_status'] for x in result])
            key = thematic.canonical_item(REG, self.legal)['finding_key']
            self.assertIsNone(db.execute('SELECT 1 FROM approved_decisions WHERE finding_key=?', (key,)).fetchone())
        self.assertEqual(before, self.retained())

    def test_mutating_retained_law_after_recheck_cannot_keep_confirmation(self):
        self.record([self.legal])
        changed = {**self.legal, 'law': 'ГК РФ, ст. 193'}
        with self.connection() as db:
            result = assessment.apply_general_reassessments(db, REG, [changed])
            self.assertEqual('requires_review', result[0]['effective_status'])

    def test_old_assessment_revision_is_read_only_history(self):
        before = self.retained()
        event = self.event([self.legal])
        event['detector_revision'] = 'registered-legal-evidence-reassessment-2'
        with self.connection() as db:
            assessment.record_assessment(db, event)
        with self.connection() as db:
            self.assertEqual('requires_review',
                assessment.apply_general_reassessments(db, REG, [self.legal])[0]['effective_status'])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM payment_finding_assessments').fetchone()[0])
        self.assertEqual(before, self.retained())

    def test_variant_only_purchase_participates_in_source_cache_invalidation(self):
        from thematic_proof_variants import initialize, insert_variant
        with self.connection() as db:
            initialize(db)
            db.execute('DELETE FROM thematic_findings')
            insert_variant(db, BATCH, REG, self.legal)
            before = assessment.revision(db, BATCH)
            self.assertTrue(before[1])
            db.execute('UPDATE docs SET metadata_json=? WHERE reg_number=?',
                       ('{"sha256":"changed-exact-source"}', REG))
            after = assessment.revision(db, BATCH)
            self.assertNotEqual(before, after)


if __name__ == '__main__':
    unittest.main()
