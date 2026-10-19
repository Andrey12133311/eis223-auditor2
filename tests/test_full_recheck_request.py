"""Synthetic SQLite-only tests for explicit full saved-procurement requests."""
import contextlib
import hashlib
import json
import sqlite3
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
        self.assertEqual(created, {"full_recheck_requests", "full_recheck_members"})

    def test_inventory_contains_only_aggregate_public_safe_fields(self):
        report = self.create()
        text = json.dumps(report)
        for private in (A, B, C, "Unknown private name", "Other organization", "retain-a"):
            self.assertNotIn(private, text)
        self.assertIn("membership_digest", report)
        self.assertEqual(sum(report["states"].values()), report["actual_count"])

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


if __name__ == "__main__":
    unittest.main()
