"""Common qualification must agree with saved verdicts and the exact sheet.

The source fixtures are synthetic. Legal analyzers, qualification and SQLite
persistence are real; production data and network services are never touched.
"""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vp_inspection_engine as engine
import v330_amendments_223 as amendments
import v348_cross_document_legal as cross
import test_general_thematic_parity as fixture
from test_methodology_pipeline import run_pipeline
from test_clarification_identity import pair as clarification_pair


CODE = "v372-protocol-rejected-bid-no-reasons"
CLAUSE = "Отклонена заявка № 12, основания отклонения не указаны."
P = {**fixture.P, "reg": fixture.REG, "published": "2026-10-01"}
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32


def original(text=CLAUSE, name="Протокол.docx", *, url=URL, **meta):
    return {"name": name, "text": text, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(text.encode()).hexdigest(), **meta}}


def claim(doc=None, **extra):
    doc = doc or original()
    return {"code": CODE, "kind": "violation",
            "title": "В протоколе прямо указано отсутствие основания отклонения заявки",
            "law": "223-ФЗ, ст. 3.2 ч. 13 п. 3 пп. «б»",
            "document": doc["name"], "source_url": doc["meta"]["source_url"],
            "evidence": CLAUSE, **extra}


def criterion(result, key="protocol_compliance"):
    return next(row for row in result["rows"] if row["key"] == key)


def pipeline(documents, *, purchase=None, injected=None, category="protocol_compliance"):
    review = fixture.theme.independent_review

    async def exact(ns, reg, categories):
        return await review(ns, reg, [category])

    with ExitStack() as stack:
        stack.enter_context(patch.object(fixture, "P", deepcopy(purchase or P)))
        stack.enter_context(patch.object(fixture.theme, "independent_review", side_effect=exact))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return run_pipeline(documents, injected)


class LegalEvidenceQualification(unittest.TestCase):
    def assert_review(self, p, docs, item, reason):
        before = deepcopy((p, docs, item))
        findings, checks = engine.qualify_outcomes(p, docs, [item])
        self.assertEqual(findings, [])
        self.assertEqual(len(checks), 1)
        self.assertEqual((checks[0]["kind"], checks[0]["status"]), ("review", "check"))
        self.assertIn(reason, checks[0]["methodology_reason_codes"])
        self.assertEqual(checks[0]["evidence"], item["evidence"])
        self.assertEqual((p, docs, item), before)
        self.assertEqual(engine.qualify_outcomes(p, docs, findings, checks), (findings, checks))
        return checks[0]

    def test_unquoted_or_mixed_source_claim_is_review_before_persistence(self):
        source = original("Все заявки допущены к участию в закупке.")
        self.assert_review(P, [source], claim(source), "source_fragment_unverified")
        other = original("Все заявки допущены к участию в закупке.", "Другой протокол.docx",
                         url=URL.replace("A" * 32, "B" * 32))
        self.assert_review(P, [original(), other], claim(source, source_url=other["url"]),
                           "source_fragment_unverified")

    def test_ambiguous_source_versions_cannot_supply_a_saved_verdict(self):
        self.assert_review(P, [original(), original(CLAUSE + "\nДругая редакция.")],
                           claim(), "source_fragment_unverified")

    def test_explicit_source_hash_cannot_borrow_another_version(self):
        source = original()
        self.assert_review(P, [source], claim(source, sha256="b" * 64), "source_fragment_unverified")
        matching = claim(source, sha256=source["meta"]["sha256"])
        self.assertEqual(len(engine.qualify_outcomes(P, [source], [matching])[0]), 1)
        alternatives = [source, original(CLAUSE + "\nДругая редакция.")]
        self.assertEqual(len(engine.qualify_outcomes(P, alternatives, [matching])[0]), 1)

    def test_archive_member_is_part_of_the_exact_citation(self):
        first = original(name="Протокол.docx", source="official_eis_archive_member",
                         archive_member="лот-1/Протокол.docx")
        second = original(name="Протокол.docx", source="official_eis_archive_member",
                          archive_member="лот-2/Протокол.docx")
        self.assert_review(P, [first, second], claim(first), "source_fragment_unverified")
        self.assert_review(P, [first], claim(first, archive_member="лот-2/Протокол.docx"),
                           "source_fragment_unverified")
        correct = claim(first, archive_member=first["meta"]["archive_member"])
        self.assertEqual(len(engine.qualify_outcomes(P, [first, second], [correct])[0]), 1)

    def test_explicit_other_purchase_or_lot_cannot_supply_a_saved_verdict(self):
        for p, doc, reason in (
            (P, original(purchase_reg_number="32619999999"), "source_other_purchase"),
            ({**P, "lot_number": "1"}, original(lot_number="2"), "source_other_lot"),
        ):
            with self.subTest(p=p, doc=doc):
                self.assert_review(p, [doc], claim(doc), reason)
        matching = original(purchase_reg_number=P["reg"], lot_number="1")
        self.assertEqual(len(engine.qualify_outcomes({**P, "lot_number": "1"}, [matching], [claim(matching)])[0]), 1)

    def test_exact_occurrence_binds_the_selected_lot_inside_one_original(self):
        text = "Лот № 1.\n" + CLAUSE + "\n\nЛот № 2.\n" + CLAUSE
        doc = original(text)
        p = {**P, "lot_number": "1"}
        valid = claim(doc, source_position=text.index(CLAUSE))
        self.assertEqual(len(engine.qualify_outcomes(p, [doc], [valid])[0]), 1)
        self.assert_review(p, [doc], claim(doc, source_position=text.rindex(CLAUSE)), "source_other_lot")

    def test_unknown_noncompetitive_and_legacy_scope_are_review_before_persistence(self):
        for changes in ({"method": ""}, {"method": "Запрос оферт"},
                        {"method": "Закупка у единственного поставщика"},
                        {"published": "2017-10-01", "published_at": "2017-10-01"}):
            with self.subTest(changes=changes):
                self.assert_review({**P, **changes}, [original()], claim(), "criterion_scope_unverified")

    def test_unknown_sme_scope_cannot_confirm_a_saved_special_rule(self):
        item = claim(code="sme_collective_participation", law="223-ФЗ, ст. 3.4 ч. 19.1")
        self.assert_review(P, [original()], item, "criterion_scope_unverified")

    def test_legacy_security_claim_still_needs_verified_nmcd(self):
        text = "Обеспечение заявки: 8% от НМЦД."
        doc = original(text, "Документация.docx")
        item = claim(doc, code="223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT",
                     law="223-ФЗ, ст. 3.2 ч. 27", evidence=text)
        self.assert_review({**P, "price_num": 8000000}, [doc], item, "security_nmcd_unverified")
        price = original("НМЦД: 8 000 000 рублей.", "Извещение.docx",
                         url=URL.replace("A" * 32, "B" * 32))
        findings, checks = engine.qualify_outcomes({**P, "price_num": 8000000}, [doc, price], [item])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["security_assessment"]["status"], "confirmed")
        self.assertEqual(checks, [])

    def test_legacy_security_claim_cannot_bypass_conflicting_originals(self):
        text = "Обеспечение заявки: 8% от НМЦД."
        doc = original("НМЦД: 8 000 000 рублей.\n" + text, "Документация.docx")
        alternative = original("Обеспечение заявки: 3% от НМЦД.", "Уточнение.docx",
                               url=URL.replace("A" * 32, "B" * 32))
        item = claim(doc, code="223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT",
                     law="223-ФЗ, ст. 3.2 ч. 27", evidence=text)
        review = self.assert_review({**P, "price_num": 8000000}, [doc, alternative], item,
                                   "security_conflicting_terms")
        self.assertEqual(review["security_assessment"]["status"], "review")
        self.assertEqual(review["security_assessment"]["related_sources"][0]["document"], alternative["name"])

    def test_repeated_quote_without_valid_occurrence_is_review_in_both_orders(self):
        active = "Действующая редакция документации:\n\n" + CLAUSE
        old = "В предыдущей редакции документации было установлено:\n\n" + CLAUSE
        for text in (active + "\n\n" + old, old + "\n\n" + active):
            for position in ({}, {"source_position": "0"}, {"source_position": True},
                             {"source_position": -1}, {"source_position": 99999}):
                with self.subTest(text=text, position=position):
                    self.assert_review(P, [original(text)], claim(**position), "source_occurrence_ambiguous")

    def test_valid_occurrence_preserves_current_clause_and_rejects_historical_one(self):
        text = ("В предыдущей редакции документации было установлено:\n\n" + CLAUSE +
                "\n\nДействующая редакция документации:\n\n" + CLAUSE)
        source = original(text)
        current = claim(source, source_position=text.rindex(CLAUSE))
        findings, checks = engine.qualify_outcomes(P, [source], [current])
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])
        self.assertEqual(criterion(engine.evaluate(P, [source], findings, checks))["status"], "violation")
        self.assert_review(P, [source], claim(source, source_position=text.index(CLAUSE)), "historical_reference")

    def test_unique_legacy_quote_without_position_stays_assessable(self):
        findings, checks = engine.qualify_outcomes(P, [original()], [claim()])
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])
        self.assertEqual(criterion(engine.evaluate(P, [original()], findings, checks))["status"], "violation")

    def test_incomplete_sources_cannot_establish_entire_criterion_compliance(self):
        positive = claim(kind="check", status="ok", criterion_complete=True)
        for coverage in ({"source_incomplete": True}, {"documents_unreadable": 1},
                         {"original_expected": 2, "original_received": 1}):
            with self.subTest(coverage=coverage):
                p = {**P, **coverage}
                _, checks = engine.qualify_outcomes(p, [original()], [], [positive])
                self.assertEqual(checks[0]["status"], "check")
                self.assertIn("criterion_sources_incomplete", checks[0]["methodology_reason_codes"])
                row = criterion(engine.evaluate(p, [original()], [], checks))
                self.assertEqual(row["status"], "unknown")
                self.assertFalse(row["no_violation"])
        unreadable = original("", "Приложение.pdf", url=URL.replace("A" * 32, "B" * 32))
        unreadable["status"] = "downloaded_no_text"
        row = criterion(engine.evaluate(P, [original(), unreadable], [], [positive]))
        self.assertEqual(row["status"], "unknown")
        self.assertFalse(row["no_violation"])

    def test_empty_results_and_narrow_positive_flags_stay_unknown(self):
        self.assertEqual(criterion(engine.evaluate(P, [original()]))["status"], "unknown")
        positive = claim(kind="check", status="ok", criterion_complete=True)
        row = criterion(engine.evaluate(P, [original()], [], [positive]))
        self.assertEqual(row["status"], "unknown")
        self.assertFalse(row["no_violation"])
        self.assertTrue(row["review_required"])
        self.assertTrue(row["legal_basis_verified"])
        self.assertIn("criterion_completion_unverified", row["methodology_reason_codes"])
        self.assertEqual(row["unresolved"][0]["reported_status"], "ok")
        self.assertEqual(row["unresolved"][0]["evidence"], CLAUSE)

    def test_real_cross_original_clarification_chain_survives_common_qualification(self):
        docs = clarification_pair()
        findings = cross.cross_doc_clarifications(P, docs)
        self.assertEqual(len(findings), 1)
        result, reviews = engine.qualify_outcomes(P, docs, findings)
        self.assertEqual(len(result), 1)
        self.assertEqual(reviews, [])
        row = criterion(engine.evaluate(P, docs, result), "clarifications")
        self.assertEqual(row["status"], "violation")

    def test_example_in_second_cross_original_limits_the_actual_event(self):
        docs = clarification_pair()
        docs[0]["text"] = "Пример заполнения:\n" + docs[0]["text"]
        docs[0]["meta"]["sha256"] = hashlib.sha256(docs[0]["text"].encode()).hexdigest()
        findings, checks = cross._clarification_outcomes(P, docs)
        self.assertEqual(findings, [])
        self.assertEqual(len(checks), 1)
        qualified, reviews = engine.qualify_outcomes(P, docs, findings, checks)
        self.assertEqual(qualified, [])
        self.assertIn("template_or_example", reviews[0]["methodology_reason_codes"])

    def test_verified_xml_date_chain_keeps_its_original_anchor(self):
        from test_clarification_xml_evidence import ClarificationXmlEvidence
        case = ClarificationXmlEvidence()
        with redirect_stdout(io.StringIO()):
            case.setUp()
            self.addCleanup(case.doCleanups)
            case.write_archive(case.explanation())
            collected = case.collect()
            findings, checks = case.evaluate()
        relevant = [item for item in findings if item.get("code") == "223-3.2-3-CLARIFICATION-RESPONSE-LATE"]
        self.assertEqual(len(relevant), 1)
        self.assertIn("SHA256 XML", relevant[0]["evidence"])
        confirmed, _ = engine.qualify_outcomes(collected["p"], collected["docs"], relevant)
        self.assertEqual(confirmed[0]["kind"], "violation")

    def test_official_history_verdict_retains_existing_external_proof_gate(self):
        from datetime import date
        event = {"decision_date": date(2026, 10, 6), "publication_date": date(2026, 10, 13),
                 "document": "История изменений ЕИС", "source": "https://zakupki.gov.ru/changes.html",
                 "kind": "eis_history"}
        with patch.object(amendments, "manifest_changes", return_value=[event]):
            findings, _ = amendments.evaluation(P, [])
        self.assertEqual(len(findings), 1)
        # This stage must not introduce filesystem/network reads. The existing
        # general persistence gate independently verifies the saved EIS history.
        with patch.object(amendments, "manifest_changes", side_effect=AssertionError("extra source read")):
            confirmed, checks = engine.qualify_outcomes(P, [], findings)
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(checks, [])

    def test_unbound_download_cannot_bypass_source_gate_as_history(self):
        item = claim(code="223-4-11-AMENDMENT-PUBLICATION-DELAY", law="223-ФЗ, ст. 4 ч. 11",
                     document="История ЕИС", evidence="Решение об изменении: 2026-10-06; публикация изменений в ЕИС: 2026-10-13")
        self.assert_review(P, [], item, "source_fragment_unverified")


class LegalEvidencePersistence(unittest.TestCase):
    def test_legacy_security_claim_uses_common_context_gate_in_both_pipelines(self):
        text = "Обеспечение заявки: 8% от НМЦД."
        doc = original(text, "Документация.docx")
        price = original("НМЦД: 8 000 000 рублей.", "Извещение.docx",
                         url=URL.replace("A" * 32, "B" * 32))
        item = claim(doc, code="223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT",
                     law="223-ФЗ, ст. 3.2 ч. 27", evidence=text)
        for documents, expected in (([doc], 0), ([doc, price], 1)):
            with self.subTest(expected=expected):
                (first, second, thematic, found, counts), _ = pipeline(
                    documents, purchase={**P, "price_num": 8000000}, injected=item, category="bid_security")
                self.assertEqual(first[0], "completed")
                self.assertEqual(first[1]["new_findings"], expected)
                self.assertEqual(second[1]["new_findings"], 0)
                self.assertEqual(counts["violations"], expected)
                self.assertEqual(len(found), expected)
                self.assertEqual(len(thematic[1]), expected)

    def test_general_and_exact_thematic_paths_reject_unsupported_legacy_claims(self):
        for p, docs, item in (
            (P, [original("Все заявки допущены к участию в закупке.")], claim()),
            ({**P, "method": ""}, [original()], claim()),
            (P, [original(), original(CLAUSE + "\nИная редакция.")], claim()),
        ):
            with self.subTest(p=p, docs=docs):
                (first, second, thematic, found, counts), decisions = pipeline(docs, purchase=p, injected=item)
                self.assertEqual(first[0], "completed")
                self.assertEqual(first[1]["new_findings"], 0)
                self.assertEqual(second[1]["new_findings"], 0)
                self.assertEqual(found, [])
                self.assertEqual(counts["violations"], 0)
                self.assertEqual(thematic, ("completed", []))
                self.assertTrue(all(not findings for findings, _ in decisions))

    def test_real_cross_original_chain_reaches_both_pipelines_once(self):
        (first, second, thematic, found, counts), _ = pipeline(clarification_pair(), category="clarifications")
        self.assertEqual(first[0], "completed")
        self.assertEqual(first[1]["new_findings"], 1)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual(counts["violations"], 1)
        self.assertEqual([item["code"] for item in found], ["223-3.2-3-CLARIFICATION-CROSS-DOC-LATE"])
        self.assertEqual([item["code"] for item in thematic[1]], [found[0]["code"]])


if __name__ == "__main__":
    unittest.main()
