"""Offline tests for the optional LLM adapter (no API calls)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from optional_llm_legal_review import configured, _clean_suggestions, suggest_checks


class LlmReviewTests(unittest.TestCase):
    def test_default_is_disabled(self):
        with patch.dict(os.environ, {"EIS223_LLM_ENABLED": "false",
                                     "EIS223_LLM_API_KEY": "",
                                     "EIS223_LLM_MODEL": ""}):
            self.assertFalse(configured())

    def test_never_marks_violation_confirmed(self):
        output = _clean_suggestions({"suggestions": [
            {"quote": "цитата", "issue": "риск", "legal_question": "проверить"}
        ]}, "Документ")
        self.assertEqual(output[0]["status"], "requires_human_review")
        self.assertFalse(output[0]["confirmed_violation"])

    def test_missing_key_blocks_outbound_request(self):
        with patch.dict(os.environ, {"EIS223_LLM_ENABLED": "false"}):
            with self.assertRaises(RuntimeError):
                suggest_checks(document_name="test", verified_text="sample",
                               receipt_verified=True, consent_external_processing=True)

if __name__ == "__main__":
    unittest.main()
