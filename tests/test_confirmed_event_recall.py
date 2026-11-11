"""Closed event labels retain confirmed outcomes without weakening proof gates.

All facts are synthetic. Integration cases use actual temporary DOCX originals,
SHA receipts and immutable ZIP/XML evidence, never live procurement writes.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import official_publication_evidence as proof
import test_clarification_xml_evidence as clarification_saved
import test_date_label_pipeline_parity as pipeline_saved
import test_official_publication_evidence as saved
from test_legal_date_label_archive_recall import P, original
import v341_precise_legal as legal
import v348_cross_document_legal as cross
from vp_inspection_engine import qualify_outcomes


PROTOCOL = '223-4-12-PROTOCOL-PUBLICATION-LATE'
CLARIFICATION = '223-3.2-3-CLARIFICATION-RESPONSE-LATE'
CANCELLATION = '223-3.2-6-CANCELLATION-POSTED-LATE'
SIGNATURE = ('Номер протокола: 1\n'
             'Дата подписания протокола подведения итогов закупки: 06.10.2026.')
PROTOCOL_POSTED = 'Дата размещения протокола подведения итогов закупки в ЕИС: 13.10.2026.'
RECEIPT = 'Номер запроса: 1\nДата поступления заказчику запроса: 06.10.2026.'
ANSWER = 'Дата публикации ответа в ЕИС: 13.10.2026.'
DECISION = ('Решение об отмене закупки № 7.\n'
            'Дата принятия решения об отмене конкурентной закупки: 06.10.2026.')
CANCEL_POSTED = 'Дата размещения решения об отмене конкурентной закупки в ЕИС: 13.10.2026.'
CASES = ((PROTOCOL, 'Протокол № 1.pdf', SIGNATURE + '\n' + PROTOCOL_POSTED),
         (CLARIFICATION, 'Разъяснение на запрос № 1.pdf', RECEIPT + '\n' + ANSWER),
         (CANCELLATION, 'Решение об отмене.pdf', DECISION + '\n' + CANCEL_POSTED))


class ConfirmedEventRecall(unittest.TestCase):
    def assess(self, name, text, purchase=None):
        p = deepcopy(P if purchase is None else purchase)
        docs = [original(name, text)]
        return qualify_outcomes(p, docs, *legal.evaluate(p, docs))

    def test_complete_closed_labels_confirm_one_existing_rule_each(self):
        for code, name, text in CASES:
            with self.subTest(code=code):
                findings, reviews = self.assess(name, text)
                self.assertEqual([item['code'] for item in findings], [code])
                self.assertFalse(reviews)
                self.assertIn('2026-10-06', findings[0]['evidence'])
                self.assertIn('2026-10-13', findings[0]['evidence'])

    def test_ordinary_nominative_reversed_and_time_labels(self):
        examples = (
            (PROTOCOL, 'Протокол № 1.pdf',
             'Номер протокола: 1\nПротокол подведения итогов закупки подписан: 06.10.2026.\n'
             'Протокол подведения итогов закупки размещён в ЕИС: 13.10.2026.'),
            (PROTOCOL, 'Протокол № 1.pdf',
             'Номер протокола: 1\nДата и время публикации в ЕИС протокола подведения итогов закупки: '
             '13.10.2026 10:00\nДата и время подписания протокола подведения итогов закупки: 06.10.2026 12:00'),
            (CLARIFICATION, 'Разъяснение на запрос № 1.pdf', ANSWER + '\n' + RECEIPT),
            (CANCELLATION, 'Решение об отмене.pdf',
             'Решение № 7. Решение об отмене конкурентной закупки принято: 06.10.2026.\n'
             'Решение об отмене конкурентной закупки размещено в ЕИС: 13.10.2026.'))
        for code, name, text in examples:
            with self.subTest(code=code, text=text):
                findings, _ = self.assess(name, text)
                self.assertEqual([item['code'] for item in findings], [code])

    def test_unchanged_shorter_labels_keep_confirmed_outcomes(self):
        for code, name, text in CASES:
            text = (text.replace('подведения итогов закупки', 'подведения итогов')
                    .replace('поступления заказчику запроса', 'поступления запроса')
                    .replace('отмене конкурентной закупки', 'отмене закупки'))
            with self.subTest(code=code):
                self.assertEqual([item['code'] for item in self.assess(name, text)[0]], [code])

    def test_timely_and_same_day_events_do_not_become_violations(self):
        for code, name, text in CASES:
            replacement = '06.10.2026' if code == CANCELLATION else '09.10.2026'
            with self.subTest(code=code):
                findings, reviews = self.assess(name, text.replace('13.10.2026', replacement))
                self.assertFalse(findings)
                self.assertFalse(reviews)

    def test_planned_and_example_events_remain_unconfirmed(self):
        for code, name, text in CASES:
            variants = (
                (text.replace('Дата ', 'Плановая дата ', 1), P),
                ('Пример заполнения:\n' + text, P))
            for body, purchase in variants:
                with self.subTest(code=code, body=body, method=purchase['method']):
                    self.assertFalse(self.assess(name, body, purchase)[0])

    def test_competitive_event_rules_retain_noncompetitive_scope_gate(self):
        for code, name, text in CASES[1:]:
            with self.subTest(code=code):
                self.assertFalse(self.assess(name, text, {
                    **P, 'method': 'Закупка у единственного поставщика'})[0])

    def test_unrelated_words_cannot_extend_closed_event_labels(self):
        for code, name, text in CASES:
            changed = (text.replace('итогов закупки', 'итогов подготовки закупки')
                       .replace('поступления заказчику', 'направления заказчику')
                       .replace('отмене конкурентной закупки', 'отмене планируемой закупки'))
            with self.subTest(code=code):
                self.assertFalse(self.assess(name, changed)[0])

    def test_non_eis_publication_does_not_gain_confirmed_event(self):
        for code, name, text in CASES[1:]:
            with self.subTest(code=code):
                findings, reviews = self.assess(name, text.replace(' в ЕИС', ' на сайте заказчика'))
                self.assertFalse(findings)
                self.assertTrue(reviews)
        self.assertFalse(self.assess('Разъяснение на запрос № 1.pdf',
                                    RECEIPT + '\nОтвет размещён 13.10.2026.')[0])

    def test_cancellation_foreign_lots_purchase_and_legacy_notice_stay_reviews(self):
        variants = (
            ('Лот № 1. ' + DECISION + '\nЛот № 2. ' + CANCEL_POSTED, P),
            ('Закупка № 32612345678. ' + DECISION + '\n' + CANCEL_POSTED, P),
            (DECISION + '\n' + CANCEL_POSTED, {**P, 'published': '2018-06-01'}))
        for body, purchase in variants:
            with self.subTest(body=body, published=purchase['published']):
                findings, reviews = self.assess('Решение об отмене.pdf', body, purchase)
                self.assertFalse(findings)
                self.assertTrue(reviews)

    def test_final_protocol_cannot_pair_with_a_different_stage(self):
        body = SIGNATURE + '\n' + PROTOCOL_POSTED.replace('подведения итогов закупки', 'рассмотрения заявок')
        findings, reviews = self.assess('Протокол № 1.pdf', body)
        self.assertFalse(findings)
        self.assertTrue(reviews)

    def test_cross_originals_use_the_same_closed_date_labels(self):
        for family, first, second, reader in (
                ('protocol', SIGNATURE, 'Номер протокола: 1\n' + PROTOCOL_POSTED, cross.cross_doc_protocols),
                ('clarification', RECEIPT, 'Номер запроса: 1\n' + ANSWER, cross.cross_doc_clarifications)):
            title = 'Итоговый протокол № 1.pdf' if family == 'protocol' else 'Разъяснение на запрос № 1.pdf'
            docs = [original(title, first, 1), original(title, second, 2)]
            p = deepcopy(P)
            with self.subTest(family=family):
                findings, reviews = qualify_outcomes(p, docs, reader(p, docs))
                self.assertEqual(len(findings), 1)
                self.assertFalse(reviews)


class ConfirmedEventReceiptIntegration(unittest.TestCase):
    # Reuse saved-original ingress and persistence without inheriting its tests.
    setUp = saved.OfficialPublicationEvidenceTests.setUp
    connect = saved.OfficialPublicationEvidenceTests.connect
    make_xml = saved.OfficialPublicationEvidenceTests.make_xml
    write_archive = saved.OfficialPublicationEvidenceTests.write_archive
    write_original = saved.OfficialPublicationEvidenceTests.write_original
    collect = saved.OfficialPublicationEvidenceTests.collect
    explanation = clarification_saved.ClarificationXmlEvidence.explanation
    seed_collected = pipeline_saved.DateLabelPipelineParity.seed_collected
    assert_both_paths = pipeline_saved.DateLabelPipelineParity.assert_both_paths

    def prepare(self, family, text=None, publication='2026-10-13T12:00:00+03:00', **xml):
        for path in (self.root / 'eis223_official_archive_inbox' / saved.REG).iterdir():
            path.unlink()
        filename = {'protocol': 'Протокол.docx', 'clarification': 'Разъяснение.docx',
                    'cancellation': 'Решение об отмене.docx'}[family]
        body = {'protocol': SIGNATURE, 'clarification': RECEIPT,
                'cancellation': DECISION + '\n' + CANCEL_POSTED}[family]
        self.write_original(body if text is None else text)
        self.write_archive(self.make_xml('purchaseNotice', publication='2026-10-01T12:00:00+03:00',
                                         filename=filename))
        if family == 'protocol':
            self.write_archive(self.make_xml(publication=publication, **xml))
        elif family == 'clarification':
            self.write_archive(self.explanation(publication=publication, **xml))
        return self.collect()

    def qualified(self, result):
        return qualify_outcomes(result['p'], result['docs'], *legal.evaluate(result['p'], result['docs']))

    def assert_immutable_audit(self, code):
        files = {path: path.read_bytes() for path in self.root.rglob('*') if path.suffix in ('.zip', '.docx')}
        found = self.assert_both_paths(1)
        self.assertEqual([item['code'] for item in found], [code])
        self.assertEqual({path: path.read_bytes() for path in files}, files)

    def test_final_protocol_exact_xml_and_repeated_persistence_confirm_once(self):
        result = self.prepare('protocol')
        evidence = proof.protocol_publication_verifier(result['p'])(result['docs'][0])
        self.assertEqual(evidence['state'], 'confirmed')
        self.assertEqual(evidence['publication_date'], '2026-10-13')
        self.assertNotIn('13.10.2026', result['docs'][0]['text'])
        self.assertEqual([item['code'] for item in self.qualified(result)[0]], [PROTOCOL])
        self.assert_immutable_audit(PROTOCOL)

    def test_dative_receipt_exact_xml_and_repeated_persistence_confirm_once(self):
        result = self.prepare('clarification')
        evidence = proof.clarification_publication_verifier(result['p'])(result['docs'][0])
        self.assertEqual(evidence['state'], 'confirmed')
        self.assertEqual((evidence['request_date'], evidence['publication_date']), ('2026-10-06', '2026-10-13'))
        findings, reviews = self.qualified(result)
        self.assertEqual([item['code'] for item in findings], [CLARIFICATION])
        self.assertFalse(reviews)
        self.assertIn('2026-10-09', findings[0]['evidence'])
        self.assert_immutable_audit(CLARIFICATION)

    def test_competitive_cancellation_original_and_repeated_persistence_confirm_once(self):
        result = self.prepare('cancellation')
        findings, reviews = self.qualified(result)
        self.assertEqual([item['code'] for item in findings], [CANCELLATION])
        self.assertFalse(reviews)
        self.assert_immutable_audit(CANCELLATION)

    def test_timely_or_conflicting_xml_cannot_confirm_recovered_labels(self):
        controls = (
            ('protocol', SIGNATURE, '2026-10-09T12:00:00+03:00', {}),
            ('protocol', SIGNATURE + '\n' + PROTOCOL_POSTED, '2026-10-14T12:00:00+03:00', {}),
            ('clarification', RECEIPT, '2026-10-09T12:00:00+03:00', {}),
            ('clarification', RECEIPT, '2026-10-13T12:00:00+03:00', {'request': '2026-10-07T09:00:00+03:00'}))
        for family, body, publication, xml in controls:
            with self.subTest(family=family, publication=publication, xml=xml):
                self.assertFalse(self.qualified(self.prepare(family, body, publication, **xml))[0])

    def test_noncompetitive_method_cannot_use_xml_publication_fallback(self):
        for family in ('protocol', 'clarification'):
            with self.subTest(family=family):
                result = self.prepare(family)
                result['p']['method'] = 'Закупка у единственного поставщика'
                self.assertFalse(self.qualified(result)[0])

    def test_tampered_original_or_foreign_xml_uid_cannot_supply_publication(self):
        for family in ('protocol', 'clarification'):
            with self.subTest(family=family, case='foreign_uid'):
                self.assertFalse(self.qualified(self.prepare(family, uid='A' * 32))[0])
            with self.subTest(family=family, case='tampered_original'):
                result = self.prepare(family)
                self.original_path.write_bytes(self.original_path.read_bytes() + b'tampered')
                self.assertFalse(self.qualified(result)[0])


if __name__ == '__main__':
    unittest.main()
