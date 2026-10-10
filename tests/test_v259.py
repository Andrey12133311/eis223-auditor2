import sqlite3
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from patch_v259 import RULES,BOOTSTRAP,patch_text

class RecoveryTests(unittest.TestCase):
    def test_source_regression_guard(self):
        with self.assertRaisesRegex(RuntimeError,'anchor mismatch'):
            patch_text('def noop():return None')

    def test_only_link_backed_documents_may_stage(self):
        scope={'safe_host':lambda url: url.startswith('https://zakupki.gov.ru/') or url.startswith('https://roseltorg.ru/')}
        exec(BOOTSTRAP,scope)
        is_source=scope['_source_candidate259']
        self.assertFalse(is_source({'name':'PDF','url':'https://evil.example/file.pdf'}))
        self.assertFalse(is_source({'name':'Unknown','url':None,'meta':None}))
        self.assertFalse(is_source({'name':'Unknown','meta':{'alt_urls':'bad URL'}}))
        self.assertTrue(is_source({'name':'PDF','url':'https://zakupki.gov.ru/file.pdf','meta':None}))
        self.assertTrue(is_source({'name':'PDF','url':'','meta':{'alt_urls':'https://roseltorg.ru/file.pdf'}}))
        self.assertFalse(is_source({'meta':{'alt_urls':['https://[bad']}}))

    def test_pending_jobs_have_bounded_attempts(self):
        db=sqlite3.connect(':memory:')
        db.executescript("""CREATE TABLE document_jobs_v204(reg TEXT,status TEXT,attempts INT,source_host TEXT);
        CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,last_seen TEXT,documents_checked INT);
        INSERT INTO purchases VALUES('ok','2026-01-01',0);
        INSERT INTO purchases VALUES('expired','2026-01-01',0);
        INSERT INTO purchases VALUES('documentless','2026-01-01',0);
        INSERT INTO document_jobs_v204 VALUES('ok','waiting',2,'zakupki.gov.ru');
        INSERT INTO document_jobs_v204 VALUES('expired','waiting',8,'zakupki.gov.ru');""")
        query=RULES[0][1]
        self.assertIn('NOT EXISTS',query)
        self.assertIn('j.attempts<8',query)
        sql='SELECT reg_number FROM purchases WHERE '+query.split('WHERE ',1)[1]
        data=[x[0] for x in db.execute(sql,('2026-06-01',))]
        self.assertNotIn('ok',data)
        self.assertIn('expired',data)
        self.assertIn('documentless',data)
        db.close()

    def test_ui_counters_untouched(self):
        text=' '.join(y for x,y in RULES)+BOOTSTRAP
        for phrase in ('v259-progress','new-dashboard','verified_stats244','v257_features','stats.total'):
            self.assertNotIn(phrase,text)
        self.assertIn('STAGED_PENDING_ORIGINAL',text)
        self.assertIn('HIDDEN_PENDING_DOCS',text)

if __name__=='__main__':unittest.main()
