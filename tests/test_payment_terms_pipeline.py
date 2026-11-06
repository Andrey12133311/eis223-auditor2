"""Payment applicability must agree before general and thematic persistence.

Only retrieval is synthetic: the fixture runs both real queues, installs the
legal rules and common qualifier, and persists general findings in SQLite.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

import test_general_thematic_parity as fixture
from test_methodology_pipeline import run_pipeline
import vp_inspection_engine as engine


CODE = "1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS"
LONG = "Оплата производится в течение 30 рабочих дней со дня подписания сторонами акта приемки."
SHORT = "Оплата производится в течение 7 рабочих дней со дня подписания сторонами акта приемки."
PURCHASE = {
    **fixture.P,
    "reg": fixture.REG,
    "method": "Открытый конкурс только для субъектов малого и среднего предпринимательства",
}


def original(text, name="Проект договора.docx", *, uid="A", **metadata):
    url = ("https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
           + uid * 32)
    return {
        "name": name, "text": text, "status": "checked", "url": url,
        "meta": {
            "source": "official_eis_original", "source_url": url,
            "uid": uid * 32, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            **metadata,
        },
    }


def payment_pipeline(documents, *, injected=None, coverage=None):
    review = fixture.theme.independent_review
    collect = fixture.general.collect_saved_evidence

    async def exact_criterion(ns, reg, categories):
        return await review(ns, reg, ["sme_payment"])

    async def collection_with_coverage(*args, **kwargs):
        source = await collect(*args, **kwargs)
        source.update(coverage)
        return source

    with ExitStack() as stack:
        stack.enter_context(patch.object(fixture, "P", deepcopy(PURCHASE)))
        stack.enter_context(patch.object(fixture.theme, "independent_review",
                                        side_effect=exact_criterion))
        if coverage is not None:
            stack.enter_context(patch.object(fixture.general, "collect_saved_evidence",
                                            side_effect=collection_with_coverage))
        return run_pipeline(documents, injected)


def payment_items(items):
    return [item for item in items
            if str(item.get("code") or item.get("field_path") or "") == CODE]


class PaymentTermsPipeline(unittest.TestCase):
    def assert_review_only(self, result, decisions, reason, *, thematic_status="completed"):
        first, second, thematic, found, counts = result
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["new_findings"], 0)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual(second[1]["new_checks"], 0)
        self.assertEqual(found, [])
        self.assertEqual(counts["violations"], 0)
        self.assertEqual(thematic, (thematic_status, []))
        # Both general passes and the exact thematic pass must make the same
        # decision; checking only the final export could hide an earlier leak.
        self.assertEqual(len(decisions), 3)
        for findings, checks in decisions:
            self.assertEqual(payment_items(findings), [])
            reviews = payment_items(checks)
            self.assertTrue(reviews)
            for item in reviews:
                self.assertEqual((item["kind"], item["status"]), ("review", "check"))
                self.assertIn(reason, item["methodology_reason_codes"])
                self.assertNotEqual(item["payment_assessment"]["status"], "confirmed")

    def assert_confirmed(self, result, decisions, document):
        first, second, thematic, found, counts = result
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["new_findings"], 1)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual(second[1]["new_checks"], 0)
        self.assertEqual(counts["violations"], 1)
        self.assertEqual([item["code"] for item in found], [CODE])
        self.assertEqual(found[0]["document"], document["name"])
        self.assertEqual(found[0]["source_url"], document["meta"]["source_url"])
        self.assertEqual(thematic[0], "completed")
        self.assertEqual([item["code"] for item in thematic[1]], [CODE])
        self.assertEqual(thematic[1][0]["category"], "sme_payment")
        self.assertEqual(thematic[1][0]["decision_kind"], "contract_condition")
        self.assertEqual(thematic[1][0]["evidence"], found[0]["evidence"])
        self.assertEqual(len(decisions), 3)
        for findings, _ in decisions:
            confirmed = payment_items(findings)
            self.assertEqual(len(confirmed), 1)
            self.assertEqual(confirmed[0]["payment_assessment"]["status"], "confirmed")

    def test_unselected_thirty_and_seven_day_variants_require_review(self):
        doc = original("Оплата выполненных работ осуществляется в следующем порядке:\n"
                       "(редакция 1) " + LONG + "\n(редакция 2) " + SHORT)
        result, decisions = payment_pipeline([doc])
        self.assert_review_only(result, decisions, "payment_alternative_unselected")
        findings, checks = decisions[0]
        projection = engine.evaluate(PURCHASE, [doc], findings, checks)
        row = next(item for item in projection["rows"] if item["key"] == "sme_payment")
        self.assertEqual(row["status"], "unknown")
        self.assertFalse(row["has_violation"])
        self.assertFalse(row["no_violation"])
        self.assertTrue(row["review_required"])

    def test_related_signed_seven_day_contract_does_not_confirm_draft_thirty(self):
        draft = original(LONG, lot_number="1", contract_number="24")
        signed = original("ДОГОВОР № 24\nДата заключения договора: 06.11.2026\n" + SHORT,
                          "Договор № 24.docx", uid="B", lot_number="1", contract_number="24")
        result, decisions = payment_pipeline([draft, signed])
        self.assert_review_only(result, decisions, "payment_cross_phase_terms")
        assessment = payment_items(decisions[0][1])[0]["payment_assessment"]
        self.assertTrue(any(item["days"] == 7 and item["phase"] == "executed_contract"
                            for item in assessment["related_terms"]))

    def test_seven_day_term_in_another_lot_does_not_cancel_thirty(self):
        draft = original(LONG, "Проект договора лот 1.docx", lot_number="1")
        other_lot = original(SHORT, "Проект договора лот 2.docx", uid="B", lot_number="2")
        result, decisions = payment_pipeline([draft, other_lot])
        self.assert_confirmed(result, decisions, draft)
        self.assertEqual(payment_items(decisions[0][0])[0]["payment_assessment"]["related_terms"], [])

    def test_single_unambiguous_thirty_day_condition_remains_confirmed(self):
        draft = original(LONG)
        result, decisions = payment_pipeline([draft])
        self.assert_confirmed(result, decisions, draft)

    def test_unreadable_original_blocks_confirmation_in_both_queues(self):
        unreadable = original("", "Заключенный договор.pdf", uid="B")
        unreadable["status"] = "downloaded_no_text"
        result, decisions = payment_pipeline([original(LONG), unreadable])
        self.assert_review_only(result, decisions, "payment_incomplete_originals", thematic_status="retry")
        self.assertTrue(result[0][1]["source_incomplete"])

    def test_archive_extraction_warning_survives_readable_document_filter(self):
        member = original(LONG, "Документация.7z / Проект договора.docx",
                          source="official_eis_archive_member", archive_member="Проект договора.docx")
        archive = original("", "Документация.7z", uid="B",
                           archive_warnings=["Заключенный договор.docx: не удалось извлечь"])
        archive["status"] = "archive_processed"
        result, decisions = payment_pipeline([member, archive])
        self.assert_review_only(result, decisions, "payment_incomplete_originals", thematic_status="retry")
        self.assertEqual(result[0][1]["verified_original_documents"], 1)

    def test_missing_or_counted_unreadable_original_prevents_confirmation(self):
        for coverage in (
            {"unreadable": 1, "source_incomplete": False},
            {"original_expected": 2, "original_received": 1, "source_incomplete": True},
        ):
            with self.subTest(coverage=coverage):
                result, decisions = payment_pipeline([original(LONG)], coverage=coverage)
                self.assert_review_only(result, decisions, "payment_incomplete_originals", thematic_status="retry")

    def test_legacy_injected_violation_cannot_bypass_common_qualifier(self):
        draft = original(LONG)
        other = original(SHORT, "Договор.docx", uid="B")
        legacy = {
            "code": CODE, "kind": "violation", "status": "violation",
            "title": "Legacy payment finding",
            "law": "ПП РФ № 1352, Положение, пункт 28",
            "document": draft["name"], "source_url": draft["meta"]["source_url"],
            "evidence": LONG, "match_text": LONG, "source_position": 0,
        }
        result, decisions = payment_pipeline([draft, other], injected=legacy)
        self.assert_review_only(result, decisions, "payment_conflicting_terms")
        for _, checks in decisions:
            reviewed_legacy = [item for item in checks if item.get("title") == legacy["title"]]
            self.assertEqual(len(reviewed_legacy), 1)
            self.assertEqual(reviewed_legacy[0]["reported_kind"], "violation")
            self.assertEqual(reviewed_legacy[0]["reported_status"], "violation")
        self.assertNotIn("payment_assessment", legacy)

    def test_requalifying_payment_reviews_is_idempotent(self):
        documents = [original(LONG), original(SHORT, "Договор.docx", uid="B")]
        _, decisions = payment_pipeline(documents)
        findings, checks = decisions[0]
        unchanged = deepcopy((findings, checks))
        first = engine.qualify_outcomes(PURCHASE, documents, findings, checks)
        second = engine.qualify_outcomes(PURCHASE, documents, *first)
        self.assertEqual(first, second)
        self.assertEqual((findings, checks), unchanged)
        self.assertEqual(payment_items(first[0]), [])
        for item in payment_items(first[1]):
            self.assertEqual((item["kind"], item["status"]), ("review", "check"))
            self.assertIn("payment_conflicting_terms", item["methodology_reason_codes"])
            for reason in item["payment_assessment"]["reason_labels"]:
                self.assertEqual(item["note"].count(reason), 1)


if __name__ == "__main__":
    unittest.main()
