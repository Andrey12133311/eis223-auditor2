"""Cached price sweeps release SQLite between purchases and reject stale evidence."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v335_price_recovery as recovery


class PriceRecoveryLockScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "prices.db"
        c = self.connect()
        c.executescript('''
            CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, price TEXT,
                price_num REAL, card_fields_json TEXT);
            CREATE TABLE v322_verified_prices(reg TEXT PRIMARY KEY,price TEXT);
            CREATE TABLE intake(id INTEGER);
            INSERT INTO purchases VALUES('1','100',NULL,'{}'),('2','200',NULL,'{}');
        ''')
        c.commit()
        c.close()

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.05)
        c.row_factory = sqlite3.Row
        return c

    def test_other_writer_can_commit_between_repairs(self):
        original = recovery.cached_evidence
        def evidence(row, verified):
            c = self.connect()
            try:
                c.execute("INSERT INTO intake VALUES(1)")
                c.commit()
            finally:
                c.close()
            return original(row, verified)
        with patch.object(recovery, "cached_evidence", side_effect=evidence):
            self.assertEqual(recovery.repair_cached({"conn": self.connect}), 2)
        c = self.connect()
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM intake").fetchone()[0], 2)
            self.assertEqual([r[0] for r in c.execute("SELECT price_num FROM purchases ORDER BY reg_number")], [100, 200])
        finally:
            c.close()

    def test_concurrent_metadata_is_preserved(self):
        original = recovery.cached_evidence
        def evidence(row, verified):
            result = original(row, verified)
            if row["reg_number"] == "1":
                c = self.connect()
                c.execute("UPDATE purchases SET card_fields_json=? WHERE reg_number='1'",
                          (json.dumps({"new_official_metadata": True}),))
                c.commit()
                c.close()
            return result
        with patch.object(recovery, "cached_evidence", side_effect=evidence):
            self.assertEqual(recovery.repair_cached({"conn": self.connect}), 1)
        c = self.connect()
        try:
            row = c.execute("SELECT * FROM purchases WHERE reg_number='1'").fetchone()
            self.assertIsNone(row["price_num"])
            self.assertEqual(json.loads(row["card_fields_json"]), {"new_official_metadata": True})
        finally:
            c.close()
        self.assertEqual(recovery.repair_cached({"conn": self.connect}), 1)

    def test_concurrent_price_evidence_is_reassessed_next_sweep(self):
        original = recovery.cached_evidence
        def evidence(row, verified):
            result = original(row, verified)
            if row["reg_number"] == "1":
                c = self.connect()
                c.execute("INSERT INTO v322_verified_prices VALUES('1','300')")
                c.commit()
                c.close()
            return result
        with patch.object(recovery, "cached_evidence", side_effect=evidence):
            self.assertEqual(recovery.repair_cached({"conn": self.connect}), 1)
        c = self.connect()
        try:
            self.assertIsNone(c.execute("SELECT price_num FROM purchases WHERE reg_number='1'").fetchone()[0])
        finally:
            c.close()

    def test_failed_update_rolls_back_only_current_purchase(self):
        original = recovery.update_price
        def update(db, reg, evidence):
            result = original(db, reg, evidence)
            if reg == "2":
                raise RuntimeError("forced failure")
            return result
        with patch.object(recovery, "update_price", side_effect=update):
            with self.assertRaisesRegex(RuntimeError, "forced failure"):
                recovery.repair_cached({"conn": self.connect})
        c = self.connect()
        try:
            self.assertEqual([r[0] for r in c.execute("SELECT price_num FROM purchases ORDER BY reg_number")], [100, None])
            c.execute("INSERT INTO intake VALUES(1)")
            c.commit()
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
