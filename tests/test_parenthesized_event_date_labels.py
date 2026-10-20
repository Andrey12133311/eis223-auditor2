"""Explicit synonymous date labels, with actual-date and copy guards retained."""
from copy import deepcopy
import hashlib
import unittest
from test_legal_date_label_archive_recall import original, P, archive_pair
import v341_precise_legal as precise
import v348_cross_document_legal as cross


class ParenthesizedEventLabels(unittest.TestCase):
    def assess(self, receipt, publication):
        return precise.evaluate(P, [original("Разъяснение на запрос № 1.pdf",
            receipt + ": 06.10.2026\n" + publication + ": 13.10.2026")])

    def test_receipt_and_placement_synonyms_recover_same_late_answer(self):
        for receipt in ("Дата поступления (получения) запроса",
                        "Дата и время регистрации (поступления) запроса на разъяснение документации"):
            for publication in ("Дата размещения (публикации) ответа в ЕИС",
                                "Дата публикации (размещения) разъяснения в ЕИС"):
                with self.subTest(receipt=receipt, publication=publication):
                    found, _ = self.assess(receipt, publication)
                    self.assertEqual(len(found), 1)
                    self.assertIn("2026-10-09", found[0]["evidence"])

    def test_unrelated_or_planned_event_cannot_supply_receipt(self):
        for receipt in ("Дата поступления (составления) запроса",
                        "Дата отправки (получения) запроса",
                        "Планируемая дата поступления (получения) запроса"):
            with self.subTest(receipt=receipt):
                self.assertFalse(self.assess(receipt, "Дата размещения (публикации) ответа в ЕИС")[0])

    def test_preparation_alias_or_non_eis_site_is_not_publication(self):
        for publication in ("Дата размещения (подготовки) ответа в ЕИС",
                            "Дата публикации (размещения) ответа на сайте организатора"):
            with self.subTest(publication=publication):
                self.assertFalse(self.assess("Дата поступления (получения) запроса", publication)[0])

    def test_protocol_publication_alias_retains_three_day_chain(self):
        doc = original("Итоговый протокол № 1.pdf",
            "Дата подписания итогового протокола: 06.10.2026\n"
            "Дата размещения (публикации) итогового протокола в ЕИС: 13.10.2026")
        found, _ = precise.evaluate(P, [doc])
        self.assertEqual(len(found), 1)
        self.assertIn("PROTOCOL", found[0]["code"])

    def test_cross_original_pair_and_identical_copy_produce_one_event(self):
        docs = archive_pair()
        docs[0]["text"] = docs[0]["text"].replace("Дата поступления запроса", "Дата поступления (получения) запроса")
        docs[1]["text"] = docs[1]["text"].replace("Дата публикации разъяснения", "Дата публикации (размещения) разъяснения")
        for doc in docs:
            doc["meta"]["sha256"] = hashlib.sha256(doc["text"].encode()).hexdigest()
        self.assertEqual(len(cross.cross_doc_clarifications(P, docs + [deepcopy(docs[0]), deepcopy(docs[1])])), 1)

    def test_unknown_procurement_scope_does_not_gain_confirmed_violation(self):
        p = deepcopy(P)
        p["method"] = ""
        doc = original("Разъяснение на запрос № 1.pdf",
            "Дата поступления (получения) запроса: 06.10.2026\n"
            "Дата публикации (размещения) разъяснения в ЕИС: 13.10.2026")
        self.assertFalse(precise.evaluate(p, [doc])[0])


if __name__ == "__main__":
    unittest.main()
