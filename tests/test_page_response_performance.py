"""Exact dashboard reuse and responsive card reads, without live data."""
import asyncio
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v343_fast_cards as cards
import v352_dashboard_truth as truth


class DashboardCountReuseTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, last_seen TEXT,
            customer TEXT, violations INTEGER, risks INTEGER,
            documents_found INTEGER, documents_checked INTEGER);
          CREATE TABLE findings(reg_number TEXT, title TEXT, law TEXT,
            evidence TEXT, document TEXT, source_url TEXT, code TEXT, kind TEXT);
          CREATE TABLE checks(reg_number TEXT, item TEXT, law TEXT,
            evidence TEXT, document TEXT, source_url TEXT, status TEXT);
        """)
        self.regs = ["32610000001", "32610000002", "32610000003"]
        for i, reg in enumerate(self.regs):
            self.db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?)",
                            (reg, str(i), "АО Тест", 7, i, 2, 2))
            values = (reg, "Срок оплаты МСП превышен", "ПП РФ № 1352 п. 28",
                      self.proof(15), "Договор.pdf", "https://zakupki.gov.ru/" + reg)
            self.db.execute("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)",
                            (*values, "1352-28", "violation"))
            self.db.execute("INSERT INTO checks VALUES(?,?,?,?,?,?,?)",
                            (*values, "violation"))
        self.db.commit()
        with truth._COUNT_CACHE_LOCK:
            truth._COUNT_CACHE.clear()

    @staticmethod
    def proof(days):
        return ("Порядок оплаты: расчет производится в течение " + str(days) +
                " рабочих дней с даты подписания документов о приемке продукции заказчиком.")

    def test_full_filter_summary_stays_exact_across_pagination_and_filters(self):
        changes = self.db.total_changes
        with patch.object(truth, "collapse_legal_events", wraps=truth.collapse_legal_events) as reconcile:
            stats, rows = truth.dashboard_counts(self.db, " FROM purchases p WHERE 1=1", [], 0, 1)
            self.assertEqual((stats["purchases"], stats["violations"]), (3, 3))
            self.assertEqual(stats["legacy_violations_cache"], 21)
            self.assertEqual((len(rows), rows[0]["violations"]), (1, 1))
            self.assertEqual(reconcile.call_count, 3)
            stats2, rows2 = truth.dashboard_counts(self.db, " FROM purchases p WHERE 1=1", [], 1, 1)
            self.assertEqual(stats2, stats)
            self.assertNotEqual(rows2[0]["reg_number"], rows[0]["reg_number"])
            filtered, _ = truth.dashboard_counts(self.db,
                " FROM purchases p WHERE p.reg_number IN (?,?)", self.regs[:2], 0, 1)
            self.assertEqual((filtered["purchases"], filtered["violations"]), (2, 2))
            self.assertEqual(reconcile.call_count, 3)
        self.assertEqual(self.db.total_changes, changes)

    def test_edits_deletions_status_changes_and_insertions_invalidate_immediately(self):
        reg = self.regs[0]
        with patch.object(truth, "collapse_legal_events", wraps=truth.collapse_legal_events) as reconcile:
            self.assertEqual(truth.load_counts(self.db, self.regs)[reg], 1)
            self.db.execute("UPDATE checks SET evidence=? WHERE reg_number=?", (self.proof(25), reg))
            self.assertEqual(truth.load_counts(self.db, self.regs)[reg], 2)
            self.assertEqual(reconcile.call_count, 4)  # Only changed procurement recomputed.
            self.db.execute("UPDATE checks SET status='check' WHERE reg_number=?", (reg,))
            self.assertEqual(truth.load_counts(self.db, self.regs)[reg], 1)
            self.db.execute("DELETE FROM findings WHERE reg_number=?", (reg,))
            self.assertEqual(truth.load_counts(self.db, self.regs)[reg], 0)
            self.db.execute("UPDATE checks SET status='violation' WHERE reg_number=?", (reg,))
            self.assertEqual(truth.load_counts(self.db, self.regs)[reg], 1)
            self.db.execute("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)",
                            (reg, "Иной срок", "223-ФЗ ст. 3.2 ч. 3", self.proof(35),
                             "Извещение.pdf", "https://zakupki.gov.ru/new", "new", "violation"))
            self.assertEqual(truth.load_counts(self.db, self.regs)[reg], 2)

    def test_same_purchase_number_in_another_database_cannot_reuse_other_evidence(self):
        self.assertEqual(truth.load_counts(self.db, self.regs)[self.regs[0]], 1)
        other = sqlite3.connect(":memory:")
        other.row_factory = sqlite3.Row
        try:
            self.db.backup(other)
            other.execute("UPDATE checks SET law='223-ФЗ ст. 3.2 ч. 3'")
            self.assertEqual(truth.load_counts(other, self.regs)[self.regs[0]], 2)
            self.assertEqual(truth.load_counts(self.db, self.regs)[self.regs[0]], 1)
        finally:
            other.close()

    def test_digest_cache_is_bounded_and_does_not_retain_evidence(self):
        with patch.object(truth, "_COUNT_CACHE_LIMIT", 2):
            truth.load_counts(self.db, self.regs)
            self.assertEqual(len(truth._COUNT_CACHE), 2)
            for (reg, digest), count in truth._COUNT_CACHE.items():
                self.assertIn(reg, self.regs)
                self.assertEqual(len(digest), 32)
                self.assertIsInstance(count, int)


class CardEventLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_card_render_does_not_block_other_http_requests(self):
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        thread_ids, review_flags, health_messages, card_messages = [], [], [], []
        legal_flags = []
        legal_snapshot = {"state": "pending", "docs_read": 3}

        def read(ns, reg):
            thread_ids.append(threading.get_ident())
            return "saved-data"

        def read_legal_state(ns, reg):
            thread_ids.append(threading.get_ident())
            return legal_snapshot

        def render(reg, data, account_review=False, legal_review=None):
            thread_ids.append(threading.get_ident())
            review_flags.append(account_review)
            legal_flags.append(legal_review)
            entered.set()
            release.wait(2)
            return "<html>saved card</html>"

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"healthy"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def card_send(message):
            card_messages.append(message)

        async def health_send(message):
            health_messages.append(message)

        app = cards.FastCardASGI(inner, {})
        with patch.object(cards, "read", side_effect=read), \
             patch.object(cards, "read_legal_state", side_effect=read_legal_state), \
             patch.object(cards, "render", side_effect=render):
            task = asyncio.create_task(app({"type": "http", "method": "GET",
                "path": "/check-new/32610000001", "eis223_account_id": "owner"}, receive, card_send))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                await app({"type": "http", "method": "GET", "path": "/health"}, receive, health_send)
                self.assertEqual(health_messages[-1]["body"], b"healthy")
                self.assertFalse(task.done())
                self.assertNotIn(loop_thread, thread_ids)
            finally:
                release.set()
                await task
        self.assertEqual(review_flags, [True])
        self.assertEqual(legal_flags, [legal_snapshot])
        self.assertEqual(card_messages[0]["status"], 200)
        self.assertIn((b"cache-control", b"no-store"), card_messages[0]["headers"])
        self.assertIn(b"saved card", card_messages[-1]["body"])


if __name__ == "__main__":
    unittest.main()
