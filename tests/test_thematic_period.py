"""Real SQLite/ASGI regressions for inclusive thematic publication periods."""
import asyncio
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import thematic_period as period
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast


class PublicationDates(unittest.TestCase):
    def test_bounds_are_real_dates_and_inclusive(self):
        self.assertEqual(period.normalize_period(None, ""), ("", ""))
        self.assertEqual(period.normalize_period(date(2024, 2, 29), "2024-02-29"),
                         ("2024-02-29", "2024-02-29"))
        for value in ("2026-02-29", "2026-13-01", "19.10.2026", "2026-10-19T12:00:00", True, [], {}, 20261019, datetime(2026, 10, 19)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                period.normalize_period(value, None)
        with self.assertRaisesRegex(ValueError, "позже"):
            period.normalize_period("2026-10-20", "2026-10-19")

    def test_source_calendar_day_ignores_clock_and_zone(self):
        for value in ("2026-10-19T00:01:00+14:00", "2026-10-19T23:59:59-12:00",
                      "19.10.2026 23:59 (МСК)", "2026-10-19"):
            with self.subTest(value=value):
                self.assertTrue(period.contains(value, "2026-10-19", "2026-10-19"))
        for value in (None, "", "неизвестно", "2026-02-30", "2026-10-190", "Дата изменения 19.10.2026"):
            self.assertFalse(period.contains(value, "2026-10-19", "2026-10-19"))
            self.assertTrue(period.contains(value))


class QueuePeriods(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = str(Path(self.folder.name) / "audit.sqlite")
        self.ns = {"conn": self.connect, "app": FastAPI()}
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,published_at TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT);
            """)
        thematic.init(self.ns)
        fast.install(self.ns)
        self.previous_state = dict(thematic.WORKER_STATE)
        thematic.WORKER_STATE.clear()
        self.addCleanup(self.restore_state)
        self.batch = "a" * 24

    def restore_state(self):
        thematic.WORKER_STATE.clear()
        thematic.WORKER_STATE.update(self.previous_state)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def start_selected(self, item):
        self.assertTrue(fast._start_item(self.ns, item))
        source, engine = fast._watermark(self.ns, item["reg"])
        item.update(source_version=source, semantic_version=source, engine_signature=engine)
        return item

    def purchase(self, suffix, published, customer="ООО Пример", ready=True):
        reg = f"326100{suffix:05d}"
        with self.connect() as c:
            c.execute("INSERT INTO purchases VALUES(?,?,?)", (reg, customer, published))
            if ready:
                c.execute("INSERT INTO docs(reg_number,local_path,status) VALUES(?,?,'checked')",
                          (reg, "/fixture/" + reg + ".pdf"))
        return reg

    def campaign(self, date_from="", date_to="", identifier=None, status="active"):
        identifier = identifier or self.batch
        with self.connect() as c:
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,legal_forms,date_from,date_to,status) "
                      "VALUES(?,?,?,?,?,?,?)", (identifier, '["dates"]', thematic.now(), '["ООО"]', date_from, date_to, status))
        return identifier

    def test_exact_period_applies_before_queue_with_both_boundary_days(self):
        self.campaign("2026-10-01", "2026-10-15")
        expected = {self.purchase(2, "2026-10-01T00:00:00+14:00"),
                    self.purchase(3, "15.10.2026 23:59 (МСК)")}
        self.purchase(1, "2026-09-30")
        self.purchase(4, "2026-10-16")
        self.purchase(5, None)
        self.purchase(6, "ошибка")
        self.purchase(7, "2026-10-10", customer="АО Другая форма")
        items = fast._prepare(self.ns)
        self.assertEqual({item["reg"] for item in items}, expected)
        with self.connect() as c:
            self.assertEqual({row[0] for row in c.execute("SELECT reg FROM thematic_items")}, expected)

    def test_one_sided_and_unbounded_campaigns_keep_separate_scopes(self):
        start, end, all_time = "a" * 24, "b" * 24, "c" * 24
        self.campaign("2026-10-10", identifier=start)
        self.campaign(date_to="2026-10-10", identifier=end)
        self.campaign(identifier=all_time)
        before = self.purchase(1, "2026-10-09")
        boundary = self.purchase(2, "2026-10-10")
        after = self.purchase(3, "2026-10-11")
        unknown = self.purchase(4, "")
        by_batch = {}
        for item in fast._prepare(self.ns):
            by_batch.setdefault(item["batch"], set()).add(item["reg"])
        self.assertEqual(by_batch[start], {boundary, after})
        self.assertEqual(by_batch[end], {before, boundary})
        self.assertEqual(by_batch[all_time], {before, boundary, after, unknown})

    def test_index_closes_read_snapshot_before_an_interleaved_writer(self):
        # Reproduce a real two-connection race. With the old streaming SELECT,
        # WAL raised SQLITE_BUSY_SNAPSHOT (517) at INSERT after the 1000th
        # customer; rollback journal mode blocked the second writer's commit.
        for journal in ("WAL", "DELETE"):
            with self.subTest(journal=journal), tempfile.TemporaryDirectory() as folder:
                path = str(Path(folder) / "index-race.sqlite")
                reader = sqlite3.connect(path, timeout=0.1)
                reader.row_factory = sqlite3.Row
                writer = sqlite3.connect(path, timeout=0.1)
                try:
                    reader.execute("PRAGMA journal_mode=" + journal)
                    reader.executescript("""
                        CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,published_at TEXT);
                        CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT DEFAULT 'queued',
                            detail TEXT DEFAULT '',updated_at TEXT,PRIMARY KEY(batch_id,reg));
                    """)
                    regs = [str(32610000000 + i) for i in range(1101)]
                    reader.executemany("INSERT INTO purchases VALUES(?,?,'2026-10-19')",
                                       [(reg, "ООО Пример") for reg in regs])
                    reader.commit()
                    visited = []

                    def customer_form(name):
                        visited.append(name)
                        if len(visited) == 100:
                            writer.execute("UPDATE purchases SET customer='ООО Уточнено' WHERE reg_number=?",
                                           (regs[-1],))
                            writer.commit()
                        return thematic.customer_form(name)

                    batch = {"id": self.batch, "legal_forms": '["ООО"]',
                             "date_from": "2026-10-19", "date_to": "2026-10-19"}
                    period.index_campaign(reader, batch, customer_form, thematic.now())
                    self.assertEqual(len(visited), len(regs))
                    self.assertEqual({r[0] for r in reader.execute("SELECT reg FROM thematic_items")}, set(regs))
                    self.assertEqual(reader.execute("SELECT customer FROM purchases WHERE reg_number=?",
                                                    (regs[-1],)).fetchone()[0], "ООО Уточнено")
                    # The helper must not commit the caller's indexing write.
                    self.assertTrue(reader.in_transaction)
                    self.assertEqual(writer.execute("SELECT COUNT(*) FROM thematic_items").fetchone()[0], 0)
                    reader.rollback()
                    self.assertEqual(writer.execute("SELECT COUNT(*) FROM thematic_items").fetchone()[0], 0)
                finally:
                    reader.close()
                    writer.close()

    def test_empty_form_pages_do_not_pin_a_snapshot_or_stop_later_pages(self):
        self.campaign()
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            regs = [str(32610000000 + i) for i in range(2501)]
            c.executemany("INSERT INTO purchases VALUES(?,?,'2026-10-19')",
                          [(reg, "АО Не выбрано" if i < 2000 else "ООО Выбрано")
                           for i, reg in enumerate(regs)])
        reader, writer = self.connect(), self.connect()
        visited = []
        try:
            batch = reader.execute("SELECT * FROM thematic_batches WHERE id=?", (self.batch,)).fetchone()

            def customer_form(name):
                visited.append(name)
                if len(visited) == 1100:
                    # The first empty page must not have started BEGIN through
                    # executemany([]), otherwise this commit stales page two.
                    self.assertFalse(reader.in_transaction)
                    writer.execute("UPDATE purchases SET customer='ООО Исправлено' WHERE reg_number=?",
                                   (regs[-1],))
                    writer.commit()
                return thematic.customer_form(name)

            period.index_campaign(reader, batch, customer_form, thematic.now())
            self.assertEqual(len(visited), len(regs))
            self.assertEqual({r[0] for r in reader.execute("SELECT reg FROM thematic_items")}, set(regs[2000:]))
            reader.rollback()
        finally:
            reader.close()
            writer.close()

    def test_index_preserves_an_existing_caller_transaction(self):
        self.campaign()
        reg = self.purchase(1, "2026-10-19")
        reader = self.connect()
        try:
            batch = reader.execute("SELECT * FROM thematic_batches WHERE id=?", (self.batch,)).fetchone()
            reader.execute("BEGIN IMMEDIATE")
            reader.execute("UPDATE purchases SET customer='ООО В транзакции' WHERE reg_number=?", (reg,))
            period.index_campaign(reader, batch, thematic.customer_form, thematic.now())
            self.assertTrue(reader.in_transaction)
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM thematic_items").fetchone()[0], 1)
            reader.rollback()
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM thematic_items").fetchone()[0], 0)
            self.assertEqual(reader.execute("SELECT customer FROM purchases WHERE reg_number=?", (reg,)).fetchone()[0],
                             "ООО Пример")
        finally:
            reader.close()

    def test_new_arrivals_and_corrected_publication_are_checked_against_saved_bounds(self):
        self.campaign("2026-10-10", "2026-10-10")
        first = self.purchase(1, "2026-10-10")
        initial = fast._prepare(self.ns)
        self.start_selected(initial[0])
        self.assertEqual([item["reg"] for item in initial], [first])
        second = self.purchase(2, "2026-10-10T23:59:00+03:00")
        self.purchase(3, "2026-10-11")
        with self.connect() as c:
            c.execute("UPDATE purchases SET published_at='2026-10-09' WHERE reg_number=?", (first,))
        # A metadata change during an in-flight review must not add an outside result.
        fast._persist(self.ns, [(initial[0], "completed", [{"category": "dates", "title": "Факт", "law": "Норма", "evidence": "Источник"}], "Проверено")])
        thematic.WORKER_STATE["index_at"] = 0
        self.assertEqual([item["reg"] for item in fast._prepare(self.ns)], [second])
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT status FROM thematic_items WHERE reg=?", (first,)).fetchone()[0], "outside_period")
            c.execute("UPDATE purchases SET published_at='2026-10-10' WHERE reg_number=?", (first,))
        thematic.WORKER_STATE["index_at"] = 0
        self.assertIn(first, {item["reg"] for item in fast._prepare(self.ns)})

    def test_scope_guard_and_persistence_reject_interleaved_metadata_writer(self):
        self.campaign("2026-10-10", "2026-10-10")
        reg = self.purchase(1, "2026-10-10")
        selected = self.start_selected(fast._prepare(self.ns)[0])
        historical = (self.batch, reg, "dates", "Ранее подтверждённый эпизод", "Норма", "Сохранённый источник")
        with self.connect() as c:
            c.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence) VALUES(?,?,?,?,?,?)", historical)
        attempts = []
        fixture_path = self.path

        class InterleavedConnection(sqlite3.Connection):
            def execute(connection, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if sql.startswith("SELECT 1 FROM purchases p WHERE p.reg_number=?"):
                    allowed = cursor.fetchone()
                    cursor.close()
                    # A second real connection tries to commit an EIS correction
                    # at the exact gap between the guard read and result write.
                    other = sqlite3.connect(fixture_path, timeout=0)
                    try:
                        try:
                            other.execute("UPDATE purchases SET published_at='2026-10-11' WHERE reg_number=?", (reg,))
                            other.commit()
                        except sqlite3.OperationalError as exc:
                            if "locked" not in str(exc).lower():
                                raise
                            attempts.append("blocked")
                        else:
                            attempts.append("committed")
                    finally:
                        other.close()
                    return SimpleNamespace(fetchone=lambda: allowed)
                return cursor

        def interleaved_connect():
            return sqlite3.connect(self.path, factory=InterleavedConnection)

        first_finding = {"category": "dates", "title": "Новый отдельный эпизод",
                         "law": "Другая норма", "evidence": "Новый источник"}
        with patch.dict(self.ns, {"conn": interleaved_connect}):
            fast._persist(self.ns, [(selected, "completed", [first_finding], "Проверено")])
        self.assertEqual(attempts, ["blocked"])
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT published_at FROM purchases WHERE reg_number=?", (reg,)).fetchone()[0], "2026-10-10")
            saved = [tuple(row) for row in c.execute("SELECT batch_id,reg,category,title,law,evidence FROM thematic_findings ORDER BY rowid")]
            self.assertEqual(saved[0], historical)
            self.assertEqual(len(saved), 2)
            # The correction can commit after persistence; a subsequent stale
            # analysis must then be rejected without erasing either saved fact.
            c.execute("UPDATE purchases SET published_at='2026-10-11' WHERE reg_number=?", (reg,))
        later = {"category": "dates", "title": "Запоздалый результат",
                 "law": "Новая норма", "evidence": "Поздний источник"}
        fast._persist(self.ns, [(selected, "completed", [later], "Проверено")])
        with self.connect() as c:
            self.assertEqual([tuple(row) for row in c.execute("SELECT batch_id,reg,category,title,law,evidence FROM thematic_findings ORDER BY rowid")], saved)
            self.assertEqual(c.execute("SELECT status FROM thematic_items WHERE reg=?", (reg,)).fetchone()[0], "completed")

    def test_growth_diagnostic_reports_only_committed_unique_rows(self):
        self.campaign()
        reg = self.purchase(1, "2026-10-10")
        selected = self.start_selected(fast._prepare(self.ns)[0])
        finding = {"category": "dates", "title": "Подтверждённый эпизод",
                   "law": "Норма", "evidence": "Оригинал ЕИС"}
        messages = []

        def diagnostic(*args, **kwargs):
            message = " ".join(str(arg) for arg in args)
            if message.startswith("THEMATIC_EVIDENCE_SAVED "):
                # A separate reader must already see the committed finding.
                with self.connect() as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0], 1)
                    self.assertEqual(c.execute("SELECT last_analysis_version FROM thematic_items WHERE reg=?", (reg,)).fetchone()[0], fast.ANALYSIS_VERSION)
                messages.append(message)

        result = (selected, "completed", [finding], "Проверено")
        with patch("builtins.print", side_effect=diagnostic):
            fast._persist(self.ns, [result])
            fast._persist(self.ns, [result])
            # Force the duplicate through to SQLite's UNIQUE constraint to
            # verify that INSERT OR IGNORE does not create a growth event.
            with self.connect() as c:
                c.execute("UPDATE thematic_items SET status='running',reservation_token=? WHERE reg=?", (selected["reservation_token"], reg))
            with patch("v333_dedup_ui._same_finding", return_value=False):
                fast._persist(self.ns, [result])
        self.assertEqual(len(messages), 1)
        self.assertIn("reg=" + reg, messages[0])
        self.assertIn("new_unique_findings=1", messages[0])
        self.assertIn("analysis_version=" + str(fast.ANALYSIS_VERSION), messages[0])

    def test_completed_originals_get_one_recheck_for_new_rule_version(self):
        self.campaign()
        self.campaign(identifier="b" * 24, status="stopped")
        ready = self.purchase(1, "2026-10-10")
        missing = self.purchase(2, "2026-10-10", ready=False)
        with self.connect() as c:
            for batch in (self.batch, "b" * 24):
                for reg in (ready, missing):
                    c.execute("INSERT INTO thematic_items(batch_id,reg,status,updated_at,last_doc_id,last_ready_count,last_analysis_version) "
                              "VALUES(?,?,'completed',?,?,?,0)",
                              (batch, reg, thematic.now(), 1 if reg == ready else 0, 1 if reg == ready else 0))
        items = fast._prepare(self.ns)
        self.assertEqual([(item["batch"], item["reg"]) for item in items], [(self.batch, ready)])
        self.start_selected(items[0])
        fast._persist(self.ns, [(items[0], "completed", [], "Проверено")])
        self.assertEqual(fast._prepare(self.ns), [])
        fast.install(self.ns)
        self.assertEqual(fast._prepare(self.ns), [])
        # An engine change (not a fake nonexistent path) requests one review.
        with patch.object(fast, "engine_signature", return_value="new-legal-engine"):
            self.assertEqual([item["reg"] for item in fast._prepare(self.ns)], [ready])

    def test_migration_preserves_existing_all_time_campaign_and_saved_evidence(self):
        self.campaign()
        with self.connect() as c:
            c.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence) VALUES(?,?,?,?,?,?)",
                      (self.batch, "32610000001", "dates", "Факт", "Норма", "Доказательство"))
        thematic.init(self.ns)
        thematic.init(self.ns)
        with self.connect() as c:
            row = c.execute("SELECT date_from,date_to FROM thematic_batches").fetchone()
            self.assertEqual(tuple(row), ("", ""))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0], 1)

    def test_saved_reads_recover_after_exclusive_sqlite_lock(self):
        self.campaign()
        lock = self.connect()
        self.addCleanup(lock.close)
        lock.execute("BEGIN EXCLUSIVE")
        # Release the real competing transaction between bounded attempts.
        with patch("time.sleep", side_effect=lambda _: lock.rollback()) as sleep:
            result = thematic.saved_read(thematic.thematic_history_records, self.ns, [self.batch])
        self.assertEqual(result["batches"][0]["id"], self.batch)
        sleep.assert_called_once()
        thematic.WORKER_STATE.update(last_cycle_at="", last_cycle_finished_at="", last_error="")
        result = thematic.saved_read(thematic.overview, self.ns, self.batch)
        self.assertEqual(result["id"], self.batch)
        with thematic.read_db(self.ns) as c:
            self.assertEqual(c.execute("PRAGMA busy_timeout").fetchone()[0], 1000)
            with self.assertRaises(sqlite3.OperationalError):
                c.execute("DELETE FROM thematic_batches")

    def test_saved_reads_report_busy_instead_of_empty_results(self):
        from unittest.mock import Mock
        from fastapi import HTTPException
        operation = Mock(side_effect=sqlite3.OperationalError("database is locked"))
        with patch("time.sleep"), self.assertRaises(HTTPException) as error:
            thematic.saved_read(operation)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.headers, {"Retry-After": "2"})
        self.assertEqual(operation.call_count, 3)
        operation = Mock(side_effect=sqlite3.OperationalError("no such table: missing"))
        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
            thematic.saved_read(operation)
        self.assertEqual(operation.call_count, 1)

    def test_http_validation_happens_before_activation_and_saved_period_is_readable(self):
        thematic.install(self.ns)

        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns["app"]), base_url="http://localhost") as client:
                payload = {"categories": ["dates"], "legal_forms": ["ООО"], "date_from": "2026-10-20", "date_to": "2026-10-19"}
                response = await client.post("/api/thematic/start", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertIn("позже", response.json()["detail"])
                with self.connect() as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0], 0)
                payload.update(date_from="2026-10-01", date_to="2026-10-19")
                response = await client.post("/api/thematic/start", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["date_from"], "2026-10-01")
                records = thematic.thematic_history_records(self.ns, [response.json()["id"]])["batches"]
                self.assertEqual((records[0]["date_from"], records[0]["date_to"]), ("2026-10-01", "2026-10-19"))
        with patch.object(thematic.ownership, "protect_write"), patch.object(thematic.ownership, "enabled", return_value=False), \
             patch.object(thematic.ownership, "link_started") as linked:
            asyncio.run(check())
            linked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
