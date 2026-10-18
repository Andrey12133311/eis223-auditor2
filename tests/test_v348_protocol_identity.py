"""Synthetic evidence fixtures, never production EIS or account data."""
import copy
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import v348_cross_document_legal as legal
from v332_all_procurements_audit import persist
from v352_dashboard_truth import collapse_legal_events, load_counts
from v353_episode_guard import same_episode
from v341_precise_legal import evaluate as evaluate_local_dates
from v372_auto_regulation_protocols import install as install_protocol_review


REG = "32615969056"
PURCHASE = {"reg": REG, "method": "Аукцион в электронной форме"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def document(name, text, uid):
    url = SOURCE + f"{uid:032X}"
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


def pair(number="1", *, first=1, signed="06.10.2026", posted="13.10.2026",
         inline=False):
    suffix = f" № {number}" if number else ""
    label = suffix if inline else ""
    return [
        document(f"Итоговый протокол{suffix}.pdf",
                 f"Дата подписания итогового протокола{label}: {signed}", first),
        document(f"Публикация итогового протокола{suffix}.pdf",
                 f"Дата размещения итогового протокола{label} в ЕИС: {posted}", first + 1),
    ]


class ProtocolIdentityTests(unittest.TestCase):
    def evaluate(self, docs, purchase=None):
        return legal.cross_doc_protocols(purchase or PURCHASE, docs)

    def test_single_numbered_protocol_preserves_source_and_calculation(self):
        docs = pair()
        found = self.evaluate(docs)
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(legal.finding_protocol_number(item), "1")
        self.assertEqual(item["law"], "223-ФЗ, ст. 4 ч. 12; ГК РФ, ст. 193")
        for value in ("2026-10-06", "2026-10-09", "2026-10-13"):
            self.assertIn(value, item["evidence"])
        for doc in docs:
            self.assertIn(doc["meta"]["source_url"], item["evidence"])

    def test_two_same_day_numbered_episodes_survive_installed_analyzer(self):
        docs = pair("1") + pair("2", first=3)
        ns = {"analyze": lambda p, d: []}
        legal.install(ns)
        found = ns["analyze"](PURCHASE, docs)
        self.assertEqual({legal.finding_protocol_number(x) for x in found}, {"1", "2"})
        self.assertEqual(len(collapse_legal_events(found)), 2)
        self.assertFalse(same_episode(*found))

    def test_active_neighboring_legal_layers_preserve_numbered_episodes(self):
        docs = pair("1") + pair("2", first=3)
        ns = {"analyze": lambda p, d: evaluate_local_dates(p, d)[0],
              "build_checks": lambda p, detail, card, d: []}
        legal.install(ns)
        install_protocol_review(ns)
        found = ns["analyze"](PURCHASE, docs)
        self.assertEqual({legal.finding_protocol_number(x) for x in found}, {"1", "2"})
        self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_case_differences_in_complete_number_are_supported(self):
        docs = pair("КП-02/26")
        docs[1]["name"] = docs[1]["name"].replace("КП", "кп")
        found = self.evaluate(docs)
        self.assertEqual(len(found), 1)
        self.assertEqual(legal.finding_protocol_number(found[0]), "кп-02/26")

    def test_explicit_number_field_and_document_number_agree(self):
        docs = pair("А-1.2")
        docs[0]["text"] = "Номер протокола: А-1.2\n" + docs[0]["text"]
        self.assertEqual(len(self.evaluate(docs)), 1)

    def test_purchase_and_bid_numbers_are_not_protocol_identifiers(self):
        self.assertEqual(legal.protocol_numbers("Закупка № 32615969056, заявка № 12"), set())
        self.assertEqual(legal.protocol_numbers("Итоговый протокол 12.pdf"), set())
        self.assertEqual(legal.protocol_numbers("Протокол № 12.pdf"), {"12"})

    def test_different_protocol_numbers_are_never_paired(self):
        self.assertEqual(self.evaluate([pair("1")[0], pair("2", first=3)[1]]), [])

    def test_numbered_and_unnumbered_are_not_paired(self):
        self.assertEqual(self.evaluate([pair("1")[0], pair("", first=3)[1]]), [])

    def test_unparseable_explicit_identifiers_do_not_use_unnumbered_fallback(self):
        for number in ("не указан", "1" * 81, "___", "I", "II", "А", "Б"):
            with self.subTest(number):
                self.assertEqual(self.evaluate(pair(number)), [])

    def test_partial_identifier_near_misses_never_pair(self):
        for first, second in (("1–А", "1–Б"), ("1 / 2025", "1 / 2026"),
                              ("1(1)", "1(2)"), ("I", "II"), ("А", "Б"),
                              ("1:А", "1:Б"), ("1+А", "1+Б"), ("1 А", "1 Б"),
                              ("1.pdf-А", "1-А")):
            with self.subTest(first=first, second=second):
                docs = [pair(first)[0], pair(second, first=3)[1]]
                self.assertEqual(self.evaluate(docs), [])

    def test_supported_unicode_dashes_and_separator_spacing_keep_full_identity(self):
        docs = [pair("1–А / 2026")[0], pair("1-А/2026", first=3)[1]]
        found = self.evaluate(docs)
        self.assertEqual(len(found), 1)
        self.assertEqual(legal.finding_protocol_number(found[0]), "1-а/2026")

    def test_unsupported_numbering_in_protocol_title_blocks_fallback(self):
        docs = pair("")
        docs[0]["name"] = "Итоговый протокол заседания комиссии № 1.pdf"
        docs[1]["name"] = "Публикация итогового протокола заседания комиссии № 2.pdf"
        self.assertEqual(self.evaluate(docs), [])

    def test_date_shaped_number_is_never_used_as_signing_date(self):
        for number in ("123-2026-10-06", "123-06.10.2026"):
            docs = pair(number)
            docs[0]["text"] = f"Дата подписания итогового протокола №{number}"
            with self.subTest(number):
                self.assertEqual(self.evaluate(docs), [])

    def test_inline_numbered_date_labels_require_separate_supported_evidence(self):
        self.assertEqual(self.evaluate(pair("1", inline=True)), [])

    def test_unnumbered_extra_event_makes_same_kind_ambiguous(self):
        self.assertEqual(self.evaluate(pair("1") + [pair("", first=3)[1]]), [])

    def test_legacy_single_unnumbered_pair_is_preserved(self):
        self.assertEqual(len(self.evaluate(pair(""))), 1)

    def test_multiple_unnumbered_pairs_remain_ambiguous(self):
        self.assertEqual(self.evaluate(pair("") + pair("", first=3)), [])

    def test_conflicting_title_and_body_number_block_related_episode(self):
        docs = pair("1")
        conflicting = pair("1", first=3)[1]
        conflicting["text"] += "\nНомер протокола: 2"
        self.assertEqual(self.evaluate(docs + [conflicting]), [])

    def test_conflicting_dates_and_duplicate_versions_remain_ambiguous(self):
        docs = pair("1")
        with self.subTest("duplicate"):
            self.assertEqual(self.evaluate(docs + [copy.deepcopy(docs[1])]), [])
        with self.subTest("different date"):
            self.assertEqual(self.evaluate(docs + [pair("1", first=3, posted="14.10.2026")[1]]), [])

    def test_ambiguous_episode_does_not_erase_independent_numbered_evidence(self):
        docs = pair("1")
        found = self.evaluate(docs + [copy.deepcopy(docs[1])] + pair("2", first=3))
        self.assertEqual([legal.finding_protocol_number(x) for x in found], ["2"])

    def test_different_protocol_kinds_are_not_paired(self):
        docs = pair("1")
        docs[1]["name"] = "Публикация протокола рассмотрения № 1.pdf"
        docs[1]["text"] = "Дата размещения протокола в ЕИС: 13.10.2026"
        self.assertEqual(self.evaluate(docs), [])

    def test_same_receipt_with_different_url_spelling_is_not_independent(self):
        docs = pair("1")
        alias = docs[0]["url"] + "&download=1"
        docs[1]["url"] = docs[1]["meta"]["source_url"] = alias
        self.assertEqual(self.evaluate(docs), [])

    def test_unverified_original_cannot_establish_violation(self):
        for key, value in (("source", "unknown"), ("sha256", "invalid"), ("source_url", "https://example.org/1")):
            docs = pair("1")
            docs[1]["meta"][key] = value
            with self.subTest(key):
                self.assertEqual(self.evaluate(docs), [])

    def test_template_and_noncompetitive_purchase_are_excluded(self):
        docs = pair("1")
        docs[0]["name"] = "Образец " + docs[0]["name"]
        self.assertEqual(self.evaluate(docs), [])
        self.assertEqual(self.evaluate(pair("1"), {"method": "Единственный поставщик"}), [])

    def test_timely_and_reverse_dates_do_not_create_violation(self):
        for posted in ("06.10.2026", "08.10.2026", "09.10.2026", "05.10.2026"):
            with self.subTest(posted):
                self.assertEqual(self.evaluate(pair("1", posted=posted)), [])

    def test_existing_weekend_and_unknown_calendar_behavior_is_preserved(self):
        self.assertEqual(self.evaluate(pair("1", signed="08.10.2026", posted="12.10.2026")), [])
        self.assertEqual(len(self.evaluate(pair("1", signed="08.10.2026", posted="13.10.2026"))), 1)
        with patch.object(legal, "federal_workday", return_value=None):
            self.assertEqual(self.evaluate(pair("1")), [])

    def test_same_original_is_not_a_cross_document_pair(self):
        docs = pair("1")
        docs[0]["text"] += "\n" + docs[1]["text"]
        self.assertEqual(self.evaluate([docs[0]]), [])

    def test_same_event_already_found_by_local_analyzer_is_not_added(self):
        docs = pair("1")
        existing = self.evaluate(docs)[0]
        existing["code"] = "223-4-12-PROTOCOL-PUBLICATION-LATE"
        ns = {"analyze": lambda p, d: [existing]}
        legal.install(ns)
        found = ns["analyze"](PURCHASE, docs + pair("2", first=3))
        self.assertEqual(len(found), 2)
        self.assertEqual({legal.finding_protocol_number(x) for x in found}, {"1", "2"})

    def test_date_spelling_alias_is_still_same_episode(self):
        item = self.evaluate(pair("1"))[0]
        alias = copy.deepcopy(item)
        alias["evidence"] = alias["evidence"].replace("2026-10-06", "06.10.2026")
        self.assertTrue(same_episode(item, alias))
        self.assertEqual(len(collapse_legal_events([item, alias])), 1)

    def test_all_source_inputs_are_unchanged(self):
        docs = pair("1") + pair("2", first=3)
        original = copy.deepcopy(docs)
        self.evaluate(docs)
        self.assertEqual(docs, original)

    def test_distinct_events_persist_and_display_once_each_after_reaudit(self):
        with tempfile.TemporaryDirectory(prefix="eis-protocol-identity-") as root:
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
                db.execute("INSERT INTO purchases(reg_number) VALUES (?)", (REG,))
            ns = {"conn": connect, "analyze": lambda p, d: []}
            legal.install(ns)
            found = ns["analyze"](PURCHASE, pair("1") + pair("2", first=3))
            self.assertEqual(persist(ns, REG, found, []), (2, 0))
            self.assertEqual(persist(ns, REG, found, []), (0, 0))
            with connect() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM findings")]
                self.assertEqual(len(rows), 2)
                self.assertEqual(len(collapse_legal_events(rows)), 2)
                self.assertEqual(load_counts(db, [REG]), {REG: 2})
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
