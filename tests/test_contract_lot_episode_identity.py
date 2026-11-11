"""Source-bound contract conditions retain the incidents of independent lots."""
from copy import deepcopy
from itertools import permutations
import json
import unittest

from test_contract_conclusion_terms import P, clause, original
import contract_conclusion_terms as contract
import v333_dedup_ui as dedup
import v352_dashboard_truth as truth
import v353_episode_guard as episodes
import security_episode_identity as identity
import vp_inspection_engine as inspection
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
from test_general_audit_recovery import DatabaseFixture


def confirmed(lot, *, uid="A", long_url=False):
    doc = original(f"Лот № {lot}.\n"+clause(), uid=uid, lot_number=str(lot))
    if long_url:
        doc["meta"]["source_url"] += "&receipt_context="+"r"*2200
        doc["url"] = doc["meta"]["source_url"]
    findings, checks = contract.evaluate(P, [doc])
    if len(findings) != 1 or checks:
        raise AssertionError((findings, checks))
    return findings[0]


def unscoped(item):
    result = deepcopy(item)
    result["evidence"] = result["evidence"].split("\nОбласть условия заключения договора:", 1)[0]
    result.pop("contract_conclusion_assessment", None)
    return result


class ContractLotEpisodeIdentity(unittest.TestCase):
    def test_distinct_lots_are_separate_in_grouping_and_saved_episode_guard(self):
        first, second = confirmed(1), confirmed(2, uid="B")
        self.assertFalse(episodes.same_episode(first, second))
        self.assertTrue(episodes.distinct_new(second, [first]))
        self.assertEqual(len(truth.collapse_legal_events([first, second])), 2)

    def test_copies_of_one_lot_remain_one_incident(self):
        first, copy = confirmed(1), confirmed(1, uid="B")
        self.assertEqual(len(truth.collapse_legal_events([first, copy])), 1)
        self.assertTrue(episodes.same_episode(first, copy))
        self.assertTrue(dedup._same_finding(first, copy, "title"))
        self.assertEqual(1, len(dedup.collapse([first, copy])))

    def test_unknown_scope_cannot_bridge_two_established_lots(self):
        first, second = confirmed(1), confirmed(2, uid="B")
        unknown = unscoped(first)
        unknown["evidence"] += " Дополнительные реквизиты источника."*150
        for ordered in permutations((unknown, first, second)):
            with self.subTest(order=[item["source_url"] for item in ordered]):
                self.assertEqual(len(truth.collapse_legal_events(ordered)), 2)
                self.assertTrue(all(identity.finding_contract_scope(item)
                                    for item in truth.collapse_legal_events(ordered)))
        self.assertTrue(episodes.distinct_new(second, [unknown, first]))
        self.assertFalse(episodes.distinct_new(first, [unknown, first]))

    def test_arbitrary_lot_flags_do_not_establish_another_incident(self):
        first = unscoped(confirmed(1))
        other = {**deepcopy(first), "lot_number": "2", "lot_id": "unknown"}
        self.assertEqual(len(truth.collapse_legal_events([first, other])), 1)
        self.assertTrue(episodes.same_episode(first, other))

    def test_long_official_url_retains_lot_identity_and_same_lot_copies(self):
        first = confirmed(1, long_url=True)
        second = confirmed(2, uid="B", long_url=True)
        self.assertGreater(len(first["source_url"]), 2000)
        self.assertIsNotNone(identity.finding_contract_scope(first))
        self.assertIsNotNone(identity.finding_contract_scope(second))
        self.assertEqual(2, len(truth.collapse_legal_events([first, second])))
        self.assertEqual(2, len(truth.collapse_legal_events([first, second, deepcopy(first)])))
        self.assertEqual(1, len(truth.collapse_legal_events([first, confirmed(1, uid="B", long_url=True)])))

    def test_malformed_or_changed_annotation_cannot_invent_a_second_lot(self):
        first = confirmed(1)
        head, raw = first["evidence"].rsplit("\n" + identity.CONTRACT_PREFIX, 1)
        proof = json.loads(raw[:-1])
        for field, value in (("lots", ["2"]), ("sha256", "f" * 64),
                             ("source_url_sha256", "e" * 64), ("span", [0, 1]),
                             ("quote", proof["quote"].replace("5 дней", "6 дней"))):
            with self.subTest(field=field):
                forged = {**deepcopy(first), "evidence": head + "\n" + identity.CONTRACT_PREFIX +
                    json.dumps({**proof, field: value}, ensure_ascii=False) + "."}
                self.assertIsNone(identity.finding_contract_scope(forged))
                self.assertEqual(1, len(truth.collapse_legal_events([first, forged])))
        repeated = {**deepcopy(first), "evidence": first["evidence"] + "\n" + first["evidence"].splitlines()[-1]}
        self.assertIsNone(identity.finding_contract_scope(repeated))
        self.assertEqual(1, len(truth.collapse_legal_events([first, repeated])))

    def test_richer_unknown_source_cannot_replace_the_scoped_representative(self):
        first = confirmed(1)
        unknown = unscoped(first)
        unknown["evidence"] += " Дополнительные реквизиты источника." * 150
        for ordered in ((first, unknown), (unknown, first)):
            values = dedup.collapse(ordered)
            self.assertEqual(1, len(values))
            self.assertIsNotNone(identity.finding_contract_scope(values[0]))

    def test_current_qualification_rejects_missing_or_damaged_lot_identity(self):
        doc = original("Лот № 1.\n" + clause(), lot_number="1")
        first = confirmed(1)
        current, _ = inspection.qualify_outcomes(P, [doc], [first])
        self.assertEqual(1, len(current))
        head, raw = first["evidence"].rsplit("\n" + identity.CONTRACT_PREFIX, 1)
        proof = json.loads(raw[:-1])
        changed = {**deepcopy(first), "evidence": head + "\n" + identity.CONTRACT_PREFIX +
                   json.dumps({**proof, "lots": ["2"]}, ensure_ascii=False) + "."}
        repeated = {**deepcopy(first), "evidence": first["evidence"] + "\n" + first["evidence"].splitlines()[-1]}
        missing = {**deepcopy(first), "evidence": head}
        for candidate in (changed, repeated, missing):
            with self.subTest(evidence=candidate["evidence"]):
                current, reviews = inspection.qualify_outcomes(P, [doc], [candidate])
                self.assertEqual([], current)
                self.assertEqual(1, len(reviews))
                self.assertIn("contract_term_scope_identity_unverified", reviews[0]["methodology_reason_codes"])


class SavedContractLotConditions(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def seed(self, lots):
        with self.connect() as db:
            db.execute("ALTER TABLE purchases ADD COLUMN price TEXT")
            db.execute("ALTER TABLE purchases ADD COLUMN price_num REAL")
            db.execute("ALTER TABLE purchases ADD COLUMN card_fields_json TEXT")
        reg = None
        for lot in lots:
            text = f"Лот №{lot}.\n" + clause()
            if reg is None:
                reg = self.purchase(text=text)
            else:
                self.document(reg, text)
        with self.connect() as db:
            for index, (lot, saved) in enumerate(zip(lots, db.execute("SELECT * FROM docs ORDER BY id"))):
                metadata = json.loads(saved["metadata_json"])
                metadata["lot_number"] = str(lot)
                db.execute("UPDATE docs SET name=?,doc_type='documentation',metadata_json=? WHERE id=?",
                           (f"Документация лот {lot} источник {index+1}.docx", json.dumps(metadata), saved["id"]))
        path = self.folder / "eis223_public_browser_intake" / reg / "manifest.json"
        manifest = json.loads(path.read_text())
        for index, (lot, entry) in enumerate(zip(lots, manifest["documents"])):
            entry["name"] = f"Документация лот {lot} источник {index+1}.docx"
        path.write_text(json.dumps(manifest))
        contract.install(self.ns)
        inspection.install(self.ns)
        return reg

    async def check_paths(self, lots, count):
        reg = self.seed(lots)
        before = {path: path.read_bytes() for path in self.contents}
        first = await general.inspect_one(self.ns, reg)
        repeated = await general.inspect_one(self.ns, reg)
        selected = await thematic.independent_review(self.ns, reg, ["contract_signing"])
        self.assertEqual(("completed", count), (first[0], first[1]["new_findings"]))
        self.assertEqual(("completed", 0), (repeated[0], repeated[1]["new_findings"]))
        self.assertEqual(("completed", count), (selected[0], len(selected[1])))
        self.assertTrue(all(item["code"] == contract.CODE for item in selected[1]))
        self.assertTrue(all(identity.finding_contract_scope(item) for item in selected[1]))
        with self.connect() as db:
            saved = [dict(row) for row in db.execute("SELECT * FROM findings ORDER BY id")]
            self.assertEqual(count, len(saved))
            self.assertEqual({reg: count}, truth.load_counts(db, [reg]))
        self.assertEqual(before, {path: path.read_bytes() for path in self.contents})

    async def test_distinct_lots_survive_original_ingress_both_audits_and_saved_counter(self):
        await self.check_paths((1, 2), 2)

    async def test_copies_of_one_lot_survive_ingress_as_one_current_incident(self):
        await self.check_paths((1, 1), 1)


if __name__ == "__main__":
    unittest.main()
