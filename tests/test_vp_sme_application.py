"""Conservative SME evaluation-document rule: requirements, not bid decisions."""
from copy import deepcopy
import hashlib
import json
import unittest

from vp_sme_application import CODE, LAW, evaluate, install


URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32
CLAUSE = ("Непредставление документов об опыте исполнения договоров, представляемых "
          "для оценки заявки по критерию «Квалификация участника», является "
          "основанием для отклонения заявки.")
P = {"reg": "32619000101", "published": "2026-10-01",
     "method": "Открытый конкурс в электронной форме",
     "card_fields_json": {"fact_evidence": {"sme_only": {
         "value": True, "source": "official_eis_card", "evidence": "Участники закупки: только МСП"}}}}


def original(body=CLAUSE, name="Документация закупки.pdf", **metadata):
    return {"name": name, "status": "checked", "url": URL, "text": body,
            "meta": {"source": "official_eis_original", "source_url": URL,
                     "sha256": hashlib.sha256(body.encode()).hexdigest(), **metadata}}


class SmeEvaluationDocuments(unittest.TestCase):
    def test_explicit_current_requirement_is_a_document_requirement(self):
        doc = original("1. Условия настоящей закупки.\n2. " + CLAUSE)
        snapshot = deepcopy(doc)
        findings, checks = evaluate(P, [doc])
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])
        item = findings[0]
        self.assertEqual((item["code"], item["law"], item["kind"]), (CODE, LAW, "violation"))
        self.assertEqual(item["criterion_keys"], ["sme_application_requirements"])
        self.assertEqual(item["decision_kind"], "document_requirement")
        self.assertEqual(item["legal_fact_type"], "document_requirement")
        self.assertEqual(item["source_excerpt"], item["match_text"])
        self.assertIn(item["source_excerpt"], doc["text"])
        self.assertEqual(doc["text"].index(item["source_excerpt"]), item["source_position"])
        self.assertEqual(item["source_url"], URL)
        self.assertNotIn("КоАП", json.dumps(item, ensure_ascii=False))
        self.assertEqual(doc, snapshot, "The source payload must remain unchanged")

    def test_proposals_and_explicit_electronic_xml_method(self):
        for method, xml_type in (("Открытый конкурс", "purchaseNoticeKESMBO"),
                                 ("Запрос предложений", "purchaseNoticeZPESMBO")):
            with self.subTest(method=method):
                p = {**P, "method": method, "card_fields_json": json.dumps({"soap_metadata": {
                    "source": "official_eis_getDocsIP", "xml_type": xml_type, "xml_sha256": "a" * 64}})}
                self.assertEqual(len(evaluate(p, [original()])[0]), 1)
        p = {**P, "method": "Запрос предложений в электронной форме"}
        self.assertEqual(len(evaluate(p, [original(name="Извещение закупки.pdf")])[0]), 1)

    def test_prospective_condition_and_line_wrap_are_not_examples(self):
        clause = ("При непредставлении документов об опыте исполнения договоров,\n"
                  "представляемых для оценки заявки по критерию «Квалификация», заявка подлежит отклонению.")
        self.assertEqual(len(evaluate(P, [original(clause)])[0]), 1)
        nbsp = original(CLAUSE.replace(" ", "\xa0"))
        finding = evaluate(P, [nbsp])[0][0]
        self.assertIn(finding["source_excerpt"], nbsp["text"])

    def test_negation_and_zero_points_cannot_be_rejection_evidence(self):
        clauses = [
            CLAUSE.replace("является основанием", "не является основанием"),
            CLAUSE.replace("является основанием", "не может являться основанием"),
            CLAUSE.replace("является основанием для", "не влечет"),
            "При непредставлении документов для оценки заявки заявка не подлежит отклонению.",
            "При непредставлении документов для оценки заявки не допускается отклонение заявки.",
            "При непредставлении документов для оценки заявки участнику присваивается 0 баллов.",
            "При непредставлении документов для оценки заявки участнику присваивается ноль баллов.",
        ]
        for clause in clauses:
            with self.subTest(clause=clause):
                self.assertEqual(evaluate(P, [original(clause)]), ([], []))

    def test_license_without_evaluation_link_does_not_match(self):
        clauses = [
            "Непредставление обязательной лицензии является основанием для отклонения заявки.",
            "Непредставление документов о лицензии является основанием для отклонения заявки.",
            "Документы об опыте представляются для оценки заявки. Непредставление лицензии является основанием для отклонения заявки.",
            "Непредставление документов для оценки соответствия заявки обязательным требованиям является основанием для отклонения заявки.",
            "Непредставление документов по критерию «Квалификация участника» является основанием для отклонения заявки.",
            "Непредставление документов об опыте исполнения договоров; для оценки заявки используются другие документы, отсутствие лицензии является основанием для отклонения заявки.",
        ]
        for clause in clauses:
            with self.subTest(clause=clause):
                self.assertEqual(evaluate(P, [original(clause)]), ([], []))

    def test_admission_evaluation_and_changed_subject_stay_review(self):
        clauses = [
            "Непредставление документов, подтверждающих наличие обязательной лицензии на медицинскую деятельность, представляемых для оценки заявки на соответствие требованиям законодательства, является основанием для отклонения заявки.",
            "Непредставление документов об опыте, представляемых для оценки заявки, не является нарушением требований, однако отсутствие у участника обязательной лицензии является основанием для отклонения заявки.",
            "Непредставление документов об опыте, представляемых для оценки заявки по критерию «Квалификация», не является нарушением требований, однако отсутствие у участника обязательной лицензии является основанием для отклонения заявки.",
            "Непредставление документов, представляемых для оценки заявки, является основанием для отклонения заявки.",
            "Непредставление документов, представляемых для оценки заявки по критерию «Соответствие обязательным требованиям», является основанием для отклонения заявки.",
        ]
        for clause in clauses:
            with self.subTest(clause=clause):
                findings, checks = evaluate(P, [original(clause)])
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)

    def test_examples_quotes_and_wrong_source_roles_are_excluded(self):
        for name in ("Образец документации.pdf", "Шаблон документации.docx",
                     "Инструкция по составлению протокола.pdf", "Проект договора.pdf",
                     "Положение о закупке.pdf", "Протокол рассмотрения.pdf", "Договор.pdf"):
            with self.subTest(name=name):
                self.assertEqual(evaluate(P, [original(name=name)]), ([], []))
        for prefix in ("Пример недопустимой формулировки: ", "Цитата из закона: ",
                       "Нельзя использовать формулировку: "):
            with self.subTest(prefix=prefix):
                self.assertEqual(evaluate(P, [original(prefix + CLAUSE)]), ([], []))

    def test_unproven_premises_stay_in_review(self):
        cases = [
            ({**P, "method": "Открытый конкурс"}, original()),
            ({**P, "method": "Способ закупки не указан"}, original()),
            ({**P, "method": "Электронный аукцион"}, original()),
            ({**P, "method": "Запрос котировок в электронной форме"}, original()),
            ({**P, "purchase_method": "Электронный аукцион"}, original()),
            ({**P, "purchase_method": "Открытый конкурс в бумажной форме"}, original()),
            ({**P, "published": None}, original()),
            ({**P, "published_at": "2020-03-01"}, original()),
            ({**P, "published_at": "неизвестно"}, original()),
            ({**P, "published": "2021-03-31"}, original()),
            ({**P, "card_fields_json": {}}, original()),
            (P, original(name="Приложение 1.pdf")),
            (P, original(reg_number="32619000999")),
        ]
        for purchase, doc in cases:
            with self.subTest(purchase=purchase, name=doc["name"]):
                findings, checks = evaluate(purchase, [doc])
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)
                self.assertEqual(checks[0]["status"], "check")
                self.assertEqual(checks[0]["field_path"], CODE)

    def test_known_nonexclusive_procurement_is_inapplicable(self):
        p = deepcopy(P)
        p["card_fields_json"]["fact_evidence"]["sme_only"]["value"] = False
        self.assertEqual(evaluate(p, [original()]), ([], []))

    def test_effective_date_boundary_and_no_invented_original(self):
        self.assertEqual(len(evaluate({**P, "published": "2021-04-01"}, [original()])[0]), 1)
        for changed in ({"status": "error"}, {"meta": {}},
                        {"meta": {"source": "official_eis_original", "sha256": "a" * 64,
                                  "source_url": "https://example.org/document.pdf"}}):
            with self.subTest(changed=changed):
                self.assertEqual(evaluate(P, [{**original(), **changed}]), ([], []))

    def test_sme_declaration_must_come_from_actual_tender_source(self):
        unknown = {**P, "card_fields_json": {}}
        declaration = "Участниками настоящей закупки могут быть только субъекты малого и среднего предпринимательства."
        for source in (original(declaration, "Шаблон документации.pdf"),
                       original(declaration, "Положение о закупке.pdf"),
                       original(declaration, superseded=True),
                       original("Цитата из закона: " + declaration)):
            with self.subTest(source=source["name"], text=source["text"]):
                findings, checks = evaluate(unknown, [original(), source])
                self.assertEqual(findings, [])
                self.assertTrue(checks)
        doc = original(declaration + "\n\n" + CLAUSE)
        self.assertEqual(len(evaluate(unknown, [doc])[0]), 1)

    def test_obsolete_and_ambiguous_revisions_remain_review(self):
        for flags in ({"superseded": True}, {"version_status": "obsolete"},
                      {"is_current_version": False}, {"document_cancelled": True}):
            with self.subTest(flags=flags):
                findings, checks = evaluate(P, [original(**flags)])
                self.assertEqual(findings, [])
                self.assertEqual(len(checks), 1)
        old = original()
        replacement = original("Действующая документация не содержит такого условия.")
        self.assertEqual(evaluate(P, [old, replacement])[0], [])
        self.assertEqual(len(evaluate(P, [old, replacement])[1]), 1)
        for names in (("Документация редакция 1.pdf", "Документация редакция 2.pdf"),
                      ("Документация от 01.10.2026.pdf", "Документация от 02.10.2026.pdf"),
                      ("Документация_v1.pdf", "Документация_v2.pdf"),
                      ("Документация_2026-09-01.pdf", "Документация_2026-10-01.pdf")):
            with self.subTest(names=names):
                first = original(name=names[0])
                later = original("Другая сохранённая редакция документации.", name=names[1])
                self.assertEqual(evaluate(P, [first, later])[0], [])
                self.assertEqual(len(evaluate(P, [first, later])[1]), 1)
        replacement["meta"]["is_current"] = True
        self.assertEqual(evaluate(P, [old, replacement])[0], [])
        self.assertEqual(len(evaluate(P, [old, replacement])[1]), 1)
        old["meta"]["is_current"] = True
        replacement["meta"].pop("is_current")
        self.assertEqual(evaluate(P, [old, replacement])[0], [])
        self.assertEqual(len(evaluate(P, [old, replacement])[1]), 1)

    def test_contradictory_official_method_is_review(self):
        p = {**P, "card_fields_json": {**P["card_fields_json"], "soap_metadata": {
            "source": "official_eis_getDocsIP", "xml_type": "purchaseNoticeAESMBO", "xml_sha256": "a" * 64}}}
        findings, checks = evaluate(p, [original()])
        self.assertEqual(findings, [])
        self.assertEqual(len(checks), 1)

    def test_malformed_soap_hash_cannot_establish_exclusive_sme(self):
        p = {**P, "card_fields_json": {"soap_metadata": {
            "source": "official_eis_getDocsIP", "xml_type": "purchaseNoticeKESMBO", "xml_sha256": "garbage"}}}
        findings, checks = evaluate(p, [original()])
        self.assertEqual(findings, [])
        self.assertEqual(len(checks), 1)
        malformed = {**P, "card_fields_json": {"soap_metadata": "broken", "fact_evidence": "broken"},
                     "fact_evidence": "broken"}
        self.assertEqual(evaluate(malformed, [original()])[0], [])
        self.assertEqual(evaluate(P, [{**original(), "meta": "broken"}]), ([], []))

    def test_duplicates_and_shared_wrappers(self):
        doc = original()
        self.assertEqual(len(evaluate(P, [doc, deepcopy(doc)])[0]), 1)
        prior = {"code": "prior", "kind": "violation"}
        prior_check = {"status": "check", "field_path": "prior"}
        ns = {"analyze": lambda p, docs: [prior],
              "build_checks": lambda p, detail, card, docs: [prior_check]}
        install(ns)
        analyze, checker = ns["analyze"], ns["build_checks"]
        install(ns)
        self.assertIs(ns["analyze"], analyze)
        self.assertIs(ns["build_checks"], checker)
        self.assertEqual(len(analyze(P, [doc])), 2)
        unknown = {**P, "card_fields_json": {}}
        self.assertEqual(len(checker(unknown, {}, {}, [doc])), 2)
        confirmed = evaluate(P, [doc])[0][0]
        review = evaluate(unknown, [doc])[1][0]
        ns = {"analyze": lambda p, docs: [confirmed],
              "build_checks": lambda p, detail, card, docs: [review]}
        install(ns)
        self.assertEqual(len(ns["analyze"](P, [doc])), 1)
        self.assertEqual(len(ns["build_checks"](unknown, {}, {}, [doc])), 1)


if __name__ == "__main__":
    unittest.main()
