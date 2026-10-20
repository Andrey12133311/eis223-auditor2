"""Distinct locally evidenced protocols survive identical dates and binary copies.

The metadata records actual parsed source slices; it is not cryptographic
authentication of caller content. Independent receipt intake is tested by
test_local_event_pipeline_parity with real DOCX/ZIP/XML bytes.
"""
import copy
import hashlib
from itertools import permutations
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))

import legal_audit_revision as revision
import v333_dedup_ui as dedup
import v341_precise_legal as precise
import v348_cross_document_legal as cross
import v352_dashboard_truth as dashboard
import v353_episode_guard as episodes
import v332_all_procurements_audit as general
import v372_auto_regulation_protocols as protocols


REG="32610002001"
PURCHASE={"reg":REG,"method":"Аукцион в электронной форме","published":"2026-10-01"}
SOURCE="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def section(number="1"):
    return ("Номер протокола: "+number+"\n"
            "Дата подписания протокола рассмотрения заявок: 06.10.2026\n"
            "Дата размещения протокола рассмотрения заявок в ЕИС: 13.10.2026")


def document(numbers=("1","2"),uid=1):
    body="\n".join(section(number) for number in numbers)
    source=SOURCE+f"{uid:032X}"
    return {"name":"Сводная информация о протоколах.pdf","text":body,"status":"checked","url":source,
            "meta":{"source":"official_eis_original","source_url":source,
                    "sha256":hashlib.sha256(body.encode()).hexdigest()}}


def tamper(item,change):
    output=copy.deepcopy(item)
    lines=output["evidence"].split("\n",3)
    payload=json.loads(lines[2][len(cross._LOCAL_EVENT_PREFIX):-1])
    change(payload)
    lines[2]=cross._LOCAL_EVENT_PREFIX+json.dumps(payload,ensure_ascii=False,separators=(",",":"))+"."
    output["evidence"]="\n".join(lines)
    return output


class LocalNumberedEventRecall(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="eis-local-episodes-")
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"audit.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,violations INTEGER DEFAULT 0,
                    checks_ok INTEGER DEFAULT 0,checks_attention INTEGER DEFAULT 0);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,kind TEXT,title TEXT,
                    law TEXT,law_url TEXT,evidence TEXT,note TEXT,document TEXT,source_url TEXT,location TEXT,match_text TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,item TEXT,status TEXT,evidence TEXT,
                    source TEXT,source_url TEXT,document TEXT,field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT);
            """)
            db.execute("INSERT INTO purchases(reg_number) VALUES (?)",(REG,))

    def connect(self):
        db=sqlite3.connect(self.path)
        db.row_factory=sqlite3.Row
        return db

    def evaluate(self,docs):
        return precise.evaluate(dict(PURCHASE),docs)[0]

    def test_distinct_same_day_local_events_retain_physical_binary_identity(self):
        found=self.evaluate([document()])
        self.assertEqual(len(found),2)
        self.assertEqual({cross.finding_protocol_number(row) for row in found},{"1","2"})
        self.assertTrue(all(cross.finding_local_event(row) for row in found))
        self.assertTrue(cross.same_original_identity(*found))
        self.assertTrue(cross.distinct_local_events(*found))
        self.assertFalse(episodes.same_episode(*found))
        self.assertEqual(len(dedup.collapse(found)),2)
        self.assertEqual(len(dashboard.collapse_legal_events(found)),2)

    def test_installed_pipeline_and_db_roundtrip_keep_two_then_replay_zero(self):
        ns={"conn":self.connect,"analyze":lambda p,d:[],"build_checks":lambda p,de,ca,d:[]}
        precise.install(ns)
        cross.install(ns)
        protocols.install(ns)
        with patch.object(precise,"enrich"),patch.object(precise,"persist_facts"):
            found=ns["analyze"](dict(PURCHASE),[document()])
        self.assertEqual(len(found),2)
        self.assertEqual(general.persist(ns,REG,found,[]),(2,0))
        self.assertEqual(general.persist(ns,REG,found,[]),(0,0))
        with self.connect() as db:
            saved=[dict(row) for row in db.execute("SELECT * FROM findings")]
            self.assertEqual(dashboard.load_counts(db,[REG]),{REG:2})
            self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0],2)
        self.assertTrue(all(cross.finding_local_event(row) for row in saved))
        self.assertTrue(cross.same_original_identity(*saved))
        self.assertTrue(cross.distinct_local_events(*saved))

    def test_same_binary_alias_and_duplicate_rows_do_not_multiply_events(self):
        first=document()
        alias=document(uid=2)
        alias["name"]="Копия сводной информации о протоколах.pdf"
        for docs in ([first,alias],[alias,first],[first,copy.deepcopy(first)]):
            with self.subTest(names=[row["name"] for row in docs]):
                self.assertEqual(len(self.evaluate(docs)),2)
        found=self.evaluate([first])
        copies=self.evaluate([alias])
        self.assertEqual(len(dashboard.collapse_legal_events(found+copies)),2)
        self.assertEqual(general.persist({"conn":self.connect},REG,found,[]),(2,0))
        self.assertEqual(general.persist({"conn":self.connect},REG,copies,[]),(0,0))

    def test_repeated_heading_same_number_retains_one_event(self):
        for numbers in (("1","1"),("1","1","1")):
            found=self.evaluate([document(numbers)])
            self.assertEqual(len(found),1)
            self.assertEqual(cross.finding_protocol_number(found[0]),"1")

    def test_unsupported_or_crossed_numbers_cannot_become_independent_proofs(self):
        for numbers in (("1(А)","1(Б)"),("I","II")):
            found=self.evaluate([document(numbers)])
            self.assertEqual(len(found),1)
            self.assertFalse(cross.finding_local_event(found[0]))
        doc=document()
        doc["text"]=("Номер протокола: 1\nДата подписания протокола рассмотрения заявок: 06.10.2026\n"
                     "Номер протокола: 2\nДата размещения протокола рассмотрения заявок в ЕИС: 13.10.2026")
        self.assertFalse(self.evaluate([doc]))

    def test_sample_numbered_section_cannot_add_a_second_real_event(self):
        doc=document()
        doc["text"]="Пример заполнения формы протокола. "+section("1")+"\n"+section("2")
        doc["meta"]["sha256"]=hashlib.sha256(doc["text"].encode()).hexdigest()
        found=self.evaluate([doc])
        self.assertEqual(len(found),1)
        self.assertFalse(cross.finding_local_event(found[0]))

    def test_conflicting_binary_representations_cannot_supply_local_proofs(self):
        first,second=document(),document(("1","3"))
        found=self.evaluate([first,second])
        self.assertEqual(len(found),1)
        self.assertFalse(any(cross.finding_local_event(row) for row in found))

    def test_caller_event_numbers_and_extra_dict_fields_cannot_override_copy_guard(self):
        doc=document(("1",))
        match=precise.matches(precise.SIGNED,doc["text"])[0]
        rows=[precise.finding(doc,"223-4-12-PROTOCOL-PUBLICATION-LATE","Протокол размещён поздно",
                "223-ФЗ, ст. 4 ч. 12","Подписание 2026-10-06; публикация 2026-10-13.",match,event_number=number)
              for number in ("1","2")]
        rows[0]["local_event"]={"number":"1"}
        rows[1]["local_event"]={"number":"2"}
        self.assertTrue(cross.same_original_identity(*rows))
        self.assertFalse(cross.distinct_local_events(*rows))
        self.assertTrue(episodes.same_episode(*rows))
        self.assertEqual(len(dedup.collapse(rows)),1)
        self.assertEqual(len(dashboard.collapse_legal_events(rows)),1)

    def test_incomplete_or_unrelated_number_is_not_a_complete_local_cohort(self):
        doc=document(("1",))
        doc["text"]+="\n"+"x"*1000+"\nНомер протокола: 2"
        found=self.evaluate([doc])
        self.assertEqual(len(found),1)
        self.assertFalse(cross.finding_protocol_number(found[0]))
        self.assertFalse(cross.finding_local_event(found[0]))

    def test_source_bound_refinement_retains_legacy_evidence_and_two_card_episodes(self):
        found=self.evaluate([document()])
        generic=copy.deepcopy(found[0])
        lines=generic["evidence"].split("\n",3)
        generic["evidence"]=(cross._identity_prefix("PROTOCOL")+"не установлен.\n"+lines[1]+"\n"+lines[3]
                             +" Дополнительное описание исходного анализа."*20)
        before=copy.deepcopy(generic)
        self.assertFalse(cross.finding_protocol_number(generic))
        for candidate in found:
            self.assertTrue(cross.local_event_refines(candidate,generic))
            self.assertTrue(episodes.distinct_new(candidate,[generic]))
        for ordering in permutations([generic,*found]):
            self.assertEqual(len(dashboard.collapse_legal_events(list(ordering))),2)
            self.assertEqual(len(dedup.collapse(list(ordering))),2)
        self.assertEqual(generic,before)

    def test_legacy_known_number_or_other_source_is_not_refined(self):
        found=self.evaluate([document()])
        generic=copy.deepcopy(found[0])
        lines=generic["evidence"].split("\n",3)
        generic["evidence"]=cross._identity_prefix("PROTOCOL")+"не установлен.\n"+lines[1]+"\n"+lines[3]
        for change in (lambda row:row.update(code="223-3.2-3-CLARIFICATION-RESPONSE-LATE"),
                       lambda row:row.update(evidence=row["evidence"].replace("не установлен.","9.",1)),
                       lambda row:row.update(evidence=row["evidence"].replace(cross.finding_binary_identity(row),"0"*64,1)),
                       lambda row:row.update(evidence=row["evidence"].replace("2026-10-13","2026-10-14"))):
            altered=copy.deepcopy(generic)
            change(altered)
            self.assertFalse(cross.local_event_refines(found[1],altered))

    def test_malformed_or_detached_local_proof_does_not_bypass_identity_guard(self):
        found=self.evaluate([document()])
        edits=(lambda p:p.update(sha256="0"*64),lambda p:p.update(family="CLARIFICATION"),
               lambda p:p.update(number="999"),lambda p:p.update(v=2),lambda p:p.update(span=[-1,750001]),
               lambda p:p["heading"].__setitem__(2,"Номер протокола: 999"),
               lambda p:p["dates"][0].__setitem__(2,"Плановая дата подписания протокола: 06.10.2026"),
               lambda p:p["dates"][0].__setitem__(0,True))
        for change in edits:
            with self.subTest(change=change):
                invalid=tamper(found[1],change)
                self.assertFalse(cross.finding_local_event(invalid))
                self.assertFalse(cross.distinct_local_events(found[0],invalid))

    def test_identifiers_or_metadata_offsets_do_not_become_event_dates(self):
        found=self.evaluate([document(("01.09.2026","02.09.2026"))])
        self.assertEqual(len(found),2)
        for row in found:
            self.assertEqual(precise._event_dates(row),("2026-10-06","2026-10-09","2026-10-13"))

    def test_new_local_proof_dependencies_participate_in_engine_signature(self):
        source=(Path(__file__).resolve().parents[1]/"scripts"/"v348_cross_document_legal.py").read_text()
        roots=revision.DEPENDENCY_REGISTRY["v348_cross_document_legal"]
        for root in ("local_protocol_event","local_protocol_cohort","finding_local_event",
                     "distinct_local_events","local_event_refines"):
            self.assertIn(root,roots)
        baseline=revision.canonical_engine_ast(source,roots,"v348_cross_document_legal")
        changed=source.replace('a[1]<=b[0] or b[1]<=a[0]','a[1]<b[0] or b[1]<a[0]',1)
        self.assertNotEqual(source,changed)
        self.assertNotEqual(baseline,revision.canonical_engine_ast(changed,roots,"v348_cross_document_legal"))


if __name__=="__main__":unittest.main()
