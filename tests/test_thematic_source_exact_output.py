"""The thematic boundary must retain the exact current proof used by the journal."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import payment_finding_reassessment as assessment
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v372_auto_regulation_protocols as protocols
import vp_inspection_engine as methodology
from v372_automatic_legal_selftest import PROTOCOL_HEAD, official
import test_general_thematic_parity as pipeline


REG = pipeline.REG
CODE = "v372-protocol-rejected-bid-no-reasons"
LAW = "223-ФЗ, ст. 3.2 ч. 13 п. 3 пп. «б»"
LONG_NAME = ("Пакет сохранённых оригиналов — " + "документы_закупки_" * 24 +
             " / Протокол рассмотрения заявок.pdf")


def source(tail="А"):
    body = (PROTOCOL_HEAD + "По заявке № 12 комиссия приняла решение: отклонена заявка № 12, "
            "основания отклонения не указаны. Итоги рассмотрения зафиксированы.\n" +
            "Реквизиты заседания закупочной комиссии и рассмотренного документа. " * 70 +
            "\nЗавершающий реквизит исходного протокола: " + tail)
    result = official(LONG_NAME, body)
    result["meta"]["sha256"] = hashlib.sha256(body.encode()).hexdigest()
    return result


def install_with_full_source_quotes(ns):
    """Use the real rejection rule with an unambiguous full source citation."""
    protocols.install(ns)
    previous = ns["analyze"]

    def analyze(purchase, originals):
        results = previous(purchase, originals)
        for item in results:
            if item.get("code") != CODE:
                continue
            # Exercise the thematic boundary with a full source quotation.
            # The real detector's own citation is tested separately below.
            matches = [doc for doc in originals
                       if doc["meta"]["source_url"] == item["source_url"]]
            assert len(matches) == 1
            original = matches[0]
            item["document"] = original["name"]
            item["evidence"] = original["text"]
        return results

    ns["analyze"] = analyze
    methodology.install(ns)


class ThematicSourceExactOutput(unittest.IsolatedAsyncioTestCase):
    async def review(self, original, *, wrong_quote=False, full_source_citation=True):
        purchase = {**pipeline.P}
        collected = {"docs": [original], "unreadable": 0, "source_incomplete": False,
                     "p": {}, "card": {}, "detail": {}, "original_received": True,
                     "original_expected": 1, "source_error": None}
        ns = {"analyze": lambda *args: [], "build_checks": lambda *args: []}
        if full_source_citation:
            install_with_full_source_quotes(ns)
        else:
            protocols.install(ns)
            methodology.install(ns)
        if wrong_quote:
            previous = ns["analyze"]

            def unrelated_quote(purchase, originals):
                values = previous(purchase, originals)
                for item in values:
                    item["evidence"] = "Отсутствующее в исходнике утверждение о действиях комиссии."
                    item["match_text"] = "Отсутствующее в исходнике утверждение о действиях комиссии."
                    item.pop("source_excerpt", None)
                    item.pop("quote", None)
                return values

            ns["analyze"] = unrelated_quote
        with patch.object(general, "existing", return_value=(purchase, [])), \
             patch.object(general, "collect_saved_evidence", return_value=collected):
            return await thematic.independent_review(ns, REG, ["protocol_compliance"])

    async def test_long_source_name_and_complete_quote_keep_the_fresh_proof_key(self):
        original = source()
        self.assertGreater(len(original["name"]), 255)
        self.assertGreater(len(original["text"]), 3000)
        before = deepcopy(original)
        state, findings = await self.review(original)
        self.assertEqual(state, "completed")
        self.assertEqual(len(findings), 1)
        actual = findings[0]
        expected = {"code": CODE, "law": LAW, "document": original["name"],
                    "source_url": original["meta"]["source_url"], "evidence": original["text"]}
        self.assertEqual(actual["document"], expected["document"])
        self.assertEqual(actual["evidence"], expected["evidence"])
        self.assertEqual(assessment.evidence_key(actual), assessment.evidence_key(expected))
        self.assertEqual(original, before)

    async def test_changed_source_tail_cannot_become_the_same_confirmed_proof(self):
        originals = [source("А"), source("Б")]
        self.assertEqual(originals[0]["text"][:3000], originals[1]["text"][:3000])
        outputs = [await self.review(original) for original in originals]
        self.assertTrue(all(state == "completed" and len(found) == 1 for state, found in outputs))
        first, second = [found[0] for _, found in outputs]
        self.assertNotEqual(assessment.evidence_key(first), assessment.evidence_key(second))
        self.assertEqual([item["evidence"] for item in (first, second)],
                         [original["text"] for original in originals])

    async def test_unverified_source_does_not_gain_a_result_from_its_long_name(self):
        original = source()
        original["meta"]["source"] = "existing_local_unverified"
        state, findings = await self.review(original)
        self.assertNotEqual(state, "completed")
        self.assertEqual(findings, [])

    async def test_unrelated_quote_is_not_confirmed_by_source_name_and_url(self):
        self.assertEqual(await self.review(source(), wrong_quote=True), ("completed", []))

    async def test_real_protocol_detector_keeps_the_full_source_without_a_citation_adapter(self):
        original = source()
        state, findings = await self.review(original, full_source_citation=False)
        self.assertEqual(state, "completed")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], CODE)
        self.assertEqual(findings[0]["document"], original["name"])
        self.assertTrue(findings[0]["legal_basis_verified"])

    async def test_supported_official_source_url_is_not_replaced_by_a_display_prefix(self):
        original = source()
        original["meta"]["source_url"] += "&receipt_context=" + "x" * 2200
        state, findings = await self.review(original, full_source_citation=False)
        self.assertEqual("completed", state)
        self.assertEqual(1, len(findings))
        actual = findings[0]
        expected = {**actual, "source_url": original["meta"]["source_url"]}
        self.assertEqual(expected["source_url"], actual["source_url"])
        self.assertEqual(assessment.evidence_key(expected), assessment.evidence_key(actual))


class GeneralThematicSourceParity(unittest.TestCase):
    def test_real_protocol_review_keeps_the_complete_source_name(self):
        original = source()
        original["text"] = (PROTOCOL_HEAD + "Отклонена заявка № 24 в связи с "
                            "несоответствием требованиям, заявка не допущена.")
        original["meta"]["sha256"] = hashlib.sha256(original["text"].encode()).hexdigest()
        findings, reviews = protocols.evaluate({**pipeline.P, "reg": REG}, [original])
        self.assertEqual(findings, [])
        review = next(item for item in reviews
                      if item["field_path"] == "v372-protocol-rejected-bid-document-clause")
        self.assertEqual(review["source"], original["name"])
        self.assertEqual(review["document"], original["name"])

    def test_real_general_storage_and_thematic_output_retain_the_same_source_quote(self):
        original = source()
        install = protocols.install

        def wrapped(ns):
            with patch.object(protocols, "install", side_effect=install):
                install_with_full_source_quotes(ns)

        with patch.object(pipeline.protocols, "install", side_effect=wrapped):
            first, second, themed, saved, counts = pipeline.compare([original])
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["new_findings"], 1)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual(counts["violations"], 1)
        self.assertEqual((themed[0], len(themed[1])), ("completed", 1))
        self.assertEqual(saved[0]["document"], original["name"])
        self.assertEqual(saved[0]["evidence"], original["text"])
        self.assertEqual(assessment.evidence_key(saved[0]),
                         assessment.evidence_key(themed[1][0]))


if __name__ == "__main__":
    unittest.main()
