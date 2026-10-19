"""Known date chains survive ordinary labels and verified archive boundaries.

All originals are synthetic. These tests do not assert a violation against a
real procurement or replace the receipt/hash verification at source intake.
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
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v314_eis_metadata as metadata
from v352_dashboard_truth import collapse_legal_events, load_counts

P = {"reg": "32610002001", "method": "Аукцион в электронной форме",
     "published": "2026-10-01", "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
REQUEST = "Дата поступления запроса: 06.10.2026"
ANSWER = "Дата публикации разъяснения в ЕИС: 13.10.2026"


def original(name, text, uid=1, member=None):
    url = SOURCE + f"{uid:032X}"
    meta = {"source": "official_eis_archive_member" if member else "official_eis_original",
            "source_url": url, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    if member:meta["archive_member"] = member
    return {"name": name, "text": text, "status": "checked", "url": url, "meta": meta}


def archive_pair(family="clarification", number="1", uid=1):
    if family == "clarification":
        names = [f"Запрос № {number}.pdf", f"Разъяснение на запрос № {number}.pdf"]
        text = [f"Номер запроса: {number}\n{REQUEST}",
                f"Номер запроса: {number}\n{ANSWER}"]
    else:
        names = [f"Итоговый протокол № {number}.pdf",
                 f"Публикация итогового протокола № {number}.pdf"]
        text = [f"Номер протокола: {number}\nДата подписания итогового протокола: 06.10.2026",
                f"Номер протокола: {number}\nДата размещения итогового протокола в ЕИС: 13.10.2026"]
    return [original("Материалы.zip / " + name, body, uid, name)
            for name, body in zip(names, text)]


def analyze_archive(family, docs, purchase=P):
    return (cross.cross_doc_clarifications if family == "clarification"
            else cross.cross_doc_protocols)(purchase, docs)


class ClarificationDateLabelRecall(unittest.TestCase):
    def assess(self, text, name="Разъяснение на запрос № 1.pdf", purchase=P):
        return precise.evaluate(purchase, [original(name, text)])

    def test_existing_short_label_positive_control(self):
        self.assertEqual(len(self.assess(REQUEST + "\n" + ANSWER)[0]), 1)

    def test_complete_documentation_labels_establish_same_date_chain(self):
        for request_label, answer_label in (
            ("Дата поступления запроса на разъяснение положений документации",
             "Дата публикации разъяснения положений документации в ЕИС"),
            ("Дата получения заказчиком запроса о разъяснении документации",
             "Дата размещения ответа на запрос о разъяснении документации в ЕИС"),
            ("Дата и время регистрации запроса разъяснений документации о конкурентной закупке",
             "Дата и время размещения разъяснений документации о конкурентной закупке в единой информационной системе"),
            ("Запрос на разъяснение положений документации получен",
             "Разъяснение положений документации размещено в ЕИС"),
            ("Запрос о разъяснении документации поступил",
             "Ответ на запрос о разъяснении документации размещён в ЕИС"),
        ):
            with self.subTest(request=request_label, answer=answer_label):
                found, _ = self.assess(request_label + " | 06.10.2026\n" +
                                       answer_label + " | 13.10.2026")
                self.assertEqual(len(found), 1)
                for day in ("2026-10-06", "2026-10-09", "2026-10-13"):
                    self.assertIn(day, found[0]["evidence"])

    def test_full_label_with_newlines_and_russian_date(self):
        found, _ = self.assess(
            "Дата поступления запроса на разъяснение\nположений документации:\n«6» октября 2026\n"
            "Дата размещения разъяснения положений документации в ЕИС:\n13 октября 2026")
        self.assertEqual(len(found), 1)

    def test_answer_with_letter_yo_is_explicit_placement(self):
        found, _ = self.assess(REQUEST + "\nОтвет размещён в ЕИС: 13.10.2026")
        self.assertEqual(len(found), 1)

    def test_preparation_response_date_or_generic_publication_is_not_placement(self):
        for label in ("Дата подготовки разъяснения положений документации",
                      "Дата ответа на запрос о разъяснении документации",
                      "Дата подписания ответа на запрос", "Дата публикации документа в ЕИС"):
            with self.subTest(label=label):
                found, review = self.assess(REQUEST + "\n" + label + ": 13.10.2026")
                self.assertFalse(found)
                self.assertTrue(review)

    def test_generic_document_date_or_date_inside_request_number_is_not_receipt(self):
        for text in ("Запрос № 123-06.10.2026", "Документ от 06.10.2026",
                     "Дата составления запроса на разъяснение документации: 06.10.2026"):
            with self.subTest(text=text):self.assertFalse(self.assess(text + "\n" + ANSWER)[0])

    def test_timely_late_request_unknown_scope_and_missing_deadline_stay_unconfirmed(self):
        text = ("Дата поступления запроса на разъяснение документации: 06.10.2026\n"
                "Дата размещения разъяснения документации в ЕИС: 13.10.2026")
        for purchase in (dict(P, method="Единственный поставщик"),
                         dict(P, method="Способ не установлен"),
                         dict(P, submission_end=""), dict(P, submission_end="2026-10-08")):
            with self.subTest(purchase=purchase):self.assertFalse(self.assess(text, purchase=purchase)[0])
        self.assertFalse(self.assess(text.replace("13.10.2026", "09.10.2026"))[0])

    def test_unknown_calendar_and_invalid_date_are_not_confirmed(self):
        with patch.object(precise, "federal_workday", return_value=None):
            self.assertFalse(self.assess(REQUEST + "\n" + ANSWER)[0])
        self.assertFalse(self.assess(REQUEST.replace("06.10.2026", "31.02.2026") + "\n" + ANSWER)[0])

    def test_unique_reversed_table_is_independent_of_row_order(self):
        for name in ("Разъяснение на запрос № 1.pdf", "Разъяснение.pdf"):
            with self.subTest(name=name):
                found, _ = self.assess(ANSWER + "\n" + REQUEST, name=name)
                self.assertEqual(len(found), 1)
                self.assertIn("2026-10-09", found[0]["evidence"])

    def test_reversed_table_requires_unique_request_and_answer(self):
        for text in (ANSWER + "\n" + ANSWER.replace("13.10.2026", "14.10.2026") + "\n" + REQUEST,
                     ANSWER + "\n" + REQUEST + "\n" + REQUEST.replace("06.10.2026", "07.10.2026"),
                     ANSWER + "\n" + "x" * 1600 + "\n" + REQUEST):
            with self.subTest(text=text[:100]):
                found, reviews = self.assess(text)
                self.assertFalse(found)
                self.assertTrue(reviews)

    def test_reversed_table_does_not_join_conflicting_or_unsupported_numbers(self):
        for prefix in ("Номер запроса: 2\n", "Запрос № 1(А)\n"):
            self.assertFalse(self.assess(prefix + ANSWER + "\n" + REQUEST)[0])
        self.assertFalse(self.assess(ANSWER + "\n" + REQUEST, name="Образец разъяснения.pdf")[0])

    def test_full_labels_are_shared_by_split_document_analyzer(self):
        docs = archive_pair()
        docs[0]["text"] = docs[0]["text"].replace("поступления запроса:",
            "поступления запроса на разъяснение положений документации:")
        docs[1]["text"] = docs[1]["text"].replace("разъяснения в ЕИС:",
            "разъяснения положений документации в ЕИС:")
        for doc in docs:doc["meta"]["sha256"] = hashlib.sha256(doc["text"].encode()).hexdigest()
        self.assertEqual(len(cross.cross_doc_clarifications(P, docs)), 1)


class ArchiveDateChainRecall(unittest.TestCase):
    def test_different_verified_archive_members_can_form_one_explicit_pair(self):
        for family in ("clarification", "protocol"):
            with self.subTest(family=family):
                docs = archive_pair(family)
                found = analyze_archive(family, docs)
                self.assertEqual(len(found), 1)
                self.assertEqual(cross.finding_original_identity(found[0]),
                                 (f"{1:032X}", docs[1]["meta"]["archive_member"]))
                self.assertEqual(cross.finding_binary_identity(found[0]), docs[1]["meta"]["sha256"])
                for doc in docs:self.assertIn(doc["name"], found[0]["evidence"])

    def test_two_independently_numbered_archive_pairs_survive_counting(self):
        for family in ("clarification", "protocol"):
            with self.subTest(family=family):
                docs = archive_pair(family, "1") + archive_pair(family, "2")
                found = analyze_archive(family, docs)
                self.assertEqual(len(found), 2)
                self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_different_numbered_acts_do_not_pair_even_inside_one_archive(self):
        for family in ("clarification", "protocol"):
            docs = [archive_pair(family, "1")[0], archive_pair(family, "2")[1]]
            self.assertFalse(analyze_archive(family, docs))

    def test_multiple_versions_of_one_event_remain_ambiguous(self):
        for family in ("clarification", "protocol"):
            with self.subTest(family=family):
                docs = archive_pair(family)
                other = copy.deepcopy(docs[1])
                other["meta"]["archive_member"] = "v2/" + other["meta"]["archive_member"]
                other["name"] = "Материалы.zip / " + other["meta"]["archive_member"]
                self.assertFalse(analyze_archive(family, docs + [other]))
                other["text"] = other["text"].replace("13.10.2026", "14.10.2026")
                other["meta"]["sha256"] = hashlib.sha256(other["text"].encode()).hexdigest()
                self.assertFalse(analyze_archive(family, docs + [other]))

    def test_same_member_cannot_masquerade_as_two_independent_originals(self):
        for family in ("clarification", "protocol"):
            docs = archive_pair(family)
            docs[1]["meta"]["archive_member"] = docs[0]["meta"]["archive_member"]
            self.assertFalse(analyze_archive(family, docs))

    def test_archive_member_without_recoverable_identity_cannot_form_pair(self):
        for family in ("clarification", "protocol"):
            docs = archive_pair(family)
            docs[1]["meta"].pop("archive_member")
            docs[1]["name"] = docs[1]["name"].split(" / ", 1)[1]
            self.assertFalse(analyze_archive(family, docs))

    def test_legacy_archive_display_path_preserves_member_identity(self):
        for family in ("clarification", "protocol"):
            docs = archive_pair(family)
            for doc in docs:doc["meta"].pop("archive_member")
            self.assertEqual(len(analyze_archive(family, docs)), 1)

    def test_exact_request_or_answer_alias_does_not_add_clarification(self):
        docs = archive_pair()
        for index in (0, 1):
            duplicate = copy.deepcopy(docs[index])
            duplicate["meta"]["source_url"] += "&download=1"
            self.assertEqual(len(analyze_archive("clarification", docs + [duplicate])), 1)

    def test_unverified_or_template_member_is_not_used(self):
        for family in ("clarification", "protocol"):
            for change in ("unverified", "template"):
                docs = archive_pair(family)
                if change == "unverified":docs[1]["status"] = "downloaded"
                else:docs[1]["name"] = "Образец " + docs[1]["name"]
                self.assertFalse(analyze_archive(family, docs))

    def test_source_documents_remain_unchanged(self):
        for family in ("clarification", "protocol"):
            docs = archive_pair(family)
            expected = copy.deepcopy(docs)
            analyze_archive(family, docs)
            self.assertEqual(docs, expected)

    def test_one_archive_passes_both_audits_and_persists_each_event_once(self):
        docs = (archive_pair("clarification", "1") + archive_pair("clarification", "2") +
                archive_pair("protocol", "1") + archive_pair("protocol", "2"))
        with tempfile.TemporaryDirectory(prefix="eis-archive-date-recall-") as directory:
            path = Path(directory) / "audit.sqlite"
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
                           (P["reg"], "ООО Синтетический тест", "Синтетические материалы",
                            P["method"], P["published"], P["submission_end"]))
            source = {"docs": docs, "unreadable": 0, "original_expected": 1,
                      "original_received": 1, "source_incomplete": False,
                      "source_error": None, "p": {}, "card": {}, "detail": {}}
            ns = {"conn": connect, "analyze": lambda p, docs: [],
                  "build_checks": lambda p, detail, card, docs: []}
            precise.install(ns)
            cross.install(ns)
            with patch.object(general, "collect_saved_evidence", return_value=source), \
                 patch.object(metadata, "from_documents", return_value={}), \
                 patch.object(precise, "enrich", side_effect=lambda ns, p, *a, **kw: p), \
                 patch.object(precise, "persist_facts", return_value=None):
                first = asyncio.run(general.inspect_one(ns, P["reg"]))
                repeat = asyncio.run(general.inspect_one(ns, P["reg"]))
                themed = asyncio.run(thematic.independent_review(ns, P["reg"], ["protocols", "dates"]))
            self.assertEqual((first[0], first[1]["new_findings"]), ("completed", 4))
            self.assertEqual(repeat[1]["new_findings"], 0)
            self.assertEqual((themed[0], len(themed[1])), ("completed", 4))
            with connect() as db:
                saved = [dict(row) for row in db.execute("SELECT * FROM findings")]
                self.assertEqual(len(saved), 4)
                self.assertEqual(load_counts(db, [P["reg"]]), {P["reg"]: 4})
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 4)
            self.assertEqual({(x["code"], x["evidence"]) for x in saved},
                             {(x["code"], x["evidence"]) for x in themed[1]})


if __name__ == "__main__":unittest.main()
