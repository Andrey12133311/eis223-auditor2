"""Source-backed complete buyer names, using synthetic procurement fixtures."""
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from v314_eis_metadata import parse_text, from_documents, apply_metadata, reconcile
from v342_identity import incomplete_customer_name, customer_name_extension
from v311_verified_customer import _name

PREFIX='Государственное бюджетное учреждение'
FULL=PREFIX+' «Городская больница № 7»'
URL='https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='+'A'*32
REG='32616467075'

def doc(name):
    return {'name':'Извещение.docx','url':URL,'text':'Заказчик: '+name,
            'meta':{'source':'official_eis_original'}}

class CustomerCompletionTests(unittest.TestCase):
    def test_multiline_buyer_and_table(self):
        for label in ('Заказчик: ', 'Наименование заказчика | ', '1 | Наименование заказчика\n'):
            for suffix in ('«Городская больница № 7»','«Городская\nбольница № 7»'):
                fields=parse_text(label+PREFIX+'\n'+suffix+'\nИНН 6501309746')
                self.assertEqual(fields['customer'],FULL)
        self.assertEqual(parse_text('Заказчик: '+PREFIX+'\nПоставщик: ООО «Чужой»')['customer'],PREFIX)
        self.assertEqual(parse_text('Заказчик: '+PREFIX+'\nПредмет закупки: «Поставка»')['customer'],PREFIX)

    def test_all_source_quote_pairs_can_wrap(self):
        for opening, closing in [('«','»'), ('"','"'), ('„','“'), ('“','”')]:
            source=PREFIX+' '+opening+'Городская\nбольница № 7'+closing
            self.assertEqual(parse_text('Заказчик: '+source)['customer'],source.replace('\n',' '))

    def test_candidates_do_not_choose_another_buyer(self):
        self.assertEqual(from_documents(REG,[doc(PREFIX),doc(FULL)])['customer'],FULL)
        self.assertEqual(from_documents(REG,[doc(PREFIX),doc(FULL),doc(PREFIX+' «Иная»')])['customer'],PREFIX)
        self.assertEqual(from_documents(REG,[doc('ООО «Полное имя»'),doc(FULL)])['customer'],'ООО «Полное имя»')
        self.assertFalse(customer_name_extension(PREFIX,'ООО «Иная»'))
        self.assertFalse(customer_name_extension('ООО «Центр»','ООО «Центр» филиал'))

    def test_conflicting_inns_cannot_complete_prefix(self):
        docs=[doc(PREFIX),doc(FULL+'\nИНН 6501309746'),doc(FULL+'\nИНН 2801020845')]
        self.assertEqual(from_documents(REG,docs)['customer'],PREFIX)
        fields=from_documents(REG,docs[1:])
        self.assertTrue(fields['customer_identity_conflict'])

    def test_long_authoritative_name_is_not_rejected(self):
        name=PREFIX+' «'+('Региональный центр ' * 28).strip()+'»'
        self.assertGreater(len(name),400)
        self.assertEqual(_name(name),name)

    def test_background_sweep_includes_prefix_with_other_fields_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sweep.db'
            def conn(): return sqlite3.connect(path)
            with conn() as db:
                db.execute('CREATE TABLE purchases(reg_number TEXT,customer TEXT,customer_inn TEXT,title TEXT,price_num REAL,last_seen TEXT)')
                db.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?)',(REG,PREFIX,'6501309746','Поставка оборудования',100,'2026-10-18'))
            with patch('v314_eis_metadata.stored_metadata',return_value={}) as stored:
                result=reconcile({'conn':conn})
            self.assertEqual(result['reviewed'],1)
            stored.assert_called_once()

    def test_repair_is_scoped_idempotent_and_checks_inn(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db'
            def conn():
                db=sqlite3.connect(path);db.row_factory=sqlite3.Row;return db
            ns={'conn':conn,'org_form':lambda _: 'ГБУ'}
            with conn() as db:
                db.executescript('''CREATE TABLE purchases(reg_number TEXT,customer TEXT,customer_inn TEXT,title TEXT,org_form TEXT,card_fields_json TEXT);
                CREATE TABLE v311_verified_buyers(reg TEXT PRIMARY KEY,name TEXT,inn TEXT,evidence_page TEXT,evidence_source TEXT,captured_at TEXT);
                CREATE TABLE findings(evidence TEXT); INSERT INTO findings VALUES('untouched');''')
                db.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?)',(REG,PREFIX,'6501309746','Поставка оборудования','ГБУ',json.dumps({'status':'partial','summary':{}})))
                db.execute('INSERT INTO v311_verified_buyers VALUES(?,?,?,?,?,?)',(REG,PREFIX,'6501309746',URL,'official_eis_document','old'))
            metadata={'customer':FULL,'customer_inn':'2801020845','source_url':URL}
            self.assertFalse(apply_metadata(ns,REG,metadata))
            metadata['customer_inn']='6501309746'
            self.assertTrue(apply_metadata(ns,REG,metadata))
            self.assertFalse(apply_metadata(ns,REG,metadata))
            with conn() as db:
                row=db.execute('SELECT * FROM purchases').fetchone()
                self.assertEqual(row['customer'],FULL)
                self.assertEqual(row['title'],'Поставка оборудования')
                self.assertEqual(json.loads(row['card_fields_json'])['status'],'partial')
                self.assertEqual(db.execute('SELECT name FROM v311_verified_buyers').fetchone()[0],FULL)
                self.assertEqual(db.execute('SELECT evidence FROM findings').fetchone()[0],'untouched')

if __name__=='__main__': unittest.main()
