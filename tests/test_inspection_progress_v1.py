"""Persisted facts, partial coverage and retry provenance for stage presentation."""
import itertools
import json
import shutil
import subprocess
import unittest

from lxml import html

from scripts.inspection_progress_v1 import (MANUAL_SCRIPT, inspection_progress,
    manual_progress, render_inspection_progress)


def snapshot(**updates):
    value = {"purchase": {"documents_found": 3},
             "documents": [{"status": "checked", "text_chars": 150}] * 3,
             "document_total": 3,
             "legal_review": {"state": "completed", "documents_read": 3}}
    value.update(updates)
    return value


def states(**data):
    return {item["key"]: item["state"] for item in inspection_progress(**data)}


class InspectionProgressTests(unittest.TestCase):
    def test_missing_or_zero_data_never_means_completion(self):
        for data in ({}, {"purchase": {}}, {"documents": [], "document_total": 0},
                     {"purchase": {"documents_found": 0}, "documents": [], "document_total": 0}):
            self.assertNotIn("complete", states(**data).values())

    def test_four_known_complete_stages_require_file_and_legal_evidence(self):
        self.assertEqual(list(states(**snapshot()).values()), ["complete"] * 4)

    def test_checked_counts_and_download_job_completion_are_not_file_or_text_evidence(self):
        row = {"reg_number": "32612345678", "job_status": "done", "documents_found": 3,
               "documents_checked": 3, "legal_review_state": "unknown"}
        result = {step["key"]: step["state"] for step in manual_progress(row)}
        self.assertEqual(result, {"request": "complete", "documents": "unknown", "text": "unknown", "legal": "unknown"})

    def test_metadata_or_waiting_originals_do_not_mean_downloaded(self):
        for status in ("metadata", "waiting_for_originals"):
            result = states(**snapshot(documents=[{"status": status}] * 3))
            self.assertEqual(result["documents"], "waiting")
            self.assertEqual(result["text"], "waiting")

    def test_file_saved_without_text_is_a_distinct_result(self):
        result = states(**snapshot(documents=[{"status": "downloaded_no_text"}] * 3,
                                   legal_review={"state": "no_readable_documents", "documents_read": 0}))
        self.assertEqual(result["documents"], "complete")
        self.assertEqual(result["text"], "error")
        self.assertEqual(result["legal"], "error")

    def test_legal_record_does_not_prove_file_coverage(self):
        result = states(**snapshot(documents=[{"status": "checked"}]))
        self.assertEqual(result["legal"], "complete")
        self.assertEqual(result["documents"], "unknown")
        self.assertEqual(result["text"], "unknown")

    def test_truncated_card_rows_cannot_finish_middle_stages(self):
        result = states(**snapshot(purchase={"documents_found": 120},
                                  document_total=120, documents=[{"status": "checked"}] * 80))
        self.assertEqual(result["documents"], "unknown")
        self.assertEqual(result["text"], "unknown")

    def test_new_or_failed_review_does_not_reuse_previous_file_success(self):
        for state in ("pending", "queued", "running", "processing", "failed", "error"):
            result = states(**snapshot(legal_review={"state": state, "documents_read": 3}, has_previous=True))
            self.assertEqual(result["documents"], "unknown", state)
            self.assertEqual(result["text"], "unknown", state)
            self.assertNotEqual(result["legal"], "complete", state)

    def test_previous_completed_legal_state_is_not_current_completion_during_new_download(self):
        result = states(**snapshot(has_previous=True, job_status="leased"))
        self.assertEqual(result["legal"], "unknown")

    def test_active_jobs_are_displayed_without_completing_later_stages(self):
        result = states(request_accepted=True, job_status="leased")
        self.assertEqual(result["documents"], "current")
        self.assertEqual(result["text"], "waiting")
        self.assertEqual(result["legal"], "unknown")

    def test_failed_job_is_not_hidden_by_old_positive_file_counts(self):
        result = states(**snapshot(job_status="error"))
        self.assertEqual(result["documents"], "error")
        self.assertNotEqual(result["text"], "complete")

    def test_known_source_incompleteness_cannot_finish_middle_stages(self):
        for data in (snapshot(purchase={"documents_found": 3, "source_incomplete": True}),
                     snapshot(purchase={"documents_found": 3, "original_expected": 4, "original_received": 3}),
                     snapshot(job_status="partial")):
            result = states(**data)
            self.assertEqual(result["documents"], "unknown")
            self.assertEqual(result["text"], "unknown")

    def test_unreadable_count_remains_visible_beside_readable_rows(self):
        result = states(**snapshot(legal_review={"state": "completed", "documents_read": 3, "documents_unreadable": 1}))
        self.assertEqual(result["text"], "error")
        self.assertEqual(result["legal"], "complete")

    def test_completed_legal_requires_nonzero_read_count(self):
        for value in (None, 0, False, "", "NaN", -1, "-1", "1.5", float("nan")):
            self.assertNotEqual(states(legal_review={"state": "completed", "documents_read": value})["legal"], "complete")

    def test_checked_status_with_explicit_empty_text_is_not_success(self):
        for value in (0, 1, 2, "0", False, "bad"):
            result = states(**snapshot(documents=[{"status": "checked", "text_chars": value}] * 3))
            self.assertEqual(result["text"], "unknown")

    def test_malformed_states_do_not_crash_or_appear_in_ui(self):
        data = snapshot(documents=[{"status": {"unsafe": "state"}}] * 3,
                        legal_review={"state": ["completed"], "documents_read": 3}, job_status={})
        result = states(**data)
        self.assertNotIn("complete", result.values())

    def test_request_acceptance_is_not_inferred_from_a_saved_purchase_alone(self):
        self.assertEqual(states(purchase={"documents_found": 3})["request"], "unknown")

    def test_thematic_state_overrides_general_legal_record(self):
        result = states(**snapshot(thematic={"state": "waiting_originals"}))
        self.assertEqual(result["legal"], "waiting")
        result = states(**snapshot(thematic={"state": "excluded_company"}))
        self.assertEqual(result["legal"], "unknown")

    def test_thematic_completion_uses_its_own_explicit_saved_state(self):
        result = states(**snapshot(thematic={"state": "completed"}, legal_review={"state": "running"}))
        self.assertEqual(result["legal"], "complete")

    def test_native_markup_exposes_four_steps_and_current_without_controls_or_percentages(self):
        markup = render_inspection_progress(**snapshot(legal_review={"state": "running", "documents_read": 3}, has_previous=True))
        root = html.fragment_fromstring(markup, create_parent="div")
        steps = root.xpath('.//ol/li')
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[-1].get("aria-current"), "step")
        self.assertFalse(root.xpath('.//*[@id] | .//button | .//input | .//a'))
        self.assertNotIn("%", root.xpath('.//section')[0].text_content())

    @unittest.skipUnless(shutil.which("node"), "Node is required for Python/JS stage parity")
    def test_manual_browser_model_matches_python_for_status_and_missing_data_matrix(self):
        fixtures = []
        for job, legal, read, previous in itertools.product(
                (None, "queued", "leased", "done", "partial", "error", "future"),
                (None, "pending", "running", "completed", "failed", "no_readable_documents", "future"),
                (None, 0, 3), (False, True)):
            fixtures.append({"reg_number": "32612345678", "job_status": job,
                             "legal_review_state": legal, "legal_review_documents": read,
                             "documents_checked": 3 if previous else None})
        fixtures.extend(({}, {"reg_number": "32612345678", "legal_review_state": {}, "job_status": []},
                         {"reg_number": "bad", "legal_review_state": "completed", "legal_review_documents": 3}))
        script = MANUAL_SCRIPT.split('>', 1)[1].rsplit('</script>', 1)[0]
        runner = "const fs=require('fs');global.window={};\n" + script + "\nprocess.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(x=>window.eis223InspectionProgress.model(x))));"
        response = subprocess.run(["node", "-e", runner], input=json.dumps(fixtures),
                                  text=True, capture_output=True, timeout=10)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(json.loads(response.stdout), [manual_progress(item) for item in fixtures])


if __name__ == "__main__":
    unittest.main()
