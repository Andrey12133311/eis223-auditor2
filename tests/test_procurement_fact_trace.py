"""Bounded synthetic replay, not a production reassessment of this procurement.

Only INFO_EXCERPT and PARTICIPATION_EXCERPT are supplied source quotations.
Receipt metadata, card values, later deadlines and event dates are fixtures.
They cannot establish the current deadline or a real procurement violation.
"""
import copy
import hashlib
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from legal_applicability import competitive_rule_scope
import v330_amendments_223 as amendments
from v339_fact_comparison import fact_checks
from v341_eis_facts import document_facts, parse_day
from v341_precise_legal import evaluate
from v383_msp_scope import resolve

REG = '32616324151'
ROOT = 'https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='
INFO_URL = ROOT + '5374AD4826C24C4D8D55AE95511F631A'
PARTICIPATION_URL = ROOT + 'FB8537E35E4A4D4C8AD760E393083536'
INFO_EXCERPT = (
    'извещения о проведении конкурса в единой информационной системе '
    '26 августа 2026 г. | Дата и время окончания срока подачи заявок | '
    '03 сентября 2026 г. в 10:00 по местному времени заказчика | '
    'Даты начала и окончания срока предоставления участникам конкурса '
    'разъяснений положений документации | Дата начала с'
)
PARTICIPATION_EXCERPT = (
    '1.3.1. В конкурсе в электронной форме могут принять участие только '
    'субъекты малого и среднего предпринимательства из числа юридических лиц '
    'или нескольких юридических лиц, выступающих на стороне одного '
    'Участника закупки...'
)


def original(text, name='Информационная карта.docx', url=INFO_URL):
    return {'name': name, 'status': 'checked', 'text': text, 'url': url,
            'meta': {'source': 'official_eis_original', 'source_url': url,
                     'sha256': hashlib.sha256(text.encode()).hexdigest()}}


def known_excerpts():
    return [original(INFO_EXCERPT,
                     'Закупочная_документация.7z / Раздел2 - Информационная карта.docx'),
            original(PARTICIPATION_EXCERPT, 'Раздел_1___Документация.pdf', PARTICIPATION_URL)]


def compare(docs, deadline='2026-09-10'):
    return [item for item in fact_checks(
        {'reg': REG, 'submission_end': deadline}, {}, {}, docs)
        if item['field_path'] == 'v339-date-submission_end']


class KnownExcerptTraceTests(unittest.TestCase):
    def test_spelled_month_is_already_recovered_as_document_fact(self):
        facts = document_facts(known_excerpts())
        self.assertEqual(set(facts), {'submission_end'})
        self.assertEqual(facts['submission_end']['value'], '2026-09-03')
        self.assertEqual(parse_day('03 сентября 2026 г.'), date(2026, 9, 3))

    def test_excerpts_do_not_bypass_existing_scope_gates(self):
        p = {'reg': REG}
        self.assertIsNone(competitive_rule_scope(p))
        self.assertEqual(resolve(p, known_excerpts())['state'], 'unknown')
        self.assertEqual(evaluate(p, known_excerpts()), ([], []))

    def test_labelled_method_control_is_recovered(self):
        facts = document_facts([original('Способ закупки | Конкурс в электронной форме')])
        self.assertEqual(facts['method']['value'], 'Конкурс в электронной форме')
        self.assertIs(competitive_rule_scope({'method': facts['method']['value']}), True)

    def test_different_amended_deadlines_do_not_become_current_fact(self):
        # September 10 is hypothetical, not an observed amended deadline.
        later = original('Дата и время окончания срока подачи заявок | 10 сентября 2026 г.',
                         'Информационная карта новая редакция.docx', ROOT + 'B' * 32)
        self.assertNotIn('submission_end', document_facts([known_excerpts()[0], later]))


class DateComparisonRecallTests(unittest.TestCase):
    def test_known_labelled_excerpt_reaches_review(self):
        checks = compare(known_excerpts())
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]['status'], 'check')
        self.assertNotEqual(checks[0].get('kind'), 'violation')
        self.assertEqual(checks[0]['source_url'], INFO_URL)
        self.assertIn('2026-09-03', checks[0]['evidence'])
        self.assertIn('03 сентября 2026', checks[0]['evidence'])
        self.assertIn('версии', checks[0]['note'])

    def test_date_formats_and_table_separators_have_parity(self):
        for value in ('03.09.2026', '2026-09-03', '03 сентября 2026', '«3» СЕНТЯБРЯ 2026'):
            for separator in (': ', ' | ', '： ', '\n'):
                with self.subTest(value=value, separator=separator):
                    checks = compare([original('Дата и время окончания срока подачи заявок' + separator + value)])
                    self.assertEqual(len(checks), 1)

    def test_same_day_different_time_is_not_mismatch(self):
        for expected in ('2026-09-03T20:00:00', '03 сентября 2026 г. в 23:00'):
            self.assertEqual(compare(known_excerpts(), expected), [])

    def test_invalid_unlabelled_or_missing_dates_do_not_create_review(self):
        for text in ('Дата и время окончания срока подачи заявок | 31 сентября 2026 г.',
                     'Документация от 03 сентября 2026 г. Разъяснения будут размещены позднее.',
                     'Срок подачи заявок составляет 15 календарных дней после публикации.'):
            self.assertEqual(compare([original(text)]), [])
        self.assertEqual(compare(known_excerpts(), ''), [])

    def test_later_card_and_original_revision_are_review_only(self):
        later = original('Дата и время окончания срока подачи заявок | 10 сентября 2026 г.',
                         'Информационная карта новая редакция.docx', ROOT + 'B' * 32)
        docs = [known_excerpts()[0], later]
        saved = copy.deepcopy(docs)
        checks = compare(docs)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]['document'], docs[0]['name'])
        self.assertEqual(checks[0]['status'], 'check')
        self.assertEqual(docs, saved)

    def test_start_date_uses_same_spelled_date_grammar(self):
        checks = fact_checks({'submission_start': '2026-08-25'}, {}, {}, [original(
            'Дата и время начала подачи заявок | 26 августа 2026 г.')])
        self.assertEqual([item['field_path'] for item in checks], ['v339-date-submission_start'])

    def test_unchecked_documents_are_not_comparison_evidence(self):
        for status in ('pending', 'downloaded_no_text', 'failed', None):
            with self.subTest(status=status):
                doc = known_excerpts()[0]
                doc['status'] = status
                self.assertEqual(compare([doc]), [])
                self.assertEqual(document_facts([doc]), {})

    def test_unverified_text_remains_review_only_and_never_official_fact(self):
        # V339 already permits a checked, unverified text as an explicit review
        # clue; this must not make it a verified fact or legal infringement.
        for meta in ({}, {'source': 'local_unverified'},
                     {'source': 'official_eis_original', 'source_url': INFO_URL,
                      'sha256': 'invalid'},
                     {'source': 'official_eis_original', 'source_url': 'https://example.invalid/document',
                      'sha256': 'a' * 64}):
            with self.subTest(meta=meta):
                doc = known_excerpts()[0]
                doc['meta'] = meta
                checks = compare([doc])
                self.assertEqual(len(checks), 1)
                self.assertEqual(checks[0]['status'], 'check')
                self.assertIn('Оригинал подтверждён: нет', checks[0]['note'])
                self.assertEqual(document_facts([doc]), {})
                self.assertEqual(evaluate({'reg': REG}, [doc]), ([], []))

    def test_unrelated_event_or_separated_date_is_not_a_submission_deadline(self):
        for text in ('Дата окончания предоставления разъяснений | 03 сентября 2026 г.',
                     'Дата и время окончания срока подачи заявок | не указаны. '
                     'Документ утвержден 03 сентября 2026 г.',
                     'Дата и время окончания срока подачи заявок | '
                     'Дата публикации извещения | 03 сентября 2026 г.'):
            with self.subTest(text=text):
                self.assertEqual(compare([original(text)]), [])

    def test_review_preserves_both_sources_and_archive_member_context(self):
        doc = known_excerpts()[0]
        doc['meta']['source'] = 'official_eis_archive_member'
        doc['meta']['archive_member'] = 'Раздел2 - Информационная карта.docx'
        card_url = 'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=' + REG
        purchase = {'reg': REG, 'submission_end': '2026-09-10', 'url': card_url}
        saved = copy.deepcopy((purchase, doc))
        checks = fact_checks(purchase, {}, {}, [doc])
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]['source_url'], INFO_URL)
        self.assertEqual(checks[0]['document'], doc['name'])
        self.assertIn(doc['name'], checks[0]['location'])
        self.assertIn(card_url, checks[0]['note'])
        self.assertIn('Оригинал подтверждён: да', checks[0]['note'])
        self.assertEqual((purchase, doc), saved)

    def test_repeated_date_is_deduplicated_without_removing_distinct_documents(self):
        doc = original((INFO_EXCERPT + '\n') * 3)
        other = original(INFO_EXCERPT, 'Извещение.docx', ROOT + 'D' * 32)
        checks = compare([doc, other])
        self.assertEqual(len(checks), 2)
        self.assertEqual({item['source_url'] for item in checks}, {INFO_URL, ROOT + 'D' * 32})
        self.assertTrue(all(item['status'] == 'check' for item in checks))

    def test_impossible_calendar_days_are_rejected_for_card_and_document(self):
        for value in ('29 февраля 2026', '00 сентября 2026', '32 сентября 2026',
                      '31 апреля 2026', '31.09.2026', '2026-02-29'):
            with self.subTest(value=value):
                self.assertEqual(compare([original('Окончание подачи заявок | ' + value)]), [])
                self.assertEqual(compare(known_excerpts(), value), [])


class AmendmentVersionSafetyTests(unittest.TestCase):
    def test_history_never_borrows_initial_or_current_card_deadline(self):
        event = {'kind': 'eis_history', 'publication_date': date(2026, 9, 4),
                 'document': 'Изменение', 'source': ROOT + 'C' * 32}
        with patch.object(amendments, 'manifest_changes', return_value=[event]):
            found, checks = amendments.evaluation(
                {'reg': REG, 'method': 'Конкурс', 'submission_end': '2026-09-03'}, known_excerpts())
        self.assertEqual(found, [])
        self.assertTrue(any('именно после данного изменения' in item['reason'] for item in checks))

    def test_explicit_later_revision_deadline_prevents_false_late_change(self):
        event = {'kind': 'eis_history', 'publication_date': date(2026, 9, 4),
                 'submission_end': date(2026, 9, 10),
                 'document': 'Изменение', 'source': ROOT + 'C' * 32}
        with patch.object(amendments, 'manifest_changes', return_value=[event]):
            found, checks = amendments.evaluation(
                {'reg': REG, 'method': 'Конкурс', 'submission_end': '2026-09-03'}, known_excerpts())
        self.assertEqual(found, [])
        self.assertTrue(any('половины срока' in item['reason'] for item in checks))


if __name__ == '__main__':
    unittest.main()
