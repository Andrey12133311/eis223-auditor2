import asyncio
import unittest
from unittest.mock import patch
from scripts.account_eis_adapter_v1 import audit


class AdapterTests(unittest.TestCase):
    def test_adapter_uses_attempt_fencing(self):
        import inspect
        from scripts.account_eis_adapter_v1 import audit
        self.assertEqual(
            list(inspect.signature(audit).parameters),
            ["owner_id", "batch_id", "reg_number", "attempts"],
        )

    def test_invalid_registration_number(self):
        self.assertFalse(audit("owner", "batch", "invalid", 1))

    def test_no_documents_means_no_completed_audit(self):
        async def no_documents(reg):
            return None
        with patch("scripts.account_eis_adapter_v1.collect", new=no_documents):
            with patch("scripts.account_eis_adapter_v1.connect") as connection:
                self.assertFalse(audit("owner", "batch", "32615969056", 1))
                connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
