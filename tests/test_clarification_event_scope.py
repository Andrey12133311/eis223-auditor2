"""A clarification's receipt and publication must describe the same lot."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import v341_precise_legal as precise
import v348_cross_document_legal as cross
from vp_inspection_engine import qualify_outcomes


P = {"reg": "32610002001", "method": "Открытый конкурс", "published": "2026-10-01",
     "submission_end": "2026-10-30", "_verified_notice_revisions": [{
         "sha256": hashlib.sha256(b"synthetic verified notice").hexdigest(),
         "xml_type": "purchaseNotice", "version": "1", "publication": "2026-10-01",
         "submission_end": "2026-10-30"}]}
RECEIPT = "Дата поступления запроса: 06.10.2026"
PUBLICATION = "Дата публикации разъяснения в ЕИС: 13.10.2026"
SCOPE_REASON = "clarification_event_scope_mismatch"


def original(text, name="Разъяснение на запрос № 1.pdf", uid=1, **metadata):
    url = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + f"{uid:032X}"
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(text.encode()).hexdigest(), **metadata}}


def combined(first="1", second="1", *, reversed_order=False):
    sections = [f"Лот № {first}\n{RECEIPT}", f"Лот № {second}\n{PUBLICATION}"]
    return original("\n".join(reversed(sections) if reversed_order else sections))


def pair(first="1", second="1", *, key="lot_number"):
    return [original(RECEIPT, "Запрос № 1.pdf", **{key: first}),
            original(PUBLICATION, uid=2, **{key: second})]


class ClarificationEventScope(unittest.TestCase):
    def assert_scope_review(self, findings, reviews):
        self.assertEqual(findings, [])
        self.assertTrue(any(SCOPE_REASON in item.get("methodology_reason_codes", []) for item in reviews), reviews)
        review = next(item for item in reviews if SCOPE_REASON in item.get("methodology_reason_codes", []))
        self.assertIn(RECEIPT, review["evidence"])
        self.assertIn(PUBLICATION, review["evidence"])

    def test_single_original_cannot_borrow_publication_from_another_lot(self):
        p = {**deepcopy(P), "lot_number": "1"}
        for reversed_order in (False, True):
            with self.subTest(reversed_order=reversed_order):
                findings, reviews = precise.evaluate(p, [combined("1", "2", reversed_order=reversed_order)])
                self.assert_scope_review(findings, reviews)

    def test_cross_original_cannot_pair_same_request_number_in_different_lots(self):
        for key in ("lot_number", "lot_id"):
            with self.subTest(key=key):
                p, docs = deepcopy(P), pair("1", "2", key=key)
                ns = {"analyze": lambda p, docs: [], "build_checks": lambda *args: []}
                cross.install(ns)
                findings = ns["analyze"](p, docs)
                reviews = ns["build_checks"](p, {}, {}, docs)
                self.assert_scope_review(findings, reviews)
                for doc in docs:
                    self.assertTrue(any(doc["url"] in item["evidence"] for item in reviews))

    def test_saved_single_original_candidate_rechecks_response_scope(self):
        p, doc = {**deepcopy(P), "lot_number": "1"}, combined("1", "2")
        request = precise.matches(precise.REQUEST, doc["text"])[0]
        legacy = precise.finding(doc, "223-3.2-3-CLARIFICATION-RESPONSE-LATE", "Проверяемый срок",
                                 "223-ФЗ, ст. 3.2 ч. 3", doc["text"], request)
        legacy.update(source_excerpt=request[0], source_position=request.start(), decision_kind="actual_event",
                      clarification_deadline_assessment={"state": "confirmed", "deadline": "2026-10-30"})
        self.assert_scope_review(*qualify_outcomes(p, [doc], [legacy]))

    def test_saved_cross_original_candidate_rechecks_both_source_scopes(self):
        p, docs = deepcopy(P), pair()
        legacy = cross.cross_doc_clarifications(p, docs)[0]
        docs[1]["meta"]["lot_number"] = "2"
        before = deepcopy((p, docs, legacy))
        self.assert_scope_review(*qualify_outcomes(p, docs, [legacy]))
        self.assertEqual((p, docs, legacy), before)

    def test_saved_candidate_requires_a_current_unambiguous_response(self):
        p = deepcopy(P)
        legacy = precise.evaluate(p, [original("Лот № 1\n" + RECEIPT + "\n" + PUBLICATION)])[0][0]
        for changed in (
            "Лот № 1\n" + RECEIPT + "\n" + ("Пояснение. " * 160) + "\nЛот № 2\n" + PUBLICATION,
            "Лот № 1\n" + RECEIPT,
            "Лот № 1\n" + RECEIPT + "\n" + PUBLICATION + "\n" + PUBLICATION,
            "Лот № 1\n" + RECEIPT + "\nОтвет размещён 13.10.2026.",
        ):
            with self.subTest(text=changed[-100:]):
                findings, reviews = qualify_outcomes(p, [original(changed)], [legacy])
                self.assertEqual(findings, [])
                self.assertTrue(any("clarification_response_source_unverified" in
                                    item.get("methodology_reason_codes", []) for item in reviews), reviews)

    def test_matching_lot_and_ordinary_originals_remain_confirmed(self):
        for doc in (combined(), combined(reversed_order=True), original(RECEIPT + "\n" + PUBLICATION)):
            with self.subTest(text=doc["text"]):
                p = deepcopy(P)
                findings, reviews = precise.evaluate(p, [doc])
                confirmed, checks = qualify_outcomes(p, [doc], findings, reviews)
                self.assertEqual(len(confirmed), 1)
                self.assertEqual(checks, [])
        for docs in (pair(), [original(RECEIPT, "Запрос № 1.pdf"), original(PUBLICATION, uid=2)]):
            with self.subTest(docs=docs):
                p = deepcopy(P)
                confirmed, reviews = qualify_outcomes(p, docs, cross.cross_doc_clarifications(p, docs))
                self.assertEqual(len(confirmed), 1)
                self.assertEqual(reviews, [])

    def test_saved_candidate_rechecks_current_response_date_and_lateness(self):
        p=deepcopy(P)
        old=original("Лот № 1\n"+RECEIPT+"\n"+PUBLICATION)
        legacy=precise.evaluate(p,[old])[0][0]
        for replacement in ("09.10.2026","05.10.2026","14.10.2026"):
            with self.subTest(publication=replacement):
                current=original(old["text"].replace("13.10.2026",replacement))
                findings,reviews=qualify_outcomes(p,[current],[legacy])
                self.assertEqual(findings,[])
                self.assertTrue(any(set(item.get("methodology_reason_codes",[])) &
                    {"clarification_response_not_late","clarification_response_source_changed"}
                    for item in reviews),reviews)

    def test_saved_cross_document_candidate_rechecks_current_response_date(self):
        p,docs=deepcopy(P),pair()
        legacy=cross.cross_doc_clarifications(p,docs)[0]
        for replacement in ("09.10.2026","14.10.2026"):
            with self.subTest(publication=replacement):
                changed=[docs[0],original(PUBLICATION.replace("13.10.2026",replacement),uid=2,lot_number="1")]
                findings,reviews=qualify_outcomes(p,changed,[legacy])
                self.assertEqual(findings,[])
                self.assertTrue(reviews)


class SavedClarificationXmlResponse(unittest.TestCase):
    # Real immutable XML/ZIP and original receipts, with all files temporary.
    from test_clarification_xml_evidence import ClarificationXmlEvidence as fixtures
    setUp = fixtures.setUp
    connect = fixtures.connect
    make_xml = fixtures.make_xml
    write_archive = fixtures.write_archive
    write_original = fixtures.write_original
    collect = fixtures.collect
    evaluate = fixtures.evaluate
    explanation = fixtures.explanation

    def test_missing_binary_publication_requires_fresh_verified_xml(self):
        archive, _ = self.write_archive(self.explanation())
        result = self.collect()
        p, docs = result["p"], result["docs"]
        legacy = precise.evaluate(p, docs)[0]
        self.assertEqual(len(legacy), 1)
        findings, reviews = qualify_outcomes(p, docs, legacy)
        self.assertEqual(len(findings), 1)
        self.assertEqual(reviews, [])
        # An old finding and its quoted XML hashes cannot repair revoked proof.
        archive.write_bytes(archive.read_bytes() + b"tampered")
        findings, reviews = qualify_outcomes(p, docs, legacy)
        self.assertEqual(findings, [])
        self.assertTrue(any("clarification_response_source_unverified" in
                            item.get("methodology_reason_codes", []) for item in reviews), reviews)


if __name__ == "__main__":
    unittest.main()
