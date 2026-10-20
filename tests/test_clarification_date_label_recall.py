"""Explicit clarification receipt/placement labels retain their legal dates.

Synthetic verified-original fixtures test label recognition; no result asserts
a violation by a real customer or replaces receipt verification at intake.
"""
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v341_precise_legal as precise
import v348_cross_document_legal as cross


PURCHASE = {"reg": "32610002001", "method": "Аукцион в электронной форме",
            "published": "2026-10-01", "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
REQUEST = "Дата поступления запроса о даче разъяснений положений документации"
ANSWER = "Дата размещения в ЕИС разъяснений положений документации"


def original(name, text, uid=1):
    url = SOURCE + f"{uid:032X}"
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


class ClarificationDateLabelRecall(unittest.TestCase):
    def assess(self, request=REQUEST, answer=ANSWER, purchase=PURCHASE):
        doc = original("Разъяснение на запрос № 1.pdf",
                       request + ": 06.10.2026\n" + answer + ": 13.10.2026")
        return precise.evaluate(purchase, [doc])

    def test_receipt_of_request_for_providing_clarification(self):
        for label in (REQUEST,
                      "Дата получения заказчиком запроса на предоставление разъяснений документации",
                      "Дата регистрации запроса о предоставлении разъяснений положений документации",
                      "Дата и время поступления запроса на дачу разъяснений документации",
                      "Запрос о даче разъяснений положений документации поступил",
                      "Запрос на предоставление разъяснений документации получен"):
            with self.subTest(label=label):
                found, _ = self.assess(request=label)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["code"], "223-3.2-3-CLARIFICATION-RESPONSE-LATE")
                for value in ("2026-10-06", "2026-10-09", "2026-10-13"):
                    self.assertIn(value, found[0]["evidence"])

    def test_placement_location_before_clarification_subject(self):
        for label in (ANSWER,
                      "Дата и время публикации в единой информационной системе разъяснения документации",
                      "Дата размещения в ЕИС ответа на запрос о даче разъяснений положений документации",
                      "Дата публикации в единой информационной системе в сфере закупок ответа на запрос на предоставление разъяснений документации"):
            with self.subTest(label=label):
                self.assertEqual(len(self.assess(answer=label)[0]), 1)

    def test_bounded_grammar_accepts_newlines_and_dates_in_words(self):
        doc = original("Разъяснение на запрос № 1.pdf",
                       "Дата поступления запроса о даче\nразъяснений положений документации:\n«6» октября 2026\n"
                       "Дата размещения в единой информационной системе\nразъяснения документации:\n13 октября 2026")
        self.assertEqual(len(precise.evaluate(PURCHASE, [doc])[0]), 1)

    def test_separate_verified_originals_use_the_same_label_grammar(self):
        docs = [original("Запрос № 1.pdf", REQUEST + ": 06.10.2026", 1),
                original("Разъяснение на запрос № 1.pdf", ANSWER + ": 13.10.2026", 2)]
        found = cross.cross_doc_clarifications(PURCHASE, docs)
        self.assertEqual(len(found), 1)
        self.assertEqual(cross.finding_clarification_number(found[0]), "1")
        for doc in docs:self.assertIn(doc["url"], found[0]["evidence"])

    def test_preparation_and_generic_publication_do_not_supply_placement(self):
        for label in ("Дата подготовки разъяснений положений документации",
                      "Дата подписания ответа на запрос о даче разъяснений",
                      "Дата публикации документа в ЕИС",
                      "Дата размещения в ЕИС документа",
                      "Дата размещения в ЕИС"):
            with self.subTest(label=label):
                found, checks = self.assess(answer=label)
                self.assertFalse(found)
                self.assertTrue(checks)

    def test_composition_and_sending_dates_do_not_supply_receipt(self):
        for label in ("Дата составления запроса о даче разъяснений документации",
                      "Дата направления запроса на предоставление разъяснений документации",
                      "Дата документа"):
            with self.subTest(label=label):self.assertFalse(self.assess(request=label)[0])

    def test_unverified_document_scope_deadline_and_timely_response_preserve_gates(self):
        for purchase in (dict(PURCHASE, method="Единственный поставщик"),
                         dict(PURCHASE, submission_end=""),
                         dict(PURCHASE, submission_end="2026-10-08")):
            with self.subTest(purchase=purchase):self.assertFalse(self.assess(purchase=purchase)[0])
        doc = original("Разъяснение на запрос № 1.pdf",
                       REQUEST + ": 06.10.2026\n" + ANSWER + ": 09.10.2026")
        self.assertFalse(precise.evaluate(PURCHASE, [doc])[0])
        doc["text"] = doc["text"].replace("09.10.2026", "13.10.2026")
        doc["status"] = "unverified"
        self.assertFalse(precise.evaluate(PURCHASE, [doc])[0])

    def test_ambiguous_or_different_requests_are_not_combined(self):
        docs = [original("Запрос № 1.pdf", REQUEST + ": 06.10.2026", 1),
                original("Разъяснение на запрос № 2.pdf", ANSWER + ": 13.10.2026", 2)]
        self.assertFalse(cross.cross_doc_clarifications(PURCHASE, docs))
        docs[1]["name"] = "Разъяснение на запрос № 1.pdf"
        docs.append(original("Запрос № 1.pdf", REQUEST + ": 07.10.2026", 3))
        self.assertFalse(cross.cross_doc_clarifications(PURCHASE, docs))


if __name__ == "__main__":
    unittest.main()
