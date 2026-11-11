"""Immutable source receipts survive deduplication and current SOAP adaptation."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import unittest

import eis223_soap_xml as xml
import official_publication_evidence as publication
import v298_eis_only as offline
import v336_legal_matrix as security
import v341_precise_legal as legal
import v383_msp_scope as sme
import vp_inspection_engine as methodology
import legal_audit_revision as revision
from v352_dashboard_truth import load_counts
import test_official_publication_evidence as fixtures
import test_clarification_xml_evidence as clarification_fixtures
from test_official_publication_evidence import REG, UID, URL

ALIAS = "A" * 32
SECOND_ALIAS = "B" * 32


class VerifiedSourceRecallTests(unittest.TestCase):
    def test_acquired_purchase_merge_is_pure_and_preserves_field_precedence(self):
        merge = getattr(fixtures.general, "merge_acquired_purchase", None)
        self.assertTrue(callable(merge), "Verified source propagation needs one shared pure adapter")
        purchase = {"method": "old", "title": "saved title", "price_num": 1,
                    "customer": "ООО Сохранённый", "card_fields_json": {"soap_metadata": "old"},
                    "fact_evidence": {"sme_only": {"value": True}}, "deadline": "2026-10-30"}
        source = {"method": "new", "title": "source title", "price_num": 2, "customer": "", "deadline": None,
                  "card_fields_json": {"soap_metadata": "current"}, "fact_evidence": {}, "source_only": True}
        before = deepcopy((purchase, source))
        result = merge(purchase, source)
        self.assertEqual(result, {"method": "new", "title": "saved title", "price_num": 2,
            "customer": "ООО Сохранённый", "deadline": "2026-10-30",
            "card_fields_json": {"soap_metadata": "current"}, "fact_evidence": {}, "source_only": True})
        self.assertEqual((purchase, source), before)
        self.assertIsNot(result, purchase)

    def test_acquired_merge_precedence_is_versioned_without_queue_timers(self):
        roots = revision.DEPENDENCY_REGISTRY["v332_all_procurements_audit"]
        self.assertIn("merge_acquired_purchase", roots)
        source = Path(fixtures.general.__file__).read_text()
        helper_roots = ("merge_acquired_purchase",)
        baseline = revision.canonical_engine_ast(source, helper_roots, filename="v332_all_procurements_audit")
        changed = source.replace('"card_fields_json","fact_evidence"', '"fact_evidence"', 1)
        self.assertNotEqual(baseline, revision.canonical_engine_ast(changed, helper_roots,
                            filename="v332_all_procurements_audit"))
        operational = source.replace("QUEUE_POLL_SECONDS=30.0", "QUEUE_POLL_SECONDS=700.0", 1)
        self.assertEqual(baseline, revision.canonical_engine_ast(operational, helper_roots,
                         filename="v332_all_procurements_audit"))

    def fixture(self, family="protocol"):
        case = (clarification_fixtures.ClarificationXmlEvidence if family == "clarification"
                else fixtures.OfficialPublicationEvidenceTests)()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for path in (case.root / "eis223_official_archive_inbox" / REG).iterdir():
            path.unlink()
        return case

    def alias_original(self, case, uid=ALIAS):
        folder = case.original_path.parent.parent / uid
        folder.mkdir()
        path = folder / case.original_path.name
        path.write_bytes(case.original_path.read_bytes())
        receipt = json.loads(case.original_receipt.read_text())
        receipt["uid"] = uid
        (folder / "receipt.json").write_text(json.dumps(receipt))
        return path

    def aliased_event(self, family="protocol", *, extra=()):
        case = self.fixture(family)
        self.alias_original(case)
        notice = case.make_xml("purchaseNotice", publication="2026-09-30T12:00:00+03:00",
                               filename="Разъяснение.docx" if family == "clarification" else "Протокол.docx")
        event = (case.explanation(uid=ALIAS) if family == "clarification"
                 else case.make_xml(uid=ALIAS))
        case.write_archive(notice, event, *extra)
        result = case.collect()
        self.assertEqual(len(result["docs"]), 1)
        self.assertEqual(result["docs"][0]["meta"]["uid"], UID)
        return case, result

    def verify(self, family, result, doc=None):
        factory = (publication.clarification_publication_verifier if family == "clarification"
                   else publication.protocol_publication_verifier)
        return factory(result["p"])(doc or result["docs"][0])

    def test_same_binary_protocol_alias_supplies_its_own_verified_xml(self):
        _, result = self.aliased_event()
        proof = self.verify("protocol", result)
        self.assertEqual(proof["state"], "confirmed")
        self.assertEqual(proof["attachment_uid"], ALIAS)
        self.assertIn(ALIAS, proof["evidence"])
        self.assertEqual(len(legal.evaluate(result["p"], result["docs"])[0]), 1)

    def test_same_binary_clarification_alias_supplies_its_own_verified_xml(self):
        _, result = self.aliased_event("clarification")
        proof = self.verify("clarification", result)
        self.assertEqual(proof["state"], "confirmed")
        self.assertEqual(proof["attachment_uid"], ALIAS)
        self.assertIn(ALIAS, proof["evidence"])
        self.assertEqual(len(legal.evaluate(result["p"], result["docs"])[0]), 1)

    def test_persisted_full_source_snapshot_alias_is_reverified(self):
        case, result = self.aliased_event()
        doc = result["docs"][0]
        alias = deepcopy(doc)
        alias["local"] = str(case.original_path.parent.parent / ALIAS / case.original_path.name)
        alias["url"] = URL.replace(UID, ALIAS)
        alias["meta"].update(uid=ALIAS, source_url=alias["url"])
        alias["meta"].pop("identical_sources", None)
        doc["meta"]["identical_sources"] = [alias]
        self.assertEqual(self.verify("protocol", result)["state"], "confirmed")

    def test_alias_labels_missing_or_changed_receipt_and_bytes_never_supply_proof(self):
        for family in ("protocol", "clarification"):
            for change in ("missing", "other_purchase", "other_hash", "tampered"):
                with self.subTest(family=family, change=change):
                    case, result = self.aliased_event(family)
                    folder = case.original_path.parent.parent / ALIAS
                    receipt = folder / "receipt.json"
                    if change == "missing":
                        receipt.unlink()
                    elif change == "tampered":
                        path = folder / case.original_path.name
                        path.write_bytes(path.read_bytes() + b"changed")
                    else:
                        value = json.loads(receipt.read_text())
                        value["reg_number" if change == "other_purchase" else "sha256"] = (
                            "32619999999" if change == "other_purchase" else "0" * 64)
                        receipt.write_text(json.dumps(value))
                    self.assertNotEqual(self.verify(family, result)["state"], "confirmed")
                    self.assertEqual(legal.evaluate(result["p"], result["docs"])[0], [])

    def test_canonical_and_verified_alias_xml_conflicts_are_not_selected_away(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                case, result = self.aliased_event(family)
                event = (case.explanation(uid=UID, publication="2026-10-09T10:00:00+03:00")
                         if family == "clarification" else
                         case.make_xml(uid=UID, publication="2026-10-09T10:00:00+03:00"))
                case.write_archive(event)
                self.assertEqual(self.verify(family, result)["state"], "conflict")
                self.assertEqual(legal.evaluate(result["p"], result["docs"])[0], [])

    def test_two_verified_aliases_with_competing_event_identities_remain_conflict(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                case, result = self.aliased_event(family)
                self.alias_original(case, SECOND_ALIAS)
                event = (case.explanation(uid=SECOND_ALIAS, guid="03253434-0000-0000-0000-000000000002")
                         if family == "clarification" else case.make_xml(uid=SECOND_ALIAS, guid="protocol-2"))
                case.write_archive(event)
                result["docs"][0]["meta"]["identical_sources"].append({
                    "uid": SECOND_ALIAS, "url": URL.replace(UID, SECOND_ALIAS), "name": "Протокол.docx"})
                self.assertEqual(self.verify(family, result)["state"], "conflict")

    def test_wrong_event_number_and_template_alias_names_remain_review(self):
        for title in ("Протокол № 2.docx", "Шаблон протокола.docx"):
            with self.subTest(title=title):
                case = self.fixture()
                self.alias_original(case)
                case.write_archive(case.make_xml("purchaseNotice"),
                                   case.make_xml(uid=ALIAS, filename=title))
                result = case.collect()
                self.assertNotEqual(self.verify("protocol", result)["state"], "confirmed")
                self.assertEqual(legal.evaluate(result["p"], result["docs"])[0], [])

    def test_verified_alias_scope_cannot_borrow_a_different_lot(self):
        _, result = self.aliased_event()
        result["p"]["lot_number"] = "1"
        alias = result["docs"][0]["meta"]["identical_sources"][0]
        alias["meta"] = {"lot_number": "2"}
        self.assertNotEqual(self.verify("protocol", result)["state"], "confirmed")

    def test_verified_alias_revision_flags_cannot_establish_an_actual_event(self):
        for family in ("protocol", "clarification"):
            for flag in ({"superseded": True}, {"cancelled": True}, {"is_current_version": False}):
                with self.subTest(family=family, flag=flag):
                    _, result = self.aliased_event(family)
                    result["docs"][0]["meta"]["identical_sources"][0]["meta"] = flag
                    self.assertNotEqual(self.verify(family, result)["state"], "confirmed")
                    self.assertEqual(legal.evaluate(result["p"], result["docs"])[0], [])

    def test_sqlite_row_alias_metadata_keeps_revision_and_lot_vetoes(self):
        for metadata in ({"superseded": True}, {"lot_number": "2"}):
            with self.subTest(metadata=metadata):
                _, result = self.aliased_event()
                result["p"]["lot_number"] = "1"
                doc = result["docs"][0]
                doc["meta"]["identical_sources"] = [{
                    "url": URL.replace(UID, ALIAS), "name": "Протокол.docx",
                    "metadata_json": json.dumps(metadata)}]
                self.assertNotEqual(self.verify("protocol", result)["state"], "confirmed")

    def test_alias_xml_nested_lot_scope_is_not_dropped_when_joining(self):
        case = self.fixture()
        self.alias_original(case)
        name, raw, _ = case.make_xml(uid=ALIAS)
        raw = raw.replace(b"<attachments>", b"<lots><lot><lotNumber>2</lotNumber><attachments>")
        raw = raw.replace(b"</attachments>", b"</attachments></lot></lots>")
        case.write_archive(case.make_xml("purchaseNotice"), (name, raw, xml.parse_document(raw, name)[0]))
        result = case.collect()
        result["p"]["lot_number"] = "1"
        self.assertNotEqual(self.verify("protocol", result)["state"], "confirmed")

    def test_xml_alias_kind_cannot_override_the_original_signature_stage(self):
        case = self.fixture()
        case.write_original("Номер протокола: 1. Дата подписания итогового протокола: 06.10.2026. "
                            "Комиссия рассмотрела заявки участников закупки и подвела итоги.")
        self.alias_original(case)
        case.write_archive(case.make_xml("purchaseNotice"),
                           case.make_xml(uid=ALIAS, filename="Протокол рассмотрения заявок.docx"))
        result = case.collect()
        self.assertNotEqual(self.verify("protocol", result)["state"], "confirmed")
        self.assertEqual(legal.evaluate(result["p"], result["docs"])[0], [])

    def test_repeated_copies_keep_one_event_in_both_audits_and_replay(self):
        case, result = self.aliased_event()
        # Existing integration helper uses real stored-doc authentication,
        # general persistence, thematic selection and the displayed counter.
        self.assertEqual(len(case.ns["analyze"](result["p"], result["docs"] * 2)), 1)
        case.assert_persisted_count(1)

    def sme_fixture(self, kind="purchaseNoticeKESMBO"):
        case = self.fixture()
        case.write_original("НМЦД: 10 000 000 рублей.\nОбеспечение заявки составляет 3 % от НМЦД.")
        name, raw, _ = case.make_xml(kind, filename="Документация.docx")
        raw = raw.replace(b"<attachments>", b"<lots><lot><lotData><initialSum>10000000</initialSum>"
                          b"</lotData></lot></lots><attachments>")
        case.write_archive((name, raw, xml.parse_document(raw, name)[0]))
        return case

    def test_current_verified_sme_type_reaches_scope_before_analysis(self):
        case = self.sme_fixture()
        offline.import_soap_metadata(case.ns, REG)
        result = case.collect()
        self.assertEqual(sme.resolve(result["p"], result["docs"])["state"], "yes")
        self.assertEqual(result["card"]["soap_metadata"]["xml_type"], "purchaseNoticeKESMBO")
        self.assertEqual([item["code"] for item in security.security_findings(result["p"], result["docs"])[0]],
                         ["223-SME-BID-SECURITY-OVER-TWO-PERCENT"])

    def test_incoming_sme_audit_retains_existing_false_scope_conflict(self):
        case = self.sme_fixture()
        offline.import_soap_metadata(case.ns, REG)
        with case.connect() as db:
            card = json.loads(db.execute("SELECT card_fields_json FROM purchases").fetchone()[0])
            card["fact_evidence"] = {"sme_only": {"source": "official_eis_card", "value": False}}
            db.execute("UPDATE purchases SET card_fields_json=?", (json.dumps(card),))
        result = case.collect()
        self.assertEqual(sme.resolve(result["p"], result["docs"])["state"], "conflict")
        self.assertEqual(security.security_findings(result["p"], result["docs"])[0], [])

    def test_current_generic_notice_does_not_reuse_a_saved_sme_edition(self):
        case = self.sme_fixture("purchaseNotice")
        with case.connect() as db:
            db.execute("INSERT INTO purchases(reg_number,card_fields_json) VALUES(?,?)", (REG, json.dumps({
                "soap_metadata": {"source": "official_eis_getDocsIP", "xml_type": "purchaseNoticeKESMBO",
                                  "xml_sha256": "a" * 64},
                "fact_evidence": {"sme_only": {"source": "official_eis_getDocsIP", "value": True}}})))
        result = case.collect()
        self.assertEqual(result["card"].get("soap_metadata", {}).get("xml_type"), "purchaseNotice")
        self.assertEqual(sme.resolve(result["p"], result["docs"])["state"], "unknown")

    def test_unverified_or_foreign_current_marker_does_not_upgrade_scope(self):
        for record in ({"reg_number": REG, "xml_type": "purchaseNoticeKESMBO", "xml_sha256": "a" * 64},
                       {"reg_number": "32619999999", "xml_type": "purchaseNoticeKESMBO", "xml_sha256": "b" * 64}):
            self.assertEqual(sme.resolve({"reg": REG, "_current_soap_context": {"record": record}})["state"], "unknown")

    def test_malformed_saved_card_does_not_break_current_verified_acquisition(self):
        for value in ([], {"fact_evidence": ["bad"]}, {"fact_evidence": True}):
            with self.subTest(value=value):
                case = self.sme_fixture()
                with case.connect() as db:
                    db.execute("INSERT INTO purchases(reg_number,card_fields_json) VALUES(?,?)",
                               (REG, json.dumps(value)))
                try:
                    result = case.collect()
                except (ValueError, TypeError, AttributeError) as exc:
                    self.fail("Malformed legacy card must not hide verified SOAP: " + type(exc).__name__)
                self.assertEqual(sme.resolve(result["p"], result["docs"])["state"], "yes")

    def test_incoming_sme_audit_reports_one_supported_violation(self):
        case = self.sme_fixture()
        offline.import_soap_metadata(case.ns, REG)
        security.install(case.ns)
        audited = asyncio.run(offline.offline_audit(case.ns, REG))
        self.assertEqual(audited["confirmed_violations"], 1)

    def seed_stored_audit(self, case, result, saved_card):
        purchase = result["p"]
        with case.connect() as db:
            db.execute("INSERT OR IGNORE INTO purchases(reg_number) VALUES(?)", (REG,))
            db.execute("UPDATE purchases SET title=?,customer=?,method=?,published_at=?,submission_end=?,"
                       "card_fields_json=? WHERE reg_number=?",
                       (purchase["title"], "ООО Тест", purchase["method"], purchase["published"],
                        purchase["submission_end"], json.dumps(saved_card), REG))
            for doc in result["docs"]:
                db.execute("INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,status,"
                           "metadata_json,final_url) VALUES(?,?,?,?,?,?,?,?,?)",
                           (REG, doc["name"], doc["url"], doc["type"], doc["published"], doc["local"],
                            doc["status"], json.dumps(doc["meta"]), doc["final_url"]))

    def assert_sme_audits(self, case, result, saved_card, expected):
        security.install(case.ns)
        methodology.install(case.ns)
        self.seed_stored_audit(case, result, saved_card)
        original_bytes = {path: path.read_bytes() for path in case.root.rglob("*")
                          if path.suffix in (".zip", ".docx")}
        first = asyncio.run(fixtures.general.inspect_one(case.ns, REG))
        repeated = asyncio.run(fixtures.general.inspect_one(case.ns, REG))
        selected = asyncio.run(fixtures.thematic.independent_review(case.ns, REG, ["sme_bid_security"]))
        self.assertEqual((first[0], first[1]["new_findings"]), ("completed", expected))
        self.assertEqual(repeated[1]["new_findings"], 0)
        self.assertEqual((selected[0], len(selected[1])), ("completed", expected))
        with case.connect() as db:
            self.assertEqual(load_counts(db, [REG]), {REG: expected})
        self.assertEqual({path: path.read_bytes() for path in original_bytes}, original_bytes)

    def test_fresh_sme_scope_reaches_both_audits_and_replay_without_duplicate_credit(self):
        case = self.sme_fixture()
        self.assert_sme_audits(case, case.collect(), {}, 1)

    def test_current_ordinary_notice_vetoes_stale_sme_scope_in_both_audits(self):
        case = self.sme_fixture("purchaseNotice")
        stale = {"soap_metadata": {"source": "official_eis_getDocsIP", "xml_type": "purchaseNoticeKESMBO",
                                    "xml_sha256": "a" * 64},
                 "fact_evidence": {"sme_only": {"source": "official_eis_getDocsIP", "value": True}}}
        self.assert_sme_audits(case, case.collect(), stale, 0)


if __name__ == "__main__":
    unittest.main()
