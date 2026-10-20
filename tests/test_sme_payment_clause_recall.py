"""Existing SME payment rule: explicit duration, acceptance clock and own clause.

The integration fixture uses synthetic DOCX/XML/ZIP bytes and real receipt
verification. No remote procurement or new legal threshold is introduced.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_official_publication_evidence as saved
import v298_eis_only as offline
import v332_all_procurements_audit as general
import v341_precise_legal as legal
import v342_thematic_queue as thematic
from v352_dashboard_truth import load_counts

CODE = '1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS'
URL = ('https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='
       + '1' * 32)
EXCLUSIVE = 'Открытый конкурс только для субъектов малого и среднего предпринимательства'
DECLARATION = ('Участниками настоящей закупки могут быть только субъекты малого '
               'и среднего предпринимательства.\n')


class SmePaymentClauseRecall(unittest.TestCase):
    def evaluate(self, text, *, method=EXCLUSIVE, verified=True):
        doc = {'name': 'Проект договора.docx', 'text': text, 'status': 'checked', 'url': URL,
               'meta': {'source': 'official_eis_original', 'source_url': URL,
                        'sha256': hashlib.sha256(text.encode()).hexdigest()}}
        if not verified:
            doc['meta']['source'] = 'uploaded_document'
        purchase = {'reg': '32610002001', 'method': method, 'submission_end': '2026-10-30'}
        findings, _ = legal.evaluate(purchase, [doc])
        return [item for item in findings if item['code'] == CODE]

    def test_ordinary_complete_labels_and_acceptance_connectors(self):
        clauses = [
            'Оплата производится в течение 15 рабочих дней с момента подписания сторонами документа о приемке.',
            'Срок оплаты составляет 15 рабочих дней с даты подписания сторонами документа о приемке.',
            'Срок оплаты: 15 рабочих дней со дня подписания заказчиком акта приемки.',
            'Срок оплаты：\n15 (пятнадцати) рабочих дней с момента утверждения документа о приёмке.',
            'Оплата оказанных услуг производится\nв течение 15 рабочих дней с даты подписания акта сдачи-приемки услуг.',
            'Расчёт производится на основании счета в течение 15 рабочих дней с даты подписания документов о приемке товара.',
            'Расчеты производятся не позднее чем 15 рабочих дней после подписания актов приемки.',
            'Оплата после подписания сторонами документа о приемке производится в течение 15 рабочих дней.',
            'Оплата после подписания акта приемки оказанных услуг осуществляется не позднее 15 рабочих дней.',
            'Срок оплаты: 21 (двадцати одного) рабочего дня с даты подписания документа о приемке.',
            'Срок оплаты: 25 (двадцать пять) рабочих дней с даты подписания документа о приемке.',
        ]
        for clause in clauses:
            with self.subTest(clause=clause):
                found = self.evaluate(clause)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]['source_url'], URL)
                self.assertIn('рабочих днях', found[0]['note'])
                self.assertNotIn('календарным датам', found[0]['note'])

    def test_invoice_clock_and_unrelated_acceptance_cannot_confirm(self):
        clauses = [
            'Оплата производится в течение 15 рабочих дней с даты получения счета. После подписания акта приемки заказчик составляет отчет.',
            'Оплата производится в течение 15 рабочих дней с даты получения счета; после подписания документа о приемке производится сверка.',
            'Оплата производится в течение 15 рабочих дней с даты получения счета, который передается после подписания акта приемки.',
            'Оплата после подписания документа о приемке производится по счету, который выставляется в течение 15 рабочих дней.',
            'Оплата производится в течение 15 рабочих дней после поставки товара, а документы оформляются после подписания акта приемки.',
            'Авансовая оплата производится в течение 15 рабочих дней с даты подписания документа о приемке.',
            'Предварительная оплата производится в течение 15 рабочих дней с момента подписания документа о приемке.',
            'Оплата аванса производится в течение 15 рабочих дней с момента подписания документа о приемке.',
            'Оплата авансового платежа производится в течение 15 рабочих дней с момента подписания документа о приемке.',
            'Оплата производится авансом в течение 15 рабочих дней с момента подписания документа о приемке.',
            'Авансовая оплата производится в течение 15 рабочих дней с даты получения счета, окончательная оплата производится в течение 7 рабочих дней с даты подписания документа о приемке.',
            'Оплата производится в течение 15 рабочих дней; окончательный расчет производится в течение 7 рабочих дней с даты подписания документа о приемке.',
        ]
        for clause in clauses:
            with self.subTest(clause=clause):
                self.assertEqual(self.evaluate(clause), [])

    def test_final_payment_keeps_its_own_acceptance_clock(self):
        found = self.evaluate(
            'Авансовая оплата производится в течение 7 рабочих дней с даты получения счета, '
            'окончательная оплата производится в течение 15 рабочих дней с момента '
            'подписания документа о приемке.')
        self.assertEqual(len(found), 1)
        self.assertIn('15 рабочих дней', found[0]['evidence'])

    def test_examples_and_explicit_statute_quotations_do_not_confirm(self):
        term = 'оплата производится в течение 15 рабочих дней с даты подписания документа о приемке.'
        for prefix in ('Например, ', 'Справочно: ', 'Цитата из постановления: ',
                       'Образец расчета: ', 'Пример условия: ',
                       'Пунктом 28 Постановления Правительства РФ № 1352 предусмотрено, что '):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.evaluate(prefix + term), [])
        # A contract clause may cite a statute without quoting a reference.
        self.assertEqual(len(self.evaluate(
            'В соответствии с пунктом 28 постановления № 1352 ' + term)), 1)
        self.assertEqual(len(self.evaluate(
            term + ' Например, счет оформляется после приемки.')), 1)

    def test_inactive_or_proposed_clause_is_not_an_operative_term(self):
        term = ('Оплата производится в течение 15 рабочих дней со дня '
                'подписания акта приёмки')
        for clause in ('Следующее условие исключено: «' + term + '».',
                       'Исключено следующее условие: «' + term + '».',
                       'Данное условие не применяется: «' + term + '».',
                       'Предлагаемая ' + term.lower() + '.',
                       'Планируемая ' + term.lower() + '.'):
            with self.subTest(clause=clause):
                self.assertEqual(self.evaluate(clause), [])
        # A procurement contract draft still stipulates actual tender terms.
        self.assertEqual(len(self.evaluate('Проект договора. ' + term + '.')), 1)

    def test_unchanged_threshold_units_and_ambiguous_spelled_amount(self):
        for duration in ('7 рабочих дней', '6 рабочих дней', '15 календарных дней',
                         '15 (семи) рабочих дней', '15 (не определено) рабочих дней',
                         '21 (двадцати двух) рабочего дня'):
            with self.subTest(duration=duration):
                self.assertEqual(self.evaluate('Срок оплаты: ' + duration
                    + ' с даты подписания документа о приемке.'), [])

    def test_exclusive_scope_and_verified_source_remain_required(self):
        term = 'Срок оплаты: 15 рабочих дней с момента подписания документа о приемке.'
        for prefix in ('Поддержка субъектов малого и среднего предпринимательства. ',
                       'Если закупка проводится только для субъектов МСП, применяется порядок оплаты. ',
                       'Квота привлечения субподрядчиков из числа субъектов МСП составляет 20 процентов. '):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.evaluate(prefix + term, method='Открытый конкурс'), [])
        conflict = ('Участниками закупки могут быть только субъекты малого '
                    'и среднего предпринимательства: Нет. ')
        self.assertEqual(self.evaluate(conflict + term), [])
        self.assertEqual(self.evaluate(term, verified=False), [])
        self.assertEqual(len(self.evaluate(DECLARATION + term, method='Открытый конкурс')), 1)


class SmePaymentOriginalPipeline(unittest.TestCase):
    setUp = saved.OfficialPublicationEvidenceTests.setUp
    connect = saved.OfficialPublicationEvidenceTests.connect
    make_xml = saved.OfficialPublicationEvidenceTests.make_xml
    write_archive = saved.OfficialPublicationEvidenceTests.write_archive
    write_original = saved.OfficialPublicationEvidenceTests.write_original
    collect = saved.OfficialPublicationEvidenceTests.collect

    def prepare_contract(self, clause):
        shutil.rmtree(self.root / 'eis223_official_archive_inbox' / saved.REG)
        self.write_archive(self.make_xml('purchaseNotice', filename='Проект договора.docx'))
        self.write_original(DECLARATION + clause)
        result = self.collect()
        self.assertEqual(result['docs'][0]['name'], 'Проект договора.docx')
        self.assertEqual(result['docs'][0]['status'], 'checked')
        purchase = result['p']
        with self.connect() as db:
            db.execute('INSERT INTO purchases(reg_number,title,customer,method,published_at,'
                       'submission_end,card_fields_json) VALUES(?,?,?,?,?,?,?)',
                       (saved.REG, purchase['title'], 'ООО Тест', purchase['method'],
                        purchase['published'], purchase['submission_end'], '{}'))
            for doc in result['docs']:
                metadata = deepcopy(doc['meta'])
                metadata.pop('official_publication_evidence', None)
                db.execute('INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,'
                           'status,metadata_json,final_url) VALUES(?,?,?,?,?,?,?,?,?)',
                           (saved.REG, doc['name'], doc['url'], doc['type'], doc['published'],
                            doc['local'], doc['status'], json.dumps(metadata), doc['final_url']))
        return result

    def assert_both_paths(self, expected):
        first = asyncio.run(general.inspect_one(self.ns, saved.REG))
        repeated = asyncio.run(general.inspect_one(self.ns, saved.REG))
        selected = asyncio.run(thematic.independent_review(self.ns, saved.REG, ['sme', 'contract']))
        self.assertEqual((first[0], first[1]['new_findings']), ('completed', expected))
        self.assertEqual(repeated[1]['new_findings'], 0)
        self.assertEqual((selected[0], len(selected[1])), ('completed', expected))
        self.assertEqual({item['code'] for item in selected[1]}, {CODE} if expected else set())
        with self.connect() as db:
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: expected})
            findings = [dict(row) for row in db.execute('SELECT * FROM findings')]
        self.assertEqual({item['code'] for item in findings}, {CODE} if expected else set())

    def test_full_label_survives_receipt_ingress_and_both_audit_paths_once(self):
        self.prepare_contract('Срок оплаты: 15 (пятнадцати) рабочих дней с момента '
                              'подписания сторонами документа о приемке товара.')
        audited = asyncio.run(offline.offline_audit(self.ns, saved.REG))
        self.assertEqual(audited['confirmed_violations'], 1)
        self.assert_both_paths(1)

    def test_receipt_verified_invoice_clock_stays_unconfirmed_in_both_paths(self):
        self.prepare_contract('Оплата производится в течение 15 рабочих дней с даты получения счета. '
                              'После подписания акта приемки заказчик составляет отчет.')
        self.assert_both_paths(0)


if __name__ == '__main__':
    unittest.main()
