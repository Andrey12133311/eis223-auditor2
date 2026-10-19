"""Synthetic independently verified EIS fixtures for clarification identity."""
import copy
import hashlib
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v348_cross_document_legal as legal
from v341_precise_legal import evaluate
from v332_all_procurements_audit import persist
from v352_dashboard_truth import collapse_legal_events, load_counts
from v353_episode_guard import same_episode

PURCHASE = {"method": "Аукцион в электронной форме", "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def document(name, text, uid):
    url = SOURCE + f"{uid:032X}"
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


def pair(number="1", first=1, published="13.10.2026"):
    suffix = f" № {number}" if number else ""
    return [document(f"Запрос{suffix}.pdf", "Дата поступления запроса: 06.10.2026", first),
            document(f"Разъяснение на запрос{suffix}.pdf",
                     f"Дата публикации разъяснения в ЕИС: {published}", first+1)]


class ClarificationIdentityTests(unittest.TestCase):
    def run_analyzer(self, docs):
        ns = {"analyze": lambda p, d: evaluate(p, d)[0]}
        legal.install(ns)
        return [x for x in ns["analyze"](PURCHASE, docs)
                if x.get("code") == "223-3.2-3-CLARIFICATION-CROSS-DOC-LATE"]

    def test_positive_preserves_legal_calculation_and_provenance(self):
        docs = pair()
        found = self.run_analyzer(docs)
        self.assertEqual(len(found), 1)
        self.assertEqual(legal.finding_clarification_number(found[0]), "1")
        self.assertEqual(found[0]["law"], "223-ФЗ, ст. 3.2 ч. 3")
        for value in ("2026-10-06", "2026-10-09", "2026-10-13", "2026-10-30"):
            self.assertIn(value, found[0]["evidence"])
        for doc in docs:self.assertIn(doc["url"], found[0]["evidence"])

    def test_distinct_same_day_pairs_survive_actual_analyzer(self):
        found = self.run_analyzer(pair("1") + pair("2", 3))
        self.assertEqual({legal.finding_clarification_number(x) for x in found}, {"1", "2"})
        self.assertEqual(len(found), 2)
        self.assertFalse(same_episode(*found))
        self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_mismatched_one_sided_and_unsupported_ids(self):
        for first, second in (("1", "2"), ("1", ""), ("", "1"),
                              ("1(А)", "1(Б)"), ("1 А", "1 Б"),
                              ("1+А", "1+Б"), ("I", "II"), ("___", "___"),
                              ("1"*81, "1"*81)):
            with self.subTest(first=first, second=second):
                self.assertEqual(self.run_analyzer([pair(first)[0], pair(second, 3)[1]]), [])

    def test_complete_normalized_identifiers(self):
        found = self.run_analyzer([pair("А–1 / 2026")[0], pair("а-1/2026", 3)[1]])
        self.assertEqual(len(found), 1)
        self.assertEqual(legal.finding_clarification_number(found[0]), "а-1/2026")

    def test_body_number_field_supported(self):
        docs = pair("")
        for doc in docs:doc["text"] = "Идентификатор запроса: A-7\n" + doc["text"]
        self.assertEqual(len(self.run_analyzer(docs)), 1)

    def test_conflicting_title_body_or_compilation_is_ambiguous(self):
        docs = pair()
        docs[1]["text"] += "\nНомер запроса: 2"
        self.assertEqual(self.run_analyzer(docs), [])

    def test_missing_event_unverified_or_deadline_cannot_establish_violation(self):
        self.assertEqual(self.run_analyzer(pair()[:1]), [])
        docs = pair()
        docs[1]["status"] = "unverified"
        self.assertEqual(self.run_analyzer(docs), [])
        self.assertEqual(legal.cross_doc_clarifications({"method": PURCHASE["method"]}, pair()), [])

    def test_same_original_uid_even_with_url_variants_cannot_pair(self):
        docs = pair()
        docs[1]["meta"]["source_url"] = docs[0]["url"] + "&download=1"
        self.assertEqual(self.run_analyzer(docs), [])

    def test_exact_duplicate_original_url_variant_is_not_an_extra_episode(self):
        docs = pair()
        duplicate = copy.deepcopy(docs[1])
        duplicate["meta"]["source_url"] += "&download=1"
        self.assertEqual(len(self.run_analyzer(docs + [duplicate])), 1)

    def test_distinct_original_versions_or_multiple_dates_are_ambiguous(self):
        self.assertEqual(self.run_analyzer(pair() + [pair("1", 3)[1]]), [])
        docs = pair()
        docs[1]["text"] += "\nДата публикации разъяснения в ЕИС: 14.10.2026"
        self.assertEqual(self.run_analyzer(docs), [])

    def test_single_unnumbered_pair_preserved_but_multiple_not_guessed(self):
        self.assertEqual(len(self.run_analyzer(pair(""))), 1)
        self.assertEqual(self.run_analyzer(pair("") + pair("", 3)), [])
        self.assertEqual(self.run_analyzer(pair("1") + [pair("", 3)[1]]), [])

    def test_purchase_number_and_date_filename_are_not_request_ids(self):
        for text in ("Закупка № 32615969056", "Заявка № 12", "Запрос 06.10.2026.pdf"):
            self.assertEqual(legal._clarification_identity(text), (set(), False))
        docs = pair()
        docs[0]["text"] = "Запрос № 123-06.10.2026"
        self.assertEqual(self.run_analyzer(docs), [])

    def test_timely_response_and_late_request_not_flagged(self):
        self.assertEqual(self.run_analyzer(pair(published="09.10.2026")), [])
        p = dict(PURCHASE, submission_end="2026-10-08")
        self.assertEqual(legal.cross_doc_clarifications(p, pair()), [])

    def test_explicit_historical_purchase_does_not_use_current_rule(self):
        for key in ("published", "published_at"):
            with self.subTest(key=key):
                purchase = dict(PURCHASE, **{key: "2018-06-01"})
                self.assertEqual(legal.cross_doc_clarifications(purchase, pair()), [])

    def test_existing_same_identified_episode_deduplicates(self):
        docs = pair()
        issue = legal.cross_doc_clarifications(PURCHASE, docs)[0]
        ns = {"analyze": lambda p, d: [dict(issue)]}
        legal.install(ns)
        self.assertEqual(len(ns["analyze"](PURCHASE, docs)), 1)


    def test_distinct_events_persist_and_display_once_each_after_reaudit(self):
        with tempfile.TemporaryDirectory(prefix="eis-clarification-identity-") as root:
            path = Path(root) / "audit.sqlite"

            def connect():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db

            with connect() as db:
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
                db.execute("INSERT INTO purchases(reg_number) VALUES (?)", ("synthetic",))
            ns = {"conn": connect, "analyze": lambda p, d: []}
            legal.install(ns)
            found = ns["analyze"](PURCHASE, pair("1") + pair("2", first=3))
            self.assertEqual(persist(ns, "synthetic", found, []), (2, 0))
            self.assertEqual(persist(ns, "synthetic", found, []), (0, 0))
            with connect() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM findings")]
                self.assertEqual(len(rows), 2)
                self.assertEqual(len(collapse_legal_events(rows)), 2)
                self.assertEqual(load_counts(db, ["synthetic"]), {"synthetic": 2})
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 2)


if __name__ == "__main__":unittest.main()
