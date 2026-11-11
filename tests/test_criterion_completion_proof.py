"""A narrow detector's positive flag cannot certify its whole inspection row."""
from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import vp_inspection_engine as engine
from vp_inspection_criteria import inspection_rows
from test_vp_inspection_engine import doc, finding, purchase, row


class CriterionCompletionProof(unittest.TestCase):
    def test_complete_flag_cannot_invert_a_current_source_backed_security_breach(self):
        for status in ('ok', 'pass', 'compliant'):
            with self.subTest(status=status):
                # This exact original requires 8% against an established NMCD
                # of 8 million. The positive flag currently bypasses the
                # subject rule because _security_context handles violations.
                item = finding(kind='check', status=status, criterion_complete=True)
                target = row(engine.evaluate(purchase(), [doc()], [], [item]), 'bid_security')
                self.assertEqual('unknown', target['status'])
                self.assertFalse(target['no_violation'])
                self.assertTrue(target['review_required'])
                self.assertIn('criterion_completion_unverified', target['methodology_reason_codes'])
                self.assertEqual(item['evidence'], target['unresolved'][0]['evidence'])

    def test_lawful_one_clause_still_does_not_prove_all_security_requirements(self):
        text = 'Размер обеспечения заявки составляет 2 процента НМЦД.'
        item = finding(kind='check', status='compliant', criterion_complete=True,
                       evidence=text, match_text=text)
        target = row(engine.evaluate(purchase(), [doc(text)], [], [item]), 'bid_security')
        self.assertEqual('unknown', target['status'])
        self.assertFalse(target['no_violation'])
        self.assertIn('criterion_completion_unverified', target['methodology_reason_codes'])

    def test_payload_cannot_register_itself_as_a_complete_criterion_validator(self):
        item = finding(kind='check', status='compliant', criterion_complete=True,
            full_criterion_validator=True, complete_coverage_verified=True,
            legal_basis_verified=True, substantive_validation_verified=True)
        decision = engine.decision_qualification(item, 'bid_security')
        self.assertEqual('unknown', decision['status'])
        self.assertIn('criterion_completion_unverified', decision['methodology_reason_codes'])
        self.assertFalse(decision['decision_basis']['criterion_completion_rule_registered'])

    def test_shared_qualification_retains_the_rejected_positive_claim(self):
        p, originals = purchase(), [doc()]
        item = finding(kind='check', status='compliant', criterion_complete=True)
        before = deepcopy((p, originals, item))
        findings, checks = engine.qualify_outcomes(p, originals, [], [item])
        self.assertEqual([], findings)
        self.assertEqual(1, len(checks))
        self.assertEqual('check', checks[0]['status'])
        self.assertEqual('compliant', checks[0]['reported_status'])
        self.assertEqual(item['evidence'], checks[0]['evidence'])
        self.assertIn('criterion_completion_unverified', checks[0]['methodology_reason_codes'])
        self.assertEqual(before, (p, originals, item))
        self.assertEqual((findings, checks), engine.qualify_outcomes(p, originals, findings, checks))

    def test_established_violation_cannot_be_used_to_close_the_remaining_questions(self):
        positive = finding(kind='check', status='compliant', criterion_complete=True)
        target = row(engine.evaluate(purchase(), [doc()], [finding()], [positive]), 'bid_security')
        self.assertEqual('violation', target['status'])
        self.assertEqual('✓', target['has_violation'])
        self.assertFalse(target['no_violation'])
        self.assertEqual(1, sum(item['status'] == 'violation' for item in target['results']))
        self.assertEqual(1, sum(item['status'] == 'unknown' for item in target['results']))
        self.assertEqual(1, len(target['unresolved']))
        self.assertTrue(target['review_required'])

    def test_positive_field_checks_without_completion_claim_stay_unresolved(self):
        item = finding(kind='check', status='ok')
        target = row(engine.evaluate(purchase(), [doc()], [], [item]), 'bid_security')
        self.assertEqual('unknown', target['status'])
        self.assertFalse(target['no_violation'])
        self.assertIn('criterion_not_fully_checked', target['methodology_reason_codes'])

    def test_registered_current_violation_is_not_suppressed_by_the_completion_gate(self):
        findings, _ = engine.qualify_outcomes(purchase(), [doc()], [finding()], [])
        self.assertEqual(1, len(findings))
        self.assertEqual('violation', findings[0]['kind'])
        self.assertEqual('violation', row(engine.evaluate(purchase(), [doc()], findings), 'bid_security')['status'])

    def test_independently_verified_projection_is_a_separate_existing_contract(self):
        rows = inspection_rows({'bid_security': {
            'status': 'compliant', 'evidence': 'Самостоятельная полная проверка',
            'document': 'Независимое заключение по критерию', 'legal_basis_verified': True,
        }})
        target = next(item for item in rows if item['key'] == 'bid_security')
        self.assertEqual('✓', target['no_violation'])

    def test_full_criterion_capability_is_part_of_the_signed_decision_policy(self):
        from legal_audit_revision import canonical_engine_ast
        source = Path(engine.__file__).read_text()
        declaration = 'COMPLETE_CRITERION_VALIDATORS = {}'
        self.assertIn(declaration, source)
        base = canonical_engine_ast(source, ('decision_qualification',), 'vp_inspection_engine')
        amended = source.replace(declaration,
            "COMPLETE_CRITERION_VALIDATORS = {'bid_security': frozenset({'future-full-validator'})}", 1)
        self.assertNotEqual(base, canonical_engine_ast(amended, ('decision_qualification',), 'vp_inspection_engine'))

    def test_capability_registry_does_not_register_an_unrecognized_rule(self):
        item = finding(code='future-full-validator', criterion_keys=['bid_security'],
                       kind='check', status='compliant', criterion_complete=True)
        with patch.object(engine, 'COMPLETE_CRITERION_VALIDATORS',
                          {'bid_security': frozenset({'future-full-validator'})}):
            target = row(engine.evaluate(purchase(), [doc()], [], [item]), 'bid_security')
        self.assertEqual('unknown', target['status'])
        self.assertFalse(target['no_violation'])
        self.assertIn('unregistered_decision_rule', target['methodology_reason_codes'])

    def test_api_snapshot_and_both_report_formats_explain_incomplete_control(self):
        import vp_inspection_export as export
        from test_vp_inspection_export import snapshot
        item = finding(kind='check', status='compliant', criterion_complete=True)
        evaluated = engine.evaluate(purchase(), [doc()], [], [item])
        target = row(evaluated, 'bid_security')
        self.assertFalse(target['no_violation'])
        self.assertTrue(target['review_required'])
        value = snapshot()
        value['rows'] = evaluated['rows']
        for extension in ('odt', 'docx'):
            with self.subTest(extension=extension):
                with ZipFile(BytesIO(export.render(value, extension))) as archive:
                    root = ET.fromstring(archive.read('content.xml' if extension == 'odt' else 'word/document.xml'))
                text = ' '.join(root.itertext())
                self.assertNotIn('✓', text)
                self.assertIn('Требует проверки', text)
                self.assertIn('Для завершения проверки:', text)
                self.assertIn(item['evidence'], text)

    def test_general_and_thematic_queues_do_not_persist_a_self_declared_clean_criterion(self):
        import test_general_thematic_parity as fixture
        installer = fixture.protocols.install
        decisions = []
        positive = finding(kind='check', status='ok', criterion_complete=True)

        def install_checker(ns):
            installer(ns)
            previous = ns['build_checks']
            ns['build_checks'] = lambda p, detail, card, docs: list(previous(p, detail, card, docs)) + [deepcopy(positive)]
            engine.install(ns)
            qualify = ns['vp_methodology_qualify']

            def capture(p, docs, findings, checks):
                result = qualify(p, docs, findings, checks)
                decisions.append(deepcopy(result))
                return result

            ns['vp_methodology_qualify'] = capture

        p = {**purchase(), 'reg': fixture.REG, 'reg_number': fixture.REG,
             'published_at': '2026-10-01'}
        p['verified_price_source'] = {**p['verified_price_source'],
            'source': 'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=' + fixture.REG}
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'EIS223_DATA_DIR': folder}), \
             patch.object(fixture, 'P', p), \
             patch.object(fixture.protocols, 'install', side_effect=install_checker):
            first, second, themed, findings, counts = fixture.compare([doc()])
        self.assertEqual('completed', first[0])
        self.assertEqual('completed', second[0])
        self.assertEqual(('completed', []), themed)
        self.assertEqual([], findings)
        self.assertEqual(0, counts['checks_ok'])
        self.assertGreater(counts['checks_attention'], 0)
        observed = [item for _, checks in decisions for item in checks
                    if item.get('code') == positive['code']]
        self.assertTrue(observed)
        self.assertTrue(all(item.get('status') == 'check' and
            'criterion_completion_unverified' in item.get('methodology_reason_codes', []) for item in observed))


if __name__ == '__main__':
    unittest.main()
