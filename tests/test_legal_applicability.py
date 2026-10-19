"""No network or production state: applicability and amendment evidence fixtures."""
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from legal_applicability import competitive_scope
import v330_amendments_223 as changes
from v341_precise_legal import evaluate
from v336_legal_matrix import security_findings
from v372_auto_regulation_protocols import _competitive
from v342_thematic_queue import competitive_kind

SOURCE='https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='+'A'*32
P={'reg':'32616233661','method':'Конкурс','submission_end':'2026-10-26'}
def original(text,source=SOURCE):
 return {'name':'Изменения.pdf','status':'checked','text':text,'url':source,
         'meta':{'source':'official_eis_original','sha256':'a'*64,'source_url':source}}
def pair(decision='06.10.2026',published='13.10.2026'):
 return 'Дата принятия решения о внесении изменений: '+decision+'. Дата публикации изменений в ЕИС: '+published+'.'

class ScopeTests(unittest.TestCase):
 def test_standard_methods(self):
  for method in ['Аукцион в электронной форме','Открытый конкурс','Запрос котировок','Запрос предложений','Конкурентная закупка']:
   with self.subTest(method=method):self.assertIs(competitive_scope({'method':method}),True)
 def test_explicit_noncompetitive(self):
  for method in ['Неконкурентная закупка','Неконкурентный запрос предложений','Закупка у единственного поставщика','Прямая закупка','Закупка без проведения конкурса','Не конкурентная процедура','Не-конкурентная закупка','Закупка не является конкурентной закупкой','Не конкурс, а закупка у ЕП']:
   with self.subTest(method=method):
    self.assertIs(competitive_scope({'method':method}),False)
    self.assertEqual(competitive_kind(method)['classification'],'noncompetitive')
 def test_unknown_not_inferred(self):
  for method in ['', 'Запрос оферт', 'Сравнение цен']:
   self.assertIsNone(competitive_scope({'method':method}))
 def test_conflicting_fields(self):
  self.assertIsNone(competitive_scope({'method':'Аукцион','purchase_method':'Прямая закупка'}))
 def test_secondary_field(self):
  self.assertIs(competitive_scope({'purchase_method':'Конкурс'}),True)
 def test_ui_quoted_whitespace_regression(self):
  self.assertEqual(competitive_kind('Запрос котировок')['classification'],'competitive')
  self.assertEqual(competitive_kind('Закупка у единственного поставщика')['classification'],'noncompetitive')
 def test_protocol_title_cannot_supply_scope(self):
  self.assertFalse(_competitive({}, {'name':'Протокол конкурса.pdf'}))
  self.assertFalse(_competitive({'method':'неконкурентная закупка'},{}))
 def test_security_noncompetitive_gate(self):
  for method in ['Неконкурентная закупка','Неконкурентный запрос предложений']:
   self.assertEqual(security_findings({'method':method},[]),([],[]))
 def test_clarification_noncompetitive_gate(self):
  doc=original('Дата поступления запроса разъяснений: 06.10.2026. Дата публикации разъяснения в ЕИС: 13.10.2026.')
  self.assertEqual(len(evaluate(P,[doc])[0]),1)
  found,review=evaluate({**P,'method':'Неконкурентный запрос предложений'},[doc])
  self.assertFalse(found);self.assertTrue(review)

class AmendmentTests(unittest.TestCase):
 def run_rules(self,p=P,docs=None,events=None):
  with patch.object(changes,'manifest_changes',return_value=events or []):
   return changes.evaluation(p,docs or [original(pair())])
 def test_proven_positive(self):
  found,review=self.run_rules();self.assertEqual(len(found),1)
  self.assertIn('2026-10-09',found[0]['evidence'])
 def test_no_breach_without_scope(self):
  for method in ['', 'Неконкурентная закупка','Запрос оферт']:
   with self.subTest(method=method):
    found,review=self.run_rules({**P,'method':method});self.assertFalse(found);self.assertTrue(review)
 def test_old_notice_review(self):
  found,review=self.run_rules({**P,'published':'2018-11-01'})
  self.assertFalse(found);self.assertIn('исторической',review[0]['reason'])
 def test_old_event_review(self):
  found,review=self.run_rules(docs=[original(pair('06.10.2018','13.10.2018'))])
  self.assertFalse(found);self.assertTrue(review)
 def test_missing_publication_review(self):
  found,review=self.run_rules(docs=[original('Дата принятия решения о внесении изменений: 06.10.2026. Текст решения заказчика.')])
  self.assertFalse(found);self.assertTrue(review)
 def test_distant_different_amendments_not_paired(self):
  text='Изменение № 1. Дата принятия решения о внесении изменений: 06.10.2026.'+' x '*400+'Изменение № 2. Дата публикации изменений в ЕИС: 13.10.2026.'
  found,review=self.run_rules(docs=[original(text)]);self.assertFalse(found);self.assertTrue(review)
 def test_near_different_amendments_not_paired(self):
  text='Изменение № 1. Дата принятия решения о внесении изменений: 06.10.2026. Изменение № 2. Дата публикации изменений в ЕИС: 13.10.2026.'
  found,review=self.run_rules(docs=[original(text)]);self.assertFalse(found);self.assertTrue(review)
 def test_multiple_publications_ambiguous(self):
  text=pair()+ ' Дата публикации изменений в ЕИС: 14.10.2026.'
  found,review=self.run_rules(docs=[original(text)])
  self.assertFalse(found);self.assertTrue(review)
 def test_do_not_borrow_previous_event(self):
  text=pair('06.10.2026','07.10.2026')+' Дата принятия решения о внесении изменений: 01.10.2026.'
  found,review=self.run_rules(docs=[original(text)])
  self.assertFalse(found);self.assertTrue(review)
 def test_two_local_events(self):
  found,_=self.run_rules(docs=[original(pair()+ ' '+pair('14.10.2026','22.10.2026'))])
  self.assertEqual(len(found),2)
 def test_timely_and_reversed_dates(self):
  for published in ['07.10.2026','05.10.2026']:
   found,_=self.run_rules(docs=[original(pair(published=published))]);self.assertFalse(found)
 def test_unverified_original(self):
  doc=original(pair());doc['meta']['source']='unverified'
  found,_=self.run_rules(docs=[doc]);self.assertFalse(found)
 def test_filename_cannot_join_history(self):
  event={'publication_date':date(2026,10,13),'document':'Изменения.pdf','source':'https://zakupki.gov.ru/changes.html'}
  docs=[original('Дата принятия решения о внесении изменений: 06.10.2026. Текст решения заказчика.')]
  found,review=self.run_rules(docs=docs,events=[event]);self.assertFalse(found);self.assertTrue(review)
 def test_identity_unique_join(self):
  event={'publication_date':date(2026,10,13),'document':'Иная подпись','source':SOURCE+'&download=1'}
  docs=[original('Дата принятия решения о внесении изменений: 06.10.2026. Текст решения заказчика.')]
  found,_=self.run_rules(docs=docs,events=[event]);self.assertEqual(len(found),1)
 def test_identity_ambiguous_dates_no_join(self):
  event={'publication_date':date(2026,10,13),'document':'Изменения.pdf','source':SOURCE}
  docs=[original('Дата принятия решения о внесении изменений: 06.10.2026. Дата принятия решения о внесении изменений: 07.10.2026.')]
  found,review=self.run_rules(docs=docs,events=[event]);self.assertFalse(found);self.assertTrue(review)
 def test_repeated_copy_no_duplicate(self):
  doc=original(pair());found,_=self.run_rules(docs=[doc,dict(doc)]);self.assertEqual(len(found),1)
 def test_extension_unknown_scope_not_breach(self):
  event={'publication_date':date(2026,10,13),'submission_end':date(2026,10,9),'source':SOURCE}
  found,review=self.run_rules(p={**P,'method':''},events=[event]);self.assertFalse(found);self.assertTrue(review)
if __name__=='__main__':unittest.main()
