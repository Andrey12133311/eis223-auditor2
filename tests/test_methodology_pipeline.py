"""Exercise methodology decisions before real general/thematic persistence."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import test_general_thematic_parity as fixture
import vp_inspection_engine as engine
import vp_sme_application as sme_application


def run_pipeline(documents, injected=None):
    install = fixture.protocols.install
    decisions = []

    def install_with_methodology(ns):
        install(ns)
        if injected:
            previous = ns["analyze"]
            ns["analyze"] = lambda p, docs: list(previous(p, docs)) + [deepcopy(injected)]
        sme_application.install(ns)
        engine.install(ns)
        qualify = ns["vp_methodology_qualify"]

        def record(p, docs, findings, checks):
            result = qualify(p, docs, findings, checks)
            decisions.append(deepcopy(result))
            return result

        ns["vp_methodology_qualify"] = record

    with patch.object(fixture.protocols, "install", side_effect=install_with_methodology):
        result = fixture.compare(documents)
    return result, decisions


class MethodologyPipeline(unittest.TestCase):
    def test_new_sme_rule_reaches_general_and_exact_thematic_criterion(self):
        from test_vp_sme_application import P, CLAUSE, original
        doc = original("В предыдущей редакции документации было установлено: "+CLAUSE+
                       "\n\nДействующая редакция документации:\n\n"+CLAUSE)
        purchase = {**P, "reg_number": fixture.REG, "reg": fixture.REG,
                    "customer": "ООО Тест", "published_at": P["published"],
                    "card_fields_json": json.dumps(P["card_fields_json"])}
        review = fixture.theme.independent_review

        async def exact_criterion(ns, reg, categories):
            return await review(ns, reg, ["sme_application_requirements"])

        with patch.object(fixture, "P", purchase), \
             patch.object(fixture.theme, "independent_review", side_effect=exact_criterion):
            (first, second, thematic, found, counts), decisions = run_pipeline([doc])
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["new_findings"], 1)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual(counts["violations"], 1)
        self.assertEqual([item["code"] for item in found], [sme_application.CODE])
        self.assertEqual([item["code"] for item in thematic[1]], [sme_application.CODE])
        self.assertEqual(thematic[1][0]["category"], "sme_application_requirements")
        self.assertEqual(thematic[1][0]["decision_kind"], "document_requirement")

    def test_actual_events_survive_in_both_queues(self):
        (first, second, thematic, found, counts), decisions = run_pipeline(fixture.docs)
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["new_findings"], 3)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual(len(thematic[1]), 3)
        self.assertEqual(counts["violations"], 3)
        self.assertEqual({item["code"] for item in found}, {item["code"] for item in thematic[1]})
        self.assertTrue(decisions)

    def test_receipt_verified_examples_are_review_not_actual_events(self):
        documents = deepcopy(fixture.docs)
        for doc in documents:
            doc["text"] = "Пример заполнения:\n" + doc["text"]
            doc["meta"]["sha256"] = hashlib.sha256(doc["text"].encode()).hexdigest()
        (first, _, thematic, found, counts), decisions = run_pipeline(documents)
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["verified_original_documents"], 3)
        self.assertEqual(found, [])
        self.assertEqual(thematic, ("completed", []))
        self.assertEqual(counts["violations"], 0)
        checks = [item for _, values in decisions for item in values]
        self.assertTrue(any(item.get("methodology_reasons") or "пример" in str(item).lower() for item in checks))

    def test_unrelated_norm_cannot_reach_confirmed_counts(self):
        candidate = {
            "code": "V372-PROTOCOL-REJECTED-BID-NO-REASONS", "kind": "violation",
            "status": "violation", "title": "Отсутствуют основания отклонения заявки",
            "law": "ГК РФ, статья 450", "document": fixture.docs[0]["name"],
            "source_url": fixture.docs[0]["meta"]["source_url"],
            "evidence": "Текст требует проверки правовой квалификации",
            "match_text": "основания отклонения не указаны",
        }
        (first, _, thematic, found, counts), decisions = run_pipeline([fixture.docs[0]], candidate)
        self.assertEqual(first[1]["new_findings"], 1)
        self.assertEqual(counts["violations"], 1)
        self.assertEqual(len(thematic[1]), 1)
        self.assertEqual(len(found), 1)
        reviews = [item for _, values in decisions for item in values
                   if item.get("law") == "ГК РФ, статья 450"]
        self.assertTrue(reviews)
        self.assertTrue(all(item["status"] == "check" and not item["legal_basis_verified"] for item in reviews))


if __name__ == "__main__":
    unittest.main()
