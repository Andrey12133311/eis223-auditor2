"""Each independently evidenced lot keeps its own bid-security incident.

All purchases are synthetic. Saved-path cases use real DOCX bytes, original
receipts, the stored-document loader, both audit paths and dashboard counts.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bid_security_requirement_recall import original, PURCHASE, LOW, HIGH
from test_general_audit_recovery import DatabaseFixture
import v336_legal_matrix as matrix
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import vp_inspection_engine as inspection
from v352_dashboard_truth import load_counts


def lot_documents(lots=((1, 4000000, 100000), (2, 8000000, 600000))):
    documents = []
    for lot, price, amount in lots:
        documents.extend((
            original(f"НМЦД: {price} рублей.", "Извещение.docx", lot * 10,
                     lot_number=str(lot)),
            original(f"Обеспечение заявки: {amount} рублей.", "Документация.docx", lot * 10 + 1,
                     lot_number=str(lot))))
    return documents


class DistinctSecurityLots(unittest.TestCase):
    purchase = {**PURCHASE, "price_num": None}

    def analyze(self, docs, purchase=None):
        p = self.purchase if purchase is None else purchase
        snapshot = deepcopy((p, docs))
        findings, checks = matrix.security_findings(p, docs)
        result = inspection.qualify_outcomes(p, docs, findings, checks)
        self.assertEqual((p, docs), snapshot)
        return result

    def test_two_lots_can_violate_different_existing_rules(self):
        findings, _ = self.analyze(lot_documents())
        self.assertEqual([item["code"] for item in findings], [LOW, HIGH])
        self.assertEqual([item["security_assessment"]["source_scope"]["lots"]
                          for item in findings], [["1"], ["2"]])

    def test_equal_amounts_and_rule_do_not_discard_a_second_bound_lot(self):
        docs = lot_documents(((1, 4000000, 100000), (2, 4000000, 100000)))
        findings, _ = self.analyze(docs)
        self.assertEqual([item["code"] for item in findings], [LOW, LOW])
        self.assertNotEqual(findings[0]["evidence"], findings[1]["evidence"])

    def test_copies_of_each_same_requirement_do_not_add_incidents(self):
        docs = lot_documents()
        findings, _ = self.analyze(docs + deepcopy(docs))
        self.assertEqual([item["code"] for item in findings], [LOW, HIGH])

    def test_repeated_equal_clauses_of_one_lot_remain_one_incident(self):
        docs = lot_documents(((1, 4000000, 100000),))
        docs[1] = original("Обеспечение заявки: 100000 рублей.\nОбеспечение заявки: 100000 рублей.",
                           "Документация.docx", 11, lot_number="1")
        findings, _ = self.analyze(docs)
        self.assertEqual([item["code"] for item in findings], [LOW])

    def test_exceeding_prepared_clause_inventory_does_not_claim_complete_proof(self):
        for count, expected in ((100, 1), (101, 0)):
            with self.subTest(count=count):
                docs = lot_documents(((1, 4000000, 100000),))
                docs[1] = original("Обеспечение заявки: 100000 рублей.\n" * count,
                                   "Документация.docx", 11, lot_number="1")
                findings, checks = self.analyze(docs)
                self.assertEqual(len(findings), expected)
                if not expected:
                    self.assertTrue(any("security_collection_limited" in
                        item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_selected_lot_does_not_borrow_another_lots_price_or_requirement(self):
        for lot, expected in (("1", LOW), ("2", HIGH)):
            with self.subTest(lot=lot):
                findings, _ = self.analyze(lot_documents(), {**self.purchase, "lot_number": lot})
                self.assertEqual([item["code"] for item in findings], [expected])

    def test_later_unverified_price_remains_review_after_a_confirmed_lot(self):
        docs = lot_documents()
        docs[2]["meta"]["source"] = "local_unverified"
        findings, checks = self.analyze(docs)
        self.assertEqual([item["code"] for item in findings], [LOW])
        self.assertTrue(any("security_nmcd_unverified" in
                            item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_conflicting_second_lot_cannot_use_first_lots_confirmation(self):
        docs = lot_documents()
        docs.append(original("Обеспечение заявки не требуется.", "Извещение 2.docx", 99,
                             lot_number="2"))
        findings, checks = self.analyze(docs)
        self.assertEqual([item["code"] for item in findings], [LOW])
        self.assertTrue(any("security_conflicting_terms" in
                            item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_inconsistent_number_and_guid_pairs_are_not_distinct_proven_lots(self):
        for mode in ("shared_guid", "shared_number", "shared_receipt"):
            with self.subTest(mode=mode):
                docs = lot_documents(((1, 4000000, 100000), (2, 4000000, 100000)))
                for index, doc in enumerate(docs):
                    doc["meta"]["lot_id"] = (f"lot-guid-{index // 2}" if mode == "shared_number"
                                              else "one-guid")
                    if mode == "shared_number":
                        doc["meta"]["lot_number"] = "1"
                    if mode == "shared_receipt" and index >= 2:
                        doc["meta"]["source_url"] = docs[index-2]["meta"]["source_url"]
                findings, checks = self.analyze(docs)
                self.assertEqual(findings, [])
                self.assertTrue(any("security_lot_identity_conflict" in
                    item.get("security_assessment", {}).get("reasons", []) for item in checks))

    def test_unrepresentable_explicit_lot_cannot_become_unscoped_confirmation(self):
        docs = lot_documents()
        for doc in docs[2:]:
            doc["meta"]["lot_number"] = "7" * 121
        findings, checks = self.analyze(docs)
        self.assertEqual([item["code"] for item in findings], [LOW])
        self.assertTrue(any("security_lot_identity_unavailable" in
            item.get("security_assessment", {}).get("reasons", []) for item in checks))


class SavedDistinctSecurityLots(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def seed(self, lots):
        with self.connect() as db:
            db.execute("ALTER TABLE purchases ADD COLUMN price TEXT")
            db.execute("ALTER TABLE purchases ADD COLUMN price_num REAL")
            db.execute("ALTER TABLE purchases ADD COLUMN card_fields_json TEXT")
        reg = None
        for lot, price, amount in lots:
            body = (f"Лот №{lot}. НМЦД: {price} рублей. "
                    f"Обеспечение заявки: {amount} рублей.")
            if reg is None:
                reg = self.purchase(text=body)
            else:
                self.document(reg, body)
        with self.connect() as db:
            for lot, row in zip((entry[0] for entry in lots), db.execute("SELECT * FROM docs ORDER BY id")):
                metadata = json.loads(row["metadata_json"])
                metadata["lot_number"] = str(lot)
                db.execute("UPDATE docs SET name=?,doc_type='documentation',metadata_json=? WHERE id=?",
                           (f"Документация лот {lot}.docx", json.dumps(metadata), row["id"]))
        manifest_path = self.folder / "eis223_public_browser_intake" / reg / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for lot, doc in zip((entry[0] for entry in lots), manifest["documents"]):
            doc["name"] = f"Документация лот {lot}.docx"
        manifest_path.write_text(json.dumps(manifest))
        matrix.install(self.ns)
        inspection.install(self.ns)
        return reg

    async def check_paths(self, lots, expected_codes):
        reg = self.seed(lots)
        before = {path: path.read_bytes() for path in self.contents}
        first = await general.inspect_one(self.ns, reg)
        repeated = await general.inspect_one(self.ns, reg)
        selected = await thematic.independent_review(self.ns, reg, ["bid_security"])
        self.assertEqual((first[0], first[1]["new_findings"]), ("completed", 2))
        self.assertEqual((repeated[0], repeated[1]["new_findings"]), ("completed", 0))
        self.assertEqual((selected[0], len(selected[1])), ("completed", 2))
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM findings ORDER BY id")]
            self.assertEqual([row["code"] for row in rows], expected_codes)
            self.assertEqual(load_counts(db, [reg]), {reg: 2})
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    async def test_distinct_rules_survive_real_saved_audits_and_replay(self):
        await self.check_paths(((1, 4000000, 100000), (2, 8000000, 600000)), [LOW, HIGH])

    async def test_equal_clauses_keep_two_bound_lots_in_audits_and_counter(self):
        await self.check_paths(((1, 4000000, 100000), (2, 4000000, 100000)), [LOW, LOW])


if __name__ == "__main__":
    unittest.main()
