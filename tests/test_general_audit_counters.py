"""Committed legal episodes survive retries, deduplication and process restart."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v332_all_procurements_audit as general


REG = "32616233661"
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32


class GeneralAuditCounters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audit.sqlite"
        self.ns = {"conn": self.connect, "_verified_set235": lambda: [REG]}
        with self.connection() as c:
            c.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,
                    customer TEXT DEFAULT 'ООО Тест',violations INTEGER DEFAULT 0,
                    documents_checked INTEGER DEFAULT 0,checks_ok INTEGER DEFAULT 0,
                    checks_attention INTEGER DEFAULT 0,last_seen TEXT DEFAULT '2026-10-19');
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,
                    local_path TEXT,status TEXT);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,
                    code TEXT,kind TEXT,title TEXT,law TEXT,law_url TEXT,evidence TEXT,
                    note TEXT,document TEXT,source_url TEXT,location TEXT,match_text TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,
                    item TEXT,status TEXT,evidence TEXT,source TEXT,source_url TEXT,
                    document TEXT,field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT);
                CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY);
            """)
            c.execute("INSERT INTO purchases(reg_number) VALUES(?)", (REG,))
            c.execute("INSERT INTO docs VALUES(1,?,'/fixture/original.pdf','checked')", (REG,))
        general.initialize(self.ns)
        self.assertEqual(general.claim_next(self.ns), REG)
        self.snapshot = general.evidence_version(self.ns, REG)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.2)
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def connection(self):
        c = self.connect()
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def finding(self, *, later=False):
        dates = "10.09.2026 — 02.10.2026" if later else "10.08.2026 — 02.09.2026"
        return {"code":"223-4-11-AMENDMENT-PUBLICATION-DELAY", "kind":"violation",
                "title":"Изменения опубликованы с опозданием", "law":"223-ФЗ, ст. 4 ч. 11",
                "evidence":"Дата решения об изменении и дата публикации в ЕИС: " + dates,
                "document":"Изменения документации.pdf", "source_url":SOURCE}

    def checklist(self, finding):
        return {**finding, "status":"violation", "scope":"223-ФЗ",
                "item":finding["title"], "field_path":finding["code"]}

    def result(self, token, new_f=0, new_c=0, *, unreadable=0):
        return {"result_token":token, "last_source_version":self.snapshot,
                "new_findings":new_f, "new_checks":new_c,
                "documents_found":2, "documents_read":2-unreadable,
                "documents_unreadable":unreadable, "verified_original_documents":2-unreadable,
                "original_expected":2, "original_received":2}

    def assert_counts(self, confirmed, reviews=0):
        progress = general.progress(self.ns)
        self.assertEqual(progress["new_evidence_based_findings"], confirmed)
        self.assertEqual(progress["new_legal_review_items"], reviews)
        with self.connection() as c:
            saved = c.execute("SELECT (SELECT COUNT(*) FROM findings WHERE kind='violation')"
                              "+(SELECT COUNT(*) FROM checks WHERE status='violation')").fetchone()[0]
            self.assertEqual(saved, confirmed)
            self.assertEqual(c.execute("SELECT violations FROM purchases WHERE reg_number=?", (REG,)).fetchone()[0], confirmed)

    def test_saved_violation_is_counted_before_completion_and_survives_restart(self):
        self.assertEqual(general.persist(self.ns, REG, [self.finding()], [],
                                         progress_token="before-crash"), (1, 0))
        # A process may exit after the evidence commit and before mark/defer.
        self.assert_counts(1)
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT state FROM v332_audit_queue").fetchone()[0], "running")
        general.initialize(self.ns)
        self.assert_counts(1)
        self.assertEqual(general.claim_next(self.ns), REG)
        self.assertEqual(general.persist(self.ns, REG, [self.finding()], [],
                                         progress_token="after-restart"), (0, 0))
        general.mark(self.ns, REG, "completed", self.result("after-restart"))
        self.assert_counts(1)
        general.initialize(self.ns)
        self.assert_counts(1)

    def test_partial_retry_and_zero_delta_recheck_do_not_erase_or_duplicate_count(self):
        first = self.finding()
        self.assertEqual(general.persist(self.ns, REG, [first], [], progress_token="partial"), (1, 0))
        result = self.result("partial", 1, unreadable=1)
        general.defer_retry(self.ns, REG, result, "Temporary unreadable original")
        self.assert_counts(1)
        with self.connection() as c:
            initial = dict(c.execute("SELECT * FROM v332_audit_queue").fetchone())
        general.defer_retry(self.ns, REG, result, "Temporary unreadable original")
        self.assert_counts(1)
        with self.connection() as c:
            repeated = dict(c.execute("SELECT * FROM v332_audit_queue").fetchone())
            self.assertEqual(repeated["retry_count"], initial["retry_count"])
            self.assertEqual(repeated["next_retry_at"], initial["next_retry_at"])
            c.execute("UPDATE v332_audit_queue SET next_retry_at=0")
        self.assertEqual(general.claim_next(self.ns), REG)
        self.assertEqual(general.persist(self.ns, REG, [first], [], progress_token="full"), (0, 0))
        general.mark(self.ns, REG, "completed", self.result("full"))
        general.mark(self.ns, REG, "completed", self.result("full"))
        self.assert_counts(1)
        # A later complete pass on the same evidence also has a zero delta.
        general.mark(self.ns, REG, "pending")
        self.assertEqual(general.claim_next(self.ns), REG)
        general.persist(self.ns, REG, [first], [], progress_token="unchanged")
        general.mark(self.ns, REG, "completed", self.result("unchanged"))
        self.assert_counts(1)

    def test_checklist_only_confirmation_is_counted_separately_from_review_states(self):
        violation = self.checklist(self.finding())
        reviews = [{"scope":"Review", "item":"Review " + status,
                    "field_path":"review-" + status, "status":status,
                    "document":"Other.pdf", "evidence":"Review fixture"}
                   for status in ("ok", "check", "risk")]
        self.assertEqual(general.persist(self.ns, REG, [], [violation, violation, *reviews],
                                         progress_token="checklist"), (0, 4))
        self.assert_counts(1, 3)
        general.mark(self.ns, REG, "completed", self.result("checklist", 0, 4))
        self.assert_counts(1, 3)
        self.assertEqual(general.persist(self.ns, REG, [self.finding()], [violation, *reviews],
                                         progress_token="duplicate"), (0, 0))
        general.mark(self.ns, REG, "completed", self.result("duplicate"))
        self.assert_counts(1, 3)

    def test_two_distinct_dated_episodes_increase_counter_once_each(self):
        first, second = self.finding(), self.finding(later=True)
        general.persist(self.ns, REG, [first], [self.checklist(first)], progress_token="first")
        general.mark(self.ns, REG, "completed", self.result("first", 1))
        self.assert_counts(1)
        self.assertEqual(general.persist(self.ns, REG, [first, second],
                                         [self.checklist(first), self.checklist(second)],
                                         progress_token="second"), (1, 0))
        self.assert_counts(2)
        general.mark(self.ns, REG, "completed", self.result("second", 1))
        general.mark(self.ns, REG, "completed", self.result("second", 1))
        self.assert_counts(2)

    def test_failed_evidence_transaction_does_not_credit_counter(self):
        with self.connection() as c:
            c.execute("CREATE TRIGGER fail_fixture BEFORE UPDATE OF new_findings ON v332_audit_queue "
                      "BEGIN SELECT RAISE(ABORT,'fixture write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            general.persist(self.ns, REG, [self.finding()], [], progress_token="retry-same-token")
        self.assert_counts(0)
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT last_counted_token FROM v332_audit_queue").fetchone()[0], "")
            c.execute("DROP TRIGGER fail_fixture")
        self.assertEqual(general.persist(self.ns, REG, [self.finding()], [],
                                         progress_token="retry-same-token"), (1, 0))
        self.assert_counts(1)
        self.assertEqual(general.persist(self.ns, REG, [self.finding()], [],
                                         progress_token="retry-same-token"), (0, 0))
        general.mark(self.ns, REG, "completed", self.result("retry-same-token", 1))
        self.assert_counts(1)


if __name__ == "__main__":
    unittest.main()
