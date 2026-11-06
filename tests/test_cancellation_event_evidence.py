"""An EIS receipt is not proof that two cancellation dates describe one act."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from v341_precise_legal import evaluate

REG = '32616410694'
SOURCE = 'https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=' + 'C' * 32
PURCHASE = {'reg': REG, 'method': 'Конкурс', 'published': '2026-10-01'}
DECISION = 'Дата принятия решения об отмене закупки: 01.10.2026.'
PUBLICATION = 'Дата публикации решения об отмене закупки в ЕИС: 05.10.2026.'
CODE = '223-3.2-6-CANCELLATION-POSTED-LATE'


def original(body, **meta):
    return {'name': 'Решение об отмене.pdf', 'status': 'checked', 'text': body,
            'meta': {'source': 'official_eis_original', 'source_url': SOURCE,
                     'sha256': 'c' * 64, **meta}}


class CancellationEventEvidenceTests(unittest.TestCase):
    def run_case(self, body, purchase=None, document=None):
        found, checks = evaluate(PURCHASE if purchase is None else purchase,
                                 [document or original(body)])
        return [x for x in found if x.get('code') == CODE], [
            x for x in checks if 'cancellation' in str(x.get('field_path', '')).lower()]

    def assert_review(self, body, **kwargs):
        found, checks = self.run_case(body, **kwargs)
        self.assertFalse(found)
        self.assertTrue(checks)
        return checks

    def test_actual_same_event_retains_both_exact_quotes(self):
        found, checks = self.run_case(DECISION + ' ' + PUBLICATION)
        self.assertEqual(len(found), 1)
        self.assertFalse(checks)
        self.assertIn(DECISION.rstrip('.'), found[0]['evidence'])
        self.assertIn(PUBLICATION.rstrip('.'), found[0]['evidence'])
        self.assertEqual(found[0]['source_position'], 0)

    def test_same_day_is_not_a_breach(self):
        found, checks = self.run_case(DECISION + ' ' + PUBLICATION.replace('05.10', '01.10'))
        self.assertFalse(found)
        self.assertFalse(checks)

    def test_reverse_table_has_same_bounded_event(self):
        found, checks = self.run_case(PUBLICATION + '\n' + DECISION)
        self.assertEqual(len(found), 1)
        self.assertFalse(checks)

    def test_distinct_decision_numbers_are_not_joined(self):
        self.assert_review('Решение об отмене закупки № 1. ' + DECISION +
                           ' Решение об отмене закупки № 2. ' + PUBLICATION)

    def test_parenthesized_decision_suffixes_are_not_truncated_to_one_id(self):
        self.assert_review('Решение об отмене закупки №1(А). ' + DECISION +
                           '\nРешение об отмене закупки №1(Б). ' + PUBLICATION)

    def test_spaced_decision_suffixes_are_not_truncated_to_one_id(self):
        self.assert_review('Решение об отмене закупки №1 / 2025. ' + DECISION +
                           '\nРешение об отмене закупки №1 / 2026. ' + PUBLICATION)

    def test_decision_identity_after_publication_date_is_not_discarded(self):
        self.assert_review(DECISION + ' (Решение №1).\n' +
                           PUBLICATION + ' (Решение №2).')

    def test_foreign_purchase_after_publication_date_is_not_discarded(self):
        self.assert_review(DECISION + '\n' + PUBLICATION +
                           ' (Закупка №32612345678).')

    def test_distant_unique_dates_are_not_joined(self):
        middle = 'Условия иных закупок. ' * 200
        for body in (DECISION + middle + PUBLICATION, PUBLICATION + middle + DECISION):
            with self.subTest(order=body[:25]):
                self.assert_review(body)

    def test_other_procurement_does_not_prove_this_purchase(self):
        self.assert_review('Закупка № 32612345678. ' + DECISION + ' ' + PUBLICATION)

    def test_conflicting_procurements_are_not_joined(self):
        self.assert_review('Закупка № ' + REG + '. ' + DECISION +
                           ' Закупка № 32612345678. ' + PUBLICATION)

    def test_different_lots_are_not_joined(self):
        self.assert_review('Лот № 1. ' + DECISION + ' Лот № 2. ' + PUBLICATION)

    def test_source_lot_must_match_explicit_purchase_lot(self):
        body = DECISION + ' ' + PUBLICATION
        self.assert_review(body, purchase={**PURCHASE, 'lot_number': '1'},
                           document=original(body, lot_number='2'))

    def test_display_lot_number_and_internal_id_are_independent_fields(self):
        body = 'Лот № 1. ' + DECISION + ' ' + PUBLICATION
        purchase = {**PURCHASE, 'lot_number': '1', 'lot_id': 'lot-uuid-1'}
        found, checks = self.run_case(body, purchase=purchase,
                                     document=original(body, lot_number='1', lot_id='lot-uuid-1'))
        self.assertEqual(len(found), 1)
        self.assertFalse(checks)
        self.assert_review(body, purchase=purchase,
                           document=original(body, lot_number='1', lot_id='lot-uuid-2'))

    def test_receipt_does_not_turn_customer_site_date_into_eis_publication(self):
        self.assert_review(DECISION + ' ' + PUBLICATION.replace(' в ЕИС', '') +
                           ' Сведения опубликованы на сайте заказчика.')

    def test_full_eis_name_is_a_publication_label(self):
        body = DECISION + ' ' + PUBLICATION.replace('в ЕИС', 'в единой информационной системе')
        found, checks = self.run_case(body)
        self.assertEqual(len(found), 1)
        self.assertFalse(checks)

    def test_historical_event_does_not_inherit_modern_notice_scope(self):
        self.assert_review((DECISION + ' ' + PUBLICATION).replace('2026', '2017'))

    def test_old_notice_does_not_inherit_modern_event_scope(self):
        self.assert_review(DECISION + ' ' + PUBLICATION,
                           purchase={**PURCHASE, 'published': '2018-06-01'})

    def test_unknown_and_noncompetitive_scope_are_reviewed(self):
        for method in ('', 'Неконкурентная закупка', 'Запрос оферт'):
            with self.subTest(method=method):
                self.assert_review(DECISION + ' ' + PUBLICATION,
                                   purchase={**PURCHASE, 'method': method})

    def test_unusable_chronology_is_not_silently_treated_as_timely(self):
        self.assert_review(DECISION + ' ' + PUBLICATION.replace('05.10', '30.09'))

    def test_future_dates_do_not_establish_an_actual_past_action(self):
        self.assert_review((DECISION + ' ' + PUBLICATION).replace('2026', '2099'))

    def test_explicit_obsolete_and_template_sources_are_reviewed(self):
        body = DECISION + ' ' + PUBLICATION
        for document in (original(body, superseded=True), original(body, is_cancelled=True),
                         {**original(body), 'name': 'Образец решения об отмене.pdf'}):
            with self.subTest(document=document['name'], meta=document['meta']):
                self.assert_review(body, document=document)

    def test_quoted_publication_cannot_complete_actual_decision(self):
        self.assert_review(DECISION + '\n\nПример заполнения:\n' + PUBLICATION)

    def test_missing_counterpart_and_duplicate_publication_require_review(self):
        for body in (DECISION, PUBLICATION, DECISION + PUBLICATION + PUBLICATION):
            with self.subTest(body=body):
                self.assert_review(body)

    def test_two_independently_named_complete_events_remain_detectable(self):
        body = ('Решение № 1. ' + DECISION + ' ' + PUBLICATION + '\n\nРешение № 2. ' +
                DECISION.replace('01.10', '06.10') + ' ' + PUBLICATION.replace('05.10', '09.10'))
        found, checks = self.run_case(body)
        self.assertEqual(len(found), 2)
        self.assertFalse(checks)


if __name__ == '__main__':
    unittest.main()
