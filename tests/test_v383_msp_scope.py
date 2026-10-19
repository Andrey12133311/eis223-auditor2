"""Exclusive SME classification regressions, including reported 32616480680."""
import json
import unittest
from unittest.mock import patch
import sqlite3
from scripts.v341_eis_facts import enrich
from scripts.v383_msp_scope import resolve, display
from scripts.v336_legal_matrix import security_findings
from scripts.v332_all_procurements_audit import as_procurement

URL='https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='+'A'*32

def doc(text):
    return dict(name='Аукционная_документация_НЕ_СМП_.docx',status='checked',text=text,
                meta=dict(source='official_eis_original',sha256='a'*64,source_url=URL))

class MspScopeTests(unittest.TestCase):
    def purchase(self,flag=None):
        p=dict(reg='32616480680',method='Аукцион в электронной форме',price_num=38720976,price='38720976')
        if flag is not None:p['fact_evidence']={'sme_only':dict(value=flag,source='official_eis_card',evidence='Участниками закупки могут быть только субъекты малого и среднего предпринимательства: '+('Да' if flag else 'Нет'))}
        return p
    def docs(self,scope=''):
        return [doc('Начальная (максимальная) цена договора: 38 720 976 рублей.\nОбеспечение заявки составляет: 5% от начальной (максимальной) цены договора.\n'+scope)]
    def test_screenshot_conditional_not_exclusive(self):
        docs=self.docs('При проведении закупки только среди субъектов малого и среднего предпринимательства размер обеспечения заявки не может превышать 2 процента.')
        self.assertEqual(resolve(self.purchase(),docs)['state'],'unknown')
        self.assertFalse(security_findings(self.purchase(),docs)[0])
    def test_generic_quota_and_percent_not_scope(self):
        for text in ['Доля закупок у МСП составляет 25 процентов.', 'Привлечение субподрядчиков из числа МСП: 20%.','Обеспечение заявки: 2%.']:
            self.assertEqual(resolve(self.purchase(),self.docs(text))['state'],'unknown')
    def test_explicit_false_survives_generic_clause(self):
        docs=self.docs('Если участниками закупки могут быть только субъекты малого и среднего предпринимательства, применяется пункт 23.')
        self.assertEqual(resolve(self.purchase(False),docs)['value'],False)
        self.assertFalse(security_findings(self.purchase(False),docs)[0])
    def test_true_cap(self):
        findings,_=security_findings(self.purchase(True),self.docs())
        self.assertEqual(findings[0]['code'],'223-SME-BID-SECURITY-OVER-TWO-PERCENT')
        self.assertTrue(findings[0]['sme_scope']['evidence'])
    def test_document_declaration(self):
        text='Участниками настоящей закупки могут быть только субъекты малого и среднего предпринимательства.'
        self.assertTrue(resolve(self.purchase(),self.docs(text))['value'])
    def test_label_no_is_not_yes(self):
        docs=self.docs('Участниками закупки могут быть только субъекты малого и среднего предпринимательства: Нет')
        self.assertFalse(resolve(self.purchase(),docs)['value'])
        self.assertFalse(security_findings(self.purchase(),docs)[0])
    def test_conflicting_sources_review(self):
        docs=self.docs('Участниками закупки могут быть только субъекты малого и среднего предпринимательства: Нет')
        self.assertEqual(resolve(self.purchase(True),docs)['state'],'conflict')
        findings,checks=security_findings(self.purchase(True),docs)
        self.assertFalse(findings)
        self.assertEqual(checks[0]['status'],'check')
    def test_multiline_and_parenthesis_no(self):
        label='Участниками закупки могут быть только субъекты малого и среднего предпринимательства'
        for suffix in ['\nНет',' (далее - МСП) Нет']:
            self.assertFalse(resolve(self.purchase(),self.docs(label+suffix))['value'])
    def test_other_procurement_declaration(self):
        text='В иных процедурах участниками закупки могут быть только субъекты малого и среднего предпринимательства'
        self.assertIsNone(resolve(self.purchase(),self.docs(text))['value'])
    def test_multiline_conditional_and_parenthesized_no(self):
        text='Если закупка проводится в электронной форме,\nучастниками настоящей закупки могут быть только субъекты малого и среднего предпринимательства'
        self.assertIsNone(resolve(self.purchase(),self.docs(text))['value'])
        text='Участники закупки | Только субъекты малого и среднего предпринимательства (нет)'
        self.assertFalse(resolve(self.purchase(),self.docs(text))['value'])
    def test_current_procurement_subcontract_clause_is_not_exclusive(self):
        for text in [
            'Настоящая закупка проводится с привлечением субподрядчиков только субъектов малого и среднего предпринимательства.',
            'Категория участников | Привлекаются субподрядчики только МСП',
            'Участники закупки | Предусматривается квота: только МСП',
            'Закон предусматривает, что участниками закупки являются только субъекты малого и среднего предпринимательства.',
        ]:
            self.assertIsNone(resolve(self.purchase(),self.docs(text))['value'],text)
            self.assertFalse(security_findings(self.purchase(),self.docs(text))[0])
    def test_direct_current_procurement_among_sme(self):
        for text in [
            'Настоящая закупка проводится только среди субъектов малого и среднего предпринимательства.',
            'Данный аукцион в электронной форме проводится исключительно для субъектов МСП.',
        ]:
            self.assertTrue(resolve(self.purchase(),self.docs(text))['value'],text)
    def test_method_negation(self):
        p=self.purchase();p['method']='Аукцион не только для субъектов малого и среднего предпринимательства'
        self.assertIsNone(resolve(p)['value'])
    def test_xml_and_roundtrip(self):
        card={'soap_metadata':dict(xml_type='purchaseNoticeAESMBO',xml_sha256='b'*64,source='official_eis_getDocsIP',publication='2026-10-01')}
        row=dict(reg_number='32616480680',card_fields_json=json.dumps(card))
        p=as_procurement(row)
        self.assertTrue(resolve(p)['value'])
        self.assertEqual(display(row),'Да, только МСП')
    def test_partial_card_preserves_xml_on_enrich(self):
        p=self.purchase();p['card_fields_json']={'soap_metadata':dict(xml_type='purchaseNoticeAESMBO',xml_sha256='b'*64,source='official_eis_getDocsIP')}
        ns={'conn':lambda:sqlite3.connect(':memory:')}
        enrich(ns,p,[],card={'status':'partial','pages':{}})
        self.assertTrue(resolve(p)['value'])
        self.assertEqual(p['sme_scope'],'yes')
    def test_saved_card_false_survives_document_enrichment(self):
        card={'fact_evidence':{'sme_only':dict(value=False,source='official_eis_card')}}
        p=as_procurement(dict(reg_number='32616480680',card_fields_json=json.dumps(card)))
        docs=self.docs('Участники закупки | Только субъекты малого и среднего предпринимательства')
        ns={'conn':lambda:sqlite3.connect(':memory:')}
        enrich(ns,p,docs,card={'status':'partial','pages':{}})
        self.assertEqual(resolve(p,docs)['state'],'conflict')
        self.assertFalse(p['fact_evidence']['sme_only']['value'])
    def test_legacy_document_fact_not_reused(self):
        p=self.purchase();p['fact_evidence']={'sme_only':dict(value=True,source='official_eis_document',evidence='old broad match')}
        self.assertIsNone(resolve(p,self.docs())['value'])
    def test_ordinary_xml_not_false(self):
        p=self.purchase();p['card_fields_json']={'soap_metadata':dict(xml_type='purchaseNoticeAE',xml_sha256='b'*64,source='official_eis_getDocsIP')}
        self.assertEqual(resolve(p)['state'],'unknown')
    def test_no_verified_documents(self):
        docs=self.docs('Участниками закупки могут быть только субъекты малого и среднего предпринимательства: Да')
        docs[0]['meta']['source']='unverified'
        self.assertIsNone(resolve(self.purchase(),docs)['value'])

if __name__=='__main__':unittest.main()
