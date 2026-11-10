"""A collective agreement is extra bid paperwork only with a direct duty.

Synthetic EIS descriptors exercise the real FAS analyzer and the common
qualification stage; no production data, network, or database is used.
"""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import v324_fas_practice as fas
from vp_inspection_engine import qualify_outcomes


CODE = "SME-COLLECTIVE-APPLICATION-EXTRA"
METHOD = ("Конкурс в электронной форме, участниками которого могут быть только "
          "субъекты малого и среднего предпринимательства")
PURCHASE = {"reg": "32610002002", "method": METHOD, "published": "2026-10-01"}
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
MANDATORY = ("Коллективные участники обязаны представить соглашение между "
             "членами объединения во второй части заявки на участие в конкурсе.")


def original(body, name="Документация.docx", uid=1, **metadata):
    return {"name": name, "type": "documentation", "text": body, "status": "checked",
            "meta": {"source": "official_eis_original", "source_url": URL + f"{uid:032X}",
                     "sha256": hashlib.sha256(body.encode()).hexdigest(), **metadata}}


def analyze(documents, purchase=PURCHASE):
    ns = {"analyze": lambda *_: [], "build_checks": lambda *_: []}
    with redirect_stdout(io.StringIO()):
        fas.install(ns)
        findings = ns["analyze"](purchase, documents)
        checks = ns["build_checks"](purchase, {}, {}, documents)
    return findings, checks, qualify_outcomes(purchase, documents, findings, checks)


class CollectiveAgreementApplicationDuty(unittest.TestCase):
    def assert_documented_duty(self, body):
        documents = [original(body)]
        before = deepcopy((PURCHASE, documents))
        findings, _, (confirmed, reviews) = analyze(documents)
        self.assertEqual([item["code"] for item in findings], [CODE])
        # This existing FAS rule is not registered for automatic confirmation.
        # Better text recall must not bypass that independent legal decision.
        self.assertEqual(confirmed, [])
        self.assertEqual([item["code"] for item in reviews], [CODE])
        self.assertEqual(reviews[0]["kind"], "review")
        self.assertEqual(reviews[0]["methodology_reason_codes"], ["unregistered_decision_rule"])
        self.assertEqual(reviews[0]["criterion_keys"], ["sme_application_requirements"])
        self.assertTrue(reviews[0]["decision_basis"]["source_fragment_verified"])
        self.assertTrue(reviews[0]["source_context_eligible"])
        self.assertTrue(reviews[0]["legal_basis_verified"])
        self.assertEqual((PURCHASE, documents), before)
        self.assertEqual(qualify_outcomes(PURCHASE, documents, confirmed, reviews), (confirmed, reviews))
        return reviews[0]

    def assert_unconfirmed(self, body):
        findings, _, (confirmed, _) = analyze([original(body)])
        self.assertFalse([item for item in findings if item.get("code") == CODE])
        self.assertFalse([item for item in confirmed if item.get("code") == CODE])

    def test_explicit_second_part_submission_reaches_final_qualification(self):
        self.assert_documented_duty(MANDATORY)

    def test_singular_must_and_other_explicit_inflections(self):
        for obligation in ("должен представить", "должен предоставлять", "обязан приложить"):
            with self.subTest(obligation=obligation):
                self.assert_documented_duty("Коллективный участник " + obligation +
                    " в составе заявки соглашение между членами объединения.")

    def test_application_location_before_subject_and_inside_parts(self):
        for location in ("В составе заявки", "Во второй части заявки", "В первой части заявки"):
            with self.subTest(location=location):
                self.assert_documented_duty(location + " коллективный участник обязан представить "
                    "соглашение между членами объединения.")

    def test_existing_plural_submission_and_copy_remain_documented(self):
        for obj in ("соглашение", "копию соглашения", "договор простого товарищества"):
            with self.subTest(obj=obj):
                self.assert_documented_duty("Коллективные участники обязаны представить в составе "
                    "заявки на участие в конкурсе " + obj + " между членами объединения.")

    def test_application_must_contain_agreement(self):
        self.assert_documented_duty("Заявка коллективного участника должна содержать соглашение "
                              "между всеми членами объединения.")

    def test_pdf_line_wraps_keep_the_same_direct_duty(self):
        self.assert_documented_duty("Коллективный участник должен\nпредставить соглашение между "
                              "членами объединения\nво второй части заявки.")

    def test_blank_paragraph_boundary_does_not_hide_a_direct_duty(self):
        self.assert_documented_duty("Требования к участникам закупки\n\n" + MANDATORY)

    def test_earlier_possession_mention_does_not_hide_later_direct_duty(self):
        self.assert_documented_duty("Коллективные участники должны иметь соглашение "
                                    "между членами объединения. " + MANDATORY)

    def test_explicit_inclusion_in_application(self):
        self.assert_documented_duty("Коллективный участник должен включить в состав заявки "
                                    "соглашение между членами объединения.")

    def test_possession_and_other_documents_do_not_supply_submission_duty(self):
        for other in ("В составе заявки необходимо представить сведения о цене.",
                      "В составе заявки участник обязан представить доверенность."):
            with self.subTest(other=other):
                self.assert_unconfirmed("Коллективные участники должны иметь соглашение "
                                        "между членами объединения. " + other)

    def test_other_document_in_same_sentence_does_not_supply_application_context(self):
        self.assert_unconfirmed("В составе заявки участник обязан представить сведения о цене, "
            "а коллективный участник обязан представить соглашение между членами объединения.")

    def test_optional_submission_is_not_changed_by_possession_duty(self):
        for optional in ("вправе представить", "могут предоставить", "по желанию прикладывают"):
            with self.subTest(optional=optional):
                self.assert_unconfirmed("Коллективные участники должны иметь соглашение "
                    "между членами объединения, которое " + optional + " в составе заявки.")

    def test_explicit_negations_never_create_extra_document_findings(self):
        for text in (
            "Коллективный участник не должен представлять в составе заявки соглашение между членами объединения.",
            "Коллективные участники не обязаны представить соглашение между членами объединения во второй части заявки.",
            "Коллективный участник не обязан предоставлять соглашение в составе заявки.",
            "Заявка коллективного участника не должна содержать соглашение между членами объединения.",
            "Не требуется обязательное представление соглашения коллективного участника в составе заявки.",
        ):
            with self.subTest(text=text):
                self.assert_unconfirmed(text)

    def test_indirect_agreement_reference_is_not_required_agreement(self):
        for obj in ("сведения о соглашении", "письмо о наличии соглашения",
                    "документ, подтверждающий наличие соглашения"):
            with self.subTest(obj=obj):
                self.assert_unconfirmed("Коллективный участник обязан представить " + obj +
                                         " между членами объединения в составе заявки.")

    def test_later_contract_documents_are_not_application_requirements(self):
        for suffix in ("после заключения договора с заказчиком",
                       "при заключении договора с заказчиком", "после проведения закупки"):
            with self.subTest(suffix=suffix):
                self.assert_unconfirmed("Коллективные участники должны иметь соглашение. "
                    "В составе заявки указывают его реквизиты, а оригинал соглашения "
                    "коллективный участник обязан представить " + suffix + ".")

    def test_unknown_or_non_sme_scope_never_confirms_special_rule(self):
        for method in ("Конкурс в электронной форме", "Единственный поставщик", ""):
            with self.subTest(method=method):
                findings, checks, (confirmed, _) = analyze([original(MANDATORY)], {**PURCHASE, "method": method})
                self.assertEqual(findings, [])
                self.assertEqual(confirmed, [])
                self.assertTrue(checks)

    def test_non_original_source_never_confirms_duty(self):
        doc = original(MANDATORY, source="unverified_mirror")
        findings, _, (confirmed, _) = analyze([doc])
        self.assertEqual(findings, [])
        self.assertEqual(confirmed, [])

    def test_existing_context_gates_keep_samples_and_old_revisions_unconfirmed(self):
        for doc, reason in (
            (original(MANDATORY, name="Образец документации.docx"), "template_or_example"),
            (original("Пример заполнения:\n" + MANDATORY), "illustrative_passage"),
            (original(MANDATORY, superseded=True), "explicitly_superseded_revision"),
            (original(MANDATORY, cancelled=True), "explicitly_cancelled_document"),
        ):
            with self.subTest(doc=doc["name"], meta=doc["meta"]):
                _, _, (confirmed, reviews) = analyze([doc])
                self.assertEqual(confirmed, [])
                self.assertTrue(reviews)
                self.assertIn(reason, reviews[0]["methodology_reason_codes"])

    def test_existing_scope_gates_keep_foreign_purchase_and_lot_unconfirmed(self):
        p = {**PURCHASE, "lot_number": "1"}
        for doc, reason in ((original(MANDATORY, purchase_reg_number="32610009999"), "source_other_purchase"),
                            (original(MANDATORY, lot_number="2"), "source_other_lot"),
                            (original("Лот № 2.\n" + MANDATORY), "source_other_lot")):
            with self.subTest(doc=doc):
                _, _, (confirmed, reviews) = analyze([doc], p)
                self.assertEqual(confirmed, [])
                self.assertTrue(reviews)
                self.assertIn(reason, reviews[0]["methodology_reason_codes"])

    def test_neighboring_lot_does_not_lend_application_context(self):
        self.assert_unconfirmed("Лот № 1.\nКоллективные участники должны иметь соглашение "
            "между членами объединения.\nЛот № 2.\nВ составе заявки необходимо представить сведения о цене.")

    def test_duplicate_copies_keep_existing_single_rule_identity(self):
        findings, _, (confirmed, reviews) = analyze([original(MANDATORY), original(MANDATORY, uid=2)])
        self.assertEqual([item["code"] for item in findings], [CODE])
        self.assertEqual(confirmed, [])
        self.assertEqual([item["code"] for item in reviews], [CODE])
        self.assertEqual(reviews[0]["methodology_reason_codes"], ["unregistered_decision_rule"])

    def test_repeated_duty_in_different_lots_remains_ambiguous(self):
        p = {**PURCHASE, "lot_number": "1"}
        doc = original("Лот № 1.\n" + MANDATORY + "\nЛот № 2.\n" + MANDATORY)
        _, _, (confirmed, reviews) = analyze([doc], p)
        self.assertEqual(confirmed, [])
        self.assertEqual([item["code"] for item in reviews], [CODE])
        self.assertIn("source_occurrence_ambiguous", reviews[0]["methodology_reason_codes"])


if __name__ == "__main__":
    unittest.main()
