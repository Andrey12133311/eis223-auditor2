"""Receipt authenticity does not make sample text an actual commission act."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from legal_document_context import document_context, local_context
from v372_auto_regulation_protocols import evaluate, install


URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A"*32
PURCHASE = {"reg": "32616412317", "method": "Открытый конкурс", "published": "2026-10-01"}
HEAD = "Протокол рассмотрения заявок. Комиссия рассмотрела документы участников закупки. "
EVENT = "Отклонена заявка № 12, основания отклонения не указаны."


def original(name="Протокол рассмотрения заявок.docx", text=None, **metadata):
    text = text or HEAD+EVENT+" Итоги рассмотрения зафиксированы."
    return {"name": name, "status": "checked", "text": text,
            "meta": {"source": "official_eis_original", "source_url": URL,
                     "sha256": hashlib.sha256(text.encode()).hexdigest(), **metadata}}


class SourceContext(unittest.TestCase):
    def test_roles_distinguish_actual_documents_from_templates_and_drafts(self):
        cases = {
            "Протокол рассмотрения.docx": ("protocol", True),
            "Шаблон протокола.docx": ("template", False),
            "Протокол_рассмотрения_образец.docx": ("template", False),
            "Форма протокола.docx": ("template", False),
            "Инструкция по составлению протокола.pdf": ("instruction", False),
            "Проект протокола.docx": ("draft", False),
            "Проект договора.docx": ("project_contract", False),
            "Положение о закупке.docx": ("regulation", True),
            "Документация закупки.docx": ("documentation", True),
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                result = document_context(original(title, "Содержание документа"))
                self.assertEqual((result["role"], result["eligible_actual"]), expected)

    def test_sample_heading_can_follow_an_annex_number_or_approval(self):
        doc = original("Протокол.pdf", "Приложение № 2\nУтверждено\nОбразец протокола\n"+HEAD+EVENT)
        self.assertFalse(document_context(doc)["eligible_actual"])

    def test_product_samples_and_instruction_goods_do_not_change_document_role(self):
        for title, role in (("Документация на изготовление образцов.docx", "documentation"),
                            ("Извещение о поставке инструкций.docx", "notice")):
            self.assertEqual(document_context(original(title, "Требования к товару"))["role"], role)
        text = "При непредставлении документов об опыте заявки отклоняются. В комплект входят образцы продукции."
        self.assertTrue(local_context(text, 0, 60)["eligible_actual"])

    def test_only_explicit_revision_status_limits_actuality(self):
        for metadata in ({"superseded": True}, {"version_status": "obsolete"},
                         {"is_current_version": False}, {"is_current": False},
                         {"document_cancelled": True}, {"cancelled": True}):
            with self.subTest(metadata=metadata):
                context = document_context(original(**metadata))
                self.assertFalse(context["eligible_actual"])
                self.assertTrue(context["reasons"])
        # An archive member and an old-looking filename are not a revision act.
        context = document_context(original("Протокол_2020.pdf", archive_member="архив/протокол.pdf"))
        self.assertTrue(context["eligible_actual"])
        self.assertEqual(context["version_status"], "unspecified")
        self.assertEqual(document_context(original(is_current_version=True))["version_status"], "current")
        self.assertEqual(document_context(original(version_status="active"))["version_status"], "current")
        self.assertEqual(document_context(original(is_current=True, superseded=True))["version_status"], "superseded")

    def test_prospective_clauses_and_quotes_of_actual_decisions_remain_assessable(self):
        for text in ("При непредставлении документов заявка подлежит отклонению.",
                     "Комиссия решила: «"+EVENT+"»."):
            with self.subTest(text=text):
                self.assertTrue(local_context(text, 0, len(text))["eligible_actual"])

    def test_example_scope_spans_paragraphs_until_an_actual_decision_heading(self):
        text = "Пример заполнения:\n\n"+("Объяснение условий заполнения формы. "*30)+"\n\n"+EVENT
        pos = text.index(EVENT)
        self.assertFalse(local_context(text, pos, pos+len(EVENT))["eligible_actual"])
        actual = text+"\n\nФактическое решение комиссии:\n\n"+EVENT
        pos = actual.rindex(EVENT)
        self.assertTrue(local_context(actual, pos, pos+len(EVENT))["eligible_actual"])


class ProtocolDecisions(unittest.TestCase):
    def assert_review_only(self, doc):
        before = deepcopy(doc)
        findings, checks = evaluate(PURCHASE, [doc])
        self.assertEqual(findings, [])
        self.assertTrue(any(x["field_path"] == "v372-protocol-document-context" for x in checks))
        self.assertTrue(all(x["status"] == "check" for x in checks))
        self.assertEqual(doc, before)
        evidence = next(x for x in checks if x["field_path"] == "v372-protocol-document-context")
        self.assertEqual(evidence["document"], doc["name"])
        self.assertEqual(evidence["source_url"], URL)
        self.assertIn("заявка № 12", evidence["evidence"])
        return evidence

    def test_actual_numbered_rejection_and_quoted_commission_decision_stay_confirmed(self):
        for text in (HEAD+EVENT, HEAD+"Комиссия решила: «"+EVENT+"»."):
            with self.subTest(text=text):
                findings, _ = evaluate(PURCHASE, [original(text=text)])
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["kind"], "violation")
                self.assertIn("ст. 3.2 ч. 13", findings[0]["law"])

    def test_template_in_filename_never_establishes_an_actual_rejected_bid(self):
        self.assert_review_only(original("Шаблон протокола.docx"))

    def test_local_example_after_350_characters_preserves_review_evidence(self):
        text = HEAD+("Описание объекта закупки и общих сведений. "*18)+EVENT+" Данный текст приводится как условный образец."
        self.assertGreater(text.index(EVENT), 350)
        review = self.assert_review_only(original(text=text))
        self.assertIn("иллюстративный", review["note"])

    def test_quoted_law_and_instruction_passages_are_not_actual_decisions(self):
        for prefix in ("Цитата из закона: ", "Пример заполнения: ",
                       "Нельзя оформлять протокол следующей фразой: "):
            with self.subTest(prefix=prefix):
                text = HEAD+("Описание процедуры. "*30)+prefix+EVENT
                self.assert_review_only(original(text=text))

    def test_example_section_more_than_one_fragment_before_the_event(self):
        text = HEAD+"\nПример заполнения:\n\n"+("Указания по заполнению. "*40)+"\n\n"+EVENT
        self.assert_review_only(original(text=text))

    def test_actual_decision_after_an_example_section_is_not_silently_lost(self):
        text = HEAD+"\nПример заполнения:\n\nУсловный текст без реального участника."+(
            "\n\nФактическое решение комиссии:\n\n"+EVENT)
        findings, _ = evaluate(PURCHASE, [original(text=text)])
        self.assertEqual(len(findings), 1)

    def test_explicit_superseded_or_cancelled_document_requires_version_review(self):
        for metadata in ({"superseded": True}, {"version_status": "cancelled"}):
            with self.subTest(metadata=metadata):
                review = self.assert_review_only(original(**metadata))
                self.assertIn("редакци", review["note"])

    def test_not_rejected_is_not_a_confirmed_rejection(self):
        doc = original(text=HEAD+"Не отклонена заявка № 12, основания отклонения не указаны.")
        self.assertEqual(evaluate(PURCHASE, [doc])[0], [])

    def test_an_incidental_final_evaluation_word_does_not_change_protocol_stage(self):
        doc = original(text="Протокол рассмотрения заявок. Для итоговой оценки будут применяться критерии. "+EVENT)
        findings, _ = evaluate(PURCHASE, [doc])
        self.assertEqual(len(findings), 1)
        self.assertIn("ст. 3.2 ч. 13", findings[0]["law"])

    def test_shared_installer_keeps_example_as_review_instead_of_new_violation(self):
        ns = {"analyze": lambda p, docs: [], "build_checks": lambda p, detail, card, docs: []}
        install(ns)
        sample = original("Образец протокола.docx")
        self.assertEqual(ns["analyze"](PURCHASE, [sample]), [])
        self.assertTrue(ns["build_checks"](PURCHASE, {}, {}, [sample]))
        self.assertEqual(len(ns["analyze"](PURCHASE, [original()])), 1)


if __name__ == "__main__":
    unittest.main()
