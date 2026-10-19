"""Independent numbered events must survive legal analysis, storage and display.

These isolated tests use synthetic verified-source descriptors. Actual saved
receipt, ZIP and XML verification is covered by test_official_publication_evidence.
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from itertools import permutations
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import v314_eis_metadata as metadata
import v332_all_procurements_audit as general
import v341_precise_legal as precise
import v342_thematic_queue as thematic
import v348_cross_document_legal as cross
import v372_auto_regulation_protocols as protocols
from v352_dashboard_truth import collapse_legal_events, load_counts
from v353_episode_guard import same_episode


REG = "32615969056"
PURCHASE = {"reg": REG, "method": "Аукцион в электронной форме",
            "submission_end": "2026-10-30"}
STORED_PURCHASE = {"reg_number": REG, "customer": "ООО Тест",
    "title": "Поставка материалов", "method": PURCHASE["method"],
    "published_at": "2026-10-01", "submission_end": PURCHASE["submission_end"]}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def original(name, body, uid):
    url = SOURCE + f"{uid:032X}"
    return {"name": name, "text": body, "status": "checked", "url": url,
        "meta": {"source": "official_eis_original", "source_url": url,
                 "sha256": hashlib.sha256(body.encode()).hexdigest()}}


def protocol(number, uid=1, posted="13.10.2026"):
    label = " № " + number if number else ""
    body = ("Номер протокола: " + number + "\n" if number else "")
    body += ("Дата подписания итогового протокола: 06.10.2026. "
             "Дата размещения итогового протокола в ЕИС: " + posted + ".")
    return original("Итоговый протокол" + label + ".pdf", body, uid)


def clarification(number, uid=1):
    return original("Разъяснение на запрос № " + number + ".pdf",
        "Номер запроса: " + number + "\n"
        "Дата поступления запроса: 06.10.2026. "
        "Дата публикации разъяснения в ЕИС: 13.10.2026.", uid)


class LegalEventRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="eis-legal-event-recall-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "audit.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,
                    violations INTEGER DEFAULT 0, checks_ok INTEGER DEFAULT 0,
                    checks_attention INTEGER DEFAULT 0);
                CREATE TABLE findings(id INTEGER PRIMARY KEY, reg_number TEXT,
                    code TEXT, kind TEXT, title TEXT, law TEXT, law_url TEXT,
                    evidence TEXT, note TEXT, document TEXT, source_url TEXT,
                    location TEXT, match_text TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY, reg_number TEXT,
                    scope TEXT, item TEXT, status TEXT, evidence TEXT, source TEXT,
                    source_url TEXT, document TEXT, field_path TEXT, note TEXT,
                    law TEXT, law_url TEXT, location TEXT);
            """)
            db.execute("INSERT INTO purchases(reg_number) VALUES (?)", (REG,))

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def evaluate(self, docs):
        return precise.evaluate(dict(PURCHASE), docs)[0]

    def installed(self, existing=()):
        ns = {"conn": self.connect, "analyze": lambda p, d: list(existing),
              "build_checks": lambda p, detail, card, d: []}
        precise.install(ns)
        cross.install(ns)
        protocols.install(ns)
        return ns

    def test_same_day_numbered_protocols_survive_local_analysis(self):
        docs = [protocol("1"), protocol("2", 2)]
        before = deepcopy(docs)
        found = self.evaluate(docs)
        self.assertEqual(len(found), 2)
        self.assertEqual({cross.finding_protocol_number(x) for x in found}, {"1", "2"})
        self.assertFalse(same_episode(*found))
        self.assertEqual(docs, before)

    def test_same_day_numbered_requests_survive_local_analysis(self):
        found = self.evaluate([clarification("1"), clarification("2", 2)])
        self.assertEqual(len(found), 2)
        self.assertEqual({cross.finding_clarification_number(x) for x in found}, {"1", "2"})
        self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_body_only_numbers_survive_analysis_persistence_and_display(self):
        for make, reader in ((protocol, cross.finding_protocol_number),
                             (clarification, cross.finding_clarification_number)):
            with self.subTest(make=make.__name__):
                docs = [make("1"), make("2", 2)]
                for doc in docs:doc["name"] = "Оригинал ЕИС.pdf"
                found = self.evaluate(docs)
                self.assertEqual({reader(item) for item in found}, {"1", "2"})
                self.assertEqual(len(collapse_legal_events(found)), 2)
                self.assertEqual(general.persist({"conn": self.connect}, REG, found, []), (2, 0))
        with self.connect() as db:
            self.assertEqual(load_counts(db, [REG]), {REG: 4})

    def test_conflicting_title_body_identity_cannot_create_a_second_event(self):
        for make, label, reader in ((protocol, "Номер протокола", cross.finding_protocol_number),
                                    (clarification, "Номер запроса", cross.finding_clarification_number)):
            with self.subTest(make=make.__name__):
                docs = [make("1"), make("2", 2)]
                docs[1]["text"] = docs[1]["text"].replace(label + ": 2", label + ": 1")
                docs[1]["meta"]["sha256"] = hashlib.sha256(docs[1]["text"].encode()).hexdigest()
                self.assertEqual(len(self.evaluate(docs)), 1)
                conflicting = self.evaluate(docs[1:])[0]
                self.assertEqual(reader(conflicting), "")
                # Persisted unresolved state must defeat the old filename fallback.
                self.assertEqual(reader({**conflicting, "document": make("999")["name"]}), "")

    def test_full_original_ambiguity_outside_date_snippet_is_preserved(self):
        docs = [protocol("1"), protocol("2", 2)]
        docs[1]["text"] += "\n" + "Сведения об участниках. " * 100 + "\nНомер протокола: 3"
        docs[1]["meta"]["sha256"] = hashlib.sha256(docs[1]["text"].encode()).hexdigest()
        self.assertEqual(cross.finding_protocol_number(self.evaluate(docs[1:])[0]), "")
        self.assertEqual(len(self.evaluate(docs)), 1)

    def test_same_receipt_contradictory_versions_do_not_gain_independent_ids(self):
        docs = [protocol("1"), protocol("2", 2)]
        docs[1]["meta"]["source_url"] = docs[0]["url"] + "&download=1"
        found = self.evaluate(docs)
        self.assertEqual(len(found), 1)
        self.assertEqual(cross.finding_protocol_number(found[0]), "")
        self.assertEqual(general.persist({"conn": self.connect}, REG, found, []), (1, 0))
        with self.connect() as db:self.assertEqual(load_counts(db, [REG]), {REG: 1})

    def test_same_receipt_conflicting_number_on_later_audit_is_not_new(self):
        first = self.evaluate([protocol("1", 1)])
        second = self.evaluate([protocol("2", 1)])
        ns = {"conn": self.connect}
        self.assertEqual(general.persist(ns, REG, first, []), (1, 0))
        self.assertEqual(general.persist(ns, REG, second, []), (0, 0))
        self.assertEqual(len(collapse_legal_events(first + second)), 1)
        with self.connect() as db:self.assertEqual(load_counts(db, [REG]), {REG: 1})

    def test_legacy_same_receipt_conflicting_number_is_not_new(self):
        first = self.evaluate([protocol("1", 1)])[0]
        first["evidence"] = cross.event_evidence_text(first["evidence"])
        second = self.evaluate([protocol("2", 1)])
        ns = {"conn": self.connect}
        self.assertEqual(general.persist(ns, REG, [first], []), (1, 0))
        self.assertEqual(general.persist(ns, REG, second, []), (0, 0))
        self.assertEqual(len(collapse_legal_events([first] + second)), 1)

    def test_identical_binary_under_different_receipts_is_not_a_new_event(self):
        docs = [protocol("1", 1), protocol("2", 2)]
        for doc in docs:
            doc["text"] = doc["text"].split("\n", 1)[1]
            doc["meta"]["sha256"] = hashlib.sha256(doc["text"].encode()).hexdigest()
        first, second = self.evaluate(docs[:1]), self.evaluate(docs[1:])
        ns = {"conn": self.connect}
        self.assertEqual(general.persist(ns, REG, first, []), (1, 0))
        self.assertEqual(general.persist(ns, REG, second, []), (0, 0))
        self.assertEqual(len(collapse_legal_events(first + second)), 1)

    def test_identical_binary_archive_member_copies_do_not_multiply(self):
        docs = [protocol("1", 1), protocol("2", 2)]
        for doc in docs:
            doc["text"] = doc["text"].split("\n", 1)[1]
            doc["meta"].update(source="official_eis_archive_member",
                archive_member=doc["name"], sha256=hashlib.sha256(doc["text"].encode()).hexdigest())
        first, second = self.evaluate(docs[:1]), self.evaluate(docs[1:])
        ns = {"conn": self.connect}
        self.assertEqual(general.persist(ns, REG, first, []), (1, 0))
        self.assertEqual(general.persist(ns, REG, second, []), (0, 0))
        self.assertEqual(len(collapse_legal_events(first + second)), 1)

    def test_legacy_archive_member_identity_does_not_collapse_distinct_members(self):
        docs = [protocol("1", 1), protocol("2", 1)]
        for doc in docs:
            doc["meta"]["source"] = "official_eis_archive_member"
            doc["name"] = "Архив.zip / " + doc["name"]
        first = self.evaluate(docs[:1])[0]
        first["evidence"] = cross.event_evidence_text(first["evidence"])
        second = self.evaluate(docs[1:])
        ns = {"conn": self.connect}
        self.assertEqual(general.persist(ns, REG, [first], []), (1, 0))
        self.assertEqual(general.persist(ns, REG, second, []), (1, 0))
        self.assertEqual(len(collapse_legal_events([first] + second)), 2)

    def test_generic_row_cannot_erase_two_numbered_events_in_any_order(self):
        docs = [protocol("", 1), protocol("1", 2), protocol("2", 3)]
        for ordered in permutations(docs):
            with self.subTest(names=[doc["name"] for doc in ordered]):
                found = self.evaluate(list(ordered))
                self.assertEqual({cross.finding_protocol_number(item) for item in found}, {"1", "2"})

    def test_installed_generic_result_does_not_absorb_numbered_results(self):
        ns = self.installed(self.evaluate([protocol("", 1)]))
        with patch.object(precise, "enrich"), patch.object(precise, "persist_facts"):
            found = ns["analyze"](dict(PURCHASE), [protocol("1", 2), protocol("2", 3)])
        self.assertEqual({cross.finding_protocol_number(item) for item in found}, {"1", "2"})

    def test_prior_generic_evidence_is_unchanged_and_does_not_hide_numbered_events(self):
        ns = {"conn": self.connect}
        generic = self.evaluate([protocol("", 1)])
        self.assertEqual(general.persist(ns, REG, generic, []), (1, 0))
        with self.connect() as db:
            saved = dict(db.execute("SELECT * FROM findings").fetchone())
            db.execute("CREATE TABLE user_decisions(finding_id INTEGER, decision TEXT)")
            db.execute("INSERT INTO user_decisions VALUES (?,?)", (saved["id"], "signed_off"))
        for number, uid in (("1", 2), ("2", 3)):
            found = self.evaluate([protocol(number, uid)])
            self.assertEqual(general.persist(ns, REG, found, []), (1, 0))
            self.assertEqual(general.persist(ns, REG, found, []), (0, 0))
        with self.connect() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM findings WHERE id=?", (saved["id"],)).fetchone()), saved)
            self.assertEqual(tuple(db.execute("SELECT * FROM user_decisions").fetchone()), (saved["id"], "signed_off"))
            self.assertEqual(load_counts(db, [REG]), {REG: 2})

    def test_benign_same_binary_alias_does_not_erase_a_numbered_event(self):
        first, alias, second = protocol("1", 1), protocol("1", 2), protocol("2", 3)
        alias["name"] = "Копия итогового протокола № 1.pdf"
        for ordered in permutations((first, alias, second)):
            found = self.evaluate(list(ordered))
            self.assertEqual({cross.finding_protocol_number(item) for item in found}, {"1", "2"})

    def test_date_shaped_identifier_does_not_add_an_event_date(self):
        first = self.evaluate([protocol("01.09.2026", 1)])[0]
        copied = self.evaluate([protocol("01.09.2026", 2)])[0]
        legacy = {**copied, "evidence": cross.event_evidence_text(copied["evidence"])}
        self.assertTrue(same_episode(legacy, first))
        self.assertEqual(len(collapse_legal_events([legacy, first])), 1)
        self.assertEqual(general.persist({"conn": self.connect}, REG, [legacy, first], []), (1, 0))

    def test_distinct_archive_members_keep_independent_validated_ids(self):
        docs = [protocol("1"), protocol("2", 2)]
        for doc in docs:
            doc["meta"].update(source="official_eis_archive_member",
                source_url=docs[0]["url"], archive_member=doc["name"])
        self.assertEqual(len(self.evaluate(docs)), 2)

    def test_identical_binary_and_generic_copies_do_not_multiply(self):
        docs = [protocol("1"), protocol("2", 2)]
        for doc in docs:
            doc["text"] = doc["text"].split("\n", 1)[1]
            doc["meta"]["sha256"] = hashlib.sha256(doc["text"].encode()).hexdigest()
        self.assertEqual(len(self.evaluate(docs)), 1)
        for doc in docs:doc["name"] = "Итоговый протокол.pdf"
        self.assertEqual(len(self.evaluate(docs + [deepcopy(docs[0])])), 1)

    def test_richer_legacy_generic_copy_cannot_bridge_distinct_protocols(self):
        first = self.evaluate([protocol("1")])[0]
        second = self.evaluate([protocol("2", 2)])[0]
        for item in (first, second):
            item["evidence"] = item["evidence"].split("\n", 1)[1]
        generic = {**first, "document": "Итоговый протокол.pdf",
                   "evidence": first["evidence"] + " Дополнительные проверенные сведения об исходном документе."}
        self.assertEqual(cross.finding_protocol_number(generic), "")
        self.assertEqual(len(collapse_legal_events([first, generic, second])), 2)

    def test_same_number_copies_and_normalized_number_aliases_stay_one_event(self):
        for first, second in (("1", "1"), ("А–1 / 2026", "а-1/2026")):
            with self.subTest(first=first, second=second):
                docs = [protocol(first), protocol(second, 2)]
                self.assertEqual(len(self.evaluate(docs)), 1)
                separate = self.evaluate(docs[:1]) + self.evaluate(docs[1:])
                self.assertEqual(len(collapse_legal_events(separate)), 1)

    def test_missing_or_ambiguous_numbers_do_not_create_extra_events(self):
        for first, second in (("", ""), ("1", ""), ("1(А)", "1(Б)"),
                              ("I", "II")):
            with self.subTest(first=first, second=second):
                docs = [protocol(first), protocol(second, 2)]
                self.assertEqual(len(self.evaluate(docs)), 1)

    def test_timely_dates_and_unverified_sources_still_do_not_establish_violation(self):
        self.assertEqual(self.evaluate([protocol("1", posted="09.10.2026"),
                                        protocol("2", 2, posted="09.10.2026")]), [])
        docs = [protocol("1"), protocol("2", 2)]
        for doc in docs:
            doc["meta"]["source"] = "existing_local_unverified"
        self.assertEqual(self.evaluate(docs), [])

    def test_installed_analyzer_preserves_different_id_but_not_existing_copy(self):
        docs = [protocol("1"), protocol("2", 2)]
        first = self.evaluate(docs[:1])
        ns = self.installed(first)
        with patch.object(precise, "enrich"), patch.object(precise, "persist_facts"):
            found = ns["analyze"](dict(PURCHASE), docs)
        self.assertEqual(len(found), 2)
        self.assertEqual({cross.finding_protocol_number(x) for x in found}, {"1", "2"})

    def test_separately_established_protocols_persist_and_display_once_each(self):
        # Establish individually so this also detects a UI regression independently
        # of the analyzer's own deduplication.
        found = self.evaluate([protocol("1")]) + self.evaluate([protocol("2", 2)])
        ns = {"conn": self.connect}
        self.assertEqual(general.persist(ns, REG, found, []), (2, 0))
        self.assertEqual(general.persist(ns, REG, found, []), (0, 0))
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM findings")]
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(collapse_legal_events(rows)), 2)
            self.assertEqual(load_counts(db, [REG]), {REG: 2})
            self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 2)

    def test_confirmed_checklist_protocols_keep_identity_in_dashboard_counts(self):
        found = self.evaluate([protocol("1")]) + self.evaluate([protocol("2", 2)])
        checks = [{**item, "status": "violation", "item": item["title"],
                   "field_path": item["code"], "scope": "223-ФЗ"} for item in found]
        ns = {"conn": self.connect}
        self.assertEqual(general.persist(ns, REG, [], checks), (0, 2))
        self.assertEqual(general.persist(ns, REG, [], checks), (0, 0))
        self.assertEqual(general.persist(ns, REG, found, []), (0, 0))
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM checks").fetchone()[0], 2)
            self.assertEqual(load_counts(db, [REG]), {REG: 2})

    def test_findings_and_checklist_protocols_keep_cross_table_cardinality(self):
        found = self.evaluate([protocol("1")]) + self.evaluate([protocol("2", 2)])
        check = {**found[1], "status": "violation", "item": found[1]["title"],
                 "field_path": found[1]["code"], "scope": "223-ФЗ"}
        self.assertEqual(general.persist({"conn": self.connect}, REG, found[:1], [check]), (1, 1))
        with self.connect() as db:
            self.assertEqual(load_counts(db, [REG]), {REG: 2})

    def test_legacy_check_schema_without_field_path_remains_readable(self):
        with self.connect() as db:
            db.execute("ALTER TABLE checks DROP COLUMN field_path")
            db.execute("INSERT INTO checks(reg_number,item,status,evidence,document) "
                       "VALUES(?,?,?,?,?)", (REG, "Сохранённое нарушение", "violation",
                                             "Подтверждённый исходный текст", "Документ.pdf"))
            self.assertEqual(load_counts(db, [REG]), {REG: 1})

    def test_general_and_thematic_paths_preserve_same_day_protocols(self):
        docs = [protocol("1"), protocol("2", 2)]
        ns = self.installed()
        with ExitStack() as stack:
            stack.enter_context(patch.object(general, "existing", return_value=(STORED_PURCHASE, [])))
            stack.enter_context(patch.object(general, "stored_docs", return_value=(docs, 0)))
            stack.enter_context(patch.object(general, "allowed_documents", return_value={}))
            stack.enter_context(patch.object(metadata, "from_documents", return_value={}))
            stack.enter_context(patch.object(precise, "enrich"))
            stack.enter_context(patch.object(precise, "persist_facts"))
            first = asyncio.run(general.inspect_one(ns, REG))
            repeated = asyncio.run(general.inspect_one(ns, REG))
            theme = asyncio.run(thematic.independent_review(ns, REG, ["protocols"]))
        self.assertEqual((first[0], first[1]["new_findings"]), ("completed", 2))
        self.assertEqual(repeated[1]["new_findings"], 0)
        self.assertEqual((theme[0], len(theme[1])), ("completed", 2))
        with self.connect() as db:
            self.assertEqual(load_counts(db, [REG]), {REG: 2})


if __name__ == "__main__":
    unittest.main()
