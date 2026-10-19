"""Recovery from verified NMCD evidence when cached fields/text layout lag."""
import hashlib
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from v324_official_price import prices_in_text
from v336_legal_matrix import install, security_findings, verified_price

REG = "32610000001"
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
CARD = "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=" + REG
PURCHASE = {"reg": REG, "method": "Аукцион в электронной форме",
            "published": "2026-10-01", "price_num": 4000000}
PRICE = "НМЦД: 4 000 000 рублей."
BID = "Обеспечение заявки составляет 100 000 рублей."


def original(body, uid=1, *, name="Документация.docx", trusted=True, kind="other"):
    return {"name": name, "text": body, "status": "checked", "type": kind,
            "meta": {"source": "official_eis_original" if trusted else "local_unverified",
                     "sha256": hashlib.sha256(body.encode()).hexdigest(),
                     "source_url": SOURCE + f"{uid:032x}"}}


class LegalPriceRecallTests(unittest.TestCase):
    def assert_one(self, purchase, docs):
        findings, _ = security_findings(purchase, docs)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "223-3.2-27-BID-SECURITY-LOW-NMCD")
        self.assertIn("4,000,000.00", findings[0]["evidence"])
        return findings

    def test_original_nmcd_does_not_depend_on_cached_numeric_field(self):
        for p in ({**PURCHASE, "price_num": None},
                  {k: v for k, v in PURCHASE.items() if k != "price_num"}):
            with self.subTest(p=p):
                self.assert_one(p, [original(PRICE + "\n" + BID)])

    def test_unanimous_originals_recover_missing_price_once(self):
        p = {**PURCHASE, "price_num": None}
        docs = [original(PRICE, name="Извещение.docx"),
                original(PRICE + "\n" + BID, uid=2), original(BID, uid=3)]
        first = self.assert_one(p, docs)
        self.assertEqual(self.assert_one(p, docs + [deepcopy(docs[1])]), first)

    def test_known_conflicting_cached_price_is_not_silently_replaced(self):
        for number in (0, 8000000):
            with self.subTest(number=number):
                self.assertEqual(security_findings(
                    {**PURCHASE, "price_num": number}, [original(PRICE + "\n" + BID)])[0], [])

    def test_missing_cache_does_not_resolve_conflicting_original_prices(self):
        p = {**PURCHASE, "price_num": None}
        for docs in (
            [original(PRICE), original("НМЦД: 8 000 000 рублей.", uid=2)],
            [original(PRICE + "\nНМЦД: 8 000 000 рублей.")],
            [original("Лот № 1. " + PRICE + "\nЛот № 2. " + PRICE)],
        ):
            with self.subTest(docs=docs):
                self.assertIsNone(verified_price(p, docs))
                self.assertEqual(security_findings(p, docs + [original(BID, uid=3)])[0], [])

    def test_verified_card_can_supply_price_before_numeric_cache_is_filled(self):
        p = {**PURCHASE, "price_num": None,
             "verified_price_source": {"kind": "nmcd", "price": "4000000.00", "source": CARD}}
        self.assert_one(p, [original(BID)])

    def test_verified_card_conflict_with_original_is_not_hidden_by_empty_cache(self):
        p = {**PURCHASE, "price_num": None,
             "verified_price_source": {"kind": "nmcd", "price": "8000000.00", "source": CARD}}
        self.assertIsNone(verified_price(p, [original(PRICE)]))
        self.assertEqual(security_findings(p, [original(PRICE + "\n" + BID)])[0], [])

    def test_explicit_zero_or_invalid_verified_card_does_not_confirm_original_price(self):
        for value in ("0", "0.00", "-100", "не установлена"):
            with self.subTest(value=value):
                p = {**PURCHASE, "price_num": None,
                     "verified_price_source": {"kind": "nmcd", "price": value, "source": CARD}}
                self.assertIsNone(verified_price(p, [original(PRICE)]))
                self.assertEqual(security_findings(p, [original(PRICE + "\n" + BID)])[0], [])

    def test_price_examples_and_conditional_prefixes_never_fill_missing_cache(self):
        for price in (
            "Например, " + PRICE,
            "Если НМЦД составляет 4 000 000 рублей, используется данный пример расчета.",
            "К примеру, " + PRICE,
            "Предположим, " + PRICE,
            "Например:\n" + PRICE,
            "Если\nНМЦД составляет 4 000 000 рублей, применяется этот порядок.",
        ):
            with self.subTest(price=price):
                self.assertEqual(prices_in_text(price), [])
                self.assertEqual(security_findings(
                    {**PURCHASE, "price_num": None}, [original(price + "\n" + BID)])[0], [])

    def test_completed_example_does_not_hide_independent_real_nmcd(self):
        example = "Например, НМЦД составляет 8 000 000 рублей."
        for separator in (" ", "\n"):
            with self.subTest(separator=separator):
                docs = [original(example + separator + PRICE + "\n" + BID)]
                self.assert_one({**PURCHASE, "price_num": None}, docs)

    def test_example_table_context_survives_reconstructed_price_pairs(self):
        for table in (
            "| НМЦД | 4 000 000 руб. |",
            "| 1 | НМЦД | 4 000 000 руб. |",
            "| Показатель | Значение |\n| 1 | НМЦД | 4 000 000 руб. |",
            "| 1 | Начальная (максимальная) | 4 000 000 рублей |\n| | цена договора | |",
        ):
            for gap in ("\n", "\n\n"):
                with self.subTest(table=table, gap=gap):
                    body = "Например:" + gap + table + "\n" + BID
                    self.assertEqual(prices_in_text(body), [])
                    self.assertIsNone(verified_price({**PURCHASE, "price_num": None}, [original(body)]))
                    self.assertEqual(security_findings(
                        {**PURCHASE, "price_num": None}, [original(body)])[0], [])

    def test_completed_table_example_does_not_conflict_with_actual_nmcd(self):
        example = "Например:\n| 1 | НМЦД | 8 000 000 рублей |\nПример завершён.\n"
        for actual in (PRICE, "| НМЦД | 4 000 000 руб. |",
                       "| 1 | Начальная (максимальная) | 4 000 000 рублей |\n| | цена договора | |"):
            with self.subTest(actual=actual):
                body = example + actual + "\n" + BID
                self.assertEqual({price[0] for price in prices_in_text(body)}, {"4000000.00"})
                self.assert_one({**PURCHASE, "price_num": None}, [original(body)])

    def test_standalone_example_heading_keeps_scope_despite_terminal_period(self):
        for heading in ("Пример.", "Например.", "Образец."):
            with self.subTest(heading=heading):
                body = heading + "\n| 1 | НМЦД | 4 000 000 рублей |\n" + BID
                self.assertEqual(prices_in_text(body), [])
                self.assertEqual(security_findings(
                    {**PURCHASE, "price_num": None}, [original(body)])[0], [])

    def test_unconditional_wrapped_table_still_recovers_actual_nmcd(self):
        body = ("Информационная карта закупки:\n"
                "| 1 | Начальная (максимальная) | 4 000 000 рублей |\n"
                "| | цена договора | |\n" + BID)
        self.assert_one({**PURCHASE, "price_num": None}, [original(body)])

    def test_missing_price_never_uses_unverified_or_different_price_kind(self):
        p = {**PURCHASE, "price_num": None}
        for doc in (
            original(PRICE, trusted=False),
            original(PRICE, kind="contract"),
            original(PRICE, name="Положение о закупке.docx"),
            original("Максимальное значение цены договора: 4 000 000 рублей."),
            original("Цена единицы товара: 4 000 000 рублей."),
            original("Цена победителя: 4 000 000 рублей."),
        ):
            with self.subTest(doc=doc):
                self.assertEqual(security_findings(p, [doc, original(BID, uid=2)])[0], [])
        for source, kind in (
            ("https://example.org/price", "nmcd"),
            (CARD.replace(REG, "32610000002"), "nmcd"),
            (CARD, "subject_cost"),
        ):
            other = {**p, "verified_price_source":
                     {"kind": kind, "source": source, "price": "4000000.00"}}
            self.assertEqual(security_findings(other, [original(BID)])[0], [])

    def test_long_flattened_line_keeps_explicit_nmcd(self):
        filler = "Порядок поставки и перечень документов изложены в настоящем разделе. " * 70
        for body in (PRICE + " " + filler + BID, filler + PRICE + " " + BID):
            with self.subTest(prefix=body[:40]):
                self.assertGreater(len(body), 2000)
                self.assertTrue(prices_in_text(body))
                self.assert_one(PURCHASE, [original(body)])

    def test_long_line_keeps_conflict_and_conditional_guards(self):
        filler = "Условия поставки указаны в договоре. " * 70
        for price in (
            "Если НМЦД не превышает 4 000 000 рублей, применяется особый порядок.",
            "НМЦД: 4 рабочих дня.",
            "Цена победителя: 4 000 000 рублей.",
            "Максимальное значение цены договора: 4 000 000 рублей.",
        ):
            with self.subTest(price=price):
                self.assertEqual(security_findings(
                    PURCHASE, [original(price + " " + filler + BID)])[0], [])
        conflict = PRICE + " " + filler + "НМЦД: 8 000 000 рублей. " + BID
        self.assertEqual(security_findings(PURCHASE, [original(conflict)])[0], [])

    def test_examples_do_not_consume_actual_requirement_scan(self):
        examples = "Например, обеспечение заявки составляет 100 000 рублей.\n" * 12
        docs = [original(PRICE + "\n" + examples + BID)]
        self.assert_one(PURCHASE, docs)

    def test_many_examples_without_actual_requirement_are_not_violations(self):
        examples = "Например, обеспечение заявки составляет 100 000 рублей.\n" * 12
        self.assertEqual(security_findings(PURCHASE, [original(PRICE + "\n" + examples)])[0], [])
        negative = "Обеспечение заявки составляет 100 000 рублей, но не требуется.\n" * 12
        self.assertEqual(security_findings(PURCHASE, [original(PRICE + "\n" + negative)])[0], [])

    def test_installed_analyzer_recovers_without_mutating_inputs(self):
        p = {**PURCHASE, "price_num": None}
        docs = [original(PRICE + "\n" + BID)]
        before = deepcopy((p, docs))
        ns = {"analyze": lambda p, docs: [], "build_checks": lambda p, detail, card, docs: []}
        install(ns)
        first = ns["analyze"](p, docs)
        self.assertEqual(len(first), 1)
        self.assertEqual(ns["analyze"](p, docs), first)
        self.assertEqual((p, docs), before)

    def test_actual_docx_extraction_preserves_price_in_long_information_cell(self):
        from docx import Document
        from v314_eis_metadata import document_text
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "information.docx"
            package = Document()
            cells = package.add_table(rows=1, cols=2).rows[0].cells
            cells[0].text = "Условия закупки"
            cells[1].text = PRICE + " " + "Порядок поставки. " * 180 + BID
            package.save(path)
            body = document_text(path, lambda _: "")
            self.assertGreater(len(body), 2000)
            doc = original(body)
            doc["meta"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assert_one({**PURCHASE, "price_num": None}, [doc])


if __name__ == "__main__":
    unittest.main()
