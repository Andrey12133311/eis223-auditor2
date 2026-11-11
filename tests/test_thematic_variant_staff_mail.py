"""Fresh exact sources reach owned staff views and existing mail deduplication."""
import json
import re
import sqlite3
import unittest
from unittest.mock import patch
from uuid import UUID

import test_payment_staff_view as staff_fixture
import test_account_notifications_v1 as mail_fixture
from scripts import v373_thematic_email_notifications as mail
import thematic_proof_variants as variants


class VariantStaffView(unittest.TestCase):
    def setUp(self):
        self.case = staff_fixture.PaymentStaffView()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.f, self.ns = self.case.f, self.case.ns
        self.current = {**staff_fixture.PAYMENT, 'document': 'Актуальный договор.docx',
            'source_url': staff_fixture.PAYMENT['source_url'] + '&receipt_context=current'}
        with sqlite3.connect(self.f.evidence_path) as db:
            variants.initialize(db)
            variants.insert_variant(db, staff_fixture.BATCH_A, staff_fixture.accounts.REG_A, self.current)
        self.case.record([self.current])

    def test_current_variant_is_visible_without_mutating_old_or_foreign_evidence(self):
        old = self.case.snapshot()
        foreign = self.case.reader(staff_fixture.accounts.EMPLOYEE_B)
        result = self.case.reader()
        self.assertEqual(2, result['count'])
        selected = next(row for row in result['findings']
                        if row['title'] == self.current['title'])
        self.assertEqual(self.current['document'], selected['document'])
        self.assertEqual(self.current['source_url'], selected['source_url'])
        revised = next(row for row in result['reassessed_findings']
                       if row['title'] == self.current['title'])
        self.assertEqual(staff_fixture.PAYMENT['document'], revised['document'])
        self.assertEqual('requires_review', revised['effective_status'])
        self.assertEqual(old, self.case.snapshot())
        self.assertEqual(foreign, self.case.reader(staff_fixture.accounts.EMPLOYEE_B))

    def test_low_variant_id_survives_the_bounded_slice_after_many_legacy_rows(self):
        with sqlite3.connect(self.f.evidence_path) as db:
            for number in range(1250):
                db.execute('INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) '
                    'VALUES(?,?,?,?,?,?,?,?,?)', (staff_fixture.BATCH_A, staff_fixture.accounts.REG_A,
                    'protocols', 'Архивное наблюдение', '223-ФЗ, ст. 4 ч. 12',
                    'Старая архивная цитата об одном и том же историческом обстоятельстве.' + ' '*number + ' Конец.',
                    'legacy-source', 'Архив.pdf', 'https://zakupki.gov.ru/archive'))
        result = self.case.reader()
        self.assertTrue(result['truncated'])
        self.assertTrue(any(row['source_url'] == self.current['source_url']
                            for row in result['findings']))

    def test_fresh_legacy_source_survives_many_older_variants_of_the_same_procurement(self):
        fresh = {**self.current, 'document': 'Свежий основной источник.docx',
            'evidence': self.current['evidence'].replace('30 рабочих', '45 рабочих'),
            'source_url': self.current['source_url'] + '&storage=legacy'}
        with sqlite3.connect(self.f.evidence_path) as db:
            for number in range(1250):
                variants.insert_variant(db, staff_fixture.BATCH_A, staff_fixture.accounts.REG_A,
                    {'category': 'protocols', 'title': 'Прежнее вторичное наблюдение',
                     'law': '223-ФЗ, ст. 4 ч. 12',
                     'evidence': 'Одна историческая цитата о прежнем юридическом обстоятельстве.' +
                                 ' '*number + ' Конец.',
                     'code': 'legacy-source', 'document': 'Исторический источник.pdf',
                     'source_url': 'https://zakupki.gov.ru/archive'})
            self.case.insert_payment(db, staff_fixture.BATCH_A, fresh)
        self.case.record([fresh])
        result = self.case.reader()
        self.assertTrue(result['truncated'])
        self.assertTrue(any(row['source_url'] == fresh['source_url'] for row in result['findings']))

    def test_a_single_available_store_can_fill_the_entire_bounded_window(self):
        from scripts import v370_manager_campaigns as campaigns
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute('DROP TABLE ' + variants.TABLE)
            for number in range(1250):
                db.execute('INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence) '
                    'VALUES(?,?,?,?,?,?)', (staff_fixture.BATCH_A, staff_fixture.accounts.REG_A,
                     'protocols', 'Доступная историческая запись', '223-ФЗ, ст. 4 ч. 12',
                     'Сохранённое обстоятельство номер ' + str(number)))
            db.commit()
            db.execute('PRAGMA query_only=ON')
            window = campaigns._staff_source_window(db,
                {staff_fixture.BATCH_A: ['protocols', 'sme_payment']})
        self.assertEqual(1200, len(window))
        self.assertTrue(all(row['_source_storage'] == 'legacy' for row in window))

    def test_variant_from_another_owned_context_is_not_a_cross_owner_shortcut(self):
        foreign = {**self.current, 'document': 'Чужой источник.docx',
                   'source_url': self.current['source_url'] + '&owner=other'}
        before = self.case.reader()
        with sqlite3.connect(self.f.evidence_path) as db:
            variants.insert_variant(db, staff_fixture.BATCH_B, staff_fixture.accounts.REG_A, foreign)
        self.assertEqual(before, self.case.reader())

    def test_stale_msp_scope_on_old_source_does_not_hide_a_fresh_exact_variant(self):
        import test_v385_msp_reassessment as msp_fixture
        from scripts import v384_thematic_findings as sources
        from scripts import v385_msp_reassessment as msp
        old = {'category': 'security', 'code': msp_fixture.CODE,
            'title': msp_fixture.TITLE, 'law': '223-ФЗ, ст. 3 ч. 8; ПП РФ № 1352, п. 23',
            'evidence': msp_fixture.SECURITY_EVIDENCE, 'document': 'Прежний источник обеспечения.pdf',
            'source_url': msp_fixture.URL}
        current = {**old, 'document': 'Текущий источник обеспечения.pdf',
                   'source_url': msp_fixture.URL.replace('A'*32, 'B'*32)}
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute('UPDATE thematic_batches SET categories=? WHERE id=?',
                       ('["protocols","sme_payment","security"]', staff_fixture.BATCH_A))
            self.case.insert_payment(db, staff_fixture.BATCH_A, old)
            variants.insert_variant(db, staff_fixture.BATCH_A, staff_fixture.accounts.REG_A, current)
            msp.initialize(db)
            key = sources.canonical_item(staff_fixture.accounts.REG_A, old)['finding_key']
            db.execute('INSERT INTO thematic_finding_reassessments '
                '(actor_id,batch_id,reg,finding_key,effective_status,source_fingerprint,source_token,'
                'detector_revision,finding_snapshot,scope_json,sources_json,reason,created_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (str(staff_fixture.accounts.EMPLOYEE_A), staff_fixture.BATCH_A,
                 staff_fixture.accounts.REG_A, key, 'superseded', 'f'*64, 'obsolete-scope-token',
                 msp.REVISION, json.dumps(old), '{}', '{}', 'Историческая оценка МСП', '2026-11-11'))
        self.case.record([self.current, current])
        result = self.case.reader()
        self.assertEqual(3, result['count'])
        selected = next(row for row in result['findings'] if row['title'] == msp_fixture.TITLE)
        self.assertEqual(current['source_url'], selected['source_url'])
        revised = next(row for row in result['reassessed_findings'] if row['finding_key'] == key)
        self.assertEqual('requires_review', revised['effective_status'])

    def alias_claim(self, old_code='protocol', batch=None):
        from scripts import v384_thematic_findings as sources
        old = {'category': 'protocols', 'code': old_code,
            'title': 'Отсутствуют основания отклонения в итоговом протоколе',
            'law': '223-ФЗ, ст. 3.2 ч. 14',
            'evidence': 'По заявке № 12: основания отклонения не указаны в итоговом протоколе.',
            'document': 'Протокол с неизменным доказательством.pdf',
            'source_url': staff_fixture.PAYMENT['source_url'].replace('1'*32, 'C'*32)}
        current = {**old, 'code': 'v372-protocol-rejected-bid-no-reasons'}
        self.assertEqual(sources.canonical_item(staff_fixture.accounts.REG_A, old)['finding_key'],
                         sources.canonical_item(staff_fixture.accounts.REG_A, current)['finding_key'])
        with sqlite3.connect(self.f.evidence_path) as db:
            self.case.insert_payment(db, staff_fixture.BATCH_A, old)
            variants.insert_variant(db, batch or staff_fixture.BATCH_A,
                                    staff_fixture.accounts.REG_A, current)
        return old, current

    def test_aliases_of_the_same_exact_claim_do_not_show_confirmed_and_review_twice(self):
        old, current = self.alias_claim()
        self.case.record([self.current, current])
        result = self.case.reader()
        selected = [row for row in result['findings'] if row['title'] == current['title']]
        revised = [row for row in result['reassessed_findings'] if row['title'] == current['title']]
        self.assertEqual(1, len(selected))
        self.assertEqual([], revised)
        self.assertEqual(3, result['count'])

    def test_default_legacy_alias_in_another_context_cannot_restore_a_reviewed_claim(self):
        import payment_finding_reassessment as assessment
        batch = staff_fixture.BATCH_C
        old, current = self.alias_claim('legacy-protocol-alias', batch=batch)
        with sqlite3.connect(self.f.identity_path) as db:
            db.create_function('regexp', 2, lambda pattern, value: bool(re.search(pattern, value)))
            db.execute('INSERT INTO account_manager_campaign_targets '
                '(id,manager_id,user_id,batch_id,title,categories,legal_forms,target_violations,status) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (str(UUID(int=2001)), str(staff_fixture.accounts.MANAGER_A),
                 str(staff_fixture.accounts.EMPLOYEE_A), batch, 'Проверка второго источника',
                 '["protocols"]', '["ООО"]', 1, 'active'))
        with sqlite3.connect(self.f.evidence_path) as db:
            event = assessment.prepare_assessment(staff_fixture.accounts.REG_A, [],
                scope='thematic', batch=batch, engine_signature=staff_fixture.ENGINE,
                source_version='current-exact-aliases',
                db_token=assessment.db_source_token(db, staff_fixture.accounts.REG_A))
            assessment.record_assessment(db, event)
            db.execute('UPDATE thematic_items SET status=?,last_semantic_version=?,last_engine_signature=? '
                'WHERE batch_id=? AND reg=?',
                ('completed', event['source_version'], staff_fixture.ENGINE,
                 batch, staff_fixture.accounts.REG_A))
        result = self.case.reader()
        selected = [row for row in result['findings'] if row['title'] == current['title']]
        revised = [row for row in result['reassessed_findings'] if row['title'] == current['title']]
        self.assertEqual([], selected)
        self.assertEqual(1, len(revised))
        self.assertEqual('requires_review', revised[0]['effective_status'])
        self.assertEqual(2, result['count'])


class VariantMailCapture(unittest.TestCase):
    def setUp(self):
        self.case = mail_fixture.AlertsTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.ns = self.case.ns
        self.finding = {'category': 'deadlines', 'title': 'Нарушение сроков',
            'law': 'ч. 11 ст. 4 223-ФЗ', 'evidence': 'Подтверждено оригиналом',
            'code': '223-4-11-AMENDMENT-PUBLICATION-DELAY',
            'document': 'Актуальный источник.pdf',
            'source_url': 'https://zakupki.gov.ru/current', 'law_url': ''}

    def seed_ledger(self):
        with self.ns['conn']() as db:
            variants.initialize(db)

    def insert(self):
        with self.ns['conn']() as db:
            return variants.insert_variant(db, 'a'*24, '32616412317', self.finding)

    def test_variant_is_captured_once_and_same_proof_replay_does_not_add_an_event(self):
        self.seed_ledger()
        mail.install_capture(self.ns)
        self.assertTrue(self.insert())
        self.assertFalse(self.insert())
        self.assertEqual(1, self.case._events())

    def test_source_refresh_uses_existing_mail_deduplication_without_sending(self):
        self.seed_ledger()
        mail.install_capture(self.ns)
        self.case._finding()
        pg = mail_fixture.FakePG()
        with patch('scripts.account_notifications_v1.send_smtp') as smtp:
            self.assertEqual(1, mail.poll(self.ns, pg))
            self.assertTrue(self.insert())
            self.assertEqual(0, mail.poll(self.ns, pg))
        smtp.assert_not_called()
        self.assertEqual(1, len(pg.queued))
        self.assertEqual(0, self.case._events())

    def test_variant_capture_rolls_back_with_its_evidence(self):
        self.seed_ledger()
        mail.install_capture(self.ns)
        db = self.ns['conn']()
        try:
            db.execute('BEGIN IMMEDIATE')
            variants.insert_variant(db, 'a'*24, '32616412317', self.finding)
            db.rollback()
        finally:
            db.close()
        self.assertEqual(0, self.case._events())

    def test_old_schema_does_not_gain_a_ledger_and_later_capture_does_not_backfill(self):
        mail.install_capture(self.ns)
        with self.ns['conn']() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                        (variants.TABLE,)).fetchone())
        self.seed_ledger()
        self.insert()
        mail.install_capture(self.ns)
        self.assertEqual(0, self.case._events())
        self.finding = {**self.finding, 'source_url': self.finding['source_url'] + '?version=2'}
        self.assertTrue(self.insert())
        self.assertEqual(1, self.case._events())


if __name__ == '__main__':
    unittest.main()
