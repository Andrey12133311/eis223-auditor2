"""Synthetic source/lot/revision regressions; never contact EIS or production."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from legal_document_context import local_context
from v336_legal_matrix import security_findings, verified_price, assess_security_context
import v336_legal_matrix as matrix
from v383_msp_scope import resolve
from vp_inspection_engine import qualify_outcomes


PURCHASE = {"reg": "32619990001", "method": "Аукцион в электронной форме",
            "published": "2026-10-01", "price_num": 8000000}
SME = "Участниками настоящей закупки могут быть только субъекты малого и среднего предпринимательства."
SME_NO = "Участниками закупки могут быть только субъекты малого и среднего предпринимательства: Нет"
BID = "Обеспечение заявки: 100 000 рублей."
EIS = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def original(text, name="Документация.docx", uid=1, **metadata):
    return {"name": name, "text": text, "status": "checked", "type": "documentation",
            "meta": {"source": "official_eis_original", "sha256": hashlib.sha256(text.encode()).hexdigest(),
                     "source_url": EIS + f"{uid:032X}", **metadata}}


def inspect(p, docs):
    findings, checks = security_findings(p, docs)
    return qualify_outcomes(p, docs, findings, checks)


class SecurityContextEvidence(unittest.TestCase):
    def test_repeated_below_threshold_clauses_verify_price_once_without_peer_assessment(self):
        body = "НМЦД: 8 000 000 рублей.\n" + "\n".join(
            "Обеспечение заявки: 1% от НМЦД." for _ in range(10))
        docs = [original(body, f"Документация {index+1}.docx", index+1) for index in range(20)]
        before = deepcopy((PURCHASE, docs))
        with patch.object(matrix, "verified_price", wraps=matrix.verified_price) as price_reads, \
             patch.object(matrix, "assess_security_context", wraps=matrix.assess_security_context) as peer_checks:
            findings, checks = security_findings(PURCHASE, docs)
        self.assertEqual((findings, checks), ([], []))
        self.assertEqual(price_reads.call_count, 1)
        self.assertEqual(peer_checks.call_count, 0)
        self.assertEqual((PURCHASE, docs), before)

    def test_potential_breaches_keep_conflict_reviews_but_share_price_verification(self):
        first = original("НМЦД: 8 000 000 рублей.\n" +
                         "\n".join("Обеспечение заявки: 8% от НМЦД." for _ in range(10)))
        peer = original("Обеспечение заявки: 1% от НМЦД.", "Уточнение.docx", 2)
        with patch.object(matrix, "verified_price", wraps=matrix.verified_price) as price_reads:
            findings, reviews = security_findings(PURCHASE, [first, peer])
        self.assertEqual(findings, [])
        self.assertTrue(reviews)
        self.assertIn("security_conflicting_terms", reviews[0]["security_assessment"]["reasons"])
        self.assertEqual(price_reads.call_count, 1)

    def test_legacy_qualifier_owns_its_cache_and_rechecks_missing_price(self):
        body = "Обеспечение заявки: 8% от НМЦД."
        docs = [original(body, "Документация.docx"), original(body, "Извещение.docx", 2)]
        findings = [{"code": "223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT", "kind": "violation",
                     "title": "Превышен размер обеспечения", "law": "223-ФЗ, ст. 3.2 ч. 27",
                     "evidence": body, "document": doc["name"], "source_url": doc["meta"]["source_url"],
                     "security_assessment": {"status": "confirmed", "price_basis": {"value": "8000000"}},
                     "security_scope_cache": {"price_basis": "8000000"}} for doc in docs]
        p = {**PURCHASE, "security_scope_cache": {"price_basis": "8000000"}}
        with patch.object(matrix, "verified_price", wraps=matrix.verified_price) as price_reads:
            confirmed, reviews = qualify_outcomes(p, docs, findings)
        self.assertEqual(confirmed, [])
        self.assertEqual(len(reviews), 2)
        self.assertTrue(all("security_nmcd_unverified" in item["methodology_reason_codes"] for item in reviews))
        self.assertEqual(price_reads.call_count, 1)

    def test_inactive_or_sample_document_cannot_supply_sme_premise(self):
        current = original("НМЦД: 8 000 000 рублей.\nОбеспечение заявки: 3% от НМЦД.", is_current=True)
        for stale in (
            original(SME, "Прежняя документация.docx", 2, superseded=True),
            original(SME, "Отменённая документация.docx", 3, cancelled=True),
            original(SME, "Образец документации.docx", 4),
            original("Пример заполнения:\n" + SME, "Приложение.docx", 5),
        ):
            with self.subTest(name=stale["name"]):
                docs = [current, stale]
                before = deepcopy(docs)
                self.assertIsNot(resolve(PURCHASE, docs)["value"], True)
                findings, reviews = inspect(PURCHASE, docs)
                self.assertFalse(findings)
                self.assertTrue(reviews)
                self.assertEqual(docs, before)

    def test_obsolete_negative_does_not_conflict_with_current_card_fact(self):
        p = {**PURCHASE, "fact_evidence": {"sme_only": {"value": True, "source": "official_eis_card"}}}
        stale = original(SME_NO, superseded=True)
        self.assertIs(resolve(p, [stale])["value"], True)

    def test_unselected_or_conflicting_security_variant_is_review(self):
        p = {**PURCHASE, "price_num": 4000000}
        price = original("НМЦД: 4 000 000 рублей.", "Извещение.docx")
        for selection in ("Применяется редакция 2.\n", ""):
            body = selection + "(редакция 1) " + BID + "\n(редакция 2) Обеспечение заявки не требуется."
            requirement = original(body, uid=2)
            with self.subTest(selection=selection):
                findings, reviews = inspect(p, [price, requirement])
                self.assertFalse(findings)
                self.assertTrue(reviews)
                self.assertTrue(any(item.get("security_assessment", {}).get("reasons") for item in reviews))
                self.assertTrue(any(item.get("source_url") == requirement["meta"]["source_url"] for item in reviews))

    def test_price_from_another_metadata_lot_cannot_prove_threshold(self):
        p = {key: value for key, value in PURCHASE.items() if key != "price_num"}
        p["lot_number"] = "2"
        docs = [original("НМЦД: 4 000 000 рублей.", "Извещение.docx", lot_number="1"),
                original(BID, uid=2, lot_number="2")]
        before = deepcopy(docs)
        self.assertIsNone(verified_price(p, docs))
        findings, reviews = inspect(p, docs)
        self.assertFalse(findings)
        self.assertTrue(reviews)
        self.assertEqual(docs, before)

    def test_current_sme_declaration_for_another_lot_is_not_shared(self):
        p = {**PURCHASE, "lot_number": "2"}
        docs = [original("НМЦД: 8 000 000 рублей.\nОбеспечение заявки: 3% от НМЦД.", lot_number="2"),
                original(SME, "Участники.docx", 2, lot_number="1")]
        self.assertIsNot(resolve(p, docs)["value"], True)
        self.assertFalse(inspect(p, docs)[0])

    def test_later_example_does_not_relabel_actual_security_requirement(self):
        p = {**PURCHASE, "price_num": 4000000}
        price = original("НМЦД: 4 000 000 рублей.", "Извещение.docx")
        for suffix in (
            "\nПример заполнения формы заявки: укажите наименование участника.",
            "\nНапример, наименование участника указывается полностью.",
        ):
            with self.subTest(suffix=suffix):
                body = BID + suffix
                self.assertTrue(local_context(body, 0, len(BID))["eligible_actual"])
                self.assertEqual(len(inspect(p, [price, original(body, uid=2)])[0]), 1)
        quoted = "Пример формулировки: " + BID
        pos = quoted.index(BID)
        self.assertFalse(local_context(quoted, pos, pos+len(BID))["eligible_actual"])

    def test_matching_lot_and_selected_actual_variant_remain_confirmed(self):
        p = {**PURCHASE, "price_num": 4000000, "lot_number": "2"}
        price = original("НМЦД: 4 000 000 рублей.", "Извещение.docx", lot_number="2")
        for prefix in ("", "Применяется редакция 1.\n(редакция 1) "):
            body = prefix + BID
            if prefix:
                body += "\n(редакция 2) Обеспечение заявки не требуется."
            with self.subTest(prefix=prefix):
                docs = [price, original(body, uid=2, lot_number="2")]
                self.assertIsNotNone(verified_price(p, docs))
                self.assertEqual(len(inspect(p, docs)[0]), 1)

    def test_conflicting_current_originals_are_review_without_arbitrary_version_choice(self):
        first = original("НМЦД: 8 000 000 рублей.\nОбеспечение заявки: 6% от НМЦД.",
                         "Документация_2026-10-02.docx", lot_number="2")
        p = {**PURCHASE, "lot_number": "2"}
        for metadata, expected in (({"lot_number": "2"}, 0),
                                   ({"lot_number": "1"}, 1),
                                   ({"lot_number": "2", "superseded": True}, 1)):
            peer = original("Обеспечение заявки не требуется.", "Документация_2026-10-01.docx", 2, **metadata)
            with self.subTest(metadata=metadata):
                docs = [first, peer]
                before = deepcopy(docs)
                found, reviews = inspect(p, docs)
                self.assertEqual(len(found), expected)
                if not expected:
                    assessment = next(item["security_assessment"] for item in reviews if item.get("security_assessment"))
                    self.assertIn("security_conflicting_terms", assessment["reasons"])
                    self.assertEqual(assessment["related_sources"][0]["sha256"], peer["meta"]["sha256"])
                    json.dumps(assessment, ensure_ascii=False)
                self.assertEqual(docs, before)

    def test_contract_security_selection_cannot_select_bid_security_variant(self):
        p = {**PURCHASE, "price_num": 4000000}
        price = original("НМЦД: 4 000 000 рублей.", "Извещение.docx")
        text = ("4. Обеспечение заявки\nПрименяется редакция 1.\n(редакция 1) " + BID +
                "\n(редакция 2) Обеспечение заявки не требуется.\n"
                "5. Обеспечение исполнения договора\nПрименяется редакция 2.\n"
                "(редакция 1) Обеспечение исполнения договора: 3%.\n"
                "(редакция 2) Обеспечение исполнения договора не требуется.")
        self.assertEqual(len(inspect(p, [price, original(text, uid=2)])[0]), 1)

    def test_repeated_historical_and_current_clause_keeps_actual_offset(self):
        p = {**PURCHASE, "price_num": 4000000}
        text = ("В предыдущей редакции документации было установлено:\n" + BID +
                "\n\nДействующая редакция документации:\n\n" + BID)
        docs = [original("НМЦД: 4 000 000 рублей.", "Извещение.docx"), original(text, uid=2)]
        found, reviews = inspect(p, docs)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source_position"], text.rindex(BID))
        self.assertTrue(any("historical_reference" in item.get("security_assessment", {}).get("reasons", [])
                            for item in reviews))

    def test_same_bytes_with_different_revision_metadata_do_not_swap_source_context(self):
        text = "НМЦД: 8 000 000 рублей.\nОбеспечение заявки: 6% от НМЦД."
        current = original(text, "Текущая документация.docx", is_current=True)
        old = original(text, "Прежняя документация.docx", superseded=True)
        found, reviews = inspect(PURCHASE, [old, current])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["document"], current["name"])
        self.assertTrue(any(item.get("security_assessment", {}).get("status") == "review" for item in reviews))

    def test_legacy_assessment_requires_price_and_preserves_source_identity(self):
        body = "Обеспечение заявки: 8% от НМЦД."
        doc = original(body)
        result = assess_security_context(PURCHASE, [doc], doc, 0, len(body),
                                         rule_code="223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT")
        self.assertEqual(result["status"], "review")
        self.assertIn("security_nmcd_unverified", result["reasons"])
        self.assertEqual(result["source"]["sha256"], doc["meta"]["sha256"])
        self.assertEqual(result["source"]["excerpt"], body)
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
