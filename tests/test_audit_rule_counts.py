"""Committed rule diagnostics: fixed vocabulary, exact deltas, no writer work."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_funnel as funnel
import audit_rule_counts as rules
import test_general_audit_counters as general_fixture
import test_thematic_persist_lock_scope as thematic_fixture
import test_general_thematic_parity as parity
import v332_all_procurements_audit as general
import v345_thematic_accelerator as fast

AMENDMENT = "223-4-11-AMENDMENT-PUBLICATION-DELAY"
CLARIFICATION = "223-3.2-3-CLARIFICATION-RESPONSE-LATE"


def records(output):
    return [json.loads(line.split(" ", 1)[1]) for line in output.getvalue().splitlines()
            if line.startswith("AUDIT_FUNNEL ")]


class RuleTaxonomy(unittest.TestCase):
    def test_actual_static_confirmed_emitters_only(self):
        self.assertEqual(len(rules.RULE_IDS), 17)
        self.assertEqual(len(set(rules.RULE_IDS)), 17)
        self.assertEqual(rules.count(["1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS"]),
                         {"1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS": 1})
        self.assertEqual(rules.count(rules.RULE_IDS), dict.fromkeys(rules.RULE_IDS, 1))
        # These are actual review-only catalog/checklist codes, not confirmed rules.
        review_codes = ("v383-bid-security-msp-scope", "v336-bid-security-needs-verified-nmcd",
                        "v341-clarification-applicability", "v372-regulation-version",
                        "SME-COLLECTIVE-ADMISSION-PRACTICE-CONFLICT", "223-4-9-1")
        self.assertEqual(rules.count(review_codes), {"unknown": len(review_codes)})

    def test_unknown_and_malicious_codes_are_bounded_and_never_converted(self):
        class Unprintable:
            def __str__(self):raise AssertionError("must not stringify")
            def __hash__(self):raise AssertionError("must not hash")
        class HostileString(str):
            def __hash__(self):raise AssertionError("must not hash subclass")
            def __len__(self):raise AssertionError("must not inspect subclass")
        codes = ["SECRET-PII-https://private/?token=secret", "PRIVATE" * 200000,
                 None, {}, [], Unprintable(), HostileString(AMENDMENT), AMENDMENT + "\nSECRET"]
        self.assertEqual(rules.count(codes), {"unknown": len(codes)})
        self.assertEqual(rules.count("SECRET-" + str(i) for i in range(20000)), {"unknown": 20000})
        self.assertNotIn("SECRET", json.dumps(rules.count(codes)))

    def test_output_allowlist_and_commit_applied_gate(self):
        event = funnel.new("general")
        event["persisted_rules"] = {AMENDMENT: 2, "SECRET": 5, "unknown": 3,
                                    CLARIFICATION: True, "CANCEL-LATE": -1,
                                    "PROTOCOL-LATE": rules.MAX_COUNT + 9}
        for committed, applied in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(committed=committed, applied=applied):
                event.update(committed=committed, persist_applied=applied)
                output = io.StringIO()
                with redirect_stdout(output):funnel.emit(event, "persist")
                expected = {AMENDMENT: 2, "unknown": 3, "PROTOCOL-LATE": rules.MAX_COUNT} if committed and applied else {}
                self.assertEqual(records(output)[0]["persisted_rules"], expected)
                self.assertNotIn("SECRET", output.getvalue())

    def test_no_rule_delta_inherited_by_thematic_retry(self):
        event = funnel.new("thematic")
        event.update(committed=True, persist_applied=True, persisted_rules={AMENDMENT: 10})
        before = deepcopy(event)
        events = []
        pending = funnel.pending(events, {"_audit_funnel": event}, [], "completed")
        output = io.StringIO()
        with redirect_stdout(output):funnel.emit_committed(events)
        self.assertEqual(records(output)[0]["persisted_rules"], {})
        self.assertNotIn("persisted_rules", pending)
        self.assertEqual(event, before)


class CommittedRuleCounts(unittest.TestCase):
    def general(self):
        fixture = general_fixture.GeneralAuditCounters()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def thematic(self):
        fixture = thematic_fixture.ThematicPersistLockScope()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.finding["code"] = CLARIFICATION
        return fixture

    def test_general_counts_inserted_findings_and_violation_checks_only(self):
        fixture = self.general()
        first = fixture.finding()
        later = fixture.checklist(fixture.finding(later=True))
        review = {**later, "status": "check", "field_path": "SECRET-REVIEW"}
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(general.persist(fixture.ns, general_fixture.REG, [first, first], [later, review]), (1, 2))
            self.assertEqual(general.persist(fixture.ns, general_fixture.REG, [first], [later, review]), (0, 0))
        events = records(output)
        self.assertEqual([x["persisted_rules"] for x in events], [{AMENDMENT: 2}, {}])
        self.assertEqual([x["counts"]["persisted_confirmed"] for x in events], [2, 0])
        self.assertEqual(events[0]["counts"]["persisted_review"], 1)
        self.assertNotIn("SECRET", output.getvalue())
        with fixture.connection() as c:
            self.assertEqual(c.execute("SELECT violations FROM purchases").fetchone()[0], 2)

    def test_general_unknown_persisted_code_is_redacted_without_losing_aggregate(self):
        fixture = self.general()
        finding = {**fixture.finding(), "code": "SECRET-CODE-https://private/?token=PII"}
        output = io.StringIO()
        with redirect_stdout(output):general.persist(fixture.ns, general_fixture.REG, [finding], [])
        self.assertEqual(records(output)[0]["persisted_rules"], {"unknown": 1})
        self.assertEqual(records(output)[0]["counts"]["persisted_confirmed"], 1)
        for forbidden in ("SECRET", "private", "PII", finding["document"], finding["evidence"]):
            self.assertNotIn(forbidden, output.getvalue())

    def test_general_rollback_then_retry_never_reports_uncommitted_rule(self):
        fixture = self.general()
        with fixture.connection() as c:
            c.execute("CREATE TRIGGER fail_credit BEFORE UPDATE OF new_findings ON v332_audit_queue "
                      "BEGIN SELECT RAISE(ABORT,'fixture'); END")
        output = io.StringIO()
        with redirect_stdout(output):
            with patch.object(rules, "count", wraps=rules.count) as count:
                with self.assertRaises(sqlite3.IntegrityError):
                    general.persist(fixture.ns, general_fixture.REG, [fixture.finding()], [], "retry")
                count.assert_not_called()
            with fixture.connection() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)
                c.execute("DROP TRIGGER fail_credit")
            general.persist(fixture.ns, general_fixture.REG, [fixture.finding()], [], "retry")
        self.assertEqual([x["persisted_rules"] for x in records(output)], [{}, {AMENDMENT: 1}])

    def test_general_lost_reservation_has_no_rule_delta(self):
        fixture = self.general()
        reg = general.AuditReservation(general_fixture.REG, "lost-token")
        output = io.StringIO()
        with redirect_stdout(output), patch.object(rules, "count", wraps=rules.count) as count:
            with self.assertRaises(LookupError):general.persist(fixture.ns, reg, [fixture.finding()], [])
            count.assert_not_called()
        event = records(output)[0]
        self.assertFalse(event["committed"])
        self.assertEqual(event["persisted_rules"], {})
        with fixture.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)

    def test_general_rebase_reports_only_final_insert_plan(self):
        fixture = self.general()
        actual = general._plan_audit_results
        calls = []
        def plan(*args, **kwargs):
            result = actual(*args, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                with fixture.connection() as c:
                    c.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) "
                              "VALUES(?,'concurrent','violation','Other event','different')", (general_fixture.REG,))
            return result
        output = io.StringIO()
        with patch.object(general, "_plan_audit_results", side_effect=plan), redirect_stdout(output):
            general.persist(fixture.ns, general_fixture.REG, [fixture.finding()], [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(records(output)[0]["persisted_rules"], {AMENDMENT: 1})

    def test_thematic_exact_new_rows_and_unknown_code_are_redacted(self):
        fixture = self.thematic()
        unknown = {**fixture.finding, "code": "SECRET-https://private/?token=PII",
                   "title": "Different PRIVATE title", "evidence": "Independent PRIVATE evidence"}
        output = io.StringIO()
        with redirect_stdout(output):
            fast._persist(fixture.ns, fixture.results([fixture.finding, dict(fixture.finding), unknown]))
            with fixture.connection() as c:c.execute("UPDATE thematic_items SET status='running',reservation_token='lease-a'")
            fast._persist(fixture.ns, fixture.results([fixture.finding, unknown]))
        events = records(output)
        self.assertEqual([x["persisted_rules"] for x in events], [{CLARIFICATION: 1, "unknown": 1}, {}])
        self.assertEqual([x["counts"]["persisted_confirmed"] for x in events], [2, 0])
        for forbidden in ("SECRET", "PRIVATE", "private", "PII"):
            self.assertNotIn(forbidden, output.getvalue())
        self.assertEqual(len(fixture.snapshot()[1]), 2)

    def test_thematic_rollback_then_retry_has_one_rule_delta(self):
        fixture = self.thematic()
        with fixture.connection() as c:
            c.execute("CREATE TRIGGER fail_done BEFORE UPDATE ON thematic_items "
                      "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        output = io.StringIO()
        with redirect_stdout(output):
            with patch.object(rules, "count", wraps=rules.count) as count:
                with self.assertRaises(sqlite3.IntegrityError):fast._persist(fixture.ns, fixture.results())
                count.assert_not_called()
            self.assertEqual(records(output), [])
            self.assertEqual(fixture.snapshot()[1], [])
            with fixture.connection() as c:c.execute("DROP TRIGGER fail_done")
            fast._persist(fixture.ns, fixture.results())
        self.assertEqual([x["persisted_rules"] for x in records(output)], [{CLARIFICATION: 1}])

    def test_thematic_lost_reservation_does_not_classify_planned_rows(self):
        fixture = self.thematic()
        with fixture.connection() as c:c.execute("UPDATE thematic_items SET reservation_token='new-token'")
        output = io.StringIO()
        with redirect_stdout(output), patch.object(rules, "count", wraps=rules.count) as count:
            self.assertEqual(fast._persist(fixture.ns, fixture.results()), 0)
            count.assert_not_called()
        event = records(output)[0]
        self.assertTrue(event["committed"])
        self.assertFalse(event["persist_applied"])
        self.assertEqual(event["persisted_rules"], {})
        self.assertEqual(fixture.snapshot()[1], [])

    def test_thematic_rebase_reports_only_final_insert_plan(self):
        fixture = self.thematic()
        actual = fast._plan_thematic_results
        calls = []
        def plan(*args, **kwargs):
            result = actual(*args, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                with fixture.connection() as c:
                    c.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,evidence) "
                              "VALUES(?,?,'national','Concurrent event','different')", (fixture.batch, fixture.reg))
            return result
        output = io.StringIO()
        with patch.object(fast, "_plan_thematic_results", side_effect=plan), redirect_stdout(output):
            fast._persist(fixture.ns, fixture.results())
        self.assertEqual(len(calls), 2)
        self.assertEqual(records(output)[0]["persisted_rules"], {CLARIFICATION: 1})
        self.assertEqual(len(fixture.snapshot()[1]), 2)

    def test_rule_work_is_after_commit_and_allows_another_writer_on_both_paths(self):
        for workflow in ("general", "thematic"):
            with self.subTest(workflow=workflow):
                fixture = self.general() if workflow == "general" else self.thematic()
                actual = rules.count
                observed = []
                def count(codes):
                    with fixture.connection() as c:
                        c.execute("UPDATE purchases SET customer=customer")
                        table = "findings" if workflow == "general" else "thematic_findings"
                        observed.append(c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
                    return actual(codes)
                with patch.object(rules, "count", side_effect=count):
                    if workflow == "general":general.persist(fixture.ns, general_fixture.REG, [fixture.finding()], [])
                    else:fast._persist(fixture.ns, fixture.results())
                self.assertTrue(observed)
                self.assertEqual(set(observed), {1})

    def test_broken_new_diagnostic_preserves_existing_logs_and_success(self):
        for workflow in ("general", "thematic"):
            with self.subTest(workflow=workflow):
                fixture = self.general() if workflow == "general" else self.thematic()
                output = io.StringIO()
                with patch.object(rules, "count", side_effect=RuntimeError("SECRET")), redirect_stdout(output):
                    if workflow == "general":self.assertEqual(general.persist(fixture.ns, general_fixture.REG, [fixture.finding()], []), (1, 0))
                    else:self.assertEqual(fast._persist(fixture.ns, fixture.results()), 1)
                event = records(output)[0]
                self.assertTrue(event["committed"] and event["persist_applied"])
                self.assertEqual(event["counts"]["persisted_confirmed"], 1)
                self.assertEqual(event["persisted_rules"], {})
                self.assertNotIn("SECRET", output.getvalue())

    def test_real_detectors_nested_general_trace_and_zero_delta_replay(self):
        output = io.StringIO()
        with redirect_stdout(output):first, second, _, found, _ = parity.compare(parity.docs)
        events = [event for event in records(output) if event["workflow"] == "general"]
        expected = dict.fromkeys((CLARIFICATION, "223-3.2-6-CANCELLATION-POSTED-LATE",
                                  "v372-protocol-rejected-bid-no-reasons"), 1)
        self.assertEqual([event["persisted_rules"] for event in events], [expected, {}])
        self.assertEqual((first[1]["new_findings"], second[1]["new_findings"]), (3, 0))
        self.assertEqual(set(expected), {row["code"] for row in found})


if __name__ == "__main__":
    unittest.main()

