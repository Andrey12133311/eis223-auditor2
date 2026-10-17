"""Tests for opt-in isolated thematic worker configuration."""
import unittest
from unittest.mock import patch
from scripts.account_worker_service_v1 import load_adapter, main


class WorkerServiceTests(unittest.TestCase):
    def test_missing_adapter_is_rejected(self):
        with self.assertRaises(ValueError):
            load_adapter("")
        with self.assertRaises(ValueError):
            load_adapter("scripts.account_thematic_worker_v1")
        with self.assertRaises(ValueError):
            load_adapter("os:system")

    def test_existing_callable_adapter_can_be_loaded(self):
        callback = load_adapter("scripts.account_thematic_worker_v1:claim_next")
        self.assertTrue(callable(callback))

    def test_worker_is_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                main()

    def test_worker_needs_database(self):
        with patch.dict("os.environ", {"EIS223_WORKER_ENABLED": "true"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "database"):
                main()


if __name__ == "__main__":
    unittest.main()
