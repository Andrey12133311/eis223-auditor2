"""Disk hashing must not block unrelated SQLite intake or discard new rows."""
import ast
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class ReconciliationLockScope(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/'test.db'
        def connect():
            c=sqlite3.connect(self.path,timeout=.05)
            c.row_factory=sqlite3.Row
            return c
        self.connect=connect
        c=connect()
        c.executescript('''CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,url TEXT,local_path TEXT,status TEXT,text_chars INTEGER,metadata_json TEXT);
        CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,documents_found INTEGER,documents_checked INTEGER);
        CREATE TABLE intake(id INTEGER PRIMARY KEY);
        INSERT INTO purchases VALUES('123',2,2);''')
        self.files=[]
        for i in (1,2):
            path=self.root/f'{i}.txt';path.write_text('same verified original')
            self.files.append(path)
            c.execute('INSERT INTO docs VALUES(?,?,?,?,?,?,?,?)',(i,'123',path.name,'official',str(path),'checked',22,'{}'))
        c.commit();c.close()
        source=Path(__file__).resolve().parents[1]/'scripts/v295_eis_originals.py'
        module=ast.parse(source.read_text())
        install=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='install')
        fn=next(n for n in install.body if isinstance(n,ast.FunctionDef) and n.name=='reconcile_document_rows')
        ns={'ns':{'conn':connect},'allowed_documents':lambda reg:{'official':{}},'Path':Path,
            'MAX_BYTES':100000,'hashlib':hashlib,'json':json,'uid_from_url':lambda url:url}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns)
        self.reconcile=ns['reconcile_document_rows']

    def test_intake_can_write_while_hashing_then_duplicates_reconcile(self):
        original=hashlib.file_digest
        def digest(*args,**kwargs):
            c=self.connect()
            try:
                c.execute('INSERT INTO intake DEFAULT VALUES');c.commit()
            finally:c.close()
            return original(*args,**kwargs)
        with patch.object(hashlib,'file_digest',side_effect=digest):
            result=self.reconcile('123')
        self.assertEqual(result,{'remaining':1,'checked':1,'removed':1})
        c=self.connect()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM intake').fetchone()[0],2)
        c.close()
        self.assertTrue(all(p.exists() for p in self.files))

    def test_changed_rows_are_not_deleted(self):
        original=hashlib.file_digest
        def digest(*args,**kwargs):
            c=self.connect()
            c.execute("UPDATE docs SET metadata_json='changed' WHERE id=2");c.commit();c.close()
            return original(*args,**kwargs)
        with patch.object(hashlib,'file_digest',side_effect=digest):
            self.assertIsNone(self.reconcile('123'))
        c=self.connect()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM docs').fetchone()[0],2)
        c.close()

    def test_changed_file_is_not_deduplicated(self):
        original=hashlib.file_digest
        calls=0
        def digest(*args,**kwargs):
            nonlocal calls
            result=original(*args,**kwargs);calls+=1
            if calls==2:self.files[0].write_text('changed after its hash')
            return result
        with patch.object(hashlib,'file_digest',side_effect=digest):
            self.assertIsNone(self.reconcile('123'))
        c=self.connect()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM docs').fetchone()[0],2)
        c.close()

if __name__=='__main__':unittest.main()
