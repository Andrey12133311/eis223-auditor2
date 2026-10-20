"""Synthetic SQLite-only tests for explicit full saved-procurement requests."""
import contextlib
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import full_recheck_request as ledger

A, B, C, D, E = ("3261623300" + str(i) for i in range(1, 6))
REQUEST = "explicit-user-request-1"


class FullRecheckRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="full-recheck-ledger-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.sqlite"
        self.c = sqlite3.connect(self.path, isolation_level=None, timeout=2)
        self.addCleanup(self.c.close)
        self.c.executescript("""
            CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,title TEXT);
            CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,updated_at TEXT,
                reservation_token TEXT DEFAULT '',retained TEXT,PRIMARY KEY(campaign,reg));
            CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY);
            CREATE TABLE thematic_batches(id TEXT PRIMARY KEY,status TEXT);
            INSERT INTO thematic_batches VALUES('batch','active');
            CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT,reservation_token TEXT DEFAULT '');
            CREATE TABLE findings(id INTEGER PRIMARY KEY,reg TEXT,body TEXT);
        """)
        self.c.executemany("INSERT INTO purchases VALUES(?,?,?)", [(A, "ООО Synthetic", "A"), (B, "Unknown private name", "B"),
            (C, "Other organization", "C"), (D, "", "D"), (E, "", "E")])
        self.c.executemany("INSERT INTO v332_audit_queue VALUES(?,?,?,?,?,?)", [
            (ledger.CAMPAIGN, A, "completed", "2020-01-01T00:00:00+00:00", "", "retain-a"),
            (ledger.CAMPAIGN, B, "no_readable_documents", "2020-01-01T00:00:00+00:00", "", "retain-b"),
            (ledger.CAMPAIGN, C, "failed", "2020-01-01T00:00:00+00:00", "", "retain-c"),
            ("other-campaign", E, "completed", "2020-01-01T00:00:00+00:00", "", "retain-e")])
        ledger.initialize(self.c)

    def create(self, values=(A,), request=REQUEST, **kwargs):
        with patch.object(ledger.time, "time", return_value=1000):
            return ledger.create_request(self.c, request, lambda: values, **kwargs)

    @contextlib.contextmanager
    def write(self, conn=None):
        conn = self.c if conn is None else conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def claim(self, reg=A, request=REQUEST, **kwargs):
        with self.write():
            return ledger.claim_member(self.c, request, reg, now=kwargs.pop("now", 1001), **kwargs)

    def member(self, reg=A, request=REQUEST):
        cursor = self.c.execute("SELECT * FROM full_recheck_members WHERE request_id=? AND reg=?", (request, reg))
        row = cursor.fetchone()
        return dict(zip((col[0] for col in cursor.description), row)) if row else None

    def watermark(self, claim, **kwargs):
        with self.write():
            ledger.set_review_watermark(self.c, claim, source_version=kwargs.get("source_version", "source-v1"),
                engine_signature=kwargs.get("engine_signature", "engine-v1"), now=1002)

    def receipt(self, claim, *, full=True, token="result-v1", now=1003, **kwargs):
        ledger.record_receipt(self.c, claim, source_version=kwargs.get("source_version", "source-v1"),
            engine_signature=kwargs.get("engine_signature", "engine-v1"), result_token=token,
            full_verified_read=full, now=now, metrics=kwargs.get("metrics", {"documents_read": 2, "verified_original_documents": 2}))

    def test_exact_union_no_company_gate_manual_exclusions_and_noncanonical_blocked(self):
        self.c.execute("INSERT INTO v319_manual_inputs VALUES(?)", (C,))
        self.c.execute("INSERT INTO v319_manual_inputs VALUES('unrelated-manual')")
        self.c.execute("INSERT INTO v332_audit_queue VALUES(?,?,?,?,?,?)",
            (ledger.CAMPAIGN, "32619999999", "completed", "old", "", "not-a-purchase"))
        self.c.execute("INSERT INTO purchases VALUES(?,?,?)", ("malformed-reg", "", "Malformed saved member"))
        result = self.create([A, A, D, "malformed-reg", "32618888888"], expected_count=3058)
        expected = sorted([A, B, D, "malformed-reg"])
        self.assertEqual(result["actual_count"], 4)
        self.assertEqual(result["verified_not_saved_count"], 1)
        self.assertEqual(result["manual_excluded_count"], 1)
        self.assertEqual(result["expected_count"], 3058)
        self.assertFalse(result["expected_matches_actual"])
        self.assertEqual(result["membership_digest"], hashlib.sha256(json.dumps(expected, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest())
        self.assertEqual(result["states"], dict(pending=3, running=0, completed=0, waiting_originals=0, retry=0, blocked=1))
        self.assertEqual(result["reasons"], {"noncanonical_registration": 1})
        self.assertIsNone(self.member("32618888888"))
        self.assertEqual(self.member("malformed-reg")["reason"], "noncanonical_registration")
        self.assertIsNone(self.member(E))
        self.assertIsNone(self.member(C))
        self.assertIsNone(self.member("32619999999"))
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, now=1001), [A, B, D])

    def test_same_request_is_frozen_and_idempotent_even_after_sources_change(self):
        first = self.create()
        self.c.execute("INSERT INTO v332_audit_queue VALUES(?,?,?,?,?,?)", (ledger.CAMPAIGN, D, "pending", "later", "", "retained"))
        self.c.execute("INSERT INTO v319_manual_inputs VALUES(?)", (B,))
        never = Mock(side_effect=AssertionError("Getter must not run twice"))
        repeated = ledger.create_request(self.c, REQUEST, never, expected_count=12)
        self.assertEqual(first, repeated)
        never.assert_not_called()
        newer = self.create(request="explicit-user-request-2")
        self.assertEqual(newer["actual_count"], 3)
        self.assertNotEqual(newer["membership_digest"], first["membership_digest"])

    def test_getter_called_once_outside_writer_and_timestamps_distinguish_sources(self):
        calls = []
        def getter():
            calls.append(self.c.in_transaction)
            with sqlite3.connect(self.path) as other:
                other.execute("INSERT INTO v332_audit_queue VALUES(?,?,?,?,?,?)", (ledger.CAMPAIGN, D, "pending", "later", "", "retained"))
            return [A]
        with patch.object(ledger.time, "time", side_effect=[1000, 1001, 1002]):
            report = ledger.create_request(self.c, REQUEST, getter)
        self.assertEqual(calls, [False])
        self.assertEqual((report["created_at"], report["source_captured_at"], report["db_snapshot_at"]), (1000, 1001, 1002))
        self.assertIsNotNone(self.member(D))

    def test_sql_membership_snapshot_is_stable_against_concurrent_writer(self):
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA busy_timeout=10")
        outcomes = []
        fired = False
        def trace(sql):
            nonlocal fired
            if not fired and "SELECT q.reg" in sql:
                fired = True
                with sqlite3.connect(self.path, timeout=0.01) as other:
                    try:
                        other.execute("INSERT INTO v319_manual_inputs VALUES(?)", (B,))
                    except sqlite3.OperationalError:
                        outcomes.append("writer-blocked")
        self.c.set_trace_callback(trace)
        try:
            report = self.create()
        finally:
            self.c.set_trace_callback(None)
        self.assertEqual(outcomes, ["writer-blocked"])
        self.assertEqual(report["actual_count"], 3)
        self.assertIsNotNone(self.member(B))
        self.assertEqual(self.c.execute("PRAGMA busy_timeout").fetchone()[0], 10)

    def test_budget_overflow_getter_and_sql_roll_back_whole_request(self):
        for request, values, budget in (("getter-overflow", [A] * 4, 3), ("sql-overflow", [A], 3)):
            with self.assertRaises(ledger.SnapshotError):
                self.create(values, request=request, max_rows=budget)
            self.assertIsNone(ledger.inventory(self.c, request))
            self.assertEqual(self.c.execute("SELECT COUNT(*) FROM full_recheck_members WHERE request_id=?", (request,)).fetchone()[0], 0)
        self.assertFalse(self.c.in_transaction)

    def test_failing_or_invalid_getter_does_not_create_request(self):
        for getter in (None, lambda: None, lambda: "not-a-collection", Mock(side_effect=RuntimeError("private source text"))):
            with self.assertRaises(ledger.SnapshotError):
                ledger.create_request(self.c, REQUEST, getter)
            self.assertIsNone(ledger.inventory(self.c, REQUEST))

    def test_elapsed_getter_budget_aborts_without_partial_snapshot(self):
        clock = [0.0]
        def getter():
            clock[0] = 20.0
            return [A]
        with patch.object(ledger.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(ledger.SnapshotError):
                ledger.create_request(self.c, REQUEST, getter, max_seconds=1)
        self.assertIsNone(ledger.inventory(self.c, REQUEST))

    def test_schema_failure_does_not_create_request_and_no_existing_data_changes(self):
        before = self.c.execute("SELECT * FROM v332_audit_queue ORDER BY campaign,reg").fetchall()
        self.c.execute("DROP TABLE v319_manual_inputs")
        with self.assertRaises(ledger.SnapshotError):
            self.create()
        self.assertIsNone(ledger.inventory(self.c, REQUEST))
        self.assertEqual(self.c.execute("SELECT * FROM v332_audit_queue ORDER BY campaign,reg").fetchall(), before)

    def test_only_small_additive_tables_and_all_existing_rows_unchanged(self):
        tables = [r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'full_recheck_%'")]
        before = {t: self.c.execute("SELECT * FROM " + t).fetchall() for t in tables}
        self.create()
        claim = self.claim()
        self.watermark(claim)
        with self.write():
            self.receipt(claim)
        with self.write():
            ledger.finish_member(self.c, claim, result_token="result-v1")
        after = {t: self.c.execute("SELECT * FROM " + t).fetchall() for t in tables}
        self.assertEqual(before, after)
        created = {r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'full_recheck_%'")}
        self.assertEqual(created, {"full_recheck_requests", "full_recheck_members", "full_recheck_targets", "full_recheck_receipts"})

    def test_inventory_contains_only_aggregate_public_safe_fields(self):
        report = self.create()
        text = json.dumps(report)
        for private in (A, B, C, "Unknown private name", "Other organization", "retain-a"):
            self.assertNotIn(private, text)
        self.assertIn("membership_digest", report)
        self.assertEqual(sum(report["states"].values()), report["actual_count"])

    def test_inventory_attempt_counts_exclude_pending_waiting_and_blocked_without_claim(self):
        self.c.execute("INSERT INTO purchases VALUES('invalid-registration','','')")
        self.create([A, "invalid-registration"])
        with self.write():
            ledger.note_waiting(self.c, REQUEST, B, now=1001)
        before = self.c.total_changes
        report = ledger.inventory(self.c, REQUEST)
        self.assertEqual((report["unique_attempted"], report["total_attempts"]), (0, 0))
        self.assertEqual(report["states"], dict(pending=2, running=0, completed=0,
                                              waiting_originals=1, retry=0, blocked=1))
        self.assertEqual(self.c.total_changes, before)
        # An empty immutable request reports zero, not NULL.
        self.c.execute("DELETE FROM v332_audit_queue")
        empty = self.create([], request="empty-request")
        self.assertEqual((empty["unique_attempted"], empty["total_attempts"]), (0, 0))

    def test_inventory_attempt_counts_keep_retry_completion_and_restart_identity(self):
        initial = self.create()
        first = self.claim()
        running = ledger.inventory(self.c, REQUEST)
        self.assertEqual((running["unique_attempted"], running["total_attempts"]), (1, 1))
        with self.write():
            ledger.defer_member(self.c, first, status="retry", reason="incomplete_read", retry_after=0, now=1002)
        second = self.claim(now=1003)
        with self.write():
            ledger.set_review_watermark(self.c, second, source_version="source-v1", engine_signature="engine-v1", now=1004)
            self.receipt(second, now=1005)
            self.receipt(second, now=1005)
            ledger.finish_member(self.c, second, result_token="result-v1")
        another = self.claim(B, now=1006)
        with self.write():
            ledger.defer_member(self.c, another, status="waiting_originals", reason="missing_originals", now=1007)
        report = ledger.inventory(self.c, REQUEST)
        self.assertEqual((report["unique_attempted"], report["total_attempts"]), (2, 3))
        self.assertEqual(report["states"]["completed"], 1)
        self.assertEqual(report["states"]["waiting_originals"], 1)
        self.assertEqual(report["actual_count"], initial["actual_count"])
        self.assertEqual(report["membership_digest"], initial["membership_digest"])
        never = Mock(side_effect=AssertionError("Restart must reuse the frozen request"))
        with sqlite3.connect(self.path, isolation_level=None) as restarted:
            ledger.initialize(restarted)
            self.assertEqual(ledger.create_request(restarted, REQUEST, never), report)
        never.assert_not_called()

    def test_inventory_attempt_counts_do_not_mix_requests_for_the_same_registration(self):
        self.create()
        self.create(request="second-request")
        first = self.claim()
        with self.write():
            ledger.defer_member(self.c, first, status="retry", reason="interrupted", retry_after=0, now=1002)
        other = self.claim(request="second-request", now=1003)
        with self.write():
            ledger.defer_member(self.c, other, status="retry", reason="interrupted", retry_after=0, now=1004)
        self.claim(request="second-request", now=1005)
        self.claim(B, request="second-request", now=1006)
        first_report = ledger.inventory(self.c, REQUEST)
        second_report = ledger.inventory(self.c, "second-request")
        self.assertEqual((first_report["unique_attempted"], first_report["total_attempts"]), (1, 1))
        self.assertEqual((second_report["unique_attempted"], second_report["total_attempts"]), (2, 3))

    def test_claim_returns_str_identity_and_fences_second_request(self):
        self.create()
        self.create(request="second-request")
        claim = self.claim()
        self.assertIsInstance(claim, ledger.Claim)
        self.assertEqual(str(claim), A)
        self.assertEqual(claim.request_id, REQUEST)
        self.assertTrue(claim.attempt_id and claim.lease_token)
        self.assertIsNone(self.claim())
        self.assertIsNone(self.claim(request="second-request"))
        self.assertTrue(ledger.has_active_claim(self.c, A, now=999999))
        self.assertNotIn(A, ledger.select_candidates(self.c, REQUEST, now=999999))

    def test_claim_excludes_every_general_campaign_and_reserved_thematic(self):
        self.create()
        self.c.execute("INSERT INTO v332_audit_queue VALUES(?,?,?,?,?,?)", ("other", A, "running", "now", "other-token", "retained"))
        self.assertIsNone(self.claim())
        self.assertNotIn(A, ledger.select_candidates(self.c, REQUEST, now=1001))
        self.c.execute("DELETE FROM v332_audit_queue WHERE campaign='other'")
        for status, token in (("running", ""), ("queued", "thematic-token")):
            self.c.execute("INSERT INTO thematic_items VALUES(?,?,?,?)", ("batch", A, status, token))
            self.assertIsNone(self.claim())
            self.assertNotIn(A, ledger.select_candidates(self.c, REQUEST, now=1001))
            self.c.execute("DELETE FROM thematic_items")
        self.c.execute("INSERT INTO thematic_items VALUES(?,?,?,?)", ("batch", A, "queued", ""))
        self.assertIsNotNone(self.claim())

    def test_stopped_or_deleted_thematic_history_does_not_block_request(self):
        self.create()
        for reg, batch, status in ((A, "batch", "queued"), (B, "orphan", "running")):
            self.c.execute("INSERT INTO thematic_items VALUES(?,?,?,?)", (batch, reg, status, "retained-token"))
        self.c.execute("UPDATE thematic_batches SET status='stopped' WHERE id='batch'")
        before = self.c.execute("SELECT * FROM thematic_items ORDER BY reg").fetchall()
        self.assertIn(A, ledger.select_candidates(self.c, REQUEST, now=1001))
        self.assertIn(B, ledger.select_candidates(self.c, REQUEST, now=1001))
        self.assertIsNotNone(self.claim(A))
        self.assertIsNotNone(self.claim(B))
        self.assertEqual(self.c.execute("SELECT * FROM thematic_items ORDER BY reg").fetchall(), before)

    def test_incomplete_thematic_schema_fails_closed(self):
        self.create()
        self.c.execute("DROP TABLE thematic_batches")
        with self.assertRaises(ledger.SnapshotError):
            ledger.select_candidates(self.c, REQUEST, now=1001)
        with self.assertRaises(ledger.SnapshotError):
            self.claim()
        self.assertEqual(self.member()["attempt_count"], 0)

    def test_shared_general_claim_must_match_token_campaign_and_request_epoch(self):
        self.create()
        def running(stamp, token="fresh-token"):
            value = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            self.c.execute("UPDATE v332_audit_queue SET state='running',updated_at=?,reservation_token=? WHERE campaign=? AND reg=?",
                (value, token, ledger.CAMPAIGN, A))
        running(999)
        self.assertIsNone(self.claim(general_token="fresh-token"))
        running(1001)
        self.assertIsNone(self.claim(general_token="wrong-token"))
        self.assertIsNone(self.claim())
        claim = self.claim(general_token="fresh-token")
        self.assertEqual(claim.general_token, "fresh-token")
        self.assertEqual(self.member()["general_token"], "fresh-token")

    def test_two_competing_connections_only_one_claims(self):
        self.create()
        def attempt():
            with sqlite3.connect(self.path, timeout=3, isolation_level=None) as c:
                c.execute("BEGIN IMMEDIATE")
                claim = ledger.claim_member(c, REQUEST, A, now=1001)
                c.commit()
                return claim
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.member()["attempt_count"], 1)

    def test_candidates_are_bounded_and_due_only(self):
        self.create()
        claim = self.claim(A)
        with self.write():
            ledger.defer_member(self.c, claim, status="waiting_originals", reason="waiting_originals", retry_after=600, now=1002)
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, limit=1, now=1003), [B])
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, limit=1, now=1003, after_reg=B), [C])
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, limit=1, now=1602), [A])
        for limit in (0, 129, True):
            with self.assertRaises(ValueError):
                ledger.select_candidates(self.c, REQUEST, limit=limit)

    def test_expired_lease_cannot_persist_or_renew_and_still_blocks(self):
        self.create()
        claim = self.claim(lease_seconds=2)
        self.watermark(claim)
        with self.write():
            with self.assertRaises(ledger.StaleClaim):
                self.receipt(claim, now=1003)
            with self.assertRaises(ledger.StaleClaim):
                ledger.renew_lease(self.c, claim, now=1003)
        self.assertTrue(ledger.has_active_claim(self.c, A, now=1003))
        self.assertIsNone(self.claim(now=1003))

    def test_lease_renewal_and_exact_watermark_fencing(self):
        self.create()
        claim = self.claim(lease_seconds=10)
        self.watermark(claim)
        with self.write():
            ledger.renew_lease(self.c, claim, now=1009, lease_seconds=20)
            self.assertEqual(ledger.require_claim(self.c, claim, now=1020)["lease_until"], 1029)
            with self.assertRaises(ledger.StaleClaim):
                ledger.set_review_watermark(self.c, claim, source_version="source-v2", engine_signature="engine-v1", now=1009)
            with self.assertRaises(ledger.StaleClaim):
                self.receipt(claim, source_version="source-v2", now=1009)
            with self.assertRaises(ledger.StaleClaim):
                self.receipt(claim, engine_signature="engine-v2", now=1009)

    def test_completion_requires_receipt_of_same_attempt_and_complete_verified_read(self):
        self.create()
        claim = self.claim()
        with self.write():
            with self.assertRaises(ledger.StaleClaim):
                ledger.finish_member(self.c, claim, result_token="nonexistent")
            with self.assertRaises(ledger.StaleClaim):
                self.receipt(claim)
        self.watermark(claim)
        with self.write():
            self.receipt(claim, full=False)
        with self.write():
            with self.assertRaises(ledger.StaleClaim):
                ledger.finish_member(self.c, claim, result_token="result-v1")
            ledger.defer_member(self.c, claim, status="waiting_originals", reason="incomplete_read", now=1004)
        self.assertEqual(self.member()["status"], "waiting_originals")
        self.assertEqual(self.member()["full_verified_read"], 0)
        self.assertEqual(self.member()["result_token"], "result-v1")

    def test_result_and_receipt_commit_or_rollback_together(self):
        self.create()
        claim = self.claim()
        self.watermark(claim)
        self.c.execute("BEGIN IMMEDIATE")
        self.c.execute("INSERT INTO findings(reg,body) VALUES(?,?)", (A, "fixture finding"))
        self.receipt(claim)
        self.c.rollback()
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)
        self.assertEqual(self.member()["result_token"], "")
        with self.write():
            self.c.execute("INSERT INTO findings(reg,body) VALUES(?,?)", (A, "fixture finding"))
            self.receipt(claim)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 1)
        self.assertEqual(self.member()["status"], "running")
        with self.write():
            ledger.finish_member(self.c, claim, result_token="result-v1")
            ledger.finish_member(self.c, claim, result_token="result-v1")
        self.assertEqual(self.member()["status"], "completed")

    def test_receipt_is_idempotent_and_cannot_be_replaced_by_another_result(self):
        self.create()
        claim = self.claim()
        self.watermark(claim)
        with self.write():
            self.receipt(claim)
            self.receipt(claim)
            with self.assertRaises(ledger.StaleClaim):
                self.receipt(claim, token="different-result")
            with self.assertRaises(ledger.StaleClaim):
                self.receipt(claim, metrics={"documents_read": 999})
            with self.assertRaises(ledger.StaleClaim):
                ledger.defer_member(self.c, claim, status="retry", reason="interrupted", now=1004)

    def test_crash_after_result_commit_recovers_exact_full_receipt_without_rerun(self):
        self.create()
        claim = self.claim(lease_seconds=10)
        self.watermark(claim)
        with self.write():
            self.receipt(claim)
        expired = ledger.expired_members(self.c, REQUEST, now=1011)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].lease_token, claim.lease_token)
        self.assertTrue(ledger.has_active_claim(self.c, A))
        with self.write():  # Runtime additionally holds the exclusive execution lock.
            self.assertEqual(ledger.recover_expired(self.c, expired[0], now=1011), "completed")
        self.assertEqual(self.member()["status"], "completed")
        self.assertEqual(self.member()["attempt_count"], 1)
        self.assertEqual(self.member()["result_token"], "result-v1")

    def test_expired_incomplete_work_retries_with_new_identity_and_rejects_old_ack(self):
        self.create()
        claim = self.claim(lease_seconds=10)
        self.watermark(claim)
        with self.write():
            self.receipt(claim, full=False)
        with self.write():
            self.assertIsNone(ledger.recover_expired(self.c, claim, now=1010))
            self.assertEqual(ledger.recover_expired(self.c, claim, now=1011), "retry")
        replacement = self.claim(now=1012)
        self.assertNotEqual(claim.attempt_id, replacement.attempt_id)
        self.assertNotEqual(claim.lease_token, replacement.lease_token)
        self.assertEqual(self.member()["result_token"], "")
        with self.write():
            self.assertIsNone(ledger.recover_expired(self.c, claim, now=9999))
            with self.assertRaises(ledger.StaleClaim):
                ledger.require_claim(self.c, claim, now=1012)
            with self.assertRaises(ledger.StaleClaim):
                ledger.finish_member(self.c, claim, result_token="result-v1")

    def test_request_completes_only_after_every_member_has_full_receipt(self):
        self.create()
        for reg in (A, B, C):
            claim = self.claim(reg)
            self.watermark(claim)
            with self.write():
                self.receipt(claim)
                ledger.finish_member(self.c, claim, result_token="result-v1")
            expected = "completed" if reg == C else "active"
            self.assertEqual(ledger.inventory(self.c, REQUEST)["status"], expected)
        self.assertEqual(ledger.inventory(self.c, REQUEST)["states"]["completed"], 3)
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, now=2000), [])

    def test_blocked_members_keep_request_incomplete(self):
        self.c.execute("DELETE FROM v332_audit_queue")
        self.c.execute("INSERT INTO purchases VALUES(?,?,?)", ("invalid", "", "Malformed saved member"))
        report = self.create(["invalid"])
        self.assertEqual(report["states"]["blocked"], 1)
        self.assertEqual(report["status"], "active")
        self.assertIsNone(self.claim("invalid"))
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, now=1001), [])

    def test_empty_actual_population_is_explicit_zero_not_padded(self):
        self.c.execute("DELETE FROM v332_audit_queue")
        report = self.create([])
        self.assertEqual(report["actual_count"], 0)
        self.assertEqual(report["expected_count"], 3058)
        self.assertEqual(report["status"], "completed")

    def test_mutating_runtime_helpers_do_not_create_or_commit_transactions(self):
        self.create()
        with self.assertRaises(ValueError):
            ledger.claim_member(self.c, REQUEST, A, now=1001)
        self.c.execute("BEGIN IMMEDIATE")
        claim = ledger.claim_member(self.c, REQUEST, A, now=1001)
        self.assertTrue(self.c.in_transaction)
        self.c.rollback()
        self.assertEqual(self.member()["status"], "pending")
        with self.assertRaises(ledger.StaleClaim):
            ledger.require_claim(self.c, claim, now=1002)

    def test_unknown_reason_or_unbounded_metrics_never_store_source_text(self):
        self.create()
        claim = self.claim()
        self.watermark(claim)
        with self.write():
            with self.assertRaises(ValueError):
                ledger.defer_member(self.c, claim, status="retry", reason="private source content", now=1002)
            with self.assertRaises(ValueError):
                self.receipt(claim, metrics={"customer": "private name"})
            with self.assertRaises(ValueError):
                self.receipt(claim, metrics={"documents_read": -1})
        self.assertEqual(self.member()["reason"], "")


    def test_manual_exclusion_diagnostic_counts_only_saved_union(self):
        self.c.executemany("INSERT INTO v319_manual_inputs VALUES(?)", [(A,), (D,), ("missing-manual",)])
        report = self.create([A, "missing-manual", "missing-other"])
        self.assertEqual(report["manual_excluded_count"], 1)
        self.assertEqual(report["verified_not_saved_count"], 2)
        self.assertEqual(report["actual_count"], 2)

    def test_shared_claim_during_getter_capture_cannot_satisfy_snapshot(self):
        with patch.object(ledger.time, "time", side_effect=[1000, 1002, 1003]):
            ledger.create_request(self.c, REQUEST, lambda: [A])
        self.c.execute("UPDATE v332_audit_queue SET state='running',updated_at=?,reservation_token='fresh-token' WHERE campaign=? AND reg=?",
            (datetime.fromtimestamp(1001, timezone.utc).isoformat(), ledger.CAMPAIGN, A))
        self.assertIsNone(self.claim(general_token="fresh-token", now=1004))
        self.c.execute("UPDATE v332_audit_queue SET updated_at=? WHERE campaign=? AND reg=?",
            (datetime.fromtimestamp(1003, timezone.utc).isoformat(), ledger.CAMPAIGN, A))
        self.assertIsNotNone(self.claim(general_token="fresh-token", now=1004))

    def test_purchase_removed_after_snapshot_is_blocked_without_changing_count(self):
        report = self.create()
        self.c.execute("DELETE FROM purchases WHERE reg_number=?", (A,))
        self.assertIsNone(self.claim())
        after = ledger.inventory(self.c, REQUEST)
        self.assertEqual(after["actual_count"], report["actual_count"])
        self.assertEqual(after["membership_digest"], report["membership_digest"])
        self.assertEqual(self.member()["status"], "blocked")
        self.assertEqual(self.member()["reason"], "missing_purchase")

    def test_note_waiting_preserves_queue_and_fresh_shared_claim_bypasses_only_ledger_cooldown(self):
        self.create()
        before = self.c.execute("SELECT * FROM v332_audit_queue").fetchall()
        with self.write():
            self.assertTrue(ledger.note_waiting(self.c, REQUEST, A, reason="missing_originals", retry_after=600, now=1001))
        self.assertEqual(self.c.execute("SELECT * FROM v332_audit_queue").fetchall(), before)
        self.assertEqual(self.member()["status"], "waiting_originals")
        self.assertIsNone(self.claim(now=1002))
        self.c.execute("UPDATE v332_audit_queue SET state='running',updated_at=?,reservation_token='fresh-token' WHERE campaign=? AND reg=?",
            (datetime.fromtimestamp(1002, timezone.utc).isoformat(), ledger.CAMPAIGN, A))
        claim = self.claim(general_token="fresh-token", now=1002)
        self.assertIsNotNone(claim)
        with self.write():
            self.assertFalse(ledger.note_waiting(self.c, REQUEST, A, now=1003))
        self.assertEqual(self.member()["status"], "running")

    def test_original_coverage_unknown_is_not_recorded_as_zero(self):
        self.create()
        claim = self.claim()
        self.watermark(claim)
        with self.write():
            self.receipt(claim, full=False, metrics={"documents_read": 1, "documents_unreadable": 2, "original_expected": None, "original_received": 1})
        self.assertIsNone(self.member()["original_expected"])
        self.assertEqual(self.member()["original_received"], 1)
        self.assertEqual(self.member()["documents_unreadable"], 2)
        inventory = ledger.inventory(self.c, REQUEST)
        self.assertEqual(inventory["metrics_unknown"]["original_expected"], 3)
        self.assertEqual(inventory["metrics_unknown"]["original_received"], 2)


    def test_3058_unique_saved_purchases_are_frozen_without_legacy_cap(self):
        self.c.execute("DELETE FROM v332_audit_queue")
        self.c.execute("DELETE FROM purchases")
        registrations = [str(32610000000 + index) for index in range(3058)]
        self.c.executemany("INSERT INTO purchases VALUES(?,?,?)",
            [(reg, "" if index % 2 else "Other synthetic organization", "Fixture") for index, reg in enumerate(registrations)])
        self.c.executemany("INSERT INTO v332_audit_queue VALUES(?,?,?,?,?,?)",
            [(ledger.CAMPAIGN, reg, "completed", "old", "", "retained") for reg in registrations[1000:]])
        report = self.create(registrations[:2000])
        self.assertEqual(report["expected_count"], 3058)
        self.assertEqual(report["actual_count"], 3058)
        self.assertTrue(report["expected_matches_actual"])
        self.assertEqual(report["states"]["pending"], 3058)
        self.assertEqual(self.c.execute("SELECT COUNT(DISTINCT reg) FROM full_recheck_members").fetchone()[0], 3058)

    def test_deadline_after_inserts_rolls_back_all_members_and_request(self):
        clock = [0.0]
        def trace(sql):
            if sql.startswith("INSERT INTO full_recheck_members"):
                clock[0] = 20.0
        self.c.set_trace_callback(trace)
        try:
            with patch.object(ledger.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaises(ledger.SnapshotError):
                    self.create(max_seconds=1)
        finally:
            self.c.set_trace_callback(None)
        self.assertIsNone(ledger.inventory(self.c, REQUEST))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM full_recheck_members").fetchone()[0], 0)
        self.assertFalse(self.c.in_transaction)


    def test_partial_defer_cannot_erase_committed_finding_metrics(self):
        self.create()
        claim = self.claim()
        self.watermark(claim)
        with self.write():
            self.receipt(claim, full=False, metrics={"documents_read": 2, "new_findings": 3, "new_checks": 4})
        with self.write():
            ledger.defer_member(self.c, claim, status="waiting_originals", reason="incomplete_read",
                metrics={"documents_read": 2}, now=1004)
        self.assertEqual(self.member()["new_findings"], 3)
        self.assertEqual(self.member()["new_checks"], 4)


    def test_committed_growth_survives_partial_retry_and_receipt_replay(self):
        self.create()
        first = self.claim()
        self.watermark(first)
        with self.write():
            self.receipt(first, full=False, metrics={"documents_read": 1, "new_findings": 3, "new_checks": 4})
            self.receipt(first, full=False, metrics={"documents_read": 1, "new_findings": 3, "new_checks": 4})
            ledger.defer_member(self.c, first, status="retry", reason="incomplete_read", retry_after=0, now=1004)
        second = self.claim(now=1005)
        with self.write():
            ledger.set_review_watermark(self.c, second, source_version="source-v2", engine_signature="engine-v1", now=1006)
            self.receipt(second, source_version="source-v2", now=1007, metrics={"documents_read": 2, "verified_original_documents": 2})
            ledger.finish_member(self.c, second, result_token="result-v1")
        report = ledger.inventory(self.c, REQUEST)
        self.assertEqual(report["metrics"]["new_findings"], 0)
        self.assertEqual(report["committed_new_findings"], 3)
        self.assertEqual(report["committed_new_checks"], 4)
        self.assertEqual(self.member()["total_new_findings"], 3)
        self.assertEqual(self.member()["total_new_checks"], 4)

    def test_rolled_back_result_never_increments_committed_growth(self):
        self.create()
        claim = self.claim()
        self.watermark(claim)
        self.c.execute("BEGIN IMMEDIATE")
        self.receipt(claim, metrics={"documents_read": 1, "verified_original_documents": 1, "new_findings": 3})
        self.c.rollback()
        self.assertEqual(ledger.inventory(self.c, REQUEST)["committed_new_findings"], 0)


# Exact four compatibility functions from production f68c762. Kept inline so
# the mandatory Docker tests require no Git executable or extra fixture file.
LEGACY_FUNCTIONS = '''
def claim_member(c, request_id, reg, *, now=None, lease_seconds=300, general_token=None):
    """CAS one member. A supplied normal claim must be fresh for this request.

    The caller must hold its per-reg execution lock plus a write transaction.
    No pre-request or unidentifiable normal work can acknowledge this request.
    """
    _transaction(c)
    stamp, duration = _time(now), _lease_seconds(lease_seconds)
    _request_id(request_id)
    reg = str(reg)
    if not _REG.fullmatch(reg) or has_active_claim(c, reg):
        return None
    request = c.execute("SELECT db_snapshot_at,campaign FROM full_recheck_requests WHERE request_id=? AND status='active'", (request_id,)).fetchone()
    if request is None:
        return None
    if not c.execute("SELECT 1 FROM purchases WHERE reg_number=?", (reg,)).fetchone():
        c.execute("UPDATE full_recheck_members SET status='blocked',reason='missing_purchase',updated_at=? "
            "WHERE request_id=? AND reg=? AND status IN ('pending','retry','waiting_originals')", (stamp, request_id, reg))
        return None
    # Active thematic claims block, including reserved queued work.
    clauses = _ordinary_blockers(c)
    if _exists(c, "thematic_items"):
        theme_clause = next(clause for clause in clauses if "thematic_items" in clause)
        if not c.execute("SELECT 1 FROM (SELECT ? AS reg) m WHERE " + theme_clause, (reg,)).fetchone():
            return None
    if _exists(c, "v332_audit_queue"):
        columns = {row[1] for row in c.execute("PRAGMA table_info(v332_audit_queue)")}
        if general_token is None:
            if c.execute("SELECT 1 FROM v332_audit_queue WHERE reg=? AND state='running' LIMIT 1", (reg,)).fetchone():
                return None
        else:
            _token(general_token)
            if not {"campaign", "updated_at", "reservation_token"} <= columns:
                return None
            active = c.execute("SELECT campaign,reservation_token,updated_at FROM v332_audit_queue WHERE reg=? AND state='running'", (reg,)).fetchall()
            if len(active) != 1 or active[0][0] != request[1] or active[0][1] != general_token:
                return None
            updated = _updated_epoch(active[0][2])
            if updated is None or updated < request[0]:
                return None
    elif general_token is not None:
        return None
    attempt, lease = uuid.uuid4().hex, uuid.uuid4().hex
    changed = c.execute("""UPDATE full_recheck_members SET status='running',reason='',updated_at=?,
        next_retry_at=0,lease_token=?,lease_until=?,attempt_id=?,general_token=?,attempt_count=attempt_count+1,
        source_version='',engine_signature='',receipt_attempt_id='',result_token='',result_source_version='',
        result_engine_signature='',full_verified_read=0,result_committed_at=NULL
        WHERE request_id=? AND reg=? AND status IN ('pending','retry','waiting_originals') AND (?<>'' OR next_retry_at<=?)""",
        (stamp, lease, stamp + duration, attempt, general_token or "", request_id, reg, general_token or "", stamp)).rowcount
    return Claim(reg, request_id, attempt, lease, general_token or "") if changed == 1 else None

def _refresh_request(c, request_id):
    c.execute("""UPDATE full_recheck_requests SET status='completed' WHERE request_id=? AND status='active'
        AND NOT EXISTS(SELECT 1 FROM full_recheck_members WHERE request_id=? AND status<>'completed')""", (request_id, request_id))

def finish_member(c, claim, *, result_token):
    """Acknowledge only the exact fully verified committed attempt receipt.

    Lease expiry after the result commit cannot erase that receipt. This write
    remains safe after expiry because a superseding attempt changes its token.
    """
    _transaction(c)
    identity = _claim_where(claim)
    _token(result_token)
    changed = c.execute("""UPDATE full_recheck_members SET status='completed',reason='',lease_until=0,
        next_retry_at=0 WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=?
        AND status IN ('running','completed') AND receipt_attempt_id=attempt_id AND result_token=?
        AND full_verified_read=1 AND result_committed_at IS NOT NULL
        AND source_version<>'' AND source_version=result_source_version
        AND engine_signature<>'' AND engine_signature=result_engine_signature""", (*identity, result_token)).rowcount
    if changed != 1:
        raise StaleClaim("No matching fully verified result receipt to acknowledge")
    _refresh_request(c, claim.request_id)

def recover_expired(c, claim, *, now=None):
    """Recover one exact lease only while caller holds its exclusive execution lock.

    No global recovery is provided: expiry alone does not prove a worker stopped.
    A durable fully verified receipt is finalized without rerunning the audit;
    incomplete/uncommitted attempts become retry, never successful completion.
    """
    _transaction(c)
    stamp = _time(now)
    row = _dict_one(c.execute("""SELECT * FROM full_recheck_members WHERE request_id=? AND reg=? AND attempt_id=?
        AND lease_token=? AND status='running' AND lease_until<=?""", (*_claim_where(claim), stamp)))
    if row is None:
        return None
    complete = (row["full_verified_read"] == 1 and row["receipt_attempt_id"] == claim.attempt_id
        and bool(row["result_token"]) and row["result_committed_at"] is not None
        and bool(row["source_version"]) and row["source_version"] == row["result_source_version"]
        and bool(row["engine_signature"]) and row["engine_signature"] == row["result_engine_signature"])
    if complete:
        finish_member(c, claim, result_token=row["result_token"])
        return "completed"
    c.execute("""UPDATE full_recheck_members SET status='retry',reason='lease_expired',updated_at=?,
        next_retry_at=?,lease_until=0 WHERE request_id=? AND reg=? AND attempt_id=? AND lease_token=? AND status='running'""",
        (stamp, stamp, *_claim_where(claim)))
    return "retry"
'''


class EngineTargetTests(unittest.TestCase):
    setUp = FullRecheckRequestTests.setUp
    create = FullRecheckRequestTests.create
    write = FullRecheckRequestTests.write
    claim = FullRecheckRequestTests.claim
    member = FullRecheckRequestTests.member
    watermark = FullRecheckRequestTests.watermark
    receipt = FullRecheckRequestTests.receipt
    def legacy(self):
        scope = dict(vars(ledger))
        exec(LEGACY_FUNCTIONS, scope)
        return scope

    def target(self, engine="engine-v2", generation=1):
        with self.write():
            return ledger.activate_target(self.c, REQUEST, generation=generation, engine_signature=engine, now=1010)

    def complete(self, reg=A, engine="engine-v1", token="old-result"):
        claim = self.claim(reg)
        self.watermark(claim, engine_signature=engine)
        with self.write():
            self.receipt(claim, engine_signature=engine, token=token,
                         metrics={"new_findings": 3, "new_checks": 4, "documents_read": 2})
            ledger.finish_member(self.c, claim, result_token=token)
        return claim

    def reopen(self, engine="engine-v2", generation=1, **kw):
        with self.write():
            return ledger.reopen_completed(self.c, REQUEST, engine_signature=engine, generation=generation, now=1011, **kw)

    def test_target_cas_and_equal_generation_conflict_are_durable(self):
        before = self.create()
        self.assertTrue(self.target())
        self.assertFalse(self.target())
        target = ledger.engine_target(self.c, REQUEST)
        with self.assertRaises(ledger.TargetConflict):
            self.target("engine-other")
        self.assertTrue(self.target("engine-v3", 2))
        with self.assertRaises(ledger.TargetConflict):
            self.target("engine-v2", 1)
        self.assertEqual(ledger.engine_target(self.c, REQUEST)["generation"], 2)
        self.assertEqual(target["generation"], 1)
        ledger.initialize(self.c)
        with sqlite3.connect(self.path) as reopened:
            self.assertEqual(ledger.engine_target(reopened, REQUEST), ledger.engine_target(self.c, REQUEST))
        after = ledger.inventory(self.c, REQUEST)
        for key in ("actual_count", "membership_digest", "db_snapshot_at", "source_captured_at", "manual_excluded_count"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["current_completed"], 0)
        self.assertEqual(after["current_pending"], 3)

    def test_reopen_is_bounded_completed_only_and_keeps_success_across_partial_retry(self):
        self.create()
        old = self.complete()
        running = self.claim(C)
        untouched = {r: self.member(r) for r in (B, C)}
        queue = self.c.execute("SELECT * FROM v332_audit_queue").fetchall()
        self.target()
        self.assertEqual(self.reopen(limit=1), 1)
        self.assertEqual(self.reopen(limit=1), 0)
        self.assertEqual({r: self.member(r) for r in (B, C)}, untouched)
        self.assertEqual(self.c.execute("SELECT * FROM v332_audit_queue").fetchall(), queue)
        before = self.c.execute("SELECT * FROM full_recheck_receipts").fetchall()
        self.assertEqual(len(before), 1)
        fresh = self.claim(now=1012, engine_signature="engine-v2", generation=1)
        with self.write():
            ledger.set_review_watermark(self.c, fresh, source_version="source-v1", engine_signature="engine-v2", now=1013)
            self.receipt(fresh, full=False, engine_signature="engine-v2", token="partial", now=1014,
                         metrics={"documents_read": 1, "new_findings": 0})
            ledger.defer_member(self.c, fresh, status="retry", reason="unreadable_documents", now=1015)
        self.assertEqual(self.c.execute("SELECT * FROM full_recheck_receipts WHERE attempt_id=?", (old.attempt_id,)).fetchall(), before)
        self.assertEqual((self.member()["total_new_findings"], self.member()["total_new_checks"]), (3, 4))
        self.assertEqual(ledger.inventory(self.c, REQUEST)["current_completed"], 0)
        self.assertEqual(self.member(C)["attempt_id"], running.attempt_id)

    def test_old_source_finishes_after_scan_and_is_reopened_despite_legacy_completed(self):
        self.c.execute("DELETE FROM v332_audit_queue WHERE reg<>?", (A,))
        self.create()
        old = self.claim()
        self.watermark(old)
        with self.write():
            self.receipt(old, token="old-result")
        self.target()
        self.assertEqual(self.reopen(), 0)  # Active work is never reset.
        with self.write():
            self.legacy()["finish_member"](self.c, old, result_token="old-result")
        summary = ledger.inventory(self.c, REQUEST)
        self.assertEqual(summary["legacy_status"], "completed")
        self.assertEqual(summary["status"], "active")
        self.assertEqual((summary["current_completed"], summary["stale_completed"]), (0, 1))
        self.assertEqual(self.reopen(), 1)
        # Crash/restart after reopen but before an OLD build clears the row.
        with sqlite3.connect(self.path) as restarted:
            self.assertEqual(restarted.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0], 1)
        with self.write():
            legacy_claim = self.legacy()["claim_member"](self.c, REQUEST, A, now=1012)
        self.assertIsNotNone(legacy_claim)
        self.assertEqual(self.member()["result_token"], "")
        self.assertEqual(self.c.execute("SELECT result_token FROM full_recheck_receipts WHERE attempt_id=?", (old.attempt_id,)).fetchone()[0], "old-result")

    def test_old_source_recovery_after_activation_remains_historical_until_scan(self):
        self.create()
        old = self.claim(lease_seconds=2)
        self.watermark(old)
        with self.write():
            self.receipt(old, now=1002.5)
        self.target()
        with self.write():
            self.assertEqual(self.legacy()["recover_expired"](self.c, old, now=1012), "completed")
        self.assertEqual(ledger.inventory(self.c, REQUEST)["current_completed"], 0)
        self.assertEqual(self.reopen(), 1)

    def test_new_ack_and_recovery_do_not_acknowledge_obsolete_full_receipt(self):
        self.create()
        first = self.claim(A, lease_seconds=20)
        second = self.claim(B, lease_seconds=20)
        for claim in (first, second):
            self.watermark(claim)
            with self.write():
                self.receipt(claim)
        self.target()
        with self.write():
            self.assertEqual(ledger.finish_member(self.c, first, result_token="result-v1"), "retry")
            self.assertEqual(ledger.recover_expired(self.c, second, now=1030), "retry")
        for reg in (A, B):
            self.assertEqual((self.member(reg)["status"], self.member(reg)["reason"]), ("retry", "engine_changed"))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0], 2)

    def test_claims_require_target_engine_generation_and_matching_completion_is_idempotent(self):
        self.c.execute("DELETE FROM v332_audit_queue WHERE reg<>?", (A,))
        self.create()
        self.target()
        self.assertIsNone(self.claim())
        self.assertIsNone(self.claim(engine_signature="engine-v2", generation=0))
        self.assertIsNone(self.claim(engine_signature="engine-v1", generation=1))
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, now=1012), [])
        self.assertEqual(ledger.select_candidates(self.c, REQUEST, now=1012, engine_signature="engine-v2", generation=1), [A])
        claim = self.claim(now=1012, engine_signature="engine-v2", generation=1)
        with self.write():
            ledger.set_review_watermark(self.c, claim, source_version="source-v1", engine_signature="engine-v2", now=1013)
            self.receipt(claim, engine_signature="engine-v2", now=1014)
            self.assertEqual(ledger.finish_member(self.c, claim, result_token="result-v1"), "completed")
            self.assertEqual(ledger.finish_member(self.c, claim, result_token="result-v1"), "completed")
        self.assertEqual(self.reopen(), 0)
        summary = ledger.inventory(self.c, REQUEST)
        self.assertEqual((summary["status"], summary["current_completed"], summary["current_pending"]), ("completed", 1, 0))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0], 1)

    def test_pre_upgrade_disabled_recovery_requires_no_new_tables(self):
        self.create()
        claim = self.claim(lease_seconds=2)
        self.watermark(claim)
        with self.write():
            self.receipt(claim, now=1002.5)
        self.c.execute("DROP TABLE full_recheck_targets")
        self.c.execute("DROP TABLE full_recheck_receipts")
        with self.write():
            self.assertEqual(ledger.recover_expired(self.c, claim, now=1010), "completed")
        self.assertEqual(self.member()["status"], "completed")
        self.assertFalse(ledger._exists(self.c, "full_recheck_targets"))
        self.assertFalse(ledger._exists(self.c, "full_recheck_receipts"))

    def test_completed_catchup_pages_are_bounded_and_restart_fair(self):
        self.create()
        for reg in (A, B, C):
            self.complete(reg, token="receipt-" + reg)
        self.target()
        for expected in (1, 2, 3):
            self.assertEqual(self.reopen(limit=1), 1)
            ledger.initialize(self.c)  # Restart initialization cannot reset progress.
            self.assertEqual(ledger.inventory(self.c, REQUEST)["states"]["retry"], expected)
        self.assertEqual(self.reopen(limit=1), 0)
        self.assertEqual(ledger.inventory(self.c, REQUEST)["committed_new_findings"], 9)
        for invalid in (False, 0, 129):
            with self.assertRaises(ValueError):
                self.reopen(limit=invalid)

    def test_concurrent_target_activation_cannot_downgrade(self):
        self.create()
        def activate(generation):
            with sqlite3.connect(self.path, timeout=2) as c:
                c.execute("BEGIN IMMEDIATE")
                try:
                    ledger.activate_target(c, REQUEST, generation=generation, engine_signature="engine-" + str(generation))
                except ledger.TargetConflict:
                    return "conflict"
            return "accepted"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(activate, (2, 1)))
        self.assertIn("accepted", results)
        self.assertEqual(ledger.engine_target(self.c, REQUEST)["generation"], 2)
        self.assertEqual(ledger.engine_target(self.c, REQUEST)["engine_signature"], "engine-2")

    def test_process_exit_before_and_after_target_commit_preserves_atomic_history(self):
        self.create()
        claim = self.complete()
        self.c.execute("DELETE FROM full_recheck_receipts")  # Existing production receipt before this upgrade.
        script = """
import os, sqlite3, sys
sys.path.insert(0, sys.argv[3])
import full_recheck_request as ledger
c=sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute('BEGIN IMMEDIATE')
ledger.activate_target(c, sys.argv[2], generation=1, engine_signature='engine-v2', now=1010)
ledger.reopen_completed(c, sys.argv[2], engine_signature='engine-v2', generation=1, limit=1, now=1011)
if sys.argv[4]=='commit': c.commit()
os._exit(73)
"""
        for phase in ("rollback", "commit"):
            result = subprocess.run([sys.executable, "-c", script, str(self.path), REQUEST,
                                     str(Path(ledger.__file__).parent), phase], check=False)
            self.assertEqual(result.returncode, 73)
            ledger.initialize(self.c)
            if phase == "rollback":
                self.assertIsNone(ledger.engine_target(self.c, REQUEST))
                self.assertEqual(self.member()["status"], "completed")
                self.assertEqual(self.c.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0], 0)
            else:
                self.assertEqual(ledger.engine_target(self.c, REQUEST)["engine_signature"], "engine-v2")
                self.assertEqual(self.member()["status"], "retry")
                self.assertEqual(self.c.execute("SELECT attempt_id FROM full_recheck_receipts").fetchone()[0], claim.attempt_id)

    def test_catchup_and_receipt_history_rollback_atomically_without_cross_request_rows(self):
        self.create()
        self.complete()
        self.create(request="unrelated-request")
        other = self.member(request="unrelated-request")
        self.target()
        self.c.execute("DELETE FROM full_recheck_receipts")  # Simulate a pre-upgrade successful receipt.
        self.c.execute("BEGIN IMMEDIATE")
        ledger.reopen_completed(self.c, REQUEST, engine_signature="engine-v2", generation=1, now=1012)
        self.c.rollback()
        self.assertEqual(self.member()["status"], "completed")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0], 0)
        self.assertEqual(self.reopen(), 1)
        self.assertEqual(self.member(request="unrelated-request"), other)
        self.assertEqual(self.c.execute("SELECT DISTINCT request_id FROM full_recheck_receipts").fetchall(), [(REQUEST,)])


class InterleavedReader:
    """Commit on another connection at an exact boundary between SELECTs."""
    def __init__(self, connection, callback):
        self.connection = connection
        self.callback = callback
        self.triggered = False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, parameters=()):
        if "AND status='completed' AND full_verified_read=1" in sql and not self.triggered:
            self.triggered = True
            self.callback()
        return self.connection.execute(sql, parameters)


class InventorySnapshots(unittest.TestCase):
    create = FullRecheckRequestTests.create
    write = FullRecheckRequestTests.write

    def setUp(self):
        FullRecheckRequestTests.setUp(self)
        self.assertEqual(self.c.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        self.reader = sqlite3.connect(self.path, isolation_level=None, timeout=.2)
        self.addCleanup(self.reader.close)
        self.reader.execute("PRAGMA query_only=ON")
        self.statements = []
        self.reader.set_trace_callback(self.statements.append)
        self.c.execute("DELETE FROM v332_audit_queue")

    def prepare(self, count=1):
        regs = [str(32617000000 + i) for i in range(count)]
        self.c.executemany("INSERT INTO purchases VALUES(?,'ООО Fixture','Fixture')", ((reg,) for reg in regs))
        self.create(regs, expected_count=count)
        with self.write():
            ledger.activate_target(self.c, REQUEST, generation=2, engine_signature="fixed-engine", now=1000)
            for index, reg in enumerate(regs):
                claim = ledger.claim_member(self.c, REQUEST, reg, now=1001,
                    engine_signature="fixed-engine", generation=2)
                ledger.set_review_watermark(self.c, claim, source_version="fixed-source",
                    engine_signature="fixed-engine", now=1002)
                ledger.record_receipt(self.c, claim, source_version="fixed-source",
                    engine_signature="fixed-engine", result_token="result", full_verified_read=True,
                    metrics={"documents_read":1,"verified_original_documents":1}, now=1003)
                if index < count - 1:
                    ledger.finish_member(self.c, claim, result_token="result")
        return claim

    def finish(self, claim):
        with self.write():
            ledger.finish_member(self.c, claim, result_token="result")

    def test_wal_writer_finishes_between_queries_without_mixed_snapshot(self):
        claim = self.prepare(90)
        before = tuple(self.c.execute("SELECT generation,engine_signature FROM full_recheck_targets").fetchone())
        mutations = self.reader.total_changes
        interleaved = InterleavedReader(self.reader, lambda:self.finish(claim))
        first = ledger.inventory(interleaved, REQUEST)
        self.assertTrue(interleaved.triggered)
        self.assertEqual((first["states"]["completed"], first["current_completed"], first["stale_completed"]), (89,89,0))
        self.assertEqual((first["states"]["running"], first["current_pending"]), (1,1))
        self.assertEqual(first["legacy_status"], "active")
        self.assertFalse(self.reader.in_transaction)
        self.assertEqual(self.reader.total_changes, mutations)
        self.assertEqual(tuple(self.c.execute("SELECT generation,engine_signature FROM full_recheck_targets").fetchone()), before)
        # The callback committed despite the open read snapshot. A fresh read
        # sees that completion, including the corresponding request status.
        second = ledger.inventory(self.reader, REQUEST)
        self.assertEqual((second["states"]["completed"], second["current_completed"], second["stale_completed"]), (90,90,0))
        self.assertEqual((second["legacy_status"], second["status"]), ("completed","completed"))
        control = [statement.upper() for statement in self.statements if statement.upper().startswith(("BEGIN","COMMIT","ROLLBACK"))]
        self.assertEqual(control, ["BEGIN","ROLLBACK","BEGIN","ROLLBACK"])

    def test_caller_owned_read_snapshot_remains_open_until_caller_ends_it(self):
        claim = self.prepare()
        self.reader.execute("BEGIN")
        ledger.inventory(self.reader, REQUEST)
        self.statements.clear()
        interleaved = InterleavedReader(self.reader, lambda:self.finish(claim))
        first = ledger.inventory(interleaved, REQUEST)
        self.assertTrue(self.reader.in_transaction)
        self.assertEqual(first["current_completed"], 0)
        self.assertEqual(ledger.inventory(self.reader, REQUEST)["current_completed"], 0)
        self.assertFalse(any(sql.upper().startswith(("BEGIN","COMMIT","ROLLBACK")) for sql in self.statements))
        self.reader.rollback()
        self.assertEqual(ledger.inventory(self.reader, REQUEST)["current_completed"], 1)

    def test_caller_owned_write_is_neither_committed_rolled_back_nor_mutated(self):
        self.prepare()
        self.c.execute("BEGIN")
        self.c.execute("UPDATE full_recheck_requests SET expected_count=77 WHERE request_id=?", (REQUEST,))
        changes = self.c.total_changes
        first = ledger.inventory(self.c, REQUEST)
        self.assertEqual(first["expected_count"], 77)
        self.assertTrue(self.c.in_transaction)
        self.assertEqual(self.c.total_changes, changes)
        self.assertEqual(ledger.inventory(self.reader, REQUEST)["expected_count"], 1)
        self.c.rollback()
        self.assertEqual(ledger.inventory(self.reader, REQUEST)["expected_count"], 1)

    def test_owned_snapshot_is_closed_when_a_later_query_raises(self):
        self.prepare()
        class FailedReader(InterleavedReader):
            def execute(self, sql, parameters=()):
                if sql.startswith("SELECT reason,"):
                    raise sqlite3.OperationalError("synthetic interrupted read")
                return super().execute(sql, parameters)
        with self.assertRaisesRegex(sqlite3.OperationalError,"synthetic interrupted read"):
            ledger.inventory(FailedReader(self.reader, lambda:None), REQUEST)
        self.assertFalse(self.reader.in_transaction)
        self.assertEqual(ledger.inventory(self.reader, REQUEST)["actual_count"], 1)

    def test_caller_owned_transaction_survives_read_exception(self):
        self.prepare()
        self.c.execute("BEGIN")
        self.c.execute("UPDATE full_recheck_requests SET expected_count=88 WHERE request_id=?", (REQUEST,))
        class FailedReader(InterleavedReader):
            def execute(self, sql, parameters=()):
                if sql.startswith("SELECT reason,"):
                    raise sqlite3.OperationalError("synthetic interrupted read")
                return super().execute(sql, parameters)
        with self.assertRaisesRegex(sqlite3.OperationalError,"synthetic interrupted read"):
            ledger.inventory(FailedReader(self.c, lambda:None), REQUEST)
        self.assertTrue(self.c.in_transaction)
        self.assertEqual(self.c.execute("SELECT expected_count FROM full_recheck_requests").fetchone()[0], 88)
        self.assertEqual(ledger.inventory(self.reader, REQUEST)["expected_count"], 1)
        self.c.rollback()

    def test_missing_request_and_invalid_request_leave_no_owned_transaction(self):
        self.assertIsNone(ledger.inventory(self.reader, REQUEST))
        self.assertFalse(self.reader.in_transaction)
        with self.assertRaises(ValueError):
            ledger.inventory(self.reader, "")
        self.assertFalse(self.reader.in_transaction)


if __name__ == "__main__":
    unittest.main()
