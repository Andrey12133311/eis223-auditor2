"""Conservative buyer-name presentation; synthetic names only."""
import copy
import html
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from v342_identity import organization_display_name
from v311_verified_customer import _name
from v304_official_memo_odt import _plain_company
from v304_memo_odt import _human_org
from v352_dashboard_truth import dashboard_counts
from v343_fast_cards import render


class OrganizationDisplayTests(unittest.TestCase):
    def test_only_quoted_initial_changes(self):
        cases={
            'Государственное автономное учреждение «центр ИТ и РЖД»':
                'Государственное автономное учреждение «Центр ИТ и РЖД»',
            'Государственное автономное учреждение "центр ИТ и РЖД"':
                'Государственное автономное учреждение "Центр ИТ и РЖД"',
            'ООО "центр" филиал «юг»':'ООО "Центр" филиал «Юг»',
            'ГАУ «\u00a0ёлочка iTech»':'ГАУ «\u00a0Ёлочка iTech»',
            'ООО «центр "мир" РЖД»':'ООО «Центр "Мир" РЖД»',
            'ООО „центр ИТ“ филиал':'ООО „Центр ИТ“ филиал',
            'ООО “центр ИТ”':'ООО “Центр ИТ”',
            'ООО «РЖД»':'ООО «РЖД»',
            'ФГБУ «Центр iTech РАН»':'ФГБУ «Центр iTech РАН»',
            'ГАУЗ АО «АОКБ»':'ГАУЗ АО «АОКБ»',
            'Государственное автономное учреждение без кавычек':
                'Государственное автономное учреждение без кавычек',
            'ООО «123 центр»':'ООО «123 центр»',
            'ООО «»':'ООО «»',
            'ООО «центр':'ООО «Центр',
            '': '',
        }
        for source,expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(organization_display_name(source),expected)
                self.assertEqual(organization_display_name(expected),expected)
        self.assertEqual(organization_display_name(None),"")

    def test_metadata_and_both_exports_preserve_authoritative_case(self):
        for source in ('ООО "центр iTech РЖД"',
                       'ГОСУДАРСТВЕННОЕ АВТОНОМНОЕ УЧРЕЖДЕНИЕ «ЦЕНТР РАН»',
                       'ГАУ «Центр iTech РАН»'):
            expected=organization_display_name(source)
            for formatter in (_name,_plain_company,_human_org):
                with self.subTest(formatter=formatter.__name__,source=source):
                    self.assertEqual(formatter(source),expected)

    def test_fast_card_is_read_only_and_escapes_html(self):
        source='Государственное автономное учреждение "центр ИТ <тест> ' + 'длинное официальное название '*25 + '"'
        data=({"customer":source,"title":"Синтетическая закупка"},[],[],[],0,0,0)
        before=copy.deepcopy(data)
        markup=render("32615969056",data)
        self.assertIn(html.escape(organization_display_name(source),quote=True),markup)
        self.assertEqual(data,before)

    def test_dashboard_formats_existing_record_without_db_rewrite(self):
        source='Государственное автономное учреждение "центр ИТ ' + 'длинное официальное название '*25 + '"'
        db=sqlite3.connect(":memory:")
        db.row_factory=sqlite3.Row
        db.executescript("""
          CREATE TABLE purchases(reg_number TEXT, customer TEXT, risks INT,
            documents_found INT, documents_checked INT, violations INT,last_seen TEXT);
          CREATE TABLE findings(reg_number TEXT,title TEXT,law TEXT,evidence TEXT,
            document TEXT,source_url TEXT,code TEXT,kind TEXT);
          CREATE TABLE checks(reg_number TEXT,item TEXT,law TEXT,evidence TEXT,
            document TEXT,source_url TEXT,status TEXT);
        """)
        db.execute("INSERT INTO purchases VALUES(?,?,0,1,1,0,'2026-10-18')",
                   ("32615969056",source))
        _,rows=dashboard_counts(db," FROM purchases p",[],0,20)
        self.assertEqual(rows[0]["customer"],organization_display_name(source))
        self.assertEqual(db.execute("SELECT customer FROM purchases").fetchone()[0],source)
        db.close()

    def test_long_full_names_survive_dashboard_and_card_rendering(self):
        prefix = 'Государственное бюджетное учреждение '
        body = ('Региональный центр исследований ' * 16) + 'ИТ & <Развитие> РАН'
        names = [prefix + opening + body + closing
                 for opening, closing in (('«', '»'), ('"', '"'), ('„', '“'), ('“', '”'))]
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript('''
          CREATE TABLE purchases(reg_number TEXT, customer TEXT, risks INT,
            documents_found INT, documents_checked INT, violations INT,last_seen TEXT);
          CREATE TABLE findings(reg_number TEXT,title TEXT,law TEXT,evidence TEXT,
            document TEXT,source_url TEXT,code TEXT,kind TEXT);
          CREATE TABLE checks(reg_number TEXT,item TEXT,law TEXT,evidence TEXT,
            document TEXT,source_url TEXT,status TEXT);
        ''')
        for index, name in enumerate(names):
            self.assertGreater(len(name), 400)
            db.execute("INSERT INTO purchases VALUES(?,?,0,1,1,0,'2026-10-18')",
                       (str(32615969050 + index), name))
        _, rows = dashboard_counts(db, ' FROM purchases p', [], 0, 20)
        expected_names = {str(32615969050 + index): name for index, name in enumerate(names)}
        self.assertEqual(len(rows), len(names))
        for row in rows:
            source = expected_names[row['reg_number']]
            with self.subTest(source=source):
                expected = organization_display_name(source)
                self.assertEqual(row['customer'], expected)
                markup = render(row['reg_number'], (row, [], [], [], 0, 0, 0))
                self.assertIn(html.escape(expected, quote=True), markup)
                self.assertNotIn('<Развитие>', markup)
                self.assertEqual(db.execute('SELECT customer FROM purchases WHERE reg_number=?',
                                            (row['reg_number'],)).fetchone()[0], source)


if __name__=="__main__":
    unittest.main()
