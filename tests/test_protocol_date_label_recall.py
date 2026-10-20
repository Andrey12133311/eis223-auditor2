"""Explicit protocol event labels survive ordinary wording without guessing dates.

These are synthetic verified-source descriptors. Receipt/XML authentication is
covered separately; the fixtures make no claim about a real procurement.
"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import v341_precise_legal as precise
import v348_cross_document_legal as cross


PURCHASE = {"reg": "32610002001", "method": "Аукцион в электронной форме",
            "published": "2026-10-01"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
KINDS = ("итогового протокола", "протокола подведения итогов",
         "протокола рассмотрения заявок", "протокола оценки заявок",
         "протокола рассмотрения и оценки заявок", "протокола допуска к участию")


def original(text, uid=1, name="Протокол № 1.pdf"):
    url = SOURCE + f"{uid:032X}"
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


def local_document(kind="протокола рассмотрения заявок", *, publication_kind=None,
                   posted="13.10.2026", name="Протокол № 1.pdf"):
    return original("Номер протокола: 1\nДата подписания " + kind + ": 06.10.2026\n"
                    "Дата размещения " + (publication_kind or kind) + " в ЕИС: " + posted,
                    name=name)


def split_documents(kind="протокола рассмотрения заявок", *, publication_kind=None):
    return [original("Номер протокола: 1\nДата подписания " + kind + ": 06.10.2026", 1),
            original("Номер протокола: 1\nДата размещения " + (publication_kind or kind)
                     + " в ЕИС: 13.10.2026", 2)]


class ProtocolDateLabelRecall(unittest.TestCase):
    def assess(self, docs, purchase=PURCHASE):
        return precise.evaluate(dict(purchase), docs)

    def test_complete_kind_labels_confirm_same_original_date_chain(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                docs = [local_document(kind)]
                before = copy.deepcopy(docs)
                found, _ = self.assess(docs)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["code"], "223-4-12-PROTOCOL-PUBLICATION-LATE")
                self.assertEqual(cross.finding_protocol_number(found[0]), "1")
                for value in ("2026-10-06", "2026-10-09", "2026-10-13"):
                    self.assertIn(value, found[0]["evidence"])
                self.assertEqual(docs, before)

    def test_time_label_and_eis_before_protocol_are_explicit_events(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                doc = original("Номер протокола: 1\nДата и время подписания " + kind
                    + ": 06.10.2026 15:20\nДата и время публикации в ЕИС " + kind
                    + ": 13.10.2026 09:00")
                self.assertEqual(len(self.assess([doc])[0]), 1)

    def test_full_eis_name_and_newline_cells_keep_explicit_dates(self):
        doc = original("Номер протокола: 1\nДата подписания протокола\nрассмотрения заявок:\n"
            "«6» октября 2026\nДата размещения протокола рассмотрения заявок "
            "в единой информационной системе в сфере закупок:\n13 октября 2026")
        self.assertEqual(len(self.assess([doc])[0]), 1)

    def test_nominative_signed_and_published_forms(self):
        for kind in ("Итоговый протокол", "Протокол рассмотрения заявок",
                     "Протокол рассмотрения и оценки заявок", "Протокол допуска к участию"):
            with self.subTest(kind=kind):
                doc = original("Номер протокола: 1\n" + kind + " подписан: 06.10.2026\n"
                               + kind + " размещён в ЕИС: 13.10.2026")
                self.assertEqual(len(self.assess([doc])[0]), 1)

    def test_reversed_unique_same_original_table_remains_supported(self):
        doc = original("Номер протокола: 1\nДата размещения протокола рассмотрения заявок "
            "в ЕИС: 13.10.2026\nДата подписания протокола рассмотрения заявок: 06.10.2026")
        self.assertEqual(len(self.assess([doc])[0]), 1)

    def test_same_original_different_explicit_kinds_are_not_paired(self):
        for first, second in (("протокола рассмотрения заявок", "итогового протокола"),
                              ("протокола рассмотрения заявок", "протокола оценки заявок"),
                              ("протокола рассмотрения и оценки заявок", "протокола рассмотрения заявок")):
            with self.subTest(first=first, second=second):
                found, reviews = self.assess([local_document(first, publication_kind=second)])
                self.assertFalse(found)
                self.assertTrue(reviews)

    def test_conflicting_title_and_explicit_date_kind_remains_unconfirmed(self):
        found, reviews = self.assess([local_document(name="Итоговый протокол № 1.pdf")])
        self.assertFalse(found)
        self.assertTrue(reviews)

    def test_template_draft_and_sample_protocols_do_not_establish_real_events(self):
        for name in ("Образец протокола № 1.pdf", "Пример протокола № 1.pdf",
                     "Шаблон протокола № 1.pdf", "Проект протокола № 1.pdf"):
            with self.subTest(name=name):
                self.assertFalse(self.assess([local_document(name=name)])[0])
                docs = split_documents()
                docs[0]["name"] = name
                self.assertFalse(cross.cross_doc_protocols(PURCHASE, docs))

    def test_example_section_in_actual_document_is_not_an_actual_event(self):
        for heading in ("Пример заполнения формы протокола. ", "Образец:\n", "Образец:\n\n",
                        "Шаблон протокола:\n", "Например:\n"):
            with self.subTest(heading=heading):
                doc = local_document(name="Документация.pdf")
                doc["text"] = heading + doc["text"]
                found, reviews = self.assess([doc])
                self.assertFalse(found)
                self.assertTrue(reviews)
                docs = split_documents()
                docs[0]["text"] = heading + docs[0]["text"]
                self.assertFalse(cross.cross_doc_protocols(PURCHASE, docs))

    def test_planned_or_estimated_fields_do_not_establish_actual_event_dates(self):
        for adjective in ("Плановая", "Предполагаемая", "Ориентировочная", "Планируемая"):
            for field in ("подписания", "размещения"):
                with self.subTest(adjective=adjective, field=field):
                    doc = local_document(name="Документация.pdf")
                    doc["text"] = doc["text"].replace("Дата " + field, adjective + " дата " + field)
                    self.assertFalse(self.assess([doc])[0])
                    docs = split_documents()
                    index = 0 if field == "подписания" else 1
                    docs[index]["text"] = docs[index]["text"].replace("Дата " + field,
                        adjective + " дата " + field)
                    self.assertFalse(cross.cross_doc_protocols(PURCHASE, docs))

    def test_planned_clarification_receipt_or_answer_is_not_actual_event_evidence(self):
        purchase = dict(PURCHASE, submission_end="2026-10-30")
        text = ("Номер запроса: 1\nДата поступления запроса на предоставление разъяснений "
                "положений документации: 06.10.2026\nДата размещения в ЕИС ответа на запрос "
                "о даче разъяснений положений документации: 13.10.2026")
        self.assertEqual(len(self.assess([original(text, name="Разъяснение на запрос № 1.pdf")], purchase)[0]), 1)
        for label in ("поступления", "размещения"):
            with self.subTest(label=label):
                planned=text.replace("Дата " + label,"Ориентировочная\nдата " + label)
                self.assertFalse(self.assess([original(planned, name="Разъяснение на запрос № 1.pdf")], purchase)[0])

    def test_example_or_planned_signature_cannot_use_xml_fallback(self):
        for prefix in ("Образец:\n", "Пример заполнения формы протокола. "):
            with self.subTest(prefix=prefix):
                doc = original(prefix + "Номер протокола: 1\n"
                    "Дата подписания протокола рассмотрения заявок: 06.10.2026")
                with patch("official_publication_evidence.protocol_publication_verifier",
                           return_value=lambda d: {"state": "confirmed", "publication_date": "2026-10-13"}):
                    self.assertFalse(self.assess([doc])[0])
        doc = original("Номер протокола: 1\nПлановая дата подписания протокола рассмотрения заявок: 06.10.2026")
        with patch("official_publication_evidence.protocol_publication_verifier",
                   return_value=lambda d: {"state": "confirmed", "publication_date": "2026-10-13"}):
            self.assertFalse(self.assess([doc])[0])

    def test_dates_on_opposite_sides_of_distinct_numbered_headings_are_not_paired(self):
        for reverse in (False, True):
            first = "Дата подписания протокола рассмотрения заявок: 06.10.2026"
            second = "Дата размещения протокола рассмотрения заявок в ЕИС: 13.10.2026"
            if reverse:first, second = second, first
            doc = original("Номер протокола: 1\n" + first + "\nНомер протокола: 2\n" + second,
                           name="Сводная информация о протоколах.pdf")
            found, reviews = self.assess([doc])
            self.assertFalse(found)
            self.assertTrue(reviews)

    def test_genuine_multiple_local_pairs_survive_independent_numbered_sections(self):
        first = local_document()["text"]
        second = (local_document()["text"].replace("Номер протокола: 1", "Номер протокола: 2")
                  .replace("06.10.2026", "07.10.2026").replace("13.10.2026", "14.10.2026"))
        doc = original(first + "\n" + second, name="Сводная информация о протоколах.pdf")
        self.assertEqual(len(self.assess([doc])[0]), 2)

    def test_local_example_does_not_suppress_later_actual_numbered_section(self):
        sample = "Образец:\n" + local_document()["text"]
        actual = (local_document()["text"].replace("Номер протокола: 1", "Номер протокола: 2")
                  .replace("06.10.2026", "07.10.2026").replace("13.10.2026", "14.10.2026"))
        doc = original(sample + "\n" + actual, name="Сводная информация о протоколах.pdf")
        found, _ = self.assess([doc])
        self.assertEqual(len(found), 1)
        self.assertIn("2026-10-07", found[0]["evidence"])

    def test_remote_unrelated_example_heading_is_not_a_global_document_veto(self):
        doc = local_document(name="Документация.pdf")
        doc["text"] = "Образец:\n" + "Пояснение к другому разделу. " * 40 + "\n" + doc["text"]
        self.assertEqual(len(self.assess([doc])[0]), 1)

    def test_procurement_subject_project_design_is_not_a_template_name(self):
        for name in ("Протокол рассмотрения заявок на проектирование № 1.pdf",
                     "Протокол по проектным работам № 1.pdf"):
            with self.subTest(name=name):
                self.assertEqual(len(self.assess([local_document(name=name)])[0]), 1)

    def test_closed_kind_vocabulary_does_not_borrow_neighboring_dates(self):
        labels = ("Дата подготовки протокола рассмотрения заявок",
                  "Дата составления протокола рассмотрения заявок",
                  "Дата подписания проекта протокола рассмотрения заявок",
                  "Дата подписания документа к протоколу рассмотрения заявок",
                  "Дата подписания протокола рассмотрения заявок комиссией")
        for label in labels:
            with self.subTest(label=label):
                doc = original("Номер протокола: 1\n" + label + ": 06.10.2026\n"
                    "Дата размещения протокола рассмотрения заявок в ЕИС: 13.10.2026")
                self.assertFalse(self.assess([doc])[0])

    def test_other_publication_actions_are_not_eis_placement(self):
        labels = ("Дата отправки протокола рассмотрения заявок в ЕИС",
                  "Дата размещения протокола рассмотрения заявок на ЭТП",
                  "Дата публикации проекта протокола рассмотрения заявок в ЕИС",
                  "Дата размещения документа к протоколу в ЕИС")
        for label in labels:
            with self.subTest(label=label):
                doc = original("Номер протокола: 1\nДата подписания протокола рассмотрения заявок: "
                    "06.10.2026\n" + label + ": 13.10.2026")
                self.assertFalse(self.assess([doc])[0])

    def test_timely_unknown_calendar_invalid_date_and_unverified_stay_unconfirmed(self):
        self.assertFalse(self.assess([local_document(posted="09.10.2026")])[0])
        with patch.object(precise, "federal_workday", return_value=None):
            self.assertFalse(self.assess([local_document()])[0])
        doc = local_document()
        doc["text"] = doc["text"].replace("06.10.2026", "31.02.2026")
        self.assertFalse(self.assess([doc])[0])
        doc = local_document()
        doc["meta"]["source"] = "local_upload"
        self.assertFalse(self.assess([doc])[0])

    def test_complete_labels_are_shared_by_distinct_originals(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                docs = split_documents(kind)
                found = cross.cross_doc_protocols(PURCHASE, docs)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["code"], "223-4-12-PROTOCOL-CROSS-DOC-LATE")
                self.assertEqual(cross.finding_protocol_number(found[0]), "1")
                for doc in docs:self.assertIn(doc["url"], found[0]["evidence"])

    def test_split_original_different_explicit_kinds_are_not_paired(self):
        for first, second in (("протокола рассмотрения заявок", "итогового протокола"),
                              ("протокола рассмотрения и оценки заявок", "протокола оценки заявок")):
            with self.subTest(first=first, second=second):
                self.assertFalse(cross.cross_doc_protocols(PURCHASE,
                    split_documents(first, publication_kind=second)))

    def test_split_original_requires_unique_numbered_pair_and_independent_sources(self):
        docs = split_documents()
        second = copy.deepcopy(docs[1])
        self.assertFalse(cross.cross_doc_protocols(PURCHASE, docs + [second]))
        second = original(docs[1]["text"].replace("Номер протокола: 1", "Номер протокола: 2"),
                          2, "Протокол № 2.pdf")
        self.assertFalse(cross.cross_doc_protocols(PURCHASE, [docs[0], second]))
        second = original(docs[1]["text"], 1)
        self.assertFalse(cross.cross_doc_protocols(PURCHASE, [docs[0], second]))


if __name__ == "__main__":
    unittest.main()
