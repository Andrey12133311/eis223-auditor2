"""Evidence-based recall: ordinary wording is not permission to guess facts."""
import hashlib
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import v330_amendments_223 as rules

REG='32616233661'
URL='https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='+'A'*32
PAGE='https://zakupki.gov.ru/223/purchase/public/purchase/info/changes.html?regNumber='+REG
P={'reg':REG,'method':'Конкурс','submission_end':'2026-10-26'}
POST='Дата публикации изменений в ЕИС: 13.10.2026.'


def doc(text,name='Изменения.pdf'):
    return {'name':name,'status':'checked','text':text,'url':URL,
            'meta':{'source':'official_eis_original','source_url':URL,
                    'sha256':hashlib.sha256(text.encode()).hexdigest()}}


class AmendmentRecallTests(unittest.TestCase):
    def assess(self,text,events=None,name='Изменения.pdf',p=P):
        with patch.object(rules,'manifest_changes',return_value=events or []):
            return rules.evaluation(p,[doc(text,name)])

    def test_formal_dated_decision_and_order(self):
        for start in ('Решение от 06.10.2026 о внесении изменений в документацию',
                      'Приказ № 7/26 от 06.10.2026 о внесении изменений в извещение',
                      'Распоряжение от 06.10.2026 № 17 «О внесении изменений в документацию'):
            with self.subTest(start=start):
                found,_=self.assess(start+'. '+POST)
                self.assertEqual(len(found),1)
                self.assertIn('2026-10-06',found[0]['evidence'])
                self.assertIn('2026-10-09',found[0]['evidence'])
                self.assertIn(URL,found[0]['evidence'])

    def test_table_separators_and_spelled_dates(self):
        found,_=self.assess('Дата принятия решения о внесении изменений | «6» октября 2026 г.\n'
                            'Дата размещения изменений в документацию в ЕИС | 13 октября 2026 г.')
        self.assertEqual(len(found),1)
        self.assertIn('2026-10-13',found[0]['evidence'])

    def test_publication_before_decision_row_is_unique_pair(self):
        found,_=self.assess(POST+'\nДата принятия решения о внесении изменений: 06.10.2026.')
        self.assertEqual(len(found),1)

    def test_reversed_table_cannot_pair_different_changes(self):
        found,review=self.assess('Изменение № 2. '+POST+' Изменение № 1. '
                                'Дата принятия решения о внесении изменений: 06.10.2026.')
        self.assertFalse(found)
        self.assertTrue(review)

    def test_reversed_table_cannot_pair_different_revisions(self):
        for label in ('Редакция','Версия'):
            found,review=self.assess(label+' № 2. '+POST+' '+label+' № 1. '
                                    'Дата принятия решения о внесении изменений: 06.10.2026.')
            self.assertFalse(found)
            self.assertTrue(review)

    def test_original_date_and_generic_notice_publication_are_not_amendment_facts(self):
        for text in ('Документация от 06.10.2026. '+POST,
                     'Решение от 06.10.2026 о проведении закупки. '+POST,
                     'Решение от 06.10.2026 о внесении изменений в документацию. '
                     'Дата публикации извещения в ЕИС: 13.10.2026.'):
            with self.subTest(text=text):self.assertFalse(self.assess(text)[0])

    def test_sample_and_draft_are_not_adoption_evidence(self):
        text='Решение от 06.10.2026 о внесении изменений в документацию. '+POST
        for name in ('Проект решения.pdf','Образец.docx','Пример изменений.docx'):
            self.assertFalse(self.assess(text,name=name)[0])
        self.assertFalse(self.assess('Проект '+text)[0])
        self.assertFalse(self.assess('Например: '+text)[0])

    def test_incomplete_publication_is_reviewed_not_silently_lost(self):
        found,review=self.assess(POST)
        self.assertFalse(found)
        self.assertTrue(any('обе даты' in item['reason'] for item in review))

    def test_history_attachment_identity_survives_page_link(self):
        events=[{'kind':'eis_history','publication_date':date(2026,10,13),
                 'document':'Строка истории ЕИС','source':PAGE,'attachment_source':URL,
                 'publication_evidence':POST}]
        found,_=self.assess('Решение от 06.10.2026 о внесении изменений в документацию.',events)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['document'],'Изменения.pdf')
        self.assertEqual(found[0]['source_url'],PAGE)
        self.assertIn('Решение от 06.10.2026',found[0]['match_text'])
        self.assertIn(URL,found[0]['evidence'])

    def test_history_and_original_date_spelling_are_one_event(self):
        for spelling in ('2026-10-13','13.10.2026','13 октября 2026'):
            event={'kind':'eis_history','publication_date':date(2026,10,13),
                   'document':'Строка истории ЕИС','source':PAGE,'attachment_source':URL,
                   'publication_evidence':'Дата публикации изменений в ЕИС: '+spelling}
            found,_=self.assess('Решение от 06.10.2026 о внесении изменений в документацию. '+POST,[event])
            self.assertEqual(len(found),1,spelling)

    def test_wrapper_deduplicates_semantic_dates_not_quoted_dates(self):
        text='Решение от 06.10.2026 о внесении изменений в документацию. '+POST
        old={'code':'223-4-11-AMENDMENT-PUBLICATION-DELAY','kind':'violation',
             'evidence':'Решение об изменении: 2026-10-06; публикация изменений в ЕИС: 2026-10-13; '
                        'иная дата в выдержке: 01.10.2026.'}
        ns={'analyze':lambda p,d:[old], 'build_checks':lambda *a:[]}
        rules.install(ns)
        with patch.object(rules,'manifest_changes',return_value=[]):
            self.assertEqual(ns['analyze'](P,[doc(text)]),[old])
            different=doc(text.replace('06.10.2026','05.10.2026'))
            self.assertEqual(len(ns['analyze'](P,[different])),2)

    def test_history_does_not_join_other_attachment(self):
        events=[{'kind':'eis_history','publication_date':date(2026,10,13),
                 'document':'Изменения.pdf','source':PAGE,'attachment_source':URL.replace('A'*32,'B'*32)}]
        found,review=self.assess('Решение от 06.10.2026 о внесении изменений в документацию.',events)
        self.assertFalse(found)
        self.assertTrue(review)

    def test_timely_weekend_rollover_invalid_and_historical_dates(self):
        for start,posted in [('06.10.2026','09.10.2026'),('07.10.2026','12.10.2026'),
                             ('31.02.2026','13.10.2026'),('06.10.2018','13.10.2018')]:
            text=f'Решение от {start} о внесении изменений в документацию. Дата публикации изменений в ЕИС: {posted}.'
            with self.subTest(text=text):self.assertFalse(self.assess(text)[0])

    def test_unknown_calendar_does_not_confirm(self):
        with patch.object(rules,'federal_workday',return_value=None):
            found,review=self.assess('Решение от 06.10.2026 о внесении изменений в документацию. '+POST)
            self.assertFalse(found)
            self.assertTrue(any('календарь' in item['reason'] for item in review))

    def test_ambiguous_pairs_remain_review_even_with_single_adoption_day(self):
        text='Решение от 06.10.2026 о внесении изменений в документацию. '+POST+' Дата публикации изменений в ЕИС: 14.10.2026.'
        found,review=self.assess(text)
        self.assertFalse(found)
        self.assertTrue(review)

    def test_copy_not_a_second_violation(self):
        text='Решение от 06.10.2026 о внесении изменений в документацию. '+POST
        with patch.object(rules,'manifest_changes',return_value=[]):
            self.assertEqual(len(rules.evaluation(P,[doc(text),doc(text,'Копия.pdf')])[0]),1)


if __name__=='__main__':unittest.main()
