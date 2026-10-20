"""Synthetic typed-price conflicts must never establish a bid-security breach.

These are invented fixtures, not the source documents of any real procurement.
The integration cases authenticate local DOCX bytes and SOAP ZIP/XML hashes
through the existing collectors; they do not change saved historical findings.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v336_legal_matrix as matrix
import v341_eis_facts as facts
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
from test_general_audit_recovery import DatabaseFixture
import test_receipt_original_parity as receipt_fixtures

REG = "32619000001"
CARD = "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=" + REG
PRICE = "Начальная (максимальная) цена договора: 195 167,20 руб."
BID = "Обеспечение заявки составляет 404 000 рублей."
PURCHASE = {"reg": REG, "method": "Запрос предложений в электронной форме",
            "published": "2026-10-19", "price_num": 195167.2}


def original(body):
    return {"name": "Документация.docx", "type": "documentation", "text": body,
            "status": "checked", "meta": {"source": "official_eis_original",
                "source_url": "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "1" * 32,
                "sha256": hashlib.sha256(body.encode()).hexdigest()}}


class TypedPriceGate(unittest.TestCase):
    def test_selected_non_nmcd_kinds_do_not_corroborate_matching_original(self):
        for kind in facts.KINDS:
            if kind == "nmcd":
                continue
            for source in ("selected", "verified_card"):
                with self.subTest(kind=kind, source=source):
                    p = dict(PURCHASE)
                    if source == "selected":
                        p["price_kind"] = kind
                    else:
                        p["verified_price_source"] = {"kind": kind, "price": "195167.20", "source": CARD}
                    docs = [original(PRICE + "\n" + BID)]
                    before = deepcopy((p, docs))
                    self.assertIsNone(matrix.verified_price(p, docs))
                    findings, checks = matrix.security_findings(p, docs)
                    self.assertEqual(findings, [])
                    self.assertTrue(checks)
                    self.assertEqual((p, docs), before)

    def test_explicit_nmcd_and_unknown_legacy_kind_keep_existing_positive(self):
        for kind in (None, "", "nmcd"):
            with self.subTest(kind=kind):
                p = {**PURCHASE, "price_kind": kind}
                found, _ = matrix.security_findings(p, [original(PRICE + "\n" + BID)])
                self.assertEqual([f["code"] for f in found], ["223-3.2-27-BID-SECURITY-LOW-NMCD"])

    def test_foreign_or_unverified_card_does_not_veto_verified_original(self):
        for source in ("https://example.invalid/price", CARD.replace(REG, "32619000002")):
            p = {**PURCHASE, "verified_price_source": {"kind": "unit_price",
                "price": "195167.20", "source": source}}
            found, _ = matrix.security_findings(p, [original(PRICE + "\n" + BID)])
            self.assertEqual(len(found), 1)

    def test_maximum_value_remains_review_not_an_automatic_legal_basis(self):
        p = {**PURCHASE, "price_num": 40400000, "price_kind": "maximum_contract_value"}
        docs = [original("Максимальное значение цены договора: 40 400 000 рублей.\n" + BID)]
        found, checks = matrix.security_findings(p, docs)
        self.assertEqual(found, [])
        self.assertTrue(checks)
        self.assertTrue(all(c["status"] == "check" for c in checks))

    def test_existing_competitive_and_publication_scope_is_unchanged(self):
        for change in ({"method": "Единственный поставщик"}, {"published": "2018-01-01"}):
            found, checks = matrix.security_findings({**PURCHASE, **change}, [original(PRICE + "\n" + BID)])
            self.assertEqual((found, checks), ([], []))


class ReceiptTypedPriceContext(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    soap = receipt_fixtures.SavedCardContext.soap
    stale_purchase = receipt_fixtures.SavedCardContext.stale_purchase
    source = receipt_fixtures.SavedCardContext.source

    def setUp(self):
        super().setUp()
        with self.connect() as con:
            for declaration in ("source TEXT", "url TEXT", "customer_inn TEXT", "price TEXT", "price_num REAL",
                                "submission_start TEXT", "deadline TEXT", "card_fields_json TEXT"):
                con.execute("ALTER TABLE purchases ADD COLUMN " + declaration)
        from v311_verified_customer import _initialize
        _initialize(self.ns)
        facts.initialize(self.ns)
        self.observed = []

        def analyze(p, docs):
            facts.enrich(self.ns, p, docs)
            self.observed.append(deepcopy(p))
            return matrix.security_findings(p, docs)[0]

        def checks(p, detail, card, docs):
            facts.enrich(self.ns, p, docs, card, detail)
            return matrix.security_findings(p, docs)[1]

        self.ns.update(analyze=analyze, build_checks=checks)

    def prepare(self, number, *, xml_price, captured, card_price="195167.20", kind="total_unit_prices"):
        reg = self.purchase(number)
        self.document(reg, PRICE + "\n" + BID)
        with self.connect() as con:
            con.execute("UPDATE docs SET name='Документация.docx' WHERE id=(SELECT MAX(id) FROM docs)")
        self.stale_purchase(reg)
        self.soap(reg, price=xml_price, published="2026-10-19T06:16:00+03:00")
        card = {"reg_number": reg, "source_page": CARD.replace(REG, reg), "captured_at": captured,
                "fields": {}, "prices": facts.validate_prices([{"price": card_price,
                    "price_kind": kind, "price_label": facts.LABELS[kind], "currency": "RUB"}])}
        with self.connect() as con:
            con.execute("INSERT INTO v341_card_facts VALUES(?,?,?,?)",
                        (reg, card["source_page"], captured, json.dumps(card)))
        return reg

    async def test_unordered_typed_card_price_conflicts_even_when_amount_is_equal(self):
        for number, xml_price in enumerate(("195167.20", "40400000.00"), 1):
            with self.subTest(xml_price=xml_price):
                reg = self.prepare(number, xml_price=xml_price, captured="2026-10-19T08:00:00+03:00")
                source = await self.source(reg)
                self.assertFalse(source["source_incomplete"])
                self.assertEqual(source["p"]["_current_soap_context"]["record"]["xml_family"], "notice")
                facts.enrich(self.ns, source["p"], source["docs"], source["card"], source["detail"])
                self.assertIn("price", source["p"]["_official_context_conflicts"])
                self.assertIsNone(source["p"]["price_num"])
                self.assertIsNone(matrix.verified_price(source["p"], source["docs"]))
                self.assertEqual(matrix.security_findings(source["p"], source["docs"])[0], [])

    async def test_imprecise_card_revision_cannot_settle_a_price_kind_conflict(self):
        for number, captured in enumerate(("", "invalid", "2026-10-19", "2026-10-19T05:00:00"), 1):
            with self.subTest(captured=captured):
                reg = self.prepare(number, xml_price="195167.20", captured=captured)
                source = await self.source(reg)
                for _ in range(2):
                    facts.enrich(self.ns, source["p"], source["docs"], source["card"], source["detail"])
                    self.assertIn("price", source["p"]["_official_context_conflicts"])
                    conflict = source["p"]["_official_context_conflicts"]["price"]
                    self.assertEqual({x["price_kind"] for x in conflict["alternatives"]}, {"nmcd", "total_unit_prices"})
                    self.assertIsNone(source["p"]["price_num"])
                    self.assertFalse(source["detail"]["initialSum"])
                    self.assertIsNone(source["card"]["summary"]["price_num"])
                    self.assertEqual(matrix.security_findings(source["p"], source["docs"])[0], [])

    async def test_matching_nmcd_card_and_current_soap_keep_positive(self):
        reg = self.prepare(1, xml_price="195167.20", captured="2026-10-19T08:00:00+03:00", kind="nmcd")
        source = await self.source(reg)
        facts.enrich(self.ns, source["p"], source["docs"], source["card"], source["detail"])
        self.assertEqual(source["p"]["_official_context_conflicts"], {})
        self.assertEqual(len(matrix.security_findings(source["p"], source["docs"])[0]), 1)

    async def test_older_different_kind_at_equal_amount_is_replaced_by_current_nmcd(self):
        reg = self.prepare(1, xml_price="195167.20", captured="2026-10-18T08:00:00+03:00")
        source = await self.source(reg)
        facts.enrich(self.ns, source["p"], source["docs"], source["card"], source["detail"])
        self.assertEqual(source["p"]["price_kind"], "nmcd")
        self.assertEqual(source["p"]["price_num"], 195167.2)
        self.assertEqual(source["p"]["_official_context_conflicts"], {})
        self.assertEqual(len(matrix.security_findings(source["p"], source["docs"])[0]), 1)

    async def test_demonstrably_older_unit_card_cannot_override_current_nmcd(self):
        reg = self.prepare(1, xml_price="40400000.00", captured="2026-10-18T08:00:00+03:00")
        source = await self.source(reg)
        facts.enrich(self.ns, source["p"], source["docs"], source["card"], source["detail"])
        self.assertEqual(source["p"]["price_num"], 40400000)
        self.assertEqual(source["p"]["price_kind"], "nmcd")
        self.assertEqual(source["p"]["_official_context_conflicts"], {})
        # The old low-price original cannot turn the current 40.4m source into
        # a low-NMCD breach, even though the same amount survives in history.
        self.assertEqual(matrix.security_findings(source["p"], source["docs"])[0], [])

    async def test_general_and_thematic_leave_history_and_original_bytes_intact(self):
        reg = self.prepare(1, xml_price="195167.20", captured="2026-10-19T08:00:00+03:00")
        with self.connect() as con:
            con.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) VALUES(?,?,'violation',?,?)",
                        (reg, "HISTORICAL", "Preserved earlier event", "Original stored text"))
            before = tuple(con.execute("SELECT * FROM findings WHERE reg_number=?", (reg,)).fetchone())
            card_before = list(map(tuple, con.execute("SELECT * FROM v341_card_facts")))
        files = {p: p.read_bytes() for p in self.folder.rglob("*") if p.suffix in (".zip", ".json", ".docx")}
        status, detail = await general.inspect_one(self.ns, reg)
        self.assertEqual(status, "completed")
        self.assertEqual(detail["new_findings"], 0)
        selected = await thematic.independent_review(self.ns, reg, ["security"])
        self.assertEqual(selected[1], [])
        with self.connect() as con:
            self.assertEqual(list(map(tuple, con.execute("SELECT * FROM findings WHERE reg_number=?", (reg,)))), [before])
            self.assertEqual(list(map(tuple, con.execute("SELECT * FROM v341_card_facts"))), card_before)
        self.assertEqual({p: p.read_bytes() for p in files}, files)


if __name__ == "__main__":
    unittest.main()
