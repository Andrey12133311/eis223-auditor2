"""Runtime funnel diagnostics: outcomes, privacy, bounded work and commit isolation."""
import asyncio
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_funnel as funnel
from audit_source_version import SemanticSnapshotError
from audit_write_guard import guarded_transaction
import legal_audit_revision as revision
import v314_eis_metadata as metadata
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import test_general_audit_counters as general_fixture
import test_general_thematic_parity as parity
import test_thematic_persist_lock_scope as thematic_fixture


def records(output):
    return [json.loads(line.split(" ", 1)[1]) for line in output.getvalue().splitlines()
            if line.startswith("AUDIT_FUNNEL ")]


def source(docs):
    return {"docs": docs, "unreadable": 0, "_reconciled_rows": (), "original_expected": len(docs),
            "original_received": len(docs), "source_incomplete": False,
            "source_error": "", "p": {}, "card": {}, "detail": {}}


class FunnelRuntime(unittest.TestCase):
    def general_fixture(self):
        fixture = general_fixture.GeneralAuditCounters()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def thematic_fixture(self):
        fixture = thematic_fixture.ThematicPersistLockScope()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def run_general(self, fixture, docs, findings, checks):
        ns = {**fixture.ns, "analyze": lambda *args: deepcopy(findings),
              "build_checks": lambda *args: deepcopy(checks)}
        output = io.StringIO()
        with patch.object(general, "existing", return_value=(dict(parity.P), [])), \
             patch.object(general, "collect_saved_evidence", new=AsyncMock(return_value=source(docs))), \
             patch.object(metadata, "from_documents", return_value={}), redirect_stdout(output):
            result = asyncio.run(general.inspect_one(ns, general_fixture.REG))
        return result, records(output)

    def test_general_end_to_end_candidates_source_gate_reviews_and_dedup(self):
        fixture = self.general_fixture()
        doc = deepcopy(parity.docs[0])
        first = fixture.finding()
        first.update(document=doc["name"], source_url=doc["meta"]["source_url"])
        later = fixture.checklist(fixture.finding(later=True))
        later.update(document=doc["name"])
        unverified = {**first, "document": "PRIVATE-NAME", "source_url": "SECRET-URL"}
        check = {"status": "check", "item": "PRIVATE-REVIEW", "document": doc["name"],
                 "field_path": "v383-bid-security-msp-scope", "sme_scope": {"state": "unknown"}}
        risk = {"status": "risk", "item": "PRIVATE-RISK", "field_path": "v341-clarification-applicability"}
        ok = {"status": "ok", "item": "PRIVATE-OK", "field_path": "safe"}
        findings = [first, unverified, {"kind": "risk", "evidence": "SECRET-TEXT"}]
        checks = [later, check, risk, ok]
        initial, lines = self.run_general(fixture, [doc], findings, checks)
        self.assertEqual(initial[0], "completed")
        self.assertEqual(len(lines), 1)
        event = lines[0]
        self.assertTrue(event["committed"] and event["persist_applied"])
        self.assertEqual({k: event["counts"][k] for k in ("verified_documents", "analyzer_candidates", "checklist_candidates", "confirmed", "review", "persisted_confirmed", "persisted_review", "persisted_ok")},
                         dict(verified_documents=1, analyzer_candidates=3, checklist_candidates=4,
                              confirmed=2, review=3, persisted_confirmed=2, persisted_review=3, persisted_ok=1))
        self.assertEqual(event["reasons"], dict(source_unverified=1, non_violation=1,
                          msp_scope_unknown=1, applicability_review=1))
        repeated, lines = self.run_general(fixture, [doc], findings, checks)
        self.assertEqual((repeated[1]["new_findings"], repeated[1]["new_checks"]), (0, 0))
        self.assertEqual(lines[0]["counts"]["confirmed"], 2)
        self.assertEqual(lines[0]["counts"]["persisted_confirmed"], 0)
        self.assertEqual(lines[0]["reasons"]["duplicate"], 6)
        serialized = json.dumps(event)
        self.assertEqual(event["reg"], general_fixture.REG)
        for value in ("PRIVATE", "SECRET", doc["name"], doc["meta"]["source_url"]):
            self.assertNotIn(value, serialized)

    def test_no_documents_is_not_reported_as_successful_zero_findings(self):
        fixture = self.general_fixture()
        result, lines = self.run_general(fixture, [], [], [])
        self.assertEqual(result[0], "no_readable_documents")
        self.assertEqual(lines[0]["reasons"], {"no_readable_documents": 1})
        self.assertFalse(lines[0]["committed"])
        self.assertEqual(lines[0]["counts"]["analyzer_candidates"], 0)

    def test_real_detectors_still_report_positive_and_zero_delta_replay(self):
        output = io.StringIO()
        with redirect_stdout(output):
            first, second, selected, found, counts = parity.compare(parity.docs)
        lines = [x for x in records(output) if x["workflow"] == "general"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(first[1]["new_findings"], 3)
        self.assertEqual(second[1]["new_findings"], 0)
        self.assertEqual([x["counts"]["persisted_confirmed"] for x in lines], [3, 0])
        self.assertEqual([x["counts"]["confirmed"] for x in lines], [3, 3])
        self.assertEqual(len(selected[1]), len(found))

    def test_analyzer_output_remains_visible_when_checklist_fails(self):
        fixture = self.general_fixture()
        ns = {**fixture.ns, "analyze": lambda *args: [fixture.finding()],
              "build_checks": lambda *args: (_ for _ in ()).throw(ValueError("checker failed"))}
        output = io.StringIO()
        with patch.object(general, "existing", return_value=(dict(parity.P), [])), \
             patch.object(general, "collect_saved_evidence", new=AsyncMock(return_value=source(parity.docs))), \
             patch.object(metadata, "from_documents", return_value={}), redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, "checker failed"):
                asyncio.run(general.inspect_one(ns, general_fixture.REG))
        event = records(output)[0]
        self.assertEqual(event["state"], "failed")
        self.assertEqual(event["counts"]["analyzer_candidates"], 1)
        self.assertEqual(event["counts"]["checklist_candidates"], 0)
        self.assertFalse(event["committed"])

    def test_general_commit_failure_does_not_report_persisted_rows(self):
        fixture = self.general_fixture()
        with fixture.connection() as c:
            c.execute("CREATE TRIGGER fail_findings BEFORE INSERT ON findings BEGIN SELECT RAISE(ABORT,'fixture'); END")
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(sqlite3.IntegrityError):
            general.persist(fixture.ns, general_fixture.REG, [fixture.finding()], [])
        event = records(output)[0]
        self.assertFalse(event["committed"])
        self.assertEqual(event["counts"]["persisted_confirmed"], 0)
        with fixture.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)

    def test_general_discarded_snapshot_plan_does_not_inflate_gates_or_reviews(self):
        fixture = self.general_fixture()
        first = fixture.finding()
        general.persist(fixture.ns, general_fixture.REG, [first], [])
        review = {"status": "check", "item": "Review", "field_path": "review-fixture"}
        actual = general._plan_audit_results
        calls = []
        def plan(*args, **kwargs):
            result = actual(*args, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                # Real concurrent evidence mutation while no writer is held.
                with fixture.connection() as c:
                    c.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) "
                              "VALUES(?,'concurrent','violation','Other event','different')",
                              (general_fixture.REG,))
            return result
        output = io.StringIO()
        with patch.object(general, "_plan_audit_results", side_effect=plan), redirect_stdout(output):
            self.assertEqual(general.persist(fixture.ns, general_fixture.REG, [first], [review]), (0, 1))
        self.assertEqual(len(calls), 2)
        events = records(output)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reasons"], {"duplicate": 1})
        self.assertEqual(events[0]["counts"]["persist_input"], 2)
        self.assertEqual(events[0]["counts"]["persisted_review"], 1)
        self.assertEqual(events[0]["counts"]["persisted_confirmed"], 0)

    def test_thematic_discarded_snapshot_plan_does_not_inflate_duplicates(self):
        fixture = self.thematic_fixture()
        fast._persist(fixture.ns, fixture.results())
        with fixture.connection() as c:
            c.execute("UPDATE thematic_items SET status='running',reservation_token='lease-a'")
        later = {**fixture.finding, "evidence": "Запрос 08.10.2026; ответ 14.10.2026"}
        actual = fast._plan_thematic_results
        calls = []
        def plan(*args, **kwargs):
            result = actual(*args, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                with fixture.connection() as c:
                    c.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,evidence) "
                              "VALUES(?,?,'national','Concurrent event','different')",
                              (fixture.batch, fixture.reg))
            return result
        output = io.StringIO()
        with patch.object(fast, "_plan_thematic_results", side_effect=plan), redirect_stdout(output):
            self.assertEqual(fast._persist(fixture.ns, fixture.results([fixture.finding, later])), 1)
        self.assertEqual(len(calls), 2)
        events = records(output)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reasons"], {"duplicate": 1})
        self.assertEqual(events[0]["counts"]["persist_input"], 2)
        self.assertEqual(events[0]["counts"]["persisted_confirmed"], 1)
        self.assertEqual(len(fixture.snapshot()[1]), 3)

    def test_retained_result_reuse_does_not_claim_fresh_analysis(self):
        fixture = self.thematic_fixture()
        fixture.item["categories"] = ["dates"]
        old = funnel.new("thematic", fixture.reg)
        old["analysis_observed"] = True
        old["counts"] = {"analyzer_candidates": 7, "verified_documents": 4, "confirmed": 1}
        fixture.item["_audit_funnel"] = old
        pending = {}
        fast._retain_pending_result(pending, fixture.results()[0])
        saved = pending[(fixture.batch, fixture.reg)]["result"]
        self.assertNotIn("_audit_funnel", saved[0])
        output = io.StringIO()
        with patch.object(fast, "_start_item", return_value=True), \
             patch.object(fast, "_watermark", return_value=("source-a", "engine-a")), \
             patch.object(thematic, "independent_review", side_effect=AssertionError("must reuse")), \
             redirect_stdout(output):
            result = asyncio.run(fast._one(fixture.ns, fixture.item, asyncio.Semaphore(1), pending))
            self.assertEqual(result[1], "completed")
            self.assertEqual(result[2], [fixture.finding])
            self.assertEqual(fast._persist(fixture.ns, [result]), 1)
        events = records(output)
        self.assertEqual([event["stage"] for event in events], ["analysis", "persist"])
        self.assertNotEqual(events[0]["trace"], old["trace"])
        self.assertEqual(events[0]["trace"], events[1]["trace"])
        for event in events:
            self.assertFalse(event["analysis_observed"])
            self.assertEqual(event["counts"]["analyzer_candidates"], 0)
            self.assertEqual(event["counts"]["verified_documents"], 0)
            self.assertEqual(event["counts"]["confirmed"], 1)
        self.assertEqual(events[1]["counts"]["persisted_confirmed"], 1)
        self.assertIn("THEMATIC_RESULT_REUSED", output.getvalue())

    def test_general_log_runs_after_intake_guard_is_released(self):
        fixture = self.general_fixture()
        held = []
        observed = []
        @contextmanager
        def guard(reg):
            held.append(reg)
            try:yield
            finally:held.pop()
        ns = {**fixture.ns, "_v295_persist_guard": guard}
        def emit(*args, **kwargs):observed.append(bool(held))
        with patch.object(funnel, "emit", side_effect=emit):
            self.assertEqual(general.persist(ns, general_fixture.REG, [fixture.finding()], []), (1, 0))
        self.assertEqual(observed, [False])

    def test_general_broken_sink_cannot_retry_or_undo_saved_findings(self):
        fixture = self.general_fixture()
        with patch("builtins.print", side_effect=OSError("closed")):
            self.assertEqual(general.persist(fixture.ns, general_fixture.REG, [fixture.finding()], [], "sink-fixture"), (1, 0))
        fixture.assert_counts(1)
        with fixture.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 1)

    def test_no_verified_thematic_documents_preserves_waiting_status(self):
        fixture = self.thematic_fixture()
        doc = deepcopy(parity.docs[0])
        doc["meta"]["source"] = "existing_local_unverified"
        evidence = source([doc])
        evidence["original_received"] = 0
        fixture.item["categories"] = ["dates"]
        ns = {**fixture.ns, "analyze": lambda *args: self.fail("must not analyze unverified thematic originals"),
              "build_checks": lambda *args: self.fail("must not check unverified thematic originals")}
        output = io.StringIO()
        with patch.object(fast, "_start_item", return_value=True), \
             patch.object(fast, "_watermark", return_value=("version", "engine")), \
             patch.object(general, "existing", return_value=(dict(parity.P), [])), \
             patch.object(general, "collect_saved_evidence", new=AsyncMock(return_value=evidence)), \
             redirect_stdout(output):
            result = asyncio.run(fast._one(ns, fixture.item, asyncio.Semaphore(1)))
        self.assertEqual(result[1:3], ("waiting_originals", []))
        event = records(output)[0]
        self.assertEqual(event["reasons"], {"no_verified_documents": 1})
        self.assertEqual(event["counts"]["analyzer_candidates"], 0)
        self.assertFalse(event["committed"])

    def test_general_transaction_retry_discards_uncommitted_diagnostics(self):
        # Exercise the same isolated scope used by each real SQLite attempt.
        output = io.StringIO()
        @funnel.trace_sync("general")
        def attempt(fail):
            funnel.observe("gate", "duplicate")
            if fail:raise sqlite3.OperationalError("database is locked")
            funnel.current_increment("counts", "persisted_confirmed", 1)
            funnel.committed()
        @funnel.trace("general")
        async def run():
            try:await asyncio.to_thread(attempt, True)
            except sqlite3.OperationalError:pass
            await asyncio.to_thread(attempt, False)
            return "completed", {}
        with redirect_stdout(output):asyncio.run(run())
        self.assertEqual(len(records(output)), 1)
        self.assertEqual(records(output)[0]["reasons"], {"duplicate": 1})
        self.assertEqual(records(output)[0]["counts"]["persisted_confirmed"], 1)

    def test_thematic_runtime_gates_and_persist_share_random_trace(self):
        fixture = self.thematic_fixture()
        doc = deepcopy(parity.docs[0])
        finding = {**fixture.finding, "kind": "violation", "document": doc["name"],
                   "source_url": doc["meta"]["source_url"]}
        findings = [finding, {**finding, "law": ""}, {**finding, "evidence": ""},
                    {**finding, "document": "PRIVATE", "source_url": "SECRET"},
                    {**finding, "title": "Оплата договора", "code": "payment", "law": "ПП 1352"},
                    {**finding, "kind": "risk"}]
        checks = [{"status": "check", "field_path": "v383-bid-security-msp-scope", "sme_scope": {"state": "conflict"}}]
        ns = {**fixture.ns, "analyze": lambda *args: findings, "build_checks": lambda *args: checks}
        fixture.item["categories"] = ["dates"]
        output = io.StringIO()
        with patch.object(fast, "_start_item", return_value=True), \
             patch.object(fast, "_watermark", return_value=("version", "engine")), \
             patch.object(general, "existing", return_value=(dict(parity.P), [])), \
             patch.object(general, "collect_saved_evidence", new=AsyncMock(return_value=source([doc]))), \
             redirect_stdout(output):
            result = asyncio.run(fast._one(ns, fixture.item, asyncio.Semaphore(1)))
            self.assertEqual(result[1], "completed")
            self.assertEqual(len(result[2]), 1)
            self.assertEqual(fast._persist(ns, [result]), 1)
        lines = records(output)
        self.assertEqual([x["stage"] for x in lines], ["analysis", "persist"])
        self.assertEqual(lines[0]["trace"], lines[1]["trace"])
        self.assertEqual([event["reg"] for event in lines], [fixture.reg, fixture.reg])
        self.assertEqual(lines[0]["counts"]["verified_documents"], 1)
        self.assertEqual(lines[0]["counts"]["readable_input_documents"], 1)
        self.assertEqual(lines[0]["counts"]["analyzer_candidates"], 6)
        self.assertEqual(lines[0]["counts"]["confirmed"], 1)
        self.assertEqual(lines[0]["counts"]["review"], 1)
        self.assertEqual(lines[0]["reasons"], dict(missing_law=1, missing_evidence=1,
            source_unverified=1, category_unselected=1, non_violation=1, msp_scope_conflict=1))
        self.assertEqual(lines[1]["counts"]["persisted_confirmed"], 1)
        self.assertTrue(lines[1]["committed"] and lines[1]["persist_applied"])

    def test_thematic_fences_are_distinct_from_dedup(self):
        fixture = self.thematic_fixture()
        with fixture.connection() as c:c.execute("UPDATE thematic_batches SET status='stopped'")
        output = io.StringIO()
        with redirect_stdout(output):self.assertEqual(fast._persist(fixture.ns, fixture.results()), 0)
        event = records(output)[0]
        self.assertEqual(event["reasons"], {"campaign_inactive": 1})
        self.assertEqual(event["state"], "superseded")
        self.assertTrue(event["committed"])
        self.assertFalse(event["persist_applied"])
        self.assertEqual(event["counts"]["persisted_confirmed"], 0)

    def test_lost_reservation_failure_is_fenced_and_fresh_owner_can_save(self):
        fixture = self.thematic_fixture()
        fast._persist(fixture.ns, fixture.results())
        with fixture.connection() as c:
            c.execute("UPDATE thematic_items SET status='queued',reservation_token='lease-a'")
        fixture.item["categories"] = ["dates"]
        before = fixture.snapshot()[1]
        def stale_write():
            # Real startup recovery can invalidate an older process's attempt.
            fast.install(fixture.ns)
            with fixture.connection() as c:
                c.execute("UPDATE thematic_items SET reservation_token='lease-b'")
            with fixture.connection() as c:
                with guarded_transaction(c):
                    c.execute("INSERT INTO unrelated_writer VALUES('must not commit')")
        async def review(*args):
            await asyncio.to_thread(stale_write)
            self.fail("stale source writes must be fenced")
        output = io.StringIO()
        with patch.object(fast, "_watermark", return_value=("source-a", "engine-a")), \
             patch.object(thematic, "independent_review", side_effect=review), redirect_stdout(output):
            result = asyncio.run(fast._one(fixture.ns, fixture.item, asyncio.Semaphore(1)))
            self.assertEqual(result[1:], ("retry", [], "LookupError"))
            self.assertEqual(fast._persist(fixture.ns, [result]), 0)
            fast._release_items(fixture.ns, [fixture.item])
        events = records(output)
        self.assertEqual([event["state"] for event in events], ["retry", "superseded"])
        self.assertEqual(events[0]["trace"], events[1]["trace"])
        self.assertEqual(events[0]["reasons"], {"analysis_failed": 1,
            "analysis_exception_lookup": 1, "analysis_reservation_changed": 1})
        self.assertEqual(events[1]["reasons"], {**events[0]["reasons"], "reservation_changed": 1})
        self.assertFalse(events[1]["persist_applied"])
        self.assertEqual(fixture.snapshot()[1], before)
        with fixture.connection() as c:
            self.assertEqual(tuple(c.execute("SELECT status,reservation_token FROM thematic_items").fetchone()),
                             ("queued", "lease-b"))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM unrelated_writer").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM retained_history").fetchone()[0], 1)
        fresh = {**fixture.item, "reservation_token": "lease-b"}
        later = {**fixture.finding, "evidence": "Запрос 08.10.2026; ответ 14.10.2026"}
        output = io.StringIO()
        with patch.object(fast, "_watermark", return_value=("source-a", "engine-a")), \
             patch.object(thematic, "independent_review", new=AsyncMock(return_value=("completed", [later]))), \
             redirect_stdout(output):
            result = asyncio.run(fast._one(fixture.ns, fresh, asyncio.Semaphore(1)))
            self.assertEqual(fast._persist(fixture.ns, [result]), 1)
        self.assertEqual(fixture.snapshot()[0]["status"], "completed")
        self.assertEqual(len(fixture.snapshot()[1]), 2)
        self.assertNotEqual(records(output)[0]["trace"], events[0]["trace"])
        self.assertEqual(records(output)[0]["reasons"], {})

    def test_source_error_and_later_reservation_change_are_not_guard_failure(self):
        fixture = self.thematic_fixture()
        fixture.item["categories"] = ["dates"]
        with fixture.connection() as c:
            c.execute("UPDATE thematic_items SET status='queued'")
        def watermark(*args):
            with fixture.connection() as c:
                c.execute("UPDATE thematic_items SET reservation_token='lease-b'")
            raise SemanticSnapshotError("concurrent_file_change", "SECRET-PATH")
        output = io.StringIO()
        with patch.object(fast, "_watermark", side_effect=watermark), \
             patch.object(thematic, "independent_review", side_effect=AssertionError("must not analyze")), \
             redirect_stdout(output):
            result = asyncio.run(fast._one(fixture.ns, fixture.item, asyncio.Semaphore(1)))
            self.assertEqual(result[1:], ("retry", [], "source_snapshot_concurrent_file_change"))
            self.assertEqual(fast._persist(fixture.ns, [result]), 0)
        events = records(output)
        self.assertEqual([event["state"] for event in events], ["retry", "superseded"])
        self.assertEqual(events[0]["reasons"], {"analysis_failed": 1,
            "analysis_exception_source_snapshot": 1, "analysis_source_changed": 1})
        self.assertEqual(events[1]["reasons"], {**events[0]["reasons"], "reservation_changed": 1})
        self.assertNotIn("SECRET", output.getvalue())

    def test_owned_source_failure_keeps_retry_and_prior_watermarks(self):
        fixture = self.thematic_fixture()
        fast._persist(fixture.ns, fixture.results())
        fixture.item["categories"] = ["dates"]
        with fixture.connection() as c:
            c.execute("UPDATE thematic_items SET status='queued',reservation_token='lease-a'")
        before = fixture.snapshot()
        output = io.StringIO()
        with patch.object(fast, "_watermark", side_effect=SemanticSnapshotError("time_budget")), \
             redirect_stdout(output):
            result = asyncio.run(fast._one(fixture.ns, fixture.item, asyncio.Semaphore(1)))
            self.assertEqual(fast._persist(fixture.ns, [result]), 1)
        row, findings, _ = fixture.snapshot()
        self.assertEqual(findings, before[1])
        self.assertEqual((row["status"], row["reservation_token"], row["retry_attempts"]), ("retry", "", 1))
        self.assertGreater(row["next_retry_at"], 0)
        for key in ("last_source_version", "last_semantic_version", "last_engine_signature"):
            self.assertEqual(row[key], before[0][key])
        self.assertTrue(records(output)[1]["persist_applied"])
        self.assertEqual(records(output)[1]["reasons"], {"analysis_failed": 1,
            "analysis_exception_source_snapshot": 1, "analysis_source_budget": 1})

    def test_unrelated_lookup_error_is_not_reported_as_reservation_guard_failure(self):
        fixture = self.thematic_fixture()
        fixture.item["categories"] = ["dates"]
        with fixture.connection() as c:c.execute("UPDATE thematic_items SET status='queued'")
        async def review(*args):
            # The row may independently change after an unrelated failure;
            # persist supersession alone does not identify its original cause.
            with fixture.connection() as c:
                c.execute("UPDATE thematic_items SET reservation_token='lease-b'")
            raise LookupError("SECRET missing lookup data")
        output = io.StringIO()
        with patch.object(fast, "_watermark", return_value=("source-a", "engine-a")), \
             patch.object(thematic, "independent_review", side_effect=review), redirect_stdout(output):
            result = asyncio.run(fast._one(fixture.ns, fixture.item, asyncio.Semaphore(1)))
            self.assertEqual(fast._persist(fixture.ns, [result]), 0)
        events = records(output)
        self.assertEqual(events[0]["reasons"], {"analysis_failed": 1, "analysis_exception_lookup": 1})
        self.assertEqual(events[1]["reasons"], {**events[0]["reasons"], "reservation_changed": 1})
        self.assertEqual(events[1]["state"], "superseded")
        self.assertNotIn("SECRET", output.getvalue())

    def test_thematic_rollback_emits_no_committed_diagnostic(self):
        fixture = self.thematic_fixture()
        with fixture.connection() as c:
            c.execute("CREATE TRIGGER fail_completed BEFORE UPDATE ON thematic_items WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(sqlite3.IntegrityError):
            fast._persist(fixture.ns, fixture.results())
        self.assertEqual(records(output), [])
        self.assertEqual(fixture.snapshot()[1], [])

    def test_thematic_rollback_then_retry_counts_one_delta_and_keeps_analysis(self):
        fixture = self.thematic_fixture()
        summary = funnel.new("thematic")
        summary["analysis_observed"] = True
        summary["counts"] = {"analyzer_candidates": 1, "confirmed": 1}
        fixture.item["_audit_funnel"] = summary
        before = deepcopy(summary)
        with fixture.connection() as c:
            c.execute("CREATE TRIGGER fail_completed BEFORE UPDATE ON thematic_items WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(sqlite3.IntegrityError):fast._persist(fixture.ns, fixture.results())
            self.assertEqual(fixture.snapshot()[1], [])
            with fixture.connection() as c:c.execute("DROP TRIGGER fail_completed")
            self.assertEqual(fast._persist(fixture.ns, fixture.results()), 1)
        self.assertEqual(summary, before)
        events = records(output)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["trace"], summary["trace"])
        self.assertEqual(events[0]["counts"]["persist_input"], 1)
        self.assertEqual(events[0]["counts"]["persisted_confirmed"], 1)
        self.assertEqual(events[0]["counts"]["confirmed"], 1)
        self.assertEqual(len(fixture.snapshot()[1]), 1)

    def test_emit_runs_after_writer_release_and_cannot_undo_commit(self):
        fixture = self.thematic_fixture()
        original = funnel.emit
        observations = []
        def emit(*args, **kwargs):
            with fixture.connection() as c:
                c.execute("INSERT INTO unrelated_writer VALUES('funnel')")
                observations.append(c.execute("SELECT status FROM thematic_items").fetchone()[0])
            return original(*args, **kwargs)
        with patch.object(funnel, "emit", side_effect=emit):
            self.assertEqual(fast._persist(fixture.ns, fixture.results()), 1)
        self.assertEqual(observations, ["completed"])
        with fixture.connection() as c:c.execute("UPDATE thematic_items SET status='running',reservation_token='lease-a'")
        with patch("builtins.print", side_effect=OSError("closed")):
            self.assertEqual(fast._persist(fixture.ns, fixture.results()), 1)
        self.assertEqual(len(fixture.snapshot()[1]), 1)


class FunnelIsolation(unittest.TestCase):
    def exception_event(self, exc):
        event = funnel.new("thematic")
        token = funnel.CURRENT.set(event)
        try:funnel.observe("analysis_exception", exc)
        finally:funnel.CURRENT.reset(token)
        output = io.StringIO()
        with redirect_stdout(output):funnel.emit(event, "analysis", "retry")
        return records(output)[0], output.getvalue()

    def test_exception_diagnostics_use_only_exact_fixed_classes_and_source_reasons(self):
        for cls, reason in funnel.EXCEPTION_REASONS:
            exc = cls("SECRET")
            event, output = self.exception_event(exc)
            self.assertEqual(event["reasons"].get(reason), 1)
            self.assertNotIn("SECRET", output)
        for label, reasons in funnel.SNAPSHOT_REASONS:
            for reason in reasons:
                with self.subTest(reason=reason):
                    event, output = self.exception_event(SemanticSnapshotError(reason, "SECRET-PATH"))
                    self.assertEqual(event["reasons"], {"analysis_exception_source_snapshot": 1, label: 1})
                    self.assertNotIn("SECRET", output)
        class HostileError(RuntimeError):
            def __str__(self):raise AssertionError("must not read exception text")
            def __getattribute__(self, key):raise AssertionError("must not inspect unknown exceptions")
        HostileError.__name__ = "SECRET-EXCEPTION-NAME"
        event, output = self.exception_event(HostileError("SECRET"))
        self.assertEqual(event["reasons"], {"analysis_exception_unknown": 1})
        self.assertNotIn("SECRET", output)
        class HostileString(str):
            def __hash__(self):raise AssertionError("must not hash custom reasons")
        for reason in ("secret_machine_code", "x" * 100000, HostileString("time_budget"), {"SECRET": 1}):
            exc = SemanticSnapshotError("unknown", "SECRET")
            exc.reason = reason
            event, output = self.exception_event(exc)
            self.assertEqual(event["reasons"], {"analysis_exception_source_snapshot": 1, "analysis_source_unknown": 1})
            self.assertNotIn("SECRET", output)

    def test_sqlite_diagnostics_require_numeric_code_never_exception_text(self):
        for code, expected in ((sqlite3.SQLITE_BUSY, "analysis_sqlite_busy"),
                               (sqlite3.SQLITE_BUSY | (2 << 8), "analysis_sqlite_busy"),
                               (sqlite3.SQLITE_LOCKED, "analysis_sqlite_locked"),
                               (sqlite3.SQLITE_ERROR, None), (None, None),
                               ("SQLITE_BUSY SECRET", None), (True, None)):
            exc = sqlite3.OperationalError("database is locked SECRET")
            if code is not None:exc.sqlite_errorcode = code
            event, output = self.exception_event(exc)
            self.assertEqual(event["reasons"], {"analysis_exception_sqlite_operational": 1,
                             **({expected: 1} if expected else {})})
            self.assertNotIn("SECRET", output)
        # Confirm a native SQLite exception, without synthesizing its message/code.
        fixture = thematic_fixture.ThematicPersistLockScope()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.connection() as owner, fixture.connection() as rival:
            owner.execute("BEGIN IMMEDIATE")
            try:rival.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                event, _ = self.exception_event(exc)
            else:self.fail("expected actual SQLite contention")
        self.assertEqual(event["reasons"], {"analysis_exception_sqlite_operational": 1, "analysis_sqlite_busy": 1})

    def test_exception_diagnostics_are_bounded_and_reallowlisted_at_emit(self):
        event = funnel.new("thematic")
        token = funnel.CURRENT.set(event)
        try:
            for _ in range(10000):
                funnel.observe("analysis_exception", RuntimeError("SECRET"))
                funnel.observe("analysis_exception", SemanticSnapshotError("arbitrary_private_code"))
        finally:funnel.CURRENT.reset(token)
        event["reasons"]["SECRET-UNKNOWN-CODE"] = 1
        output = io.StringIO()
        with redirect_stdout(output):funnel.emit(event, "analysis", "retry")
        self.assertEqual(records(output)[0]["reasons"], {"analysis_exception_runtime": 10000,
            "analysis_exception_source_snapshot": 10000, "analysis_source_unknown": 10000})
        self.assertLess(len(output.getvalue()), 1700)
        self.assertNotIn("SECRET", output.getvalue())
        self.assertNotIn("arbitrary_private_code", output.getvalue())
        class HostileKey(str):
            def __hash__(self):return hash("analysis_exception_runtime")
            def __eq__(self, other):return other == "analysis_exception_runtime"
        # A forged equal-to-allowlist key must never become the output key.
        event["reasons"] = {HostileKey("SECRET-FORGED-KEY"): 3}
        output = io.StringIO()
        with redirect_stdout(output):funnel.emit(event, "analysis", "retry")
        self.assertEqual(records(output)[0]["reasons"], {"analysis_exception_runtime": 3})
        self.assertNotIn("SECRET", output.getvalue())

    def test_broken_exception_classifier_does_not_change_retry(self):
        fixture = thematic_fixture.ThematicPersistLockScope()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.item["categories"] = ["dates"]
        with fixture.connection() as c:c.execute("UPDATE thematic_items SET status='queued'")
        output = io.StringIO()
        with patch.object(funnel, "EXCEPTION_REASONS", None), \
             patch.object(fast, "_watermark", side_effect=ValueError("SECRET")), redirect_stdout(output):
            result = asyncio.run(fast._one(fixture.ns, fixture.item, asyncio.Semaphore(1)))
            self.assertEqual(result[1:], ("retry", [], "ValueError"))
            self.assertEqual(fast._persist(fixture.ns, [result]), 1)
        self.assertEqual(records(output)[0]["reasons"], {"analysis_failed": 1})
        self.assertEqual(fixture.snapshot()[0]["status"], "retry")
        self.assertNotIn("SECRET", output.getvalue())

    def test_analyzer_kind_counts_are_bounded_disjoint_and_do_not_mutate_outputs(self):
        rows = [{"kind": "violation"}, {"kind": "check"}, {"kind": "risk"},
                {"kind": "PRIVATE-UNKNOWN", "evidence": "SECRET"}, {}, None, "SECRET"]
        before = deepcopy(rows)
        event = funnel.new("general")
        token = funnel.CURRENT.set(event)
        try:funnel.observe("analyzer_results", rows)
        finally:funnel.CURRENT.reset(token)
        self.assertEqual(rows, before)
        self.assertEqual(event["counts"], dict(analyzer_candidates=7, analyzer_violation=1,
            analyzer_check=1, analyzer_risk=1, analyzer_other=2, analyzer_invalid=2))
        output = io.StringIO()
        with redirect_stdout(output):funnel.emit(event, "analysis")
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertNotIn("SECRET", output.getvalue())
        self.assertEqual(len(records(output)), 1)

    def test_analyzer_diagnostics_never_read_evidence_or_freeform_kind_objects(self):
        class Row(dict):
            def get(self, key, *args):
                if key != "kind":raise AssertionError("only fixed kind can be inspected")
                return super().get(key, *args)
        class Unprintable:
            def __str__(self):raise AssertionError("do not serialize an arbitrary kind")
        event = funnel.new("thematic")
        token = funnel.CURRENT.set(event)
        try:funnel.observe("analyzer_results", [Row(kind=Unprintable(), evidence="SECRET")])
        finally:funnel.CURRENT.reset(token)
        self.assertEqual(event["counts"], dict(analyzer_candidates=1, analyzer_other=1))

    def test_fas_log_describes_presence_and_case_scope_without_claiming_verification(self):
        import v324_fas_practice as fas
        cases = [{"classification": "needs_scope", "scope": None},
                 {"classification": "review", "scope": "verified_scope"}]
        ns = {"analyze": lambda *_: [{"kind": "risk"}], "build_checks": lambda *_: []}
        fas.install(ns)
        output = io.StringIO()
        with patch.object(fas, "_cases", return_value=cases), \
             patch.object(fas, "_admission_cases", return_value=[]), redirect_stdout(output):
            result = ns["analyze"]({"reg": "PRIVATE-IDENTIFIER", "price_num": 100}, [])
        line = output.getvalue()
        self.assertEqual(result, [{"kind": "risk"}])
        for expected in ("reg=unavailable", "price_present=True", "agreement_scope_confirmed=True",
                         "agreement_cases=2", "definitive_agreement_cases=0", "collective_admission_cases=0",
                         "agreements_without_confirmed_scope=1", "agreements_application_duty_unproven=1"):
            self.assertIn(expected, line)
        for forbidden in ("PRIVATE", "nmcd_verified", "sme_confirmed", "verified_extra_documents"):
            self.assertNotIn(forbidden, line)

    def test_fixed_cardinality_no_text_reads_and_no_per_document_output(self):
        class Document(dict):
            def get(self, key, *args):
                raise AssertionError("diagnostics must not inspect document fields")
        output = io.StringIO()
        @funnel.trace("general")
        async def run():
            docs = [Document(text="SECRET") for _ in range(20000)]
            funnel.observe("documents_general", docs, 0, 20000, 20000)
            for i in range(20000):
                funnel.observe("gate", "source_unverified")
                funnel.observe("gate", "SECRET-" + str(i))
            return "completed", {}
        with redirect_stdout(output):asyncio.run(run())
        self.assertEqual(len(records(output)), 1)
        self.assertLess(len(output.getvalue()), 1600)
        event = records(output)[0]
        self.assertEqual(event["reasons"], {"source_unverified": 20000})
        self.assertEqual(event["counts"]["verified_documents"], 20000)
        self.assertNotIn("SECRET", output.getvalue())

    def test_concurrent_tasks_and_thread_hops_keep_independent_counts(self):
        output = io.StringIO()
        @funnel.trace("general")
        async def run(count):
            await asyncio.to_thread(funnel.current_increment, "counts", "confirmed", count)
            await asyncio.sleep(0)
            return "completed", {}
        async def all_runs():await asyncio.gather(*(run(i) for i in range(1, 21)))
        with redirect_stdout(output):asyncio.run(all_runs())
        lines = records(output)
        self.assertEqual(sorted(x["counts"]["confirmed"] for x in lines), list(range(1, 21)))
        self.assertEqual(len({x["trace"] for x in lines}), 20)
        self.assertIsNone(funnel.CURRENT.get())

    def test_retained_payload_budget_is_exact_without_diagnostic_metadata(self):
        base = {"batch": "fixture", "reg": "32616324151", "ready": 1}
        plain = (base, "completed", [], "done")
        size = len(json.dumps(plain, ensure_ascii=False).encode("utf-8"))
        metadata = funnel.new("thematic", base["reg"])
        metadata["counts"] = {"analyzer_candidates": 100, "verified_documents": 10}
        item = {**base, "_audit_funnel": metadata}
        result = (item, *plain[1:])
        self.assertGreater(len(json.dumps(result).encode()), size)
        pending = {}
        with patch.object(fast, "PENDING_RESULT_BYTES", size):
            fast._retain_pending_result(pending, result)
        entry = pending[(base["batch"], base["reg"])]
        self.assertEqual(entry["bytes"], size)
        self.assertEqual(entry["result"], plain)
        self.assertNotIn("_audit_funnel", entry["result"][0])
        self.assertIs(item["_audit_funnel"], metadata)
        with patch.object(fast, "PENDING_RESULT_BYTES", size - 1):
            rejected = {}
            fast._retain_pending_result(rejected, result)
        self.assertEqual(rejected, {})

    def test_only_validated_public_procurement_number_can_be_correlated(self):
        @funnel.trace("general", registration="reg")
        async def run(ns, reg):return "completed", {}
        valid = ("32616233661", "32616324151", "1" * 20)
        invalid = (None, 32616233661, "1" * 10, "1" * 21, "１２３４５６７８９０１",
                   "١٢٣٤٥٦٧٨٩٠١", "32616324151\nSECRET", " 32616324151", "32616324151 ",
                   "32616324151' OR 1=1", "owner-secret", {"reg": "32616324151"})
        for value in valid + invalid:
            with self.subTest(value=value):
                output = io.StringIO()
                with redirect_stdout(output):asyncio.run(run({}, value))
                event = records(output)[0]
                if value in valid:self.assertEqual(event["reg"], value)
                else:self.assertNotIn("reg", event)
                self.assertNotIn("SECRET", output.getvalue())
        class Registration(str):
            def __str__(self):return "SECRET-OVERRIDE"
        output = io.StringIO()
        with redirect_stdout(output):asyncio.run(run({}, Registration("32616324151")))
        self.assertEqual(records(output)[0]["reg"], "32616324151")
        self.assertNotIn("SECRET", output.getvalue())
        # Validate again at the sink even if a caller tampers with a saved summary.
        output = io.StringIO()
        event = funnel.new("thematic", "32616324151")
        event["reg"] = "32616324151\nSECRET"
        with redirect_stdout(output):funnel.emit(event, "persist")
        self.assertNotIn("reg", records(output)[0])
        self.assertNotIn("SECRET", output.getvalue())

    def test_cancellation_remains_cancelled_and_never_implies_commit(self):
        output = io.StringIO()
        @funnel.trace("general")
        async def run():
            raise asyncio.CancelledError()
        with redirect_stdout(output), self.assertRaises(asyncio.CancelledError):
            asyncio.run(run())
        event = records(output)[0]
        self.assertEqual(event["state"], "cancelled")
        self.assertEqual(event["reasons"], {"analysis_cancelled": 1})
        self.assertFalse(event["committed"])
        self.assertIsNone(funnel.CURRENT.get())

    def test_diagnostic_internal_failures_preserve_results_and_exceptions(self):
        @funnel.trace("general")
        async def run(fail=False):
            funnel.observe("gate", "source_unverified")
            if fail:raise ValueError("original failure")
            return "completed", {"unchanged": True}
        for target in ("uuid.uuid4", "json.dumps", "_add"):
            with self.subTest(target=target), patch("audit_funnel." + target, side_effect=OSError("diagnostics failed")):
                self.assertEqual(asyncio.run(run()), ("completed", {"unchanged": True}))
                with self.assertRaisesRegex(ValueError, "original failure"):asyncio.run(run(True))
        self.assertIsNone(funnel.CURRENT.get())


class FunnelRevision(unittest.TestCase):
    def fingerprint(self, source):
        return revision.canonical_engine_ast(source, ("independent_review",), "v342_thematic_queue")

    def test_only_named_standalone_inert_observation_is_cosmetic(self):
        base = "def independent_review(rows):\n    return rows\n"
        observed = "import audit_funnel\ndef independent_review(rows):\n    audit_funnel.observe('analyzer_results', rows, rows)\n    return rows\n"
        self.assertEqual(self.fingerprint(base), self.fingerprint(observed))
        mutations = [
            observed.replace("rows, rows)", "mutate(rows), rows)"),
            observed.replace("rows, rows)", "rows, **rows)"),
            observed.replace("    audit_funnel.observe", "    saved = audit_funnel.observe"),
            observed.replace("return rows", "return rows[:1]"),
            observed.replace("'analyzer_results'", "'unrecognized_operation'"),
            observed.replace("audit_funnel.observe", "audit_funnel.other"),
            observed.replace("def independent_review", "@audit_funnel.trace('thematic')\ndef independent_review"),
        ]
        for changed in mutations:
            with self.subTest(changed=changed):self.assertNotEqual(self.fingerprint(base), self.fingerprint(changed))
        self.assertNotEqual(revision.canonical_engine_ast(base, ("independent_review",), "other"),
                            revision.canonical_engine_ast(observed, ("independent_review",), "other"))

    def test_runtime_observations_do_not_hide_legal_gate_and_input_mutations(self):
        path = Path(thematic.__file__)
        original = path.read_text()
        baseline = self.fingerprint(original)
        changes = (
            ('if not str(f.get("law") or "").strip():', 'if False:'),
            ('is_official(d) and str(d.get("text") or "").strip()', 'str(d.get("text") or "").strip()'),
            ('analyzer,p,verified', 'analyzer,p,docs'),
            ('checker,p,detail,card,verified', 'checker,p,detail,card,docs'),
            ('category=match_legal_category(f,categories)', 'category="dates"'),
            ('return ("retry" if incomplete else "completed"),collapse_legal_events(selected)', 'return "completed",selected'),
            ('audit_funnel.observe("checklist_results",checks)', 'audit_funnel.observe("checklist_results",checks.clear())'),
        )
        for before, after in changes:
            self.assertIn(before, original)
            with self.subTest(before=before):self.assertNotEqual(baseline, self.fingerprint(original.replace(before, after, 1)))


if __name__ == "__main__":unittest.main()
