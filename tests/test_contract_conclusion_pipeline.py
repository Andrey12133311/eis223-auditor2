"""Contract conditions survive original ingress, both audits and saved rereads."""
import copy
import json
import unittest

import contract_conclusion_terms as contract
import payment_finding_reassessment as assessments
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import vp_inspection_engine as inspection
from v352_dashboard_truth import load_counts
from test_general_audit_recovery import DatabaseFixture
from test_vp_inspection_engine import doc, purchase, row


def clause(lower=5, upper=20):
    return (f'Договор заключается не ранее чем через {lower} дней и не позднее '
            f'чем через {upper} дней с даты размещения в ЕИС итогового протокола.')


def complete_purchase():
    return purchase(source_incomplete=False, original_expected=1, original_received=1,
                    documents_unreadable=0)


class SavedContractConditions(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def seed(self, text):
        with self.connect() as db:
            db.execute('ALTER TABLE purchases ADD COLUMN price TEXT')
            db.execute('ALTER TABLE purchases ADD COLUMN price_num REAL')
            db.execute('ALTER TABLE purchases ADD COLUMN card_fields_json TEXT')
        reg = self.purchase(text=text)
        with self.connect() as db:
            db.execute("UPDATE docs SET name='Документация.docx',doc_type='documentation'")
        path = self.folder / 'eis223_public_browser_intake' / reg / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['documents'][0]['name'] = 'Документация.docx'
        path.write_text(json.dumps(manifest))
        contract.install(self.ns)
        inspection.install(self.ns)
        return reg

    async def test_defective_condition_is_confirmed_in_both_audits_once(self):
        reg = self.seed(clause())
        before = {path: path.read_bytes() for path in self.contents}
        first = await general._inspect_one(self.ns, reg)
        self.assertEqual('completed', first[0])
        self.assertEqual(1, first[1]['new_findings'])
        state, findings = await thematic.independent_review(self.ns, reg, ['contract_signing'])
        self.assertEqual('completed', state)
        self.assertEqual([contract.CODE], [item['code'] for item in findings])
        second = await general._inspect_one(self.ns, reg)
        self.assertEqual(0, second[1]['new_findings'])
        with self.connect() as db:
            saved = [dict(item) for item in db.execute('SELECT * FROM findings')]
            self.assertEqual(1, len(saved))
            self.assertEqual(1, load_counts(db, [reg])[reg])
        self.assertEqual(before, {path: path.read_bytes() for path in self.contents})

    async def test_special_case_does_not_become_an_ordinary_range_breach(self):
        reg = self.seed(clause() + '\nДля заключения договора требуется одобрение органа управления заказчика.')
        first = await general._inspect_one(self.ns, reg)
        self.assertEqual('completed', first[0])
        self.assertEqual(0, first[1]['new_findings'])
        state, findings = await thematic.independent_review(self.ns, reg, ['contract_signing'])
        self.assertEqual('completed', state)
        self.assertEqual([], findings)
        with self.connect() as db:
            self.assertEqual(0, load_counts(db, [reg]).get(reg, 0))


class SerializedContractQualification(unittest.TestCase):
    def test_persisted_proof_is_reproduced_without_ephemeral_source_fields(self):
        original = doc(clause())
        records, _ = contract.evaluate(complete_purchase(), [original])
        self.assertEqual(1, len(records))
        retained = {key: records[0][key] for key in
                    ('code', 'kind', 'title', 'law', 'evidence', 'document', 'source_url', 'note')}
        before = copy.deepcopy(retained)
        current, reviews = inspection.qualify_outcomes(complete_purchase(), [original], [retained])
        self.assertEqual(1, len(current))
        self.assertEqual([], reviews)
        self.assertEqual('contract_condition', current[0]['decision_kind'])
        self.assertEqual(retained, before)
        self.assertTrue(assessments.is_reassessable_finding(retained))
        target = row(inspection.evaluate(complete_purchase(), [original], [retained]), 'contract_signing')
        self.assertEqual('violation', target['status'])
        self.assertEqual(1, len(target['results']))

    def test_old_flags_cannot_retain_changed_lawful_or_ambiguous_conditions(self):
        source = doc(clause())
        item = contract.evaluate(complete_purchase(), [source])[0][0]
        serialized = {key: item[key] for key in
                      ('code', 'kind', 'title', 'law', 'evidence', 'document', 'source_url', 'note')}
        for changed in (doc(clause(10, 20)), doc(clause() + '\n' + clause())):
            with self.subTest(text=changed['text']):
                current, reviews = inspection.qualify_outcomes(complete_purchase(), [changed], [serialized])
                self.assertEqual([], current)
                self.assertTrue(reviews)

    def test_condition_rule_cannot_prove_actual_execution_or_whole_row_compliance(self):
        original = doc(clause())
        item = contract.evaluate(complete_purchase(), [original])[0][0]
        for claim in (
            {**item, 'decision_kind': 'actual_event'},
            {**item, 'legal_fact_type': 'actual_event'},
            {**item, 'kind': 'check', 'status': 'compliant', 'criterion_complete': True},
        ):
            with self.subTest(claim=claim):
                target = row(inspection.evaluate(complete_purchase(), [original], [claim]), 'contract_signing')
                self.assertEqual('unknown', target['status'])
                self.assertFalse(target['has_violation'])
                self.assertFalse(target['no_violation'])


if __name__ == '__main__':
    unittest.main()
