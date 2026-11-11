"""Fresh subject-matter proof is required for retained SME requirement claims."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_vp_sme_application import CLAUSE, P, original
from vp_sme_application import CODE, LAW, evaluate as subject_evaluate
from vp_inspection_engine import evaluate, qualify_outcomes


def claim(doc, **changes):
    return {"code": CODE, "kind": "violation", "law": LAW,
            "title": "Условие об отклонении заявки", "document": doc["name"],
            "source_url": doc["meta"]["source_url"], "evidence": doc["text"],
            **changes}


def target(purchase, docs, item):
    result = evaluate(purchase, docs, [item])
    return next(row for row in result["rows"] if row["key"] == "sme_application_requirements")


class SmeMethodologyReassessment(unittest.TestCase):
    def assert_review(self, purchase, docs, item):
        findings, checks = qualify_outcomes(purchase, docs, [item], [])
        self.assertEqual(findings, [])
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["reported_kind"], "violation")
        self.assertEqual(checks[0]["evidence"], item["evidence"])
        row = target(purchase, docs, item)
        self.assertEqual(row["status"], "unknown")
        self.assertFalse(row["has_violation"])
        self.assertFalse(row["no_violation"])
        return checks[0]

    def test_current_exact_clause_remains_confirmed_and_inputs_unchanged(self):
        doc = original()
        item = claim(doc)
        snapshot = deepcopy((P, doc, item))
        findings, checks = qualify_outcomes(P, [doc], [item], [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])
        self.assertEqual(target(P, [doc], item)["status"], "violation")
        self.assertEqual((P, doc, item), snapshot)

    def test_method_and_electronic_form_are_rechecked(self):
        doc = original()
        for method in ("Закупка у единственного поставщика", "Электронный аукцион",
                       "Запрос котировок в электронной форме", "Открытый конкурс",
                       "Открытый конкурс в бумажной форме"):
            with self.subTest(method=method):
                self.assert_review({**P, "method": method}, [doc], claim(doc))

    def test_conflicting_secondary_method_and_soap_type_are_rechecked(self):
        doc = original()
        for purchase in (
            {**P, "purchase_method": "Электронный аукцион"},
            {**P, "card_fields_json": {**P["card_fields_json"], "soap_metadata": {
                "source": "official_eis_getDocsIP", "xml_type": "purchaseNoticeAESMBO",
                "xml_sha256": "a" * 64}}},
        ):
            with self.subTest(purchase=purchase):
                self.assert_review(purchase, [doc], claim(doc))

    def test_malformed_soap_premise_cannot_bypass_the_subject_sme_guard(self):
        doc = original()
        purchase = {**P, "card_fields_json": {"soap_metadata": {
            "source": "official_eis_getDocsIP", "xml_type": "purchaseNoticeKESMBO",
            "xml_sha256": "not-a-verified-sha"}}}
        self.assert_review(purchase, [doc], claim(doc))

    def test_current_publication_date_and_effective_boundary_are_rechecked(self):
        doc = original()
        for dates in ({"published": "2021-03-31"}, {"published": None},
                      {"published_at": "2020-10-01"}, {"published_at": "unknown"}):
            with self.subTest(dates=dates):
                self.assert_review({**P, **dates}, [doc], claim(doc))
        self.assertEqual(target({**P, "published": "2021-04-01"}, [doc], claim(doc))["status"], "violation")

    def test_scoring_nexus_cannot_be_replaced_by_a_saved_rule_code(self):
        for body in (
            "Непредставление документов, представляемых для оценки заявки, является основанием для отклонения заявки.",
            "Непредставление обязательной лицензии является основанием для отклонения заявки.",
            "Непредставление документов для оценки заявки по критерию «Квалификация» не является основанием для отклонения заявки.",
            "При непредставлении документов для оценки заявки по критерию «Квалификация» участнику присваивается 0 баллов.",
        ):
            with self.subTest(body=body):
                doc = original(body)
                self.assert_review(P, [doc], claim(doc))

    def test_ambiguous_saved_revisions_cannot_reuse_old_confirmation(self):
        doc = original()
        later = original("Другая редакция документации без прежнего условия.")
        self.assert_review(P, [doc, later], claim(doc, sha256=doc["meta"]["sha256"]))

    def test_statement_elsewhere_in_same_original_cannot_repair_a_claim(self):
        unrelated = "Непредставление обязательной лицензии является основанием для отклонения заявки."
        doc = original(unrelated + "\n\n" + CLAUSE)
        self.assert_review(P, [doc], claim(doc, evidence=unrelated))

    def test_source_quote_in_another_original_cannot_repair_a_claim(self):
        doc = original("В составе заявки представлены сведения об участнике.", name="Документация допуска.pdf")
        other = original(name="Документация оценки.pdf")
        self.assert_review(P, [doc, other], claim(doc))

    def test_duplicate_quotes_need_a_specific_source_occurrence(self):
        doc = original(CLAUSE + "\n\n" + CLAUSE)
        self.assertEqual(len(subject_evaluate(P, [doc])[0]), 1)
        self.assert_review(P, [doc], claim(doc, evidence=CLAUSE))
        item = claim(doc, evidence=CLAUSE, source_position=len(CLAUSE)+2)
        self.assertEqual(target(P, [doc], item)["status"], "violation")

    def test_current_rule_cannot_claim_that_a_bid_was_actually_rejected(self):
        doc = original()
        for field in ("decision_kind", "legal_fact_type"):
            with self.subTest(field=field):
                self.assert_review(P, [doc], claim(doc, **{field: "actual_event"}))

    def test_retained_assessment_is_replaced_and_review_is_idempotent(self):
        doc = original()
        purchase = {**P, "published": "2020-10-01"}
        item = claim(doc, sme_application_assessment={"status": "confirmed",
            "source_binding_verified": True, "current_requirement_confirmed": True})
        review = self.assert_review(purchase, [doc], item)
        assessment = review["sme_application_assessment"]
        self.assertEqual(assessment["status"], "review")
        self.assertTrue(assessment["source_binding_verified"])
        self.assertFalse(assessment["current_requirement_confirmed"])
        self.assertEqual(qualify_outcomes(purchase, [doc], [], [review]), ([], [review]))

    def test_rejected_subject_assessment_is_cached_for_the_input_snapshot(self):
        doc = original()
        purchase = {**P, "published": "2020-10-01"}
        with patch("vp_sme_application.evaluate", wraps=subject_evaluate) as subject:
            findings, reviews = qualify_outcomes(purchase, [doc], [claim(doc), claim(doc)], [])
            self.assertEqual(findings, [])
            self.assertEqual(len(reviews), 2)
            self.assertEqual(subject.call_count, 1)

    def test_negative_requirement_rule_cannot_establish_whole_row_compliance(self):
        doc = original()
        item = claim(doc, kind="check", status="compliant", criterion_complete=True)
        row = target(P, [doc], item)
        self.assertEqual(row["status"], "unknown")
        self.assertFalse(row["no_violation"])

    def test_existing_subject_output_requalifies_idempotently_and_once_per_call(self):
        doc = original()
        current = subject_evaluate(P, [doc])[0][0]
        with patch("vp_sme_application.evaluate", wraps=subject_evaluate) as subject:
            findings, checks = qualify_outcomes(P, [doc], [current, deepcopy(current)], [])
            self.assertEqual(len(findings), 2)
            self.assertEqual(checks, [])
            self.assertEqual(subject.call_count, 1)
        self.assertEqual(qualify_outcomes(P, [doc], findings, checks), (findings, checks))


if __name__ == "__main__":
    unittest.main()
