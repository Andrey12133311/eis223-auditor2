"""Compare real legal filters and SQLite persistence on shared synthetic originals.

Retrieval and metadata enrichment are isolated; existing legal rules are unchanged.
"""
import asyncio,sqlite3,tempfile,hashlib,sys,unittest
from copy import deepcopy
sys.path.insert(0,str(__import__("pathlib").Path(__file__).resolve().parents[1]/"scripts"))
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import v332_all_procurements_audit as general
import v342_thematic_queue as theme
import v341_precise_legal as precise
import v348_cross_document_legal as cross
import v372_auto_regulation_protocols as protocols
import v314_eis_metadata as metadata
from v372_automatic_legal_selftest import official,PROTOCOL_HEAD
REG='32616412317'
P={'reg_number':REG,'customer':'ООО Тест','title':'Поставка материалов','method':'Открытый конкурс','published_at':'2026-10-01','submission_end':'2026-10-30'}
docs=[official('Протокол рассмотрения заявок.pdf',PROTOCOL_HEAD+'По заявке № 12 комиссия приняла решение: отклонена заявка № 12, основания отклонения не указаны. Итоги рассмотрения зафиксированы.'),official('Разъяснения.pdf','Дата поступления запроса разъяснения: 01.10.2026. Дата публикации ответа в ЕИС: 07.10.2026.'),official('Отмена.pdf','Дата принятия решения об отмене закупки: 01.10.2026. Дата публикации решения об отмене закупки в ЕИС: 02.10.2026.')]
for d in docs:d['meta']['sha256']=hashlib.sha256(d['text'].encode()).hexdigest()
def compare(originals):
    docs=deepcopy(originals)
    with tempfile.TemporaryDirectory() as tmp:
     path=Path(tmp)/'audit.db'
     def conn():
      c=sqlite3.connect(path);c.row_factory=sqlite3.Row;return c
     c=conn();c.executescript('''CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,violations INTEGER,checks_ok INTEGER,checks_attention INTEGER); CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,kind TEXT,title TEXT,law TEXT,law_url TEXT,evidence TEXT,note TEXT,document TEXT,source_url TEXT,location TEXT,match_text TEXT); CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,item TEXT,status TEXT,evidence TEXT,source TEXT,source_url TEXT,document TEXT,field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT);''');c.execute('INSERT INTO purchases VALUES(?,0,0,0)',(REG,));c.commit();c.close()
     ns={'conn':conn,'analyze':lambda p,d:[],'build_checks':lambda p,de,ca,d:[]}
     precise.install(ns);cross.install(ns);protocols.install(ns)
     with ExitStack() as s:
      s.enter_context(patch.object(general,'existing',return_value=(P,[])))
      s.enter_context(patch.object(general,'stored_docs',return_value=(docs,0)))
      s.enter_context(patch.object(general,'allowed_documents',return_value={}))
      s.enter_context(patch.object(metadata,'from_documents',return_value={}))
      s.enter_context(patch.object(precise,'enrich',side_effect=lambda ns,p,d,*a,**kw:p))
      s.enter_context(patch.object(precise,'persist_facts',return_value=None))
      first=asyncio.run(general.inspect_one(ns,REG)); second=asyncio.run(general.inspect_one(ns,REG)); thematic=asyncio.run(theme.independent_review(ns,REG,['protocols','dates']))
     c=conn();found=[dict(r) for r in c.execute('SELECT code,document,source_url,evidence FROM findings')];counts=dict(c.execute('SELECT * FROM purchases').fetchone());c.close()
     return first,second,thematic,found,counts


class GeneralThematicParity(unittest.TestCase):
    def test_same_verified_originals_have_identical_findings(self):
        first, second, thematic, found, counts = compare(docs)
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["verified_original_documents"], 3)
        self.assertEqual(first[1]["new_findings"], 3)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual(second[1]["new_checks"], 0)
        self.assertEqual((thematic[0], len(thematic[1])), ("completed", 3))
        self.assertEqual({x["code"]: x["evidence"] for x in found},
                         {x["code"]: x["evidence"] for x in thematic[1]})
        self.assertEqual(counts["violations"], 3)

    def test_earlier_unverified_duplicate_must_not_hide_verified_protocol(self):
        unverified = deepcopy(docs[0])
        unverified["name"] = "Earlier local copy.pdf"
        unverified["meta"]["source"] = "existing_local_unverified"
        first, _, thematic, found, _ = compare([unverified] + docs)
        self.assertEqual(first[1]["verified_original_documents"], 3)
        self.assertEqual(first[1]["new_findings"], 3)
        self.assertEqual({x["code"] for x in found}, {x["code"] for x in thematic[1]})

    def test_merge_keeps_distinct_hash_order_and_first_verified_copy(self):
        unverified = deepcopy(docs[0])
        unverified["meta"]["source"] = "existing_local_unverified"
        later_verified = deepcopy(docs[0])
        later_verified["name"] = "Later official copy.pdf"
        merged = general.merge_docs([], [unverified, docs[1], docs[0], later_verified, docs[2]])
        self.assertEqual([d["name"] for d in merged], [d["name"] for d in docs])
        self.assertEqual(general.merge_docs([docs[0]], [unverified, later_verified]), [docs[0]])

    def test_merge_does_not_promote_missing_proof_or_mismatched_hash(self):
        unverified = deepcopy(docs[0])
        unverified["meta"]["source"] = "existing_local_unverified"
        for change in ({"source": "unknown"}, {"source_url": ""}):
            candidate = deepcopy(docs[0])
            candidate["meta"].update(change)
            self.assertEqual(general.merge_docs([], [unverified, candidate]), [unverified])
        for digest in ("", "invalid"):
            candidate = deepcopy(docs[0])
            candidate["meta"]["sha256"] = digest
            self.assertEqual(general.merge_docs([], [unverified, candidate]), [unverified])
        # A different valid hash is a distinct document, not proof for the old one.
        self.assertEqual(general.merge_docs([], [unverified, docs[1]]), [unverified, docs[1]])
        self.assertFalse(general.is_official(unverified))

    def test_unverified_originals_never_establish_violations(self):
        unverified = deepcopy(docs)
        for doc in unverified:
            doc["meta"]["source"] = "existing_local_unverified"
        first, _, thematic, found, counts = compare(unverified)
        self.assertEqual(first[1]["verified_original_documents"], 0)
        self.assertEqual(first[1]["new_findings"], 0)
        self.assertEqual(thematic, ("waiting_originals", []))
        self.assertEqual(found, [])
        self.assertEqual(counts["violations"], 0)


if __name__ == "__main__":
    unittest.main()
