"""Regression tests: approved-registration delivery and real thematic inserts."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.account_notifications_v1 import enqueue_access, dispatch_pending, send_smtp
from scripts.v373_thematic_email_notifications import install_capture, poll


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakePG:
    def __init__(self):
        self.user_id = uuid4()
        self.seen = set()
        self.queued = []
        self.calls = []
        self.batch = "a" * 24
    def __call__(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, args):
        self.calls.append((sql, args))
        if "SELECT b.batch_id,b.user_id" in sql:
            return Cursor([(self.batch, self.user_id)])
        if "INSERT INTO account_thematic_email_seen" in sql:
            key = tuple(args)
            if key in self.seen:
                return Cursor([])
            self.seen.add(key)
            return Cursor([(args[-1],)])
        if "INSERT INTO account_mail_outbox" in sql:
            self.queued.append(args)
        return Cursor([])


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_path = os.path.join(self.temp.name, "eis.sqlite")
        def con():
            return sqlite3.connect(db_path)
        self.ns = {"conn": con}
        # Trigger target exists in the production v342 queue before mail setup.
        c = con()
        c.execute("CREATE TABLE thematic_findings("
                  "batch_id TEXT,reg TEXT,category TEXT,title TEXT,"
                  "law TEXT,evidence TEXT,"
                  "UNIQUE(batch_id,reg,category,title,evidence))")
        c.commit()
        c.close()

    def _finding(self, evidence="Подтверждено оригиналом"):
        c = self.ns["conn"]()
        c.execute("INSERT INTO thematic_findings VALUES(?,?,?,?,?,?)",
                  ("a" * 24,"32616412317","deadlines",
                   "Нарушение сроков","ч. 11 ст. 4 223-ФЗ", evidence))
        c.commit()
        c.close()

    def _events(self):
        c = self.ns["conn"]()
        try:
            return c.execute("SELECT COUNT(*) FROM thematic_mail_events").fetchone()[0]
        finally:
            c.close()

    def test_old_findings_not_backfilled(self):
        self._finding()
        install_capture(self.ns)
        pg = FakePG()
        self.assertEqual(poll(self.ns, pg), 0)
        self.assertFalse(pg.queued)

    def test_insert_is_captured_and_queued_once(self):
        install_capture(self.ns)
        self._finding()
        self.assertEqual(self._events(), 1)
        pg = FakePG()
        self.assertEqual(poll(self.ns, pg), 1)
        self.assertEqual(len(pg.queued), 1)
        self.assertEqual(self._events(), 0)
        # Near-identical repeated record is not mailed again.
        self._finding("  Подтверждено   оригиналом  ")
        self.assertEqual(poll(self.ns, pg), 0)
        self.assertEqual(len(pg.queued), 1)

    def test_database_unavailable_keeps_journal(self):
        install_capture(self.ns)
        self._finding()
        with self.assertRaises(RuntimeError):
            poll(self.ns, lambda: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(self._events(), 1)

    def test_approval_queues_not_sends_and_uses_account_id(self):
        pg = FakePG()
        with patch.dict(os.environ, {"EIS223_PUBLIC_ORIGIN":"https://app.example.org"}):
            enqueue_access(pg, pg.user_id, "approved")
        self.assertEqual(len(pg.queued), 1)
        sql, args = pg.calls[-1]
        self.assertIn("SELECT id", sql)
        self.assertIn("status='approved'", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertEqual(args[-1], pg.user_id)
        self.assertIn("https://app.example.org/account/login", args[2])

    def test_smtp_unconfigured_preserves_outbox(self):
        pg = FakePG()
        with patch.dict(os.environ, {key: "" for key in (
            "EIS223_SMTP_HOST","EIS223_SMTP_USER","EIS223_SMTP_PASSWORD",
            "EIS223_SMTP_FROM"
        )}):
            self.assertEqual(dispatch_pending(pg), 0)
        self.assertFalse(pg.calls)

    def test_email_address_is_from_db_and_smtp_is_tls(self):
        params = {"EIS223_SMTP_HOST":"smtp.example.org",
                  "EIS223_SMTP_PORT":"465",
                  "EIS223_SMTP_USER":"sender",
                  "EIS223_SMTP_PASSWORD":"fake-secret",
                  "EIS223_SMTP_FROM":"service@example.org"}
        with patch.dict(os.environ, params), patch(
            "scripts.account_notifications_v1.smtplib.SMTP_SSL"
        ) as smtp:
            send_smtp("registered@example.org","Нарушение","Подробности в кабинете")
        self.assertEqual(smtp.call_args.args[:2], ("smtp.example.org",465))
        msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(msg["To"], "registered@example.org")
        self.assertEqual(msg["From"], "service@example.org")


if __name__ == "__main__":
    unittest.main()
