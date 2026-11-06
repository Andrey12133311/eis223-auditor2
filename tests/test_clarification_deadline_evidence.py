"""Historical request deadlines are source facts, not the latest card value."""
from copy import deepcopy
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from clarification_deadline_evidence import resolve_clarification_deadline


PURCHASE = {"reg": "32610002001", "submission_end": "2026-10-30"}
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32
RECEIPT = "Дата поступления запроса о даче разъяснений документации: 09.10.2026"
OLD = "Дата окончания срока подачи заявок по извещению, действовавшему на дату запроса: 12.10.2026"


def original(text, **meta):
    return {"name": "Разъяснение на запрос № 1.pdf", "status": "checked", "url": URL, "text": text,
            "meta": {"source": "official_eis_original", "source_url": URL,
                     "sha256": hashlib.sha256(text.encode()).hexdigest(), **meta}}


def revision(publication="2026-10-01", deadline="2026-10-12", version=1, **fields):
    return {"sha256": hashlib.sha256(str((publication, deadline, version)).encode()).hexdigest(),
            "xml_type": "purchaseNotice", "version": version,
            "publication": publication, "submission_end": deadline, **fields}


def soap_context(publication="2026-10-01", deadline="2026-10-30", version=1, modified=""):
    entry = revision(publication, deadline, version)
    return {"record": {"reg_number": PURCHASE["reg"], "xml_family": "notice",
                       "xml_type": entry["xml_type"], "xml_sha256": entry["sha256"],
                       "version": version, "publication": publication, "modified": modified},
            "fields": {"published": publication, "submission_end": deadline}}


def resolve(text=RECEIPT, p=PURCHASE, revisions=(), **kwargs):
    return resolve_clarification_deadline(p, original(text), received="2026-10-09",
                                          notice_revisions=revisions, **kwargs)


class ClarificationDeadlineEvidence(unittest.TestCase):
    def test_current_card_alone_never_proves_the_historical_deadline(self):
        result = resolve()
        self.assertEqual(result["state"], "requires_review")
        self.assertIsNone(result["deadline"])
        self.assertEqual(result["current_card_deadline"], "2026-10-30")

    def test_explicit_prior_deadline_is_not_overridden_by_current_card(self):
        text = RECEIPT + "\n" + OLD
        result = resolve(text)
        self.assertEqual(result["state"], "confirmed")
        self.assertEqual(result["deadline"], "2026-10-12")
        self.assertEqual(result["evidence"][0]["quote"], OLD)
        self.assertIn(OLD, result["evidence_text"])
        self.assertEqual(result["evidence"][0]["source_url"], URL)
        self.assertEqual(resolve(text, revisions=[revision("2026-10-13", "2026-10-30", 2)])["deadline"],
                         "2026-10-12")

    def test_closed_as_of_labels_support_wrapped_and_spelled_dates(self):
        for quote in (
            "На дату получения запроса срок окончания подачи заявок: 12.10.2026",
            "Срок подачи заявок, действовавший на момент поступления запроса: 12.10.2026",
            "По действовавшей на дату запроса редакции извещения дата окончания срока подачи заявок: 12 октября 2026",
            "Дата окончания срока подачи заявок\nна дату поступления данного запроса: «12» октября 2026",
        ):
            with self.subTest(quote=quote):
                self.assertEqual(resolve(RECEIPT + "\n" + quote)["deadline"], "2026-10-12")

    def test_generic_deadline_without_as_of_proof_remains_review(self):
        result = resolve(RECEIPT + "\nДата окончания срока подачи заявок: 30.10.2026")
        self.assertEqual(result["state"], "requires_review")

    def test_conflicting_as_of_statements_keep_both_quotes(self):
        result = resolve(RECEIPT + "\n" + OLD + "\n" + OLD.replace("12.10", "13.10"))
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("conflict"))
        self.assertEqual(len(result["evidence"]), 2)

    def test_templates_and_conditional_statements_do_not_prove_a_deadline(self):
        text = RECEIPT + "\n" + OLD
        doc = original(text)
        doc["name"] = "Образец разъяснения.pdf"
        self.assertEqual(resolve_clarification_deadline(PURCHASE, doc, received="2026-10-09")["state"],
                         "requires_review")
        for prefix in ("Планируемая ", "Не подтвержден ", "Например: ", "Если "):
            with self.subTest(prefix=prefix):
                self.assertEqual(resolve(RECEIPT + "\n" + prefix + OLD)["state"], "requires_review")

    def test_request_scopes_do_not_borrow_a_neighbouring_deadline(self):
        text = ("Запрос № 1\n" + RECEIPT + "\n" + OLD + "\nЗапрос № 2\n" +
                RECEIPT.replace("09.10", "20.10") + "\n" + OLD.replace("12.10", "30.10"))
        doc = original(text)
        for received, expected in (("09.10.2026", "2026-10-12"), ("20.10.2026", "2026-10-30")):
            receipt = RECEIPT.replace("09.10.2026", received)
            start = text.index(receipt)
            result = resolve_clarification_deadline(PURCHASE, doc, received=received,
                                                    request_span=(start, start+len(receipt)))
            self.assertEqual(result["deadline"], expected)
        self.assertEqual(resolve_clarification_deadline(PURCHASE, doc, received="2026-10-09")["state"],
                         "requires_review")

    def test_lot_scopes_do_not_borrow_another_lots_explicit_deadline(self):
        result = resolve("Лот № 1\n" + RECEIPT + "\nЛот № 2\n" + OLD)
        self.assertEqual(result["state"], "requires_review")

    def test_verified_earlier_and_later_notice_select_request_time_revision(self):
        result = resolve(revisions=[revision(), revision("2026-10-13", "2026-10-30", 2)])
        self.assertEqual(result["state"], "confirmed")
        self.assertEqual(result["deadline"], "2026-10-12")
        self.assertEqual(result["evidence"][0]["version"], "1")
        late_only = resolve(revisions=[revision("2026-10-13", "2026-10-30", 2)])
        self.assertEqual(late_only["state"], "requires_review")
        self.assertTrue(late_only["reason_code"].endswith("later_revision"))

    def test_unknown_order_of_current_card_change_remains_review(self):
        result = resolve(revisions=[revision()])
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("current_revision_unordered"))
        self.assertEqual(resolve(p=dict(PURCHASE, submission_end="2026-10-12"), revisions=[revision()])["state"],
                         "confirmed")

    def test_same_day_change_cannot_be_ordered_by_version_or_clock(self):
        revisions = [revision(), revision("2026-10-09T08:00:00+03:00", "2026-10-30", 2)]
        result = resolve(revisions=revisions)
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("same_day_revision"))
        self.assertEqual(len(result["evidence"]), 2)

    def test_conflicting_notice_revisions_or_explicit_text_require_review(self):
        result = resolve(revisions=[revision(), revision("2026-10-01", "2026-10-30", 2)])
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("conflict"))
        conflict = resolve(RECEIPT + "\n" + OLD, revisions=[revision("2026-10-01", "2026-10-30")])
        self.assertEqual(conflict["state"], "requires_review")
        self.assertEqual(len(conflict["evidence"]), 2)

    def test_wrong_or_unverified_sources_cannot_supply_a_revision(self):
        for fields in ({"sha256": "bad"}, {"xml_type": "purchaseProtocol"},
                       {"reg_number": "32610002002"}, {"verified": False}):
            with self.subTest(fields=fields):
                self.assertEqual(resolve(revisions=[revision(**fields)])["state"], "requires_review")
        doc = original(RECEIPT + "\n" + OLD, reg_number="32610002002")
        self.assertEqual(resolve_clarification_deadline(PURCHASE, doc, received="2026-10-09")["state"],
                         "requires_review")

    def test_missing_publication_is_not_inferred_from_filename_or_modification(self):
        result = resolve(revisions=[revision(publication="", modified="2026-10-01", filename="notice_20261001.xml")])
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("revision_incomplete"))

    def test_superseded_original_can_prove_its_historical_as_of_deadline(self):
        doc = original(RECEIPT + "\n" + OLD, superseded=True)
        self.assertEqual(resolve_clarification_deadline(PURCHASE, doc, received="2026-10-09")["deadline"],
                         "2026-10-12")

    def test_invalid_positions_do_not_rebind_a_request(self):
        for span in ((False, len(RECEIPT)), ("0", len(RECEIPT)), (-1, len(RECEIPT)),
                     (1, len(RECEIPT)), (0, 999999)):
            with self.subTest(span=span):
                self.assertEqual(resolve(RECEIPT + "\n" + OLD, request_span=span)["state"], "requires_review")

    def test_inputs_and_results_remain_stable_across_repeated_reads(self):
        p, doc, revisions = deepcopy(PURCHASE), original(RECEIPT + "\n" + OLD), [revision()]
        before = deepcopy((p, doc, revisions))
        first = resolve_clarification_deadline(p, doc, received="2026-10-09", notice_revisions=revisions)
        second = resolve_clarification_deadline(p, doc, received="2026-10-09", notice_revisions=revisions)
        self.assertEqual(first, second)
        self.assertEqual((p, doc, revisions), before)

    def test_purchase_snapshot_and_hash_verified_current_context_are_reused(self):
        p = dict(PURCHASE, _verified_notice_revisions=[revision()],
                 _current_soap_context=soap_context("2026-10-13", "2026-10-30", 2))
        before = deepcopy(p)
        result = resolve_clarification_deadline(p, original(RECEIPT), received="2026-10-09")
        self.assertEqual(result["deadline"], "2026-10-12")
        self.assertEqual(p, before)
        p = dict(PURCHASE, _verified_notice_revisions=[], _current_soap_context=soap_context())
        self.assertEqual(resolve_clarification_deadline(p, original(RECEIPT), received="2026-10-09")["deadline"],
                         "2026-10-30")

    def test_unavailable_snapshot_is_not_replaced_with_current_context(self):
        p = dict(PURCHASE, _verified_notice_revisions={"state": "unavailable"},
                 _current_soap_context=soap_context())
        result = resolve_clarification_deadline(p, original(RECEIPT), received="2026-10-09")
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("snapshot_unavailable"))

    def test_soap_identity_and_hash_cannot_be_assumed(self):
        for field, value in (("xml_sha256", "bad"), ("reg_number", "32610002002"),
                             ("xml_family", "protocol")):
            context = soap_context()
            context["record"][field] = value
            p = dict(PURCHASE, _current_soap_context=context)
            with self.subTest(field=field):
                self.assertEqual(resolve_clarification_deadline(p, original(RECEIPT), received="2026-10-09")["state"],
                                 "requires_review")

    def test_modified_is_only_a_veto_never_a_publication_date(self):
        for publication, modified in (("", "2026-10-01"), ("2026-10-01", "2026-10-13")):
            p = dict(PURCHASE, _current_soap_context=soap_context(publication, modified=modified))
            with self.subTest(publication=publication, modified=modified):
                result = resolve_clarification_deadline(p, original(RECEIPT), received="2026-10-09")
                self.assertEqual(result["state"], "requires_review")
        # The changed current XML cannot override a separately proved as-of fact.
        p = dict(PURCHASE, _current_soap_context=soap_context("2026-10-01", modified="2026-10-13"))
        self.assertEqual(resolve_clarification_deadline(p, original(RECEIPT + "\n" + OLD),
                                                       received="2026-10-09")["deadline"], "2026-10-12")

    def test_known_version_gap_across_the_request_date_remains_review(self):
        result = resolve(revisions=[revision(), revision("2026-10-13", "2026-10-30", 3)])
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("revision_gap"))
        self.assertEqual(len(result["evidence"]), 2)
        # A gap already superseded before this request does not hide the latest version.
        result = resolve(revisions=[revision(), revision("2026-10-05", "2026-10-30", 3)])
        self.assertEqual(result["deadline"], "2026-10-30")

    def test_known_version_conflicts_do_not_choose_a_convenient_date(self):
        for revisions in (
            [revision(), revision("2026-10-13", "2026-10-30", 1)],
            [revision("2026-10-05", version=1), revision("2026-10-01", "2026-10-30", 2)],
        ):
            with self.subTest(revisions=revisions):
                result = resolve(revisions=revisions)
                self.assertEqual(result["state"], "requires_review")
                self.assertTrue(result["reason_code"].endswith("conflict"))

    def test_soap_publication_fields_must_agree(self):
        context = soap_context()
        context["fields"]["published"] = "2026-10-02"
        result = resolve_clarification_deadline(dict(PURCHASE, _current_soap_context=context), original(RECEIPT),
                                                received="2026-10-09")
        self.assertEqual(result["state"], "requires_review")
        self.assertTrue(result["reason_code"].endswith("conflict"))


if __name__ == "__main__":
    unittest.main()
