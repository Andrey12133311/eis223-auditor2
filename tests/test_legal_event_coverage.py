"""Complete numbered events must survive analysis, persistence and card counts.

The dates below exercise existing legal rules. No production procurements or
new legal thresholds are used, and copies of one event must still count once.
"""
import asyncio
import copy
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v341_precise_legal as precise
import v348_cross_document_legal as cross
import v333_dedup_ui as dedup
from v332_all_procurements_audit import persist
from v352_dashboard_truth import collapse_legal_events, load_counts

P = {"reg": "32616412317", "method": "Открытый конкурс",
     "published": "2026-10-01", "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def original(family, number, uid=1, published="13.10.2026", neutral_name=False):
    if family == "protocol":
        name = f"Итоговый протокол № {number}.pdf"
        body = (f"Номер протокола: {number}\n"
                "Дата подписания итогового протокола: 06.10.2026\n"
                f"Дата размещения итогового протокола в ЕИС: {published}")
    else:
        name = f"Разъяснение на запрос № {number}.pdf"
        body = (f"Номер запроса: {number}\n"
                "Дата поступления запроса: 06.10.2026\n"
                f"Дата публикации ответа в ЕИС: {published}")
    url = SOURCE + f"{uid:032X}"
    return {"name": "Оригинал ЕИС.pdf" if neutral_name else name,
            "text": body, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(body.encode()).hexdigest()}}


def identifier(family, item):
    reader = (cross.finding_protocol_number if family == "protocol" else
              cross.finding_clarification_number)
    return reader(item)


def installed(docs, initial=None, checks=None):
    ns = {"analyze": lambda p, d: list(initial or []),
          "build_checks": lambda p, detail, card, d: list(checks or [])}
    precise.install(ns)
    cross.install(ns)
    with patch.object(precise, "enrich", side_effect=lambda ns, p, *a, **kw: p), \
         patch.object(precise, "persist_facts", return_value=None):
        p = dict(P)
        found = dedup.collapse(ns["analyze"](p, docs))
        checked = ns["build_checks"](p, {}, {}, docs)
    return found, checked


class LegalEventCoverage(unittest.TestCase):
    def test_two_numbered_events_with_identical_dates_survive_all_analyzers(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                docs = [original(family, "1"), original(family, "2", 2)]
                self.assertEqual([len(precise.evaluate(P, [doc])[0]) for doc in docs], [1, 1])
                found, _ = installed(docs)
                self.assertEqual(len(found), 2)
                self.assertEqual({identifier(family, item) for item in found}, {"1", "2"})
                self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_number_in_body_survives_neutral_filename_and_saved_evidence(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                docs = [original(family, "1", neutral_name=True),
                        original(family, "2", 2, neutral_name=True)]
                found, _ = installed(docs)
                self.assertEqual(len(found), 2)
                saved = [{k: item[k] for k in ("code", "title", "law", "evidence",
                                                "document", "source_url")} for item in found]
                self.assertEqual(len(dedup.collapse(saved)), 2)
                self.assertEqual(len(collapse_legal_events(saved)), 2)
                self.assertEqual({identifier(family, item) for item in saved}, {"1", "2"})

    def test_copy_with_another_filename_or_url_is_not_an_extra_event(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                one = original(family, "А–1 / 2026")
                duplicate = original(family, "а-1/2026", 2, neutral_name=True)
                found, _ = installed([one, duplicate])
                self.assertEqual(len(found), 1)
                self.assertEqual(identifier(family, found[0]), "а-1/2026")

    def test_same_number_different_dated_events_are_retained(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                docs = [original(family, "1"), original(family, "1", 2, "14.10.2026")]
                found, _ = installed(docs)
                self.assertEqual(len(found), 2)
                self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_date_spelling_does_not_create_an_extra_copy(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                one = original(family, "1")
                duplicate = copy.deepcopy(one)
                duplicate["text"] = duplicate["text"].replace("06.10.2026", "2026-10-06")
                duplicate["text"] = duplicate["text"].replace("13.10.2026", "2026-10-13")
                found, _ = installed([one, duplicate])
                self.assertEqual(len(found), 1)

    def test_conflicting_or_incomplete_numbers_do_not_create_new_identity(self):
        for family in ("protocol", "clarification"):
            label = "протокола" if family == "protocol" else "запроса"
            for number in ("1(А)", "1 А", "___"):
                with self.subTest(family=family, number=number):
                    doc = original(family, number)
                    code = ("223-4-12-PROTOCOL-PUBLICATION-LATE" if family == "protocol"
                            else "223-3.2-3-CLARIFICATION-RESPONSE-LATE")
                    self.assertEqual(precise._document_event_number(doc, code), "")
            doc = original(family, "1")
            doc["text"] += f"\nНомер {label}: 2"
            found, _ = installed([doc])
            self.assertEqual(identifier(family, found[0]), "")

    def test_unrelated_numbers_are_not_event_identifiers(self):
        for family in ("protocol", "clarification"):
            doc = original(family, "1", neutral_name=True)
            doc["text"] = "Закупка № 32616412317. Заявка № 12.\n" + doc["text"].split("\n", 1)[1]
            found, _ = installed([doc])
            self.assertEqual(identifier(family, found[0]), "")

    def test_existing_numbered_event_does_not_suppress_a_different_number(self):
        for family in ("protocol", "clarification"):
            first = original(family, "1")
            existing = precise.evaluate(P, [first])[0]
            found, _ = installed([first, original(family, "2", 2)], initial=existing)
            self.assertEqual(len(found), 2)

    def test_check_statuses_and_different_numbers_are_preserved(self):
        docs = [original("protocol", "1"), original("protocol", "2", 2)]
        rows = precise.evaluate(P, docs)[0]
        checks = [{**item, "item": item["title"], "status": "violation"} for item in rows]
        checks += [{**checks[0], "status": "check"}, dict(checks[1])]
        _, result = installed([], checks=checks)
        self.assertEqual(len(result), 3)
        self.assertEqual([item["status"] for item in result].count("violation"), 2)

    def test_unverified_and_timely_events_still_do_not_produce_findings(self):
        for family in ("protocol", "clarification"):
            doc = original(family, "1")
            doc["meta"]["source"] = "existing_local_unverified"
            self.assertEqual(precise.evaluate(P, [doc])[0], [])
            timely = original(family, "2", 2, "09.10.2026")
            self.assertEqual(precise.evaluate(P, [timely])[0], [])

    def test_general_and_thematic_pipelines_preserve_numbered_events_and_repeat_counts(self):
        import v332_all_procurements_audit as general
        import v342_thematic_queue as thematic
        import v314_eis_metadata as metadata
        import v372_auto_regulation_protocols as protocols

        with tempfile.TemporaryDirectory(prefix="eis-numbered-pipeline-") as root:
            path = Path(root) / "audit.sqlite"
            def connect():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db
            with connect() as db:
                db.executescript("""
                    CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, customer TEXT, title TEXT,
                        method TEXT, published_at TEXT, submission_end TEXT,
                        violations INTEGER DEFAULT 0, checks_ok INTEGER DEFAULT 0,
                        checks_attention INTEGER DEFAULT 0);
                    CREATE TABLE docs(id INTEGER PRIMARY KEY, reg_number TEXT, name TEXT, url TEXT,
                        doc_type TEXT, published_at TEXT, local_path TEXT, status TEXT,
                        metadata_json TEXT, final_url TEXT);
                    CREATE TABLE findings(id INTEGER PRIMARY KEY, reg_number TEXT, code TEXT, kind TEXT,
                        title TEXT, law TEXT, law_url TEXT, evidence TEXT, note TEXT, document TEXT,
                        source_url TEXT, location TEXT, match_text TEXT);
                    CREATE TABLE checks(id INTEGER PRIMARY KEY, reg_number TEXT, scope TEXT, item TEXT,
                        status TEXT, evidence TEXT, source TEXT, source_url TEXT, document TEXT,
                        field_path TEXT, note TEXT, law TEXT, law_url TEXT, location TEXT);
                """)
                db.execute("""INSERT INTO purchases(reg_number,customer,title,method,published_at,
                              submission_end) VALUES(?,?,?,?,?,?)""",
                           (P["reg"], "ООО Тест", "Поставка материалов", P["method"],
                            P["published"], P["submission_end"]))
            originals = [original(family, number, i + 1, neutral_name=True)
                         for i, (family, number) in enumerate((
                             ("protocol", "1"), ("protocol", "2"),
                             ("clarification", "1"), ("clarification", "2")))]
            source = {"docs": originals, "unreadable": 0, "original_expected": 4,
                      "original_received": 4, "source_incomplete": False,
                      "p": {}, "card": {}, "detail": {}}
            ns = {"conn": connect, "analyze": lambda p, docs: [],
                  "build_checks": lambda p, detail, card, docs: []}
            precise.install(ns)
            cross.install(ns)
            protocols.install(ns)
            # Source collection has separate receipt/reading regressions. Here
            # the real audit entry points, legal wrappers and SQL stay active.
            with patch.object(general, "collect_saved_evidence", return_value=source), \
                 patch.object(metadata, "from_documents", return_value={}), \
                 patch.object(precise, "enrich", side_effect=lambda ns, p, *a, **kw: p), \
                 patch.object(precise, "persist_facts", return_value=None):
                first = asyncio.run(general.inspect_one(ns, P["reg"]))
                repeated = asyncio.run(general.inspect_one(ns, P["reg"]))
                themed = asyncio.run(thematic.independent_review(ns, P["reg"], ["protocols", "dates"]))
            self.assertEqual(first[0], "completed")
            self.assertEqual(first[1]["verified_original_documents"], 4)
            self.assertEqual(first[1]["new_findings"], 4)
            self.assertEqual((repeated[1]["new_findings"], repeated[1]["new_checks"]), (0, 0))
            self.assertEqual((themed[0], len(themed[1])), ("completed", 4))
            with connect() as db:
                saved = [dict(row) for row in db.execute("SELECT * FROM findings")]
                self.assertEqual(len(saved), 4)
                self.assertEqual(load_counts(db, [P["reg"]]), {P["reg"]: 4})
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 4)
            self.assertEqual({(item["code"], item["evidence"]) for item in saved},
                             {(item["code"], item["evidence"]) for item in themed[1]})

    def test_distinct_events_persist_once_each_and_reaudit_does_not_inflate_count(self):
        with tempfile.TemporaryDirectory(prefix="eis-local-event-coverage-") as root:
            path = Path(root) / "audit.sqlite"
            def connect():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db
            with connect() as db:
                db.executescript("""
                    CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, violations INTEGER DEFAULT 0,
                        checks_ok INTEGER DEFAULT 0, checks_attention INTEGER DEFAULT 0);
                    CREATE TABLE findings(id INTEGER PRIMARY KEY, reg_number TEXT, code TEXT, kind TEXT,
                        title TEXT, law TEXT, law_url TEXT, evidence TEXT, note TEXT, document TEXT,
                        source_url TEXT, location TEXT, match_text TEXT);
                    CREATE TABLE checks(id INTEGER PRIMARY KEY, reg_number TEXT, scope TEXT, item TEXT,
                        status TEXT, evidence TEXT, source TEXT, source_url TEXT, document TEXT,
                        field_path TEXT, note TEXT, law TEXT, law_url TEXT, location TEXT);
                """)
                db.execute("INSERT INTO purchases(reg_number) VALUES (?)", (P["reg"],))
            ns = {"conn": connect}
            docs = [original(family, number, i + 1, neutral_name=True)
                    for i, (family, number) in enumerate((
                        ("protocol", "1"), ("protocol", "2"),
                        ("clarification", "1"), ("clarification", "2")))]
            found, _ = installed(docs)
            self.assertEqual(persist(ns, P["reg"], found, []), (4, 0))
            self.assertEqual(persist(ns, P["reg"], found, []), (0, 0))
            with connect() as db:
                self.assertEqual(load_counts(db, [P["reg"]]), {P["reg"]: 4})
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
