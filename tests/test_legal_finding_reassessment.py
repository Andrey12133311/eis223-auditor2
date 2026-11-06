"""A corrected legal verdict must also correct saved cards/counts, preserving history."""
import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import payment_finding_reassessment as assessment
import v352_dashboard_truth as truth
import v384_thematic_findings as sources
import test_payment_finding_reassessment as fixture

PAYMENT, REG, BATCH = fixture.PAYMENT, fixture.REG, fixture.BATCH


class LegalFindingReassessmentTests(unittest.TestCase):
    """Reuse only the real SQLite fixture's helpers, not its payment test cases."""
    connect = fixture.PaymentAssessmentStorage.connect
    connection = fixture.PaymentAssessmentStorage.connection
    retained = fixture.PaymentAssessmentStorage.retained
    event = fixture.PaymentAssessmentStorage.event
    record = fixture.PaymentAssessmentStorage.record
    item = fixture.PaymentAssessmentStorage.item

    def setUp(self):
        fixture.PaymentAssessmentStorage.setUp(self)
        self.legal = {**PAYMENT, 'category': 'protocols',
            'code': 'v372-protocol-rejected-bid-no-reasons',
            'title': 'В протоколе не указаны основания отклонения заявки',
            'law': '223-ФЗ, ст. 3.2 ч. 14',
            'evidence': 'По заявке № 12: основания отклонения не указаны.',
            'document': 'Протокол.pdf'}
        with self.connection() as db:
            db.execute('DELETE FROM findings')
            db.execute('DELETE FROM thematic_findings')
            db.execute('DELETE FROM approved_decisions')
            db.execute('INSERT INTO findings(reg_number,code,kind,title,law,evidence,document,source_url) '
                       'VALUES(?,?,?,?,?,?,?,?)', (REG, *(self.legal[key] for key in
                       ('code', 'kind', 'title', 'law', 'evidence', 'document', 'source_url'))))
            db.execute('INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) '
                       'VALUES(?,?,?,?,?,?,?,?,?)', (BATCH, REG, *(self.legal[key] for key in
                       ('category', 'title', 'law', 'evidence', 'code', 'document', 'source_url'))))
            db.execute('INSERT INTO approved_decisions VALUES(?,?,?)',
                       (sources.canonical_item(REG, self.legal)['finding_key'], 'approved', 'Историческое решение'))

    def test_registered_rule_without_current_assessment_requires_review(self):
        with self.connection() as db:
            actual = assessment.apply_general_reassessments(db, REG, [self.legal])[0]
            self.assertEqual(actual['effective_status'], 'requires_review')
            self.assertEqual(truth.load_counts(db, [REG]).get(REG, 0), 0)

    def test_fresh_then_superseded_result_changes_count_without_deleting_history(self):
        history = self.retained()
        self.record([self.legal])
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG])[REG], 1)
        self.record([])
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG]).get(REG, 0), 0)
            value = assessment.apply_general_reassessments(db, REG, [self.legal])[0]
            self.assertEqual(value['effective_status'], 'requires_review')
            self.assertIn('не подтверждён', value['legal_reassessment']['reason'])
        self.assertEqual(history, self.retained())

    def test_other_source_does_not_reconfirm_old_quote(self):
        self.record([{**self.legal, 'document': 'Другой протокол.pdf'}])
        with self.connection() as db:
            self.assertEqual(assessment.apply_general_reassessments(db, REG, [self.legal])[0]
                             ['effective_status'], 'requires_review')

    def test_retry_cannot_reconfirm_a_saved_legal_violation(self):
        self.record([self.legal], state='retry')
        with self.connection() as db:
            self.assertEqual(assessment.apply_general_reassessments(db, REG, [self.legal])[0]
                             ['effective_status'], 'requires_review')

    def test_changed_source_metadata_invalidates_current_confirmation(self):
        self.record([self.legal])
        with self.connection() as db:
            db.execute('UPDATE docs SET metadata_json=? WHERE reg_number=?', ('{"sha256":"new-original"}', REG))
            self.assertEqual(assessment.apply_general_reassessments(db, REG, [self.legal])[0]
                             ['effective_status'], 'requires_review')

    def test_real_thematic_save_reassesses_nonpayment_result(self):
        import v345_thematic_accelerator as fast
        history = self.retained()
        item = self.item()
        self.assertEqual(fast._persist(self.ns, [(item, 'completed', [self.legal], 'fresh')]), 1)
        with self.connection() as db:
            self.assertEqual(assessment.apply_thematic_reassessments(db, BATCH, REG, [self.legal])[0]
                             ['effective_status'], 'confirmed')
        self.assertEqual(fast._persist(self.ns, [(self.item(), 'completed', [], 'superseded')]), 1)
        with self.connection() as db:
            self.assertEqual(assessment.apply_thematic_reassessments(db, BATCH, REG, [self.legal])[0]
                             ['effective_status'], 'requires_review')
        self.assertEqual(history, self.retained())

    def test_unknown_rule_is_not_silently_requalified(self):
        unrelated = {**self.legal, 'code': 'external-review-rule'}
        with self.connection() as db:
            self.assertEqual(assessment.apply_general_reassessments(db, REG, [unrelated]), [unrelated])

    def test_review_and_confirmed_rows_are_not_merged(self):
        confirmed = {**self.legal, 'effective_status': 'confirmed', 'legal_reassessment': {'id': 1}}
        review = {**self.legal, 'effective_status': 'requires_review', 'legal_reassessment': {'id': 2}}
        self.assertEqual(len(truth.collapse_legal_events([confirmed, review])), 2)

    def clarification_history(self):
        """Same dated act, but the current notice proof has a different digest."""
        from test_clarification_identity import PURCHASE, pair
        from v348_cross_document_legal import cross_doc_clarifications
        from v333_dedup_ui import _same_finding
        from v353_episode_guard import distinct_new

        docs = pair('1') + pair('2', first=3)
        findings = cross_doc_clarifications(PURCHASE, docs)
        self.assertEqual(len(findings), 2)
        fresh, second = [{**item, 'category': 'clarifications'} for item in findings]
        proof_sha = fresh['clarification_deadline_assessment']['evidence_text'].rsplit(' ', 1)[-1]
        old = {**fresh, 'evidence': fresh['evidence'].replace(proof_sha, 'a'*64)}
        self.assertTrue(_same_finding(old, fresh, 'title'))
        self.assertFalse(distinct_new(fresh, [old]))
        self.assertNotEqual(assessment.evidence_key(old), assessment.evidence_key(fresh))
        with self.connection() as db:
            for table, columns in {
                'findings': ('law_url TEXT', 'note TEXT', 'location TEXT', 'match_text TEXT'),
                'checks': ('scope TEXT', 'source TEXT', 'note TEXT', 'law_url TEXT', 'location TEXT'),
                'purchases': ('violations INTEGER DEFAULT 0', 'checks_ok INTEGER DEFAULT 0',
                              'checks_attention INTEGER DEFAULT 0'),
            }.items():
                existing = {row[1] for row in db.execute('PRAGMA table_info('+table+')')}
                for declaration in columns:
                    if declaration.split()[0] not in existing:
                        db.execute('ALTER TABLE '+table+' ADD COLUMN '+declaration)
            db.execute('DELETE FROM findings')
            db.execute('DELETE FROM thematic_findings')
            db.execute('DELETE FROM approved_decisions')
            db.execute('DELETE FROM docs')
            db.execute('UPDATE purchases SET method=?,published_at=?,card_fields_json=? WHERE reg_number=?',
                       (PURCHASE['method'], '2026-10-01', json.dumps({
                           'verified_notice_revisions': PURCHASE['_verified_notice_revisions']}), REG))
            for identifier, doc in enumerate(docs, 1):
                db.execute('INSERT INTO docs VALUES(?,?,?,?,?,?)',
                           (identifier, REG, doc['name'], '/synthetic/'+str(identifier),
                            doc['status'], json.dumps(doc['meta'])))
            db.execute('INSERT INTO findings(reg_number,code,kind,title,law,evidence,document,source_url) '
                       'VALUES(?,?,?,?,?,?,?,?)', (REG, *(old[key] for key in
                       ('code', 'kind', 'title', 'law', 'evidence', 'document', 'source_url'))))
            db.execute('INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) '
                       'VALUES(?,?,?,?,?,?,?,?,?)', (BATCH, REG, *(old[key] for key in
                       ('category', 'title', 'law', 'evidence', 'code', 'document', 'source_url'))))
            db.execute('UPDATE thematic_batches SET categories=? WHERE id=?', ('["clarifications"]', BATCH))
            db.execute('INSERT INTO approved_decisions VALUES(?,?,?)',
                       (sources.canonical_item(REG, old)['finding_key'], 'approved', 'Историческое решение'))
        return old, fresh, second

    def test_general_writer_keeps_fresh_exact_proof_after_semantic_duplicate(self):
        from v332_all_procurements_audit import persist
        from v333_dedup_ui import _same_finding

        old, fresh, second = self.clarification_history()
        before = self.retained()
        event = self.event([fresh])
        self.assertEqual(persist(self.ns, REG, [fresh, dict(fresh)], [], payment_assessment=event), (1, 0))
        self.assertEqual(persist(self.ns, REG, [fresh], [], payment_assessment=event), (0, 0))
        with self.connection() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM findings ORDER BY id')]
            self.assertEqual(len(rows), 2)
            self.assertEqual([row['effective_status'] for row in
                              assessment.apply_general_reassessments(db, REG, rows)],
                             ['requires_review', 'confirmed'])
            self.assertEqual(truth.load_counts(db, [REG])[REG], 1)
        self.assertEqual(self.retained()['findings'][:1], before['findings'])
        self.assertEqual(self.retained()['approved_decisions'], before['approved_decisions'])
        proof_sha = fresh['clarification_deadline_assessment']['evidence_text'].rsplit(' ', 1)[-1]
        expanded = {**fresh, 'evidence': fresh['evidence'].replace('SHA-256 '+proof_sha, 'SHA-256 '+'b'*64)}
        self.assertTrue(_same_finding(fresh, expanded, 'title'))
        self.assertNotEqual(assessment.evidence_key(fresh), assessment.evidence_key(expanded))
        current = [fresh, expanded, second]
        self.assertEqual(persist(self.ns, REG, current, [], payment_assessment=self.event(current)), (2, 0))
        with self.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0], 4)
            self.assertEqual(truth.load_counts(db, [REG])[REG], 2)

    def test_thematic_writer_keeps_fresh_exact_proofs_before_collapse(self):
        import v345_thematic_accelerator as fast
        from v333_dedup_ui import _same_finding

        old, fresh, second = self.clarification_history()
        before = self.retained()
        self.assertEqual(fast._persist(self.ns, [(self.item(), 'completed', [fresh, dict(fresh)], 'fresh proof')]), 1)
        with self.connection() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM thematic_findings ORDER BY rowid')]
            self.assertEqual(len(rows), 2)
            self.assertEqual([row['effective_status'] for row in
                              assessment.apply_thematic_reassessments(db, BATCH, REG, rows)],
                             ['requires_review', 'confirmed'])
            self.assertEqual(sum(row['effective_status']=='confirmed' for row in
                                 sources.read_thematic_items(db, BATCH, REG)), 1)
        self.assertEqual(self.retained()['thematic_findings'][:1], before['thematic_findings'])
        self.assertEqual(self.retained()['approved_decisions'], before['approved_decisions'])
        proof_sha = fresh['clarification_deadline_assessment']['evidence_text'].rsplit(' ', 1)[-1]
        expanded = {**fresh, 'evidence': fresh['evidence'].replace('SHA-256 '+proof_sha, 'SHA-256 '+'b'*64)}
        self.assertTrue(_same_finding(fresh, expanded, 'title'))
        self.assertNotEqual(assessment.evidence_key(fresh), assessment.evidence_key(expanded))
        current = [fresh, expanded, dict(expanded), second]
        self.assertEqual(fast._persist(self.ns, [(self.item(), 'completed', current, 'two real acts')]), 1)
        self.assertEqual(fast._persist(self.ns, [(self.item(), 'completed', current, 'repeat')]), 1)
        with self.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM thematic_findings').fetchone()[0], 4)
            self.assertEqual(sum(row['effective_status']=='confirmed' for row in
                                 sources.read_thematic_items(db, BATCH, REG)), 2)

    def test_unknown_codes_keep_semantic_deduplication(self):
        from v332_all_procurements_audit import _plan_audit_results
        from v345_thematic_accelerator import _plan_thematic_results

        old, fresh, _ = self.clarification_history()
        old, fresh = ({**item, 'code': 'external-review-rule'} for item in (old, fresh))
        self.assertEqual(_plan_audit_results(REG, [fresh], [], ([old], []))[0], [])
        item = {'batch': BATCH, 'reg': REG}
        self.assertEqual(_plan_thematic_results([(item, 'completed', [fresh], '')],
                         {(BATCH, REG): [old]})[0][2], [])

    def test_general_finding_preserves_long_exact_evidence_and_changed_tail(self):
        from v332_all_procurements_audit import persist

        _, fresh, _ = self.clarification_history()
        prefix = fresh['evidence']+'\n'+('Подтверждённые реквизиты исходного документа. '*90)
        first = {**fresh, 'evidence': prefix+'\nКонец доказательства: А'}
        second = {**fresh, 'evidence': prefix+'\nКонец доказательства: Б'}
        self.assertGreater(len(first['evidence']), 3000)
        self.assertEqual(first['evidence'][:3000], second['evidence'][:3000])
        self.assertNotEqual(assessment.evidence_key(first), assessment.evidence_key(second))
        self.assertEqual(persist(self.ns, REG, [first], [], payment_assessment=self.event([first])), (1, 0))
        self.assertEqual(persist(self.ns, REG, [first], [], payment_assessment=self.event([first])), (0, 0))
        with self.connection() as db:
            stored = dict(db.execute('SELECT * FROM findings ORDER BY id DESC LIMIT 1').fetchone())
            self.assertEqual(stored['evidence'], first['evidence'])
            self.assertEqual(truth.load_counts(db, [REG])[REG], 1)
        self.assertEqual(persist(self.ns, REG, [second], [], payment_assessment=self.event([second])), (1, 0))
        self.assertEqual(persist(self.ns, REG, [second], [], payment_assessment=self.event([second])), (0, 0))
        with self.connection() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM findings ORDER BY id')]
            self.assertEqual(rows[-2]['evidence'], first['evidence'])
            self.assertEqual(rows[-1]['evidence'], second['evidence'])
            self.assertEqual([item['effective_status'] for item in
                              assessment.apply_general_reassessments(db, REG, rows[-2:])],
                             ['requires_review', 'confirmed'])
            self.assertEqual(truth.load_counts(db, [REG])[REG], 1)

    def test_general_checklist_preserves_long_exact_evidence_and_changed_tail(self):
        from v332_all_procurements_audit import persist

        _, fresh, _ = self.clarification_history()
        prefix = fresh['evidence']+'\n'+('Подтверждённые реквизиты исходного документа. '*65)
        base = {'field_path': fresh['code'], 'status': 'violation', 'item': fresh['title'],
                'scope': 'Проверка разъяснений', 'source': fresh['document'],
                **{key: fresh[key] for key in ('law', 'law_url', 'document', 'source_url')}}
        first = {**base, 'evidence': prefix+'\nКонец доказательства: А'}
        second = {**base, 'evidence': prefix+'\nКонец доказательства: Б'}
        self.assertGreater(len(first['evidence']), 2200)
        self.assertEqual(first['evidence'][:2200], second['evidence'][:2200])
        self.assertNotEqual(assessment.evidence_key(first), assessment.evidence_key(second))
        self.assertEqual(persist(self.ns, REG, [], [first], payment_assessment=self.event([first])), (0, 1))
        self.assertEqual(persist(self.ns, REG, [], [first], payment_assessment=self.event([first])), (0, 0))
        with self.connection() as db:
            stored = dict(db.execute('SELECT * FROM checks ORDER BY id DESC LIMIT 1').fetchone())
            self.assertEqual(stored['evidence'], first['evidence'])
            self.assertEqual(truth.load_counts(db, [REG])[REG], 1)
        self.assertEqual(persist(self.ns, REG, [], [second], payment_assessment=self.event([second])), (0, 1))
        self.assertEqual(persist(self.ns, REG, [], [second], payment_assessment=self.event([second])), (0, 0))
        with self.connection() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM checks ORDER BY id')]
            self.assertEqual(rows[-2]['evidence'], first['evidence'])
            self.assertEqual(rows[-1]['evidence'], second['evidence'])
            self.assertEqual([item['effective_status'] for item in
                              assessment.apply_general_reassessments(db, REG, rows)],
                             ['requires_review', 'confirmed'])
            self.assertEqual(truth.load_counts(db, [REG])[REG], 1)

    def test_unregistered_evidence_keeps_existing_storage_limits(self):
        from v332_all_procurements_audit import persist

        _, fresh, _ = self.clarification_history()
        text = 'Самостоятельное неподтверждённое наблюдение. '*100+'Конец текста'
        finding = {**fresh, 'code': 'external-finding', 'title': 'Наблюдение из внешнего правила', 'evidence': text}
        check = {'field_path': 'external-check', 'status': 'violation', 'item': 'Другое внешнее правило',
                 'scope': 'Внешние наблюдения', 'evidence': text, 'document': fresh['document'],
                 'source_url': fresh['source_url'], 'law': fresh['law']}
        self.assertEqual(persist(self.ns, REG, [finding], [check]), (1, 1))
        with self.connection() as db:
            self.assertEqual(db.execute("SELECT evidence FROM findings WHERE code='external-finding'").fetchone()[0], text[:3000])
            self.assertEqual(db.execute("SELECT evidence FROM checks WHERE field_path='external-check'").fetchone()[0], text[:2200])


def load_tests(loader, tests, pattern):
    # Inheriting the setup fixture must not rerun its unrelated payment cases.
    return unittest.TestSuite(LegalFindingReassessmentTests(name)
        for name, value in LegalFindingReassessmentTests.__dict__.items()
        if name.startswith('test_') and callable(value))


if __name__ == '__main__':
    unittest.main()
