"""Actual deadline revisions must govern both clarification analyzers."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import v298_eis_only as originals
import v341_precise_legal as precise
import v348_cross_document_legal as cross
import vp_inspection_engine as engine
from test_clarification_deadline_evidence import revision


REG = "32610002001"
P = {"reg": REG, "method": "Открытый конкурс", "published": "2026-09-30",
     "submission_end": "2026-10-30", "_verified_notice_revisions": []}
OLD = "Дата окончания срока подачи заявок по извещению, действовавшему на дату запроса: 12.10.2026"


def original(text, *, name="Разъяснение на запрос № 1.pdf", uid=1):
    url = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + f"{uid:032X}"
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


def pair(received="09.10.2026", published="16.10.2026", as_of=""):
    return [original("Дата поступления запроса: " + received + ".\n" + as_of,
                     name="Запрос № 1.pdf"),
            original("Дата публикации разъяснения в ЕИС: " + published,
                     name="Разъяснение на запрос № 1.pdf", uid=2)]


def combined(received="09.10.2026", published="16.10.2026", as_of=""):
    docs = pair(received, published, as_of)
    return original(docs[0]["text"] + "\n" + docs[1]["text"])


def dated(findings):
    return [item for item in findings if "CLARIFICATION" in item.get("code", "")]


class ClarificationDeadlinePipeline(unittest.TestCase):
    def test_current_card_alone_is_review_with_original_request_quote(self):
        doc = combined()
        findings, checks = precise.evaluate(deepcopy(P), [doc])
        self.assertEqual(dated(findings), [])
        review = next(item for item in checks if item.get("field_path") == "v341-clarification-deadline")
        self.assertEqual(review["clarification_deadline_assessment"]["reason_code"], "clarification_deadline_unproven")
        self.assertIn("Дата поступления запроса: 09.10.2026", review["evidence"])
        self.assertIn("текущей карточки недостаточно", review["note"])

    def test_explicit_old_deadline_prevents_false_late_response_violation(self):
        findings, checks = precise.evaluate(deepcopy(P), [combined(as_of=OLD)])
        self.assertEqual(dated(findings), [])
        review = next(item for item in checks if item.get("field_path") == "v341-clarification-applicability")
        self.assertEqual(review["clarification_deadline_assessment"]["deadline"], "2026-10-12")
        self.assertIn(OLD, review["evidence"])
        self.assertIn("clarification_request_after_cutoff", review["methodology_reason_codes"])

    def test_real_late_response_uses_the_prior_deadline_and_keeps_its_quote(self):
        findings, checks = precise.evaluate(deepcopy(P), [combined("06.10.2026", "13.10.2026", OLD)])
        issue = dated(findings)[0]
        self.assertEqual(issue["clarification_deadline_assessment"]["deadline"], "2026-10-12")
        self.assertIn("окончание подачи 2026-10-12", issue["evidence"])
        self.assertIn(OLD, issue["evidence"])
        self.assertEqual(checks, [])

    def test_notice_revisions_choose_deadline_before_request(self):
        p = {**P, "_verified_notice_revisions": [revision(), revision("2026-10-13", "2026-10-30", 2)]}
        findings, checks = precise.evaluate(p, [combined()])
        self.assertEqual(dated(findings), [])
        review = next(item for item in checks if item.get("field_path") == "v341-clarification-applicability")
        self.assertEqual(review["clarification_deadline_assessment"]["deadline"], "2026-10-12")
        self.assertIn("SHA-256", review["evidence"])

    def test_cross_original_missing_snapshot_retains_review_in_real_installer(self):
        docs = pair()
        ns = {"analyze": lambda p, docs: [], "build_checks": lambda *args: []}
        cross.install(ns)
        p = deepcopy(P)
        self.assertEqual(ns["analyze"](p, docs), [])
        reviews = ns["build_checks"](p, {}, {}, docs)
        review = next(item for item in reviews if item["field_path"] == "v341-clarification-cross-doc-deadline")
        self.assertEqual(review["status"], "check")
        self.assertEqual(review["clarification_deadline_assessment"]["state"], "requires_review")
        for doc in docs:
            self.assertIn(doc["meta"]["source_url"], review["evidence"])
        self.assertIn("Дата поступления запроса: 09.10.2026", review["evidence"])

    def test_cross_original_old_deadline_prevents_false_positive(self):
        p, docs = deepcopy(P), pair(as_of=OLD)
        self.assertEqual(cross.cross_doc_clarifications(p, docs), [])
        ns = {"analyze": lambda p, docs: [], "build_checks": lambda *args: []}
        cross.install(ns)
        reviews = ns["build_checks"](p, {}, {}, docs)
        self.assertTrue(any("clarification_request_after_cutoff" in item.get("methodology_reason_codes", [])
                            and OLD in item["evidence"] for item in reviews))

    def test_verified_snapshot_preserves_real_single_and_cross_original_breaches(self):
        p = {**P, "_verified_notice_revisions": [revision("2026-09-30", "2026-10-30")]}
        self.assertEqual(len(dated(precise.evaluate(p, [combined()])[0])), 1)
        findings = cross.cross_doc_clarifications(p, pair())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["clarification_deadline_assessment"]["deadline"], "2026-10-30")
        self.assertIn("SHA-256", findings[0]["evidence"])

    def test_snapshot_is_loaded_once_and_reused_by_both_analyzers(self):
        p = {key: value for key, value in P.items() if key != "_verified_notice_revisions"}
        revisions = [revision("2026-09-30", "2026-10-30")]
        with patch.object(originals, "official_notice_revisions", return_value=revisions) as load:
            self.assertEqual(len(dated(precise.evaluate(p, [combined()])[0])), 1)
            self.assertEqual(len(cross.cross_doc_clarifications(p, pair())), 1)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(p["_verified_notice_revisions"], revisions)

    def test_explicit_empty_snapshot_and_no_requests_never_trigger_history_reads(self):
        with patch.object(originals, "official_notice_revisions", side_effect=AssertionError("unexpected history read")):
            self.assertEqual(dated(precise.evaluate(deepcopy(P), [combined()])[0]), [])
            p = {key: value for key, value in P.items() if key != "_verified_notice_revisions"}
            precise.evaluate(p, [original("Документ не содержит запроса разъяснений.")])
        self.assertNotIn("_verified_notice_revisions", p)

    def test_snapshot_failure_preserves_review_instead_of_failed_audit(self):
        p = {key: value for key, value in P.items() if key != "_verified_notice_revisions"}
        with patch.object(originals, "official_notice_revisions", side_effect=OSError("temporary source failure")):
            findings, reviews = precise.evaluate(p, [combined()])
        self.assertEqual(dated(findings), [])
        self.assertTrue(any(item.get("clarification_deadline_assessment", {}).get("reason_code") ==
                            "clarification_deadline_snapshot_unavailable" for item in reviews))

    def test_common_qualifier_cannot_trust_a_forged_current_deadline_assessment(self):
        doc = combined(as_of=OLD)
        request = precise.matches(precise.REQUEST, doc["text"])[0]
        legacy = precise.finding(doc, "223-3.2-3-CLARIFICATION-RESPONSE-LATE", "Проверяемый срок",
                                 "223-ФЗ, ст. 3.2 ч. 3", doc["text"], request)
        legacy["clarification_deadline_assessment"] = {"state": "confirmed", "deadline": "2026-10-30"}
        with patch.object(originals, "official_notice_revisions", side_effect=AssertionError("qualifier performed I/O")):
            findings, checks = engine.qualify_outcomes(deepcopy(P), [doc], [legacy])
        self.assertEqual(findings, [])
        self.assertIn("clarification_request_after_cutoff", checks[0]["methodology_reason_codes"])
        self.assertEqual(checks[0]["clarification_deadline_assessment"]["deadline"], "2026-10-12")

    def test_common_qualifier_without_historical_evidence_requires_review_without_io(self):
        doc = combined()
        request = precise.matches(precise.REQUEST, doc["text"])[0]
        legacy = precise.finding(doc, "223-3.2-3-CLARIFICATION-RESPONSE-LATE", "Проверяемый срок",
                                 "223-ФЗ, ст. 3.2 ч. 3", doc["text"], request)
        p = {key: value for key, value in P.items() if key != "_verified_notice_revisions"}
        with patch.object(originals, "official_notice_revisions", side_effect=AssertionError("qualifier performed I/O")):
            findings, checks = engine.qualify_outcomes(p, [doc], [legacy])
        self.assertEqual(findings, [])
        self.assertIn("clarification_deadline_unproven", checks[0]["methodology_reason_codes"])

    def test_cross_original_request_reference_must_match_actual_hash_and_excerpt(self):
        p = {**P, "_verified_notice_revisions": [revision("2026-09-30", "2026-10-30")]}
        docs = pair()
        item = cross.cross_doc_clarifications(p, docs)[0]
        before = deepcopy((p, docs, item))
        with patch.object(originals, "official_notice_revisions", side_effect=AssertionError("qualifier performed I/O")):
            findings, checks = engine.qualify_outcomes(p, docs, [item])
            self.assertEqual(len(findings), 1)
            self.assertEqual(checks, [])
            self.assertEqual(engine.qualify_outcomes(p, docs, findings, checks), (findings, checks))
            for change in ({"sha256": "f" * 64}, {"source_excerpt": "Несуществующая дата поступления запроса"}):
                wrong = {**item, "clarification_request_source": {**item["clarification_request_source"], **change}}
                rejected, reviews = engine.qualify_outcomes(p, docs, [wrong])
                self.assertEqual(rejected, [])
                self.assertIn("clarification_request_source_unverified", reviews[0]["methodology_reason_codes"])
        self.assertEqual((p, docs, item), before)


if __name__ == "__main__":
    unittest.main()
