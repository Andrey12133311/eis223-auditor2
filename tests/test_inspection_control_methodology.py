"""A checklist row, an established episode and a completed investigation differ."""
import copy
import json
from io import BytesIO
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import vp_inspection_engine as engine
import vp_inspection_export as export
from vp_inspection_criteria import CRITERIA
from test_vp_inspection_engine import URL, doc, finding, purchase, row
from test_vp_inspection_export import snapshot


class InspectionControlMethodology(unittest.TestCase):
    def test_checklist_identifiers_do_not_register_a_substantive_decision(self):
        for criterion in CRITERIA:
            with self.subTest(criterion=criterion.key):
                item = finding(code=criterion.key, law=criterion.law,
                    legal_basis_verified=True, statutory_breach_verified=True,
                    criterion_complete=True)
                decision = engine.decision_qualification(item, criterion.key)
                self.assertIn('unregistered_decision_rule', decision['methodology_reason_codes'])
                self.assertEqual('unknown', decision['status'])
                self.assertEqual((criterion.key,), engine.criterion_keys(item))

    def test_precise_citation_and_exact_quote_do_not_turn_a_row_name_into_a_rule(self):
        body = 'Требования к происхождению продукции применяются согласно документации.'
        original = doc(body)
        item = finding(code='national_1875', law='ПП РФ № 1875, п. 1',
                       evidence=body, match_text=body, legal_basis_verified=True)
        before = copy.deepcopy((original, item))
        findings, reviews = engine.qualify_outcomes(purchase(), [original], [item])
        self.assertEqual([], findings)
        self.assertEqual(1, len(reviews))
        self.assertIn('unregistered_decision_rule', reviews[0]['methodology_reason_codes'])
        target = row(engine.evaluate(purchase(), [original], [item]), 'national_1875')
        self.assertEqual('unknown', target['status'])
        self.assertFalse(target['has_violation'])
        self.assertEqual((original, item), before)

    def test_registered_current_breach_remains_confirmed(self):
        findings, _ = engine.qualify_outcomes(purchase(), [doc()], [finding()])
        self.assertEqual(1, len(findings))
        self.assertEqual('violation', findings[0]['kind'])

    def test_established_breach_does_not_hide_other_unresolved_questions(self):
        extra = 'Порядок возврата обеспечения заявки определяется отдельно.'
        original = doc(doc()['text'] + '\n' + extra)
        question = {'status': 'check', 'field_path': 'v337-security_no_return',
            'item': 'Проверить порядок возврата обеспечения заявки',
            'law': '223-ФЗ, ст. 3.2 ч. 25', 'evidence': extra,
            'document': original['name'], 'source_url': URL}
        target = row(engine.evaluate(purchase(), [original], [finding()], [question]), 'bid_security')
        self.assertEqual('violation', target['status'])
        self.assertEqual('✓', target['has_violation'])
        self.assertTrue(target['unresolved'])
        self.assertTrue(target['review_required'])
        self.assertIn('не завершена', target['reason'])
        self.assertFalse(target['no_violation'])

    def test_source_locator_survives_the_sheet_and_is_bound_to_actual_text(self):
        prefix = 'Раздел 4. Обеспечение.\n'
        original = doc(prefix + doc()['text'])
        item = finding(source_excerpt=doc()['text'], source_position=len(prefix),
                       location='Документация, пункт 4.1')
        target = row(engine.evaluate(purchase(), [original], [item]), 'bid_security')
        record = target['results'][0]
        self.assertEqual(len(prefix), record['source_position'])
        self.assertEqual(item['location'], record['location'])
        bad_offset = {**item, 'source_position': 1}
        checked = row(engine.evaluate(purchase(), [original], [bad_offset]), 'bid_security')['results'][0]
        self.assertEqual(len(prefix), checked['source_position'])


class ContractConditionExplanation(unittest.TestCase):
    def test_contract_clause_and_actual_event_have_different_report_explanations(self):
        for key, expected in (
            ('contract_signing', 'условие о сроке заключения договора'),
            ('payment_223', 'условие оплаты'),
        ):
            with self.subTest(key=key):
                value = snapshot()
                value['rows'][0].update(key=key, results=[{
                    'status': 'violation', 'decision_kind': 'contract_condition',
                    'title': 'Установленное условие', 'evidence': 'Подтверждённый фрагмент',
                }])
                for extension in ('odt', 'docx'):
                    with ZipFile(BytesIO(export.render(value, extension))) as archive:
                        xml = archive.read('content.xml' if extension == 'odt' else 'word/document.xml')
                    text = ' '.join(ET.fromstring(xml).itertext())
                    self.assertIn(expected, text)
                    self.assertIn('отдельных', text)
                    self.assertNotIn('MUST NEVER LEAK', text)
                value['rows'][0]['results'][0]['decision_kind'] = 'actual_event'
                self.assertNotIn('Проверено ' + expected, ' '.join(export._appendix(value)))


class SecurityScopeReassessment(unittest.TestCase):
    def test_serialized_lot_identity_must_match_current_own_source(self):
        from security_episode_identity import PREFIX
        from test_security_lot_episode_identity import P, originals, confirmed
        p, sources, item = {**P, 'lot_number': '1'}, originals(1), confirmed(1)
        good, _ = engine.qualify_outcomes(p, sources, [item])
        self.assertEqual(1, len(good))
        head, raw = item['evidence'].split(PREFIX)
        proof = json.loads(raw.rstrip('.'))
        for field, value in (
            ('lots', ['2']), ('reg', '32611111111'), ('sha256', 'f' * 64),
            ('span', [0, 1]), ('source_url_sha256', 'e' * 64), ('quote', proof['quote'].replace('100', '200')),
        ):
            with self.subTest(field=field):
                changed = {**item, 'evidence': head + PREFIX +
                    json.dumps({**proof, field: value}, ensure_ascii=False) + '.'}
                current, reviews = engine.qualify_outcomes(p, sources, [changed])
                self.assertEqual([], current)
                self.assertEqual(1, len(reviews))
                self.assertIn('security_scope_identity_unverified', reviews[0]['methodology_reason_codes'])

    def test_legacy_proof_without_new_annotation_still_requires_current_qualification(self):
        from security_episode_identity import PREFIX
        from test_security_lot_episode_identity import P, originals, confirmed
        item = confirmed(1)
        item['evidence'] = item['evidence'].split(PREFIX)[0].strip()
        current, _ = engine.qualify_outcomes({**P, 'lot_number': '1'}, originals(1), [item])
        self.assertEqual(1, len(current))

    def test_disagreeing_purchase_identities_cannot_validate_the_lot_annotation(self):
        from test_security_lot_episode_identity import P, originals, confirmed
        p = {**P, 'lot_number': '1', 'reg_number': '32611111111'}
        current, reviews = engine.qualify_outcomes(p, originals(1), [confirmed(1)])
        self.assertEqual([], current)
        self.assertIn('security_scope_identity_unverified', reviews[0]['methodology_reason_codes'])


if __name__ == '__main__':
    unittest.main()
