import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException
from scripts.account_private_api_v1 import memo_download, batch_details


class ResponseStub:
    def __init__(self):
        self.headers = {}


class DatabaseStub:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params):
        self.params = params
        return self

    def fetchone(self):
        return None


class PrivateResourcesTests(unittest.TestCase):
    def test_unknown_memo_returns_404_not_storage_path(self):
        with patch("scripts.account_private_api_v1.current_account", return_value=("alice",)), patch(
            "scripts.account_private_api_v1.connect", return_value=DatabaseStub()
        ):
            with self.assertRaises(HTTPException) as raised:
                memo_download(uuid4(), object(), ResponseStub())
            self.assertEqual(raised.exception.status_code, 404)

    def test_unknown_thematic_batch_returns_404(self):
        with patch("scripts.account_private_api_v1.current_account", return_value=("alice",)), patch(
            "scripts.account_private_api_v1.connect", return_value=DatabaseStub()
        ):
            with self.assertRaises(HTTPException) as raised:
                batch_details("batch1", object(), ResponseStub())
            self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
