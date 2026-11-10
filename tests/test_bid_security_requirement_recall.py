"""Real legal outputs for ordinary bid-security fields and percentage bases.

All documents are synthetic; these assertions are not findings about live EIS
procurements. A percentage of an offer/advance cannot prove a percentage of NMCD.
"""
from copy import deepcopy
from pathlib import Path
import asyncio
import hashlib
import json
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import v336_legal_matrix as matrix
from vp_inspection_engine import qualify_outcomes

REG = "32619990021"
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
PURCHASE = {"reg": REG, "method": "Аукцион в электронной форме",
            "published": "2026-10-01", "price_num": 4000000}
LOW = "223-3.2-27-BID-SECURITY-LOW-NMCD"
HIGH = "223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT"


def original(body, name="Документация.docx", uid=2, **meta):
    return {"name": name, "type": "documentation", "text": body, "status": "checked",
            "meta": {"source": "official_eis_original", "source_url": URL + f"{uid:032X}",
                     "sha256": hashlib.sha256(body.encode()).hexdigest(), **meta}}


def evidence(body, price=4000000, **purchase):
    p = {**PURCHASE, "price_num": price, **purchase}
    docs = [original(f"НМЦД: {price} рублей.", "Извещение.docx", 1), original(body)]
    return p, docs


def inspect(body, price=4000000, **purchase):
    p, docs = evidence(body, price, **purchase)
    findings, checks = matrix.security_findings(p, docs)
    return qualify_outcomes(p, docs, findings, checks)


class BidSecurityRequirementRecall(unittest.TestCase):
    def test_explicit_method_labels_keep_their_own_required_amount(self):
        # Previously the optional suffix accepted only "в закупке".
        for method in ("аукционе", "электронном аукционе", "конкурсе",
                       "открытом конкурсе", "запросе котировок", "запросе предложений"):
            body = f"Размер обеспечения заявки на участие в {method}: 100 000 рублей."
            with self.subTest(method=method):
                findings, _ = inspect(body)
                self.assertEqual([item["code"] for item in findings], [LOW])
                self.assertEqual(findings[0]["match_text"], body[:-1])
                self.assertEqual(findings[0]["source_url"], URL + f"{2:032X}")

    def test_amount_connector_and_complete_ruble_tokens(self):
        for body in (
            "Обеспечение заявки установлено в сумме 100 000 рублей.",
            "Размер обеспечения заявки составляет сумму 100000 рублей.",
            "Обеспечение заявки: 100000 RUB.",
            "Размер обеспечения заявки на участие в аукционе\n100\u202f000,00 ₽.",
        ):
            with self.subTest(body=body):
                self.assertEqual([item["code"] for item in inspect(body)[0]], [LOW])

    def test_longer_words_are_not_currency_or_percentage_tokens(self):
        for unit in ("рубашек", "рублейка", "RUBRIC", "процентовка"):
            with self.subTest(unit=unit):
                self.assertEqual(inspect("Обеспечение заявки: 6 " + unit + ".")[0], [])

    def test_expanded_labels_do_not_borrow_another_field_or_negative_condition(self):
        for body in (
            "Обеспечение заявки на участие в аукционе не требуется. Обеспечение исполнения договора: 100000 рублей.",
            "Обеспечение заявки на участие в конкурсе\nОбеспечение исполнения договора: 100000 рублей.",
            "Обеспечение заявки на участие в аукционе и обеспечение исполнения договора: 100000 рублей.",
            "Пример: обеспечение заявки на участие в аукционе составляет 100000 рублей.",
            "Обеспечение заявки на участие в конкурсе в сумме 100000 рублей не требуется.",
            "Если обеспечение заявки на участие в конкурсе составляет 100000 рублей, оно вносится на счёт.",
        ):
            with self.subTest(body=body):
                self.assertEqual(inspect(body)[0], [])

    def test_percentage_of_another_or_unknown_base_stays_review(self):
        for suffix in (
            "цены, предложенной участником", "от цены победителя", "суммы аванса",
            "от стоимости единицы товара", "от цены договора", "",
            "от НМЦД или от цены, предложенной участником",
            "от НМЦД, но не более 100000 рублей",
            "от НМЦД предыдущей закупки",
            "от НМЦД лота 2",
            "от НМЦД за вычетом аванса",
            "от НМЦД без учета НДС",
            ". Цена участника определяется в процентах от НМЦД",
        ):
            body = "Обеспечение заявки: 8% " + suffix + "."
            with self.subTest(suffix=suffix):
                findings, checks = inspect(body, 8000000)
                self.assertEqual(findings, [])
                self.assertTrue(checks)
                self.assertTrue(any("security_percent_basis_unverified" in
                                    item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_modified_formula_does_not_prove_positive_security_below_five_million(self):
        for base in ("НМЦД", "суммы аванса"):
            findings, checks = inspect("Обеспечение заявки: 8% от " + base + ", но не более 0 рублей.")
            self.assertEqual(findings, [])
            self.assertTrue(checks)

    def test_explicit_nmcd_basis_includes_exact_evidence_for_ratio(self):
        for suffix in ("от НМЦД", "НМЦД", "от начальной (максимальной) цены договора",
                       "начальной максимальной цены договора", "от начальной цены договора"):
            body = "Обеспечение заявки на участие в аукционе: 8% " + suffix + "."
            with self.subTest(suffix=suffix):
                findings, _ = inspect(body, 8000000)
                self.assertEqual([item["code"] for item in findings], [HIGH])
                assessment = findings[0]["security_assessment"]
                self.assertEqual(assessment["percent_basis"]["kind"], "nmcd")
                self.assertIn(suffix, assessment["percent_basis"]["excerpt"])

    def test_general_and_sme_limits_use_the_same_verified_nmcd_base(self):
        p = {"fact_evidence": {"sme_only": {"value": True, "source": "official_eis_card"}}}
        for amount, expected in (("2", []), ("2,01", ["223-SME-BID-SECURITY-OVER-TWO-PERCENT"])):
            with self.subTest(amount=amount):
                findings, _ = inspect("Обеспечение заявки: " + amount + "% от НМЦД.", 8000000, **p)
                self.assertEqual([item["code"] for item in findings], expected)
        self.assertEqual(inspect("Обеспечение заявки: 3% цены участника.", 8000000, **p)[0], [])

    def test_different_percentage_bases_cannot_be_equal_peer_terms(self):
        p, docs = evidence("Обеспечение заявки: 8% от НМЦД.", 8000000)
        docs.append(original("Обеспечение заявки: 8% от цены, предложенной участником.", "Уточнение.docx", 3))
        findings, checks = matrix.security_findings(p, docs)
        self.assertEqual(findings, [])
        self.assertTrue(checks)

    def test_existing_legacy_candidate_is_requalified_from_original_basis(self):
        body = "Обеспечение заявки: 8% от цены, предложенной участником."
        p, docs = evidence(body, 8000000)
        candidate = {"code": HIGH, "kind": "violation", "title": "Превышен размер обеспечения",
                     "law": "223-ФЗ, ст. 3.2 ч. 27", "evidence": body,
                     "match_text": "Обеспечение заявки: 8%", "source_position": 0,
                     "document": docs[1]["name"], "source_url": docs[1]["meta"]["source_url"],
                     "security_assessment": {"status": "confirmed"}}
        findings, checks = qualify_outcomes(p, docs, [candidate])
        self.assertEqual(findings, [])
        self.assertEqual(len(checks), 1)
        self.assertIn("security_percent_basis_unverified", checks[0]["methodology_reason_codes"])

    def test_recall_does_not_override_original_scope_or_mutate_sources(self):
        body = "Обеспечение заявки на участие в аукционе: 100000 рублей."
        for metadata in ({"superseded": True}, {"reg_number": "32619999999"},
                         {"source": "local_unverified"}):
            p, docs = evidence(body)
            docs[1]["meta"].update(metadata)
            before = deepcopy((p, docs))
            findings, checks = matrix.security_findings(p, docs)
            self.assertEqual(qualify_outcomes(p, docs, findings, checks)[0], [])
            self.assertEqual((p, docs), before)


import test_official_publication_evidence as saved
import v298_eis_only as offline
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
from v352_dashboard_truth import load_counts
import vp_inspection_engine as inspection


class SavedBidSecurityPipeline(unittest.TestCase):
    setUp = saved.OfficialPublicationEvidenceTests.setUp
    connect = saved.OfficialPublicationEvidenceTests.connect
    make_xml = saved.OfficialPublicationEvidenceTests.make_xml
    write_archive = saved.OfficialPublicationEvidenceTests.write_archive
    write_original = saved.OfficialPublicationEvidenceTests.write_original
    collect = saved.OfficialPublicationEvidenceTests.collect

    def run_original(self, body, expected, price=4000000):
        # Exercise actual DOCX bytes, SHA receipts, SOAP metadata, persisted
        # general findings, thematic selection and the displayed legal count.
        shutil.rmtree(self.root / "eis223_official_archive_inbox" / saved.REG)
        name, raw, _ = self.make_xml("purchaseNotice", filename="Документация.docx")
        # This fixture supplies an explicit, single-lot NMCD. An incomplete
        # XML shape/unknown price kind is deliberately not overridden by DOCX.
        raw = raw.replace(b"</purchaseNoticeData>",
                          (f"<lots><lot><lotData><initialSum>{price}</initialSum>"
                           "</lotData></lot></lots></purchaseNoticeData>").encode())
        record, _ = saved.xml_reader.parse_document(raw, name)
        self.write_archive((name, raw, record))
        self.write_original(body)
        matrix.install(self.ns)
        inspection.install(self.ns)
        result = self.collect()
        self.assertEqual(result["docs"][0]["status"], "checked")
        p = result["p"]
        with self.connect() as db:
            db.execute("INSERT INTO purchases(reg_number,title,customer,method,published_at,"
                       "submission_end,card_fields_json) VALUES(?,?,?,?,?,?,?)",
                       (saved.REG, p["title"], "ООО Тест", p["method"], p["published"],
                        p["submission_end"], "{}"))
            for doc in result["docs"]:
                db.execute("INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,"
                           "status,metadata_json,final_url) VALUES(?,?,?,?,?,?,?,?,?)",
                           (saved.REG, doc["name"], doc["url"], doc["type"], doc["published"],
                            doc["local"], doc["status"], json.dumps(doc["meta"]), doc["final_url"]))
        first = asyncio.run(general.inspect_one(self.ns, saved.REG))
        repeated = asyncio.run(general.inspect_one(self.ns, saved.REG))
        selected = asyncio.run(thematic.independent_review(self.ns, saved.REG, ["security"]))
        self.assertEqual((first[0], first[1]["new_findings"]), ("completed", expected))
        self.assertEqual((repeated[0], repeated[1]["new_findings"]), ("completed", 0))
        self.assertEqual((selected[0], len(selected[1])), ("completed", expected))
        with self.connect() as db:
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: expected})
            findings = [dict(row) for row in db.execute("SELECT * FROM findings")]
        self.assertEqual(len(findings), expected)
        if expected:
            self.assertEqual(findings[0]["code"], LOW)
            self.assertEqual(findings[0]["source_url"], saved.URL)
            self.assertIn("на участие в аукционе", findings[0]["match_text"])

    def test_new_label_survives_real_original_ingress_and_both_audit_paths(self):
        self.run_original("НМЦД: 4 000 000 рублей.\n"
                          "Обеспечение заявки на участие в аукционе: 100 000 рублей.", 1)

    def test_another_percentage_base_never_enters_confirmed_counts(self):
        self.run_original("НМЦД: 8 000 000 рублей.\n"
                          "Обеспечение заявки: 8% от цены, предложенной участником.", 0, 8000000)


if __name__ == "__main__":
    unittest.main()
