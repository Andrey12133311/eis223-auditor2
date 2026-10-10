from __future__ import annotations
import json,sqlite3,tempfile,time,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from patch_v260 import OLD, NEW, patch_text

class RecoveryTests(unittest.TestCase):
    def test_anchor_compatibility_and_unchanged_ui(self):
        with self.assertRaisesRegex(RuntimeError,'wrapper'):
            patch_text('pass\n')
        self.assertNotIn('dashboard',NEW)
        self.assertNotIn('v260-progress',NEW)
        self.assertNotIn('counter',NEW)
    def test_new_originals_selected_without_unsafe_or_exhausted_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite'
            c=sqlite3.connect(path)
            c.executescript("""CREATE TABLE purchases(reg_number TEXT PRIMARY KEY);
            CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,url TEXT,doc_type TEXT,metadata_json TEXT,local_path TEXT,status TEXT,text_chars INT);
            CREATE TABLE retry_docs_v234(doc_id INTEGER PRIMARY KEY,tries INTEGER,next_at REAL,error TEXT);
            INSERT INTO purchases VALUES('verified'),('fresh'),('bad'),('future'),('exhausted'),('mirror');
            INSERT INTO docs VALUES(1,'verified','old','https://zakupki.gov.ru/o.pdf','pdf','{}','','waiting',0);
            INSERT INTO docs VALUES(2,'fresh','new','https://zakupki.gov.ru/n.pdf','pdf','{}','','waiting',0);
            INSERT INTO docs VALUES(3,'bad','bad','http://127.0.0.1/a.pdf','pdf','{}','','waiting',0);
            INSERT INTO docs VALUES(4,'future','late','https://zakupki.gov.ru/w.pdf','pdf','{}','','waiting',0);
            INSERT INTO docs VALUES(5,'exhausted','stale','https://zakupki.gov.ru/s.pdf','pdf','{}','','waiting',0);
            INSERT INTO docs VALUES(6,'mirror','new','', 'pdf','{"alt_urls":["https://zakupki.gov.ru/m.pdf"]}','','waiting',0);
            INSERT INTO retry_docs_v234 VALUES(4,1,99999999999,'delayed');
            INSERT INTO retry_docs_v234 VALUES(5,4,0,'exhausted');
            """);c.commit();c.close()
            def conn():
                con=sqlite3.connect(path);con.row_factory=sqlite3.Row;return con
            def previous(n,reg,force):
                with conn() as con:
                    return [dict(x) for x in con.execute("SELECT d.id,d.reg_number,d.name,d.url,d.doc_type,d.metadata_json,d.local_path,COALESCE(r.tries,0) tries FROM docs d LEFT JOIN retry_docs_v234 r ON r.doc_id=d.id WHERE d.reg_number='verified'")]
            env={'conn':conn,'_prev_pending236':previous,'_verified_set235':lambda:{'verified'},'_allowed234':lambda u:u.startswith('https://zakupki.gov.ru/'),'_t234':time,'_j234':json,'_P234':Path}
            exec('def _pending234'+NEW.split('def _pending234',1)[1],env)
            rows=env['_pending234'](12)
            regs={x['reg_number'] for x in rows}
            self.assertTrue({'verified','fresh','mirror'}<=regs,regs)
            self.assertFalse({'bad','future','exhausted'}&regs,regs)

if __name__=='__main__':unittest.main()
