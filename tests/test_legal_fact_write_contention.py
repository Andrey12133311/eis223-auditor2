"""Actual WAL writer holds, cache replay, concurrent intake and lease fencing."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v341_eis_facts as facts
import v335_price_recovery as prices
from audit_write_guard import background_write_guard

REG = "32616233661"


class LegalFactWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audit.sqlite"
        self.trace = []
        self.ns = {"conn": self.connect}
        self.purchase = {"reg": REG, "method": "Аукцион", "submission_end": "2026-10-30",
            "fact_evidence": {"method": {"source": "official_eis_card", "value": "Аукцион"},
                "submission_end": {"source": "official_eis_card", "value": "2026-10-30"}}}
        with closing(self.connect()) as c:
            c.executescript("""PRAGMA journal_mode=WAL;
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, method TEXT,
                    submission_end TEXT, deadline TEXT, card_fields_json TEXT,
                    price TEXT, price_num REAL);
                CREATE TABLE findings(reg_number TEXT,evidence TEXT);
                CREATE TABLE checks(reg_number TEXT,item TEXT,status TEXT,evidence TEXT);
                INSERT INTO findings VALUES('32616233661','retained verified event');
            """)
            c.execute("INSERT INTO purchases VALUES(?,NULL,NULL,NULL,?,NULL,NULL)",
                      (REG, json.dumps({"keep": "original evidence"})))
            c.commit()

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.03)
        c.row_factory = sqlite3.Row
        c.set_trace_callback(self.trace.append)
        return c

    def row(self):
        with closing(self.connect()) as c:
            return dict(c.execute("SELECT * FROM purchases").fetchone())

    def hold_writer(self):
        c = self.connect()
        c.execute("BEGIN IMMEDIATE")
        self.addCleanup(c.close)
        return c

    def test_identical_facts_finish_under_unrelated_writer_without_write_attempt(self):
        facts.persist_facts(self.ns, self.purchase)
        before = self.row()
        writer = self.hold_writer()
        self.trace.clear()
        facts.persist_facts(self.ns, self.purchase)
        self.assertFalse(any("BEGIN IMMEDIATE" in sql.upper() for sql in self.trace))
        self.assertEqual(self.row(), before)
        writer.rollback()
        with closing(self.connect()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 1)

    def test_real_change_waits_for_writer_and_can_be_retried(self):
        writer = self.hold_writer()
        with self.assertRaises(sqlite3.OperationalError):
            facts.persist_facts(self.ns, self.purchase)
        self.assertIsNone(self.row()["method"])
        writer.rollback()
        facts.persist_facts(self.ns, self.purchase)
        self.assertEqual(self.row()["method"], "Аукцион")

    def test_changed_deadline_cache_is_repaired_when_submission_end_already_matches(self):
        facts.persist_facts(self.ns, self.purchase)
        with closing(self.connect()) as c:
            c.execute("UPDATE purchases SET deadline='2026-10-01'")
            c.commit()
        facts.persist_facts(self.ns, self.purchase)
        self.assertEqual(self.row()["deadline"], "2026-10-30")

    def test_concurrent_intake_cache_is_rebased_and_preserved(self):
        actual = facts._fact_updates
        calls = []
        def plan(row, purchase, evidence):
            result = actual(row, purchase, evidence)
            calls.append(1)
            if len(calls) == 1:
                with closing(self.connect()) as c:
                    c.execute("UPDATE purchases SET card_fields_json=?",
                        (json.dumps({"keep": "new authoritative intake"}),))
                    c.commit()
            return result
        with patch.object(facts, "_fact_updates", side_effect=plan):
            facts.persist_facts(self.ns, self.purchase)
        self.assertGreaterEqual(len(calls), 2)
        card = json.loads(self.row()["card_fields_json"])
        self.assertEqual(card["keep"], "new authoritative intake")
        self.assertEqual(card["summary"]["method"], "Аукцион")

    def test_real_change_validates_exact_lease_inside_writer_transaction(self):
        observed = []
        def stale(c):
            observed.append(c.in_transaction)
            raise LookupError("superseded")
        with background_write_guard(stale), self.assertRaisesRegex(LookupError, "superseded"):
            facts.persist_facts(self.ns, self.purchase)
        self.assertEqual(observed, [True])
        self.assertIsNone(self.row()["method"])

    def test_xml_provenance_is_retained_without_overwriting_intake_columns(self):
        purchase = deepcopy(self.purchase)
        for item in purchase["fact_evidence"].values():
            item["source"] = "official_eis_xml"
        facts.persist_facts(self.ns, purchase)
        row = self.row()
        self.assertIsNone(row["method"])
        self.assertEqual(json.loads(row["card_fields_json"])["fact_evidence"], purchase["fact_evidence"])
        writer = self.hold_writer()
        facts.persist_facts(self.ns, purchase)
        writer.rollback()

    def test_malformed_derived_cache_fields_do_not_abort_legal_processing(self):
        with closing(self.connect()) as c:
            c.execute("UPDATE purchases SET card_fields_json=?",
                      ('{"fact_evidence":null,"summary":[],"keep":"source"}',))
            c.commit()
        facts.persist_facts(self.ns, self.purchase)
        self.assertEqual(json.loads(self.row()["card_fields_json"])["keep"], "source")

    def test_satisfied_price_cache_sweep_never_reserves_writer(self):
        with closing(self.connect()) as c:
            c.execute("UPDATE purchases SET price='100.00',price_num=100")
            c.commit()
        writer = self.hold_writer()
        self.trace.clear()
        self.assertEqual(prices.repair_cached(self.ns), 0)
        self.assertFalse(any("BEGIN IMMEDIATE" in sql.upper() for sql in self.trace))
        writer.rollback()

    def test_price_stage_retry_does_not_refetch_source(self):
        actual = prices.update_price
        calls = []
        def apply(c, reg, evidence):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return actual(c, reg, evidence)
        with patch.object(prices, "read_verified_price", return_value={"price": "100.00"}) as source, \
                patch.object(prices, "update_price", side_effect=apply):
            self.assertTrue(prices.recover_one(self.ns, REG))
        source.assert_called_once()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.row()["price_num"], 100)

    def test_pending_price_fact_check_is_still_repaired(self):
        with closing(self.connect()) as c:
            c.execute("UPDATE purchases SET price='100.00',price_num=100")
            c.execute("INSERT INTO checks VALUES(?,'НМЦД','check','pending')", (REG,))
            c.commit()
        with patch.object(prices, "read_verified_price", return_value={"price": "100.00"}):
            self.assertFalse(prices.recover_one(self.ns, REG))
        with closing(self.connect()) as c:
            self.assertEqual(c.execute("SELECT status FROM checks").fetchone()[0], "ok")

    def test_invalid_price_evidence_cannot_mark_missing_nmcd_as_ok(self):
        with closing(self.connect()) as c:
            c.execute("INSERT INTO checks VALUES(?,'НМЦД','check','pending')", (REG,))
            c.commit()
        with closing(self.connect()) as c:
            prices.update_price(c, REG, {"price": "не определена"})
            c.commit()
            self.assertEqual(c.execute("SELECT status FROM checks").fetchone()[0], "check")


if __name__ == "__main__":
    unittest.main()
