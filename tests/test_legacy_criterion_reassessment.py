"""Retain historical checklist labels without endorsing them as legal rules."""
import json
import unittest

import payment_finding_reassessment as assessment
import v352_dashboard_truth as dashboard
import v384_thematic_findings as thematic
from vp_inspection_criteria import CRITERIA, BY_KEY
from vp_inspection_engine import _rule_known
from test_payment_finding_reassessment import REG, BATCH, ENGINE, PAYMENT


class LegacyCriterionReassessment(unittest.TestCase):
    from test_payment_finding_reassessment import PaymentAssessmentStorage as _Fixture
    connect = _Fixture.connect
    connection = _Fixture.connection
    retained = _Fixture.retained
    event = _Fixture.event
    record = _Fixture.record

    def setUp(self):
        self._Fixture.setUp(self)

    def saved_criterion(self, key):
        criterion = BY_KEY[key]
        item = {**PAYMENT, "code": key, "category": key,
                "title": criterion.title, "law": criterion.law}
        with self.connection() as db:
            db.execute("UPDATE findings SET code=?,title=?,law=?", (key, item["title"], item["law"]))
            db.execute("UPDATE thematic_findings SET code=?,category=?,title=?,law=?",
                       (key, key, item["title"], item["law"]))
            db.execute("UPDATE thematic_batches SET categories=?", (json.dumps([key]),))
            db.execute("UPDATE approved_decisions SET finding_key=?",
                       (thematic.canonical_item(REG, item)["finding_key"],))
        return item

    def assert_review_in_saved_views(self, item):
        with self.connection() as db:
            overlay = assessment.apply_general_reassessments(db, REG, [item])
            self.assertEqual(overlay[0].get("effective_status", "confirmed"), "requires_review")
            self.assertTrue(overlay[0]["legal_reassessment"]["reason"])
            self.assertEqual(dashboard.load_counts(db, [REG]).get(REG, 0), 0)
            rows = thematic.read_thematic_items(db, BATCH, REG)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["effective_status"], "requires_review")
            self.assertTrue(assessment.revision(db, BATCH)[1])

    def test_all_19_historical_labels_remain_review_without_fresh_proof(self):
        for criterion in CRITERIA:
            with self.subTest(criterion=criterion.key):
                item = self.saved_criterion(criterion.key)
                before = self.retained()
                self.assertFalse(_rule_known(item))
                self.assertTrue(assessment.is_reassessable_finding(item))
                self.assert_review_in_saved_views(item)
                self.assertEqual(self.retained(), before)

    def test_fresh_pass_cannot_register_a_row_label_as_a_confirmation(self):
        for criterion in CRITERIA:
            with self.subTest(criterion=criterion.key):
                item = self.saved_criterion(criterion.key)
                alleged = {**item, "legal_basis_verified": True,
                    "statutory_breach_verified": True, "criterion_complete": True}
                for scope in ("general", "thematic"):
                    event = self.event([alleged], scope)
                    self.assertEqual(json.loads(event["confirmed_json"]), [])
                    self.assertEqual(event["audit_state"], "completed")

    def test_completed_current_recheck_retains_history_without_confirming_labels(self):
        item = self.saved_criterion("national_1875")
        before = self.retained()
        self.record([item])
        self.record([item], "thematic")
        self.assert_review_in_saved_views(item)
        self.assertEqual(self.retained(), before)

    def test_old_serialized_label_cannot_be_reconfirmed_by_a_matching_journal_key(self):
        item = self.saved_criterion("bid_security")
        before = self.retained()
        for scope in ("general", "thematic"):
            event = self.event([], scope)
            # Simulate an old journal whose metadata/key still match. The
            # label cannot become a substantive rule merely through storage.
            event["confirmed_json"] = json.dumps([{**item,
                "evidence_key": assessment.evidence_key(item)}])
            with self.connection() as db:
                assessment.record_assessment(db, event)
                if scope == "thematic":
                    db.execute("UPDATE thematic_items SET status='completed',"
                        "last_semantic_version=?,last_engine_signature=? WHERE batch_id=? AND reg=?",
                        (event["source_version"], ENGINE, BATCH, REG))
        self.assert_review_in_saved_views(item)
        self.assertEqual(self.retained(), before)

    def test_historical_check_rows_also_require_a_substantive_rule(self):
        item = self.saved_criterion("national_1875")
        with self.connection() as db:
            db.execute("DELETE FROM findings")  # Isolated fixture only.
            db.execute("INSERT INTO checks(reg_number,field_path,status,item,law,evidence,document,source_url)"
                " VALUES(?,?,'violation',?,?,?,?,?)", (REG, item["code"], *(item[key] for key in
                ("title", "law", "evidence", "document", "source_url"))))
        before = self.retained()
        self.assert_review_in_saved_views(item)
        self.assertEqual(self.retained(), before)

    def test_current_registered_rule_still_confirms(self):
        before = self.retained()
        self.record([PAYMENT])
        self.record([PAYMENT], "thematic")
        with self.connection() as db:
            self.assertEqual(dashboard.load_counts(db, [REG]), {REG: 1})
            rows = thematic.read_thematic_items(db, BATCH, REG)
            self.assertEqual(rows[0]["effective_status"], "confirmed")
        self.assertEqual(self.retained(), before)


if __name__ == "__main__":
    unittest.main()
