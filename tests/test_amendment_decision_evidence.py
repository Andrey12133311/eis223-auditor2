"""A dated revision cannot stand in for a formal amendment-adoption decision."""
import hashlib
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v330_amendments_223 as rules


PURCHASE = {"reg": "32610002001", "method": "Конкурс",
            "published": "2026-10-01", "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32
PAGE = "https://zakupki.gov.ru/223/purchase/public/purchase/info/changes.html?regNumber=" + PURCHASE["reg"]
PUBLICATION = "Дата публикации изменений в ЕИС: 13.10.2026."


def original(text, name="Изменения.pdf"):
    return {"name": name, "status": "checked", "text": text, "url": SOURCE,
            "meta": {"source": "official_eis_original", "source_url": SOURCE,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


def history(source=SOURCE):
    return {"kind": "eis_history", "publication_date": date(2026, 10, 13),
            "document": "История изменений ЕИС", "source": PAGE,
            "attachment_source": source, "publication_evidence": PUBLICATION}


class AmendmentDecisionEvidence(unittest.TestCase):
    def assess(self, text, events=()):
        with patch.object(rules, "manifest_changes", return_value=list(events)):
            return rules.evaluation(PURCHASE, [original(text)])

    def test_revision_date_does_not_become_an_adoption_date(self):
        for label in ("Редакция документации от", "Изменения документации от",
                      "Новая редакция извещения от", "Извещение от", "Документ от",
                      "Дата редакции:", "Дата документации:"):
            quote = label + " 06.10.2026"
            with self.subTest(label=label):
                found, reviews = self.assess(quote + ".\n" + PUBLICATION)
                self.assertFalse(found)
                dated = [item for item in reviews if item["event"].get("document_date")]
                self.assertTrue(dated)
                self.assertTrue(all(item["event"].get("decision_date") is None for item in dated))
                check = rules.review_check(dated[0])
                self.assertEqual(check["status"], "check")
                self.assertIn("06.10.2026", check["match_text"])
                self.assertIn(check["match_text"], check["evidence"])
                self.assertIn("решение: не установлено", check["evidence"])
                self.assertEqual(check["source_url"], SOURCE)

    def test_unpublished_dated_revision_retains_a_review_with_quote(self):
        text = "Редакция документации от 06.10.2026. Сроки и условия закупки."
        found, reviews = self.assess(text)
        self.assertFalse(found)
        self.assertEqual(len(reviews), 1)
        check = rules.review_check(reviews[0])
        self.assertIn("Редакция документации от 06.10.2026", check["evidence"])
        self.assertIn("ЕИС: не установлено", check["evidence"])

    def test_formal_decision_and_explicit_amendment_approval_still_confirm(self):
        for label in ("Дата принятия решения о внесении изменений в документацию:",
                      "Изменения документации утверждены",
                      "Редакция документации утверждена",
                      "Изменения в извещение утверждены",
                      "Новая редакция извещения утверждена"):
            with self.subTest(label=label):
                found, _ = self.assess(label + " 06.10.2026.\n" + PUBLICATION)
                self.assertEqual(len(found), 1)
                self.assertIn("Решение об изменении: 2026-10-06", found[0]["evidence"])
                self.assertEqual(found[0]["source_url"], SOURCE)

    def test_document_approval_alone_is_not_approval_of_an_amendment(self):
        for quote in ("Документация утверждена 06.10.2026",
                      "Извещение утверждено 06.10.2026"):
            with self.subTest(quote=quote):
                found, reviews = self.assess(quote + ".\n" + PUBLICATION)
                self.assertFalse(found)
                self.assertTrue(any(item["event"].get("document_date_evidence") == quote
                                    for item in reviews))

    def test_earlier_revision_date_does_not_override_later_actual_decision(self):
        found, _ = self.assess("Редакция документации от 06.10.2026.\n"
                              "Дата принятия решения о внесении изменений: 12.10.2026.\n"
                              + PUBLICATION)
        self.assertFalse(found)

    def test_exact_history_attachment_cannot_promote_a_revision_date(self):
        found, reviews = self.assess("Редакция документации от 06.10.2026. Условия закупки.",
                                    [history()])
        self.assertFalse(found)
        self.assertTrue(any(item["event"].get("document_date") for item in reviews))
        decision = "Решение от 06.10.2026 о внесении изменений в документацию."
        found, _ = self.assess(decision, [history()])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source_url"], PAGE)
        self.assertIn(SOURCE, found[0]["evidence"])
        self.assertFalse(self.assess(decision, [history(SOURCE.replace("A" * 32, "B" * 32))])[0])

    def test_installed_check_preserves_the_unproven_date_as_a_quote(self):
        ns = {"analyze": lambda *_: [], "build_checks": lambda *_: []}
        rules.install(ns)
        quote = "Редакция документации от 06.10.2026"
        docs = [original(quote + ".\n" + PUBLICATION)]
        with patch.object(rules, "manifest_changes", return_value=[]):
            self.assertFalse(ns["analyze"](PURCHASE, docs))
            checks = ns["build_checks"](PURCHASE, {}, {}, docs)
        self.assertTrue(any(quote in item["evidence"] and item["match_text"] == quote
                            and item["status"] == "check" for item in checks))


if __name__ == "__main__":
    unittest.main()
