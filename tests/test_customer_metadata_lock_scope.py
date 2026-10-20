"""Buyer metadata no-ops stay read-only; real repairs revalidate under a writer."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v299_browser_intake as intake
import v311_customer_backfill as backfill

REG = "32616422163"
TITLE = "Поставка сервера"
NAME = "ООО «Пример»"
INN = "6500000024"


class CustomerMetadataLockScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "metadata.db"
        self.trace = []
        self.connections = []
        with closing(self.connect()) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, title TEXT,
                    customer TEXT, customer_inn TEXT, last_seen TEXT);
                CREATE TABLE v311_verified_buyers(reg TEXT PRIMARY KEY, name TEXT,
                    inn TEXT, evidence_page TEXT, evidence_source TEXT);
                CREATE TABLE findings(id INTEGER PRIMARY KEY, evidence TEXT);
                CREATE TABLE intake(id INTEGER);
                INSERT INTO findings VALUES(1,'preserved legal finding');
            """)
            db.execute("INSERT INTO purchases VALUES(?,?,?,?,?)",
                       (REG, TITLE, NAME, "", "2026-10-20"))
            db.commit()
        self.trace.clear()

    def connect(self, factory=sqlite3.Connection):
        db = sqlite3.connect(self.path, timeout=.04, factory=factory)
        db.set_trace_callback(self.trace.append)
        self.connections.append(db)
        return db

    def value(self, **changes):
        return {"reg_number": REG, "title": TITLE, "customer": NAME,
                "customer_inn": "", **changes}

    def row(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT title,customer,customer_inn FROM purchases "
                              "WHERE reg_number=?", (REG,)).fetchone()

    def assert_closed(self, connections):
        for db in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

    def test_incomplete_row_without_new_evidence_is_read_only(self):
        start = len(self.connections)
        self.assertFalse(intake._apply_customer({"conn": self.connect}, self.value()))
        self.assertFalse(any(sql.startswith(("BEGIN", "UPDATE", "INSERT", "COMMIT"))
                             for sql in self.trace), self.trace)
        self.assert_closed(self.connections[start:])

    def test_missing_row_is_read_only(self):
        self.assertFalse(intake._apply_customer(
            {"conn": self.connect}, self.value(reg_number="32616422164", customer_inn=INN)))
        self.assertFalse(any(sql.startswith(("BEGIN", "UPDATE", "INSERT", "COMMIT"))
                             for sql in self.trace), self.trace)

    def test_noop_succeeds_while_an_unrelated_writer_is_active(self):
        with closing(self.connect()) as holder:
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO intake VALUES(1)")
            self.trace.clear()
            self.assertFalse(intake._apply_customer({"conn": self.connect}, self.value()))
            self.assertFalse(any(sql.startswith("BEGIN") for sql in self.trace), self.trace)
            holder.rollback()
        self.assertEqual(self.row(), (TITLE, NAME, ""))

    def test_real_change_still_reports_busy_without_losing_data(self):
        with closing(self.connect()) as holder:
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO intake VALUES(1)")
            start = len(self.connections)
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                intake._apply_customer({"conn": self.connect}, self.value(customer_inn=INN))
            self.assert_closed(self.connections[start:])
            holder.rollback()
        self.assertEqual(self.row(), (TITLE, NAME, ""))
        self.assertTrue(intake._apply_customer({"conn": self.connect}, self.value(customer_inn=INN)))

    def test_periodic_no_evidence_sweep_does_not_need_writer(self):
        with closing(self.connect()) as holder:
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("INSERT INTO intake VALUES(1)")
            self.trace.clear()
            with patch.object(backfill, "root", return_value=Path(self.tmp.name)), \
                    patch.object(backfill.logger, "exception") as errors:
                result = backfill.reconcile_buyers({"conn": self.connect})
            self.assertEqual(result, {"reviewed": 1, "corrected": 0})
            errors.assert_not_called()
            self.assertFalse(any(sql.startswith("BEGIN") for sql in self.trace), self.trace)
            holder.rollback()

    def test_preflight_does_not_hold_writer_during_planning(self):
        original = intake._customer_changes
        calls = []
        def plan(*args):
            calls.append(args)
            if len(calls) == 1:
                with closing(self.connect()) as other:
                    other.execute("INSERT INTO intake VALUES(1)")
                    other.commit()
            return original(*args)
        with patch.object(intake, "_customer_changes", side_effect=plan):
            self.assertTrue(intake._apply_customer(
                {"conn": self.connect}, self.value(customer_inn=INN)))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.row(), (TITLE, NAME, INN))

    def test_concurrent_complete_correction_is_preserved(self):
        opened = []
        def conn():
            if len(opened) == 1:
                self.assert_closed(opened)
                with closing(self.connect()) as other:
                    other.execute("UPDATE purchases SET customer_inn=?,title=?,customer=?",
                                  ("6501309746", "Поставка реактивов", "ООО «Другой»"))
                    other.commit()
            db = self.connect()
            opened.append(db)
            return db
        self.assertFalse(intake._apply_customer({"conn": conn}, self.value(customer_inn=INN)))
        self.assertEqual(self.row(), ("Поставка реактивов", "ООО «Другой»", "6501309746"))
        self.assert_closed(opened)

    def test_concurrent_partial_correction_replans_remaining_fields(self):
        with closing(self.connect()) as db:
            db.execute("UPDATE purchases SET title='Подача заявок'")
            db.commit()
        opened = []
        def conn():
            if len(opened) == 1:
                with closing(self.connect()) as other:
                    other.execute("UPDATE purchases SET title='Поставка реактивов'")
                    other.commit()
            db = self.connect()
            opened.append(db)
            return db
        self.assertTrue(intake._apply_customer({"conn": conn}, self.value(customer_inn=INN)))
        self.assertEqual(self.row(), ("Поставка реактивов", NAME, INN))

    def test_concurrent_deletion_is_not_recreated(self):
        opened = []
        def conn():
            if len(opened) == 1:
                with closing(self.connect()) as other:
                    other.execute("DELETE FROM purchases")
                    other.commit()
            db = self.connect()
            opened.append(db)
            return db
        self.assertFalse(intake._apply_customer({"conn": conn}, self.value(customer_inn=INN)))
        self.assertIsNone(self.row())

    def test_name_normalization_and_real_repair_are_idempotent(self):
        with closing(self.connect()) as db:
            db.execute("UPDATE purchases SET title=?,customer=?",
                       ("Подача заявок", "Наименование (полное) - ООО «Пример»"))
            db.commit()
        value = self.value(customer_inn=INN)
        self.assertTrue(intake._apply_customer({"conn": self.connect}, value))
        self.assertEqual(self.row(), (TITLE, NAME, INN))
        self.trace.clear()
        self.assertFalse(intake._apply_customer({"conn": self.connect}, value))
        self.assertFalse(any(sql.startswith("BEGIN") for sql in self.trace), self.trace)
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT * FROM findings").fetchall(),
                             [(1, "preserved legal finding")])
            self.assertEqual(db.execute("SELECT * FROM v311_verified_buyers").fetchall(), [])

    def test_missing_identity_fills_but_valid_identity_is_never_overwritten(self):
        with closing(self.connect()) as db:
            db.execute("UPDATE purchases SET title=?,customer=?,customer_inn=?",
                       ("Подача заявок", "Уточняем заказчика", "6500000025"))
            db.commit()
        self.assertTrue(intake._apply_customer({"conn": self.connect}, self.value(customer_inn=INN)))
        self.assertEqual(self.row(), (TITLE, NAME, INN))
        self.assertFalse(intake._apply_customer({"conn": self.connect},
            self.value(title="Поставка реактивов", customer="ООО «Другой»",
                       customer_inn="6501309746")))
        self.assertEqual(self.row(), (TITLE, NAME, INN))

    def test_failure_after_update_rolls_back_and_releases_writer(self):
        class FailAfterUpdate(sqlite3.Connection):
            def execute(self, sql, parameters=(), /):
                result = super().execute(sql, parameters)
                if sql.startswith("UPDATE purchases"):
                    raise RuntimeError("forced post-update failure")
                return result
        start = len(self.connections)
        with self.assertRaisesRegex(RuntimeError, "forced post-update"):
            intake._apply_customer({"conn": lambda: self.connect(FailAfterUpdate)},
                                   self.value(customer_inn=INN))
        self.assert_closed(self.connections[start:])
        self.assertEqual(self.row(), (TITLE, NAME, ""))
        with closing(self.connect()) as other:
            other.execute("INSERT INTO intake VALUES(1)")
            other.commit()


if __name__ == "__main__":
    unittest.main()
