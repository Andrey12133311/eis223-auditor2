"""Local SQLite regressions: diagnostics must not own the writer or undo success."""
import asyncio
from contextlib import contextmanager, redirect_stdout
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v333_dedup_ui as dedup
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import v352_dashboard_truth as truth


class ThematicPersistLockScope(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "audit.sqlite"
        self.ns = {"conn": self.connect}
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,published_at TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT);
                CREATE TABLE unrelated_writer(value TEXT);
                CREATE TABLE retained_history(value TEXT);
                INSERT INTO retained_history VALUES('approved historical decision');
            """)
        state, tick = dict(thematic.WORKER_STATE), thematic.tick
        def restore():
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(state)
            thematic.tick = tick
        self.addCleanup(restore)
        thematic.init(self.ns)
        fast.install(self.ns)
        self.reg, self.batch = "32619000001", "owned-batch"
        with self.connection() as c:
            c.execute("ALTER TABLE thematic_batches ADD COLUMN owner_user_id TEXT")
            c.execute("INSERT INTO purchases VALUES(?,'ООО Пример','2026-10-19')", (self.reg,))
            c.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms,"
                      "date_from,date_to,owner_user_id) VALUES(?,'[\"dates\"]','fixture','active',"
                      "'[\"ООО\"]','2026-10-01','2026-10-31','owner-a')", (self.batch,))
            c.execute("INSERT INTO thematic_items(batch_id,reg,status,updated_at,reservation_token) "
                      "VALUES(?,?,'running','fixture','lease-a')", (self.batch,self.reg))
            campaign = c.execute("SELECT * FROM thematic_batches WHERE id=?", (self.batch,)).fetchone()
        self.item = {"batch": self.batch, "reg": self.reg, "ready": 1, "doc_id": 1,
                     "reservation_token": "lease-a", "campaign_guard": fast._campaign_identity(campaign),
                     "source_version": "source-a", "semantic_version": "source-a", "engine_signature": "engine-a"}
        self.finding = {"category": "dates", "title": "Нарушение срока разъяснения",
                        "law": "223-ФЗ, ч. 3 ст. 3.2", "evidence": "Запрос 01.10.2026; ответ 07.10.2026",
                        "code": "223-3.2-3-CLARIFICATION-LATE", "document": "Разъяснение.xml",
                        "source_url": "https://zakupki.gov.ru/fixture"}

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.02)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def connection(self):
        c = self.connect()
        try:
            with c:
                yield c
        finally:
            c.close()

    def results(self, findings=None):
        return [(self.item, "completed", [self.finding] if findings is None else findings, "checked")]

    def snapshot(self):
        with self.connection() as c:
            return (dict(c.execute("SELECT * FROM thematic_items").fetchone()),
                    [dict(r) for r in c.execute("SELECT * FROM thematic_findings")],
                    [r[0] for r in c.execute("SELECT value FROM retained_history")])

    def test_incoming_normalization_allows_unrelated_writer(self):
        original = dedup.collapse
        observed = []
        def collapse(rows):
            with self.connection() as c:
                c.execute("INSERT INTO unrelated_writer VALUES('normalization')")
                observed.append(c.execute("SELECT status FROM thematic_items").fetchone()[0])
            return original(rows)
        with patch.object(dedup, "collapse", side_effect=collapse):
            self.assertEqual(fast._persist(self.ns, self.results()), 1)
        self.assertEqual(observed, ["running"])

    def test_exact_event_counts_run_after_commit_and_allow_unrelated_writer(self):
        original = truth.collapse_legal_events
        observed = []
        def counts(rows):
            with self.connection() as c:
                c.execute("INSERT INTO unrelated_writer VALUES('count')")
                observed.append(c.execute("SELECT status,reservation_token FROM thematic_items").fetchone())
            return original(rows)
        with patch.object(truth, "collapse_legal_events", side_effect=counts):
            self.assertEqual(fast._persist(self.ns, self.results()), 1)
        self.assertEqual([tuple(row) for row in observed], [("completed", ""), ("completed", "")])
        self.assertEqual(self.snapshot()[2], ["approved historical decision"])

    def test_diagnostic_busy_cannot_retry_or_reclassify_committed_result(self):
        output = io.StringIO()
        actual = fast._persist
        with patch.object(truth, "collapse_legal_events", side_effect=sqlite3.OperationalError("database is locked")), \
                patch.object(fast, "_persist", wraps=actual) as persist, redirect_stdout(output):
            applied = asyncio.run(fast._retry_db(fast._persist, self.ns, self.results()))
        self.assertEqual(applied, 1)
        self.assertEqual(persist.call_count, 1)
        row, findings, _ = self.snapshot()
        self.assertEqual((row["status"], row["reservation_token"], row["last_semantic_version"]),
                         ("completed", "", "source-a"))
        self.assertEqual(len(findings), 1)
        self.assertIn("THEMATIC_DIAGNOSTIC_FAILED", output.getvalue())
        self.assertIn("committed=True", output.getvalue())
        self.assertNotIn("THEMATIC_DB_RETRY", output.getvalue())

    def test_broken_log_sink_cannot_invalidate_commit(self):
        with patch("builtins.print", side_effect=OSError("closed log sink")):
            self.assertEqual(fast._persist(self.ns, self.results()), 1)
        self.assertEqual(self.snapshot()[0]["status"], "completed")
        self.assertEqual(len(self.snapshot()[1]), 1)

    def test_scope_and_lease_changes_during_normalization_still_fence_writes(self):
        cases = (
            ("UPDATE thematic_batches SET status='stopped'", "running"),
            ("UPDATE thematic_batches SET owner_user_id='owner-b'", "running"),
            ("UPDATE thematic_items SET reservation_token='lease-b'", "running"),
            ("UPDATE purchases SET customer='АО Другая форма'", "running"),
            ("UPDATE purchases SET published_at='2026-11-01'", "outside_period"),
        )
        original = dedup.collapse
        for sql, expected in cases:
            with self.subTest(sql=sql):
                with self.connection() as c:
                    c.execute("UPDATE thematic_batches SET status='active',owner_user_id='owner-a'")
                    c.execute("UPDATE thematic_items SET status='running',reservation_token='lease-a'")
                    c.execute("UPDATE purchases SET customer='ООО Пример',published_at='2026-10-19'")
                def collapse(rows):
                    with self.connection() as c:
                        c.execute(sql)
                    return original(rows)
                with patch.object(dedup, "collapse", side_effect=collapse):
                    self.assertEqual(fast._persist(self.ns, self.results()), 0)
                row, findings, history = self.snapshot()
                self.assertEqual(row["status"], expected)
                self.assertEqual(row["last_semantic_version"], "")
                self.assertEqual(findings, [])
                self.assertEqual(history, ["approved historical decision"])

    def test_roll_back_all_evidence_and_watermarks_before_any_diagnostic(self):
        with self.connection() as c:
            c.execute("CREATE TRIGGER fail_completed BEFORE UPDATE ON thematic_items "
                      "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'forced failure'); END")
        before = self.snapshot()
        with patch.object(truth, "collapse_legal_events") as counts:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "forced failure"):
                fast._persist(self.ns, self.results())
        counts.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_replay_deduplicates_without_diagnostic_or_history_changes(self):
        self.assertEqual(fast._persist(self.ns, self.results([self.finding, dict(self.finding)])), 1)
        with self.connection() as c:
            c.execute("UPDATE thematic_items SET status='running',reservation_token='lease-a'")
        before = self.snapshot()
        with patch.object(truth, "collapse_legal_events") as counts:
            self.assertEqual(fast._persist(self.ns, self.results()), 1)
        counts.assert_not_called()
        self.assertEqual(self.snapshot()[1:], before[1:])
        self.assertEqual(len(self.snapshot()[1]), 1)

    def test_diagnostic_uses_commit_snapshots_despite_concurrent_new_evidence(self):
        original = truth.collapse_legal_events
        output = io.StringIO()
        calls = []
        def counts(rows):
            calls.append(len(rows))
            if len(calls) == 1:
                with self.connection() as c:
                    c.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,evidence) "
                              "VALUES(?,?,'dates','Concurrent event','separate evidence')", (self.batch,self.reg))
            return original(rows)
        with patch.object(truth, "collapse_legal_events", side_effect=counts), redirect_stdout(output):
            self.assertEqual(fast._persist(self.ns, self.results()), 1)
        self.assertEqual(calls, [1, 0])
        self.assertIn("new_unique_findings=1", output.getvalue())
        self.assertEqual(len(self.snapshot()[1]), 2)


if __name__ == "__main__":
    unittest.main()
