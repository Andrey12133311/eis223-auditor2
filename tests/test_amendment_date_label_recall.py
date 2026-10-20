"""Closed amendment labels expose existing dates without weakening proof.

All originals are synthetic; full legal evaluation is exercised rather than
asserting only a regular-expression match.
"""
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v330_amendments_223 as rules


PURCHASE = {"reg": "32610002001", "method": "Конкурс",
            "published": "2026-10-01", "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32
DECISION = "Дата принятия решения о внесении изменений в документацию: 06.10.2026"
PUBLICATION = "Дата публикации изменений в единой информационной системе: 13.10.2026"


def original(text, name="Изменения.pdf"):
    return {"name": name, "status": "checked", "text": text, "url": SOURCE,
            "meta": {"source": "official_eis_original", "source_url": SOURCE,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


class AmendmentDateLabelRecall(unittest.TestCase):
    def assess(self, text, name="Изменения.pdf", purchase=PURCHASE):
        with patch.object(rules, "manifest_changes", return_value=[]):
            return rules.evaluation(purchase, [original(text, name)])

    def test_target_belongs_to_the_decision_field(self):
        for label in ("Дата принятия решения о внесении изменений в документацию",
                      "Дата решения о внесении изменений в извещение",
                      "Решение о внесении изменений в документацию от"):
            with self.subTest(label=label):
                found, _ = self.assess(label + ": 06.10.2026\nДата публикации изменений в ЕИС: 13.10.2026")
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["code"], "223-4-11-AMENDMENT-PUBLICATION-DELAY")
                self.assertIn("2026-10-09", found[0]["evidence"])
                self.assertIn(SOURCE, found[0]["evidence"])

    def test_full_eis_name_in_each_explicit_publication_form(self):
        for label in ("Дата публикации изменений в единой информационной системе",
                      "Дата и время размещения изменений в документацию в единой информационной системе в сфере закупок",
                      "Дата размещения в единой информационной системе изменений в извещение",
                      "Изменения опубликованы в единой информационной системе в сфере закупок",
                      "Редакция размещена в единой информационной системе"):
            with self.subTest(label=label):
                found, _ = self.assess(DECISION + "\n" + label + ": 13.10.2026")
                self.assertEqual(len(found), 1)
                self.assertIn("2026-10-13", found[0]["evidence"])

    def test_multiline_label_spelled_date_and_reversed_unique_table(self):
        text = ("Дата и время публикации изменений в единой\nинформационной системе в сфере закупок | 13 октября 2026\n"
                "Дата принятия решения о внесении изменений\nв документацию | «6» октября 2026")
        self.assertEqual(len(self.assess(text)[0]), 1)

    def test_generic_notice_and_document_dates_do_not_establish_amendment_publication(self):
        for label in ("Дата публикации извещения в единой информационной системе",
                      "Дата размещения документации в единой информационной системе",
                      "Дата размещения в единой информационной системе извещения",
                      "Дата публикации документа в ЕИС"):
            with self.subTest(label=label):
                found, reviews = self.assess(DECISION + "\n" + label + ": 13.10.2026")
                self.assertFalse(found)
                self.assertTrue(reviews)

    def test_arbitrary_prose_and_preparation_do_not_supply_adoption_date(self):
        for text in ("Дата принятия решения о внесении изменений в документацию обсуждалась на совещании 06.10.2026",
                     "Дата подготовки решения о внесении изменений в документацию: 06.10.2026",
                     "Документация от 06.10.2026",
                     "Решение от 06.10.2026 о проведении закупки"):
            with self.subTest(text=text):self.assertFalse(self.assess(text + "\n" + PUBLICATION)[0])

    def test_templates_and_different_revision_identities_do_not_supply_a_pair(self):
        text = DECISION + "\n" + PUBLICATION
        for name in ("Проект решения.pdf", "Образец изменений.docx", "Пример.pdf"):
            with self.subTest(name=name):self.assertFalse(self.assess(text, name=name)[0])
        self.assertFalse(self.assess("Проект: " + text)[0])
        for label in ("Изменение", "Редакция", "Версия"):
            text = label + " № 1. " + DECISION + "\n" + label + " № 2. " + PUBLICATION
            with self.subTest(label=label):self.assertFalse(self.assess(text)[0])

    def test_timely_calendar_scope_provenance_and_deduplication_remain_unchanged(self):
        text = DECISION + "\n" + PUBLICATION
        self.assertFalse(self.assess(text.replace("13.10.2026", "09.10.2026"))[0])
        self.assertFalse(self.assess(text, purchase=dict(PURCHASE, method="Единственный поставщик"))[0])
        with patch.object(rules, "federal_workday", return_value=None):
            self.assertFalse(self.assess(text)[0])
        bad = original(text)
        bad["status"] = "unverified"
        with patch.object(rules, "manifest_changes", return_value=[]):
            self.assertFalse(rules.evaluation(PURCHASE, [bad])[0])
            self.assertEqual(len(rules.evaluation(PURCHASE, [original(text), original(text, "Копия.pdf")])[0]), 1)


if __name__ == "__main__":
    unittest.main()
