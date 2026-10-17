import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException
from scripts.account_private_api_v1 import memo_download, batch_details, list_memos, list_batches


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
        self.sql = sql
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class PrivateResourcesTests(unittest.TestCase):
    def test_private_memo_list_filters_owner(self):
        db = DatabaseStub()
        with patch("scripts.account_private_api_v1.current_account", return_value=("alice",)), patch("scripts.account_private_api_v1.connect", return_value=db):
            self.assertEqual(list_memos(object(), ResponseStub()), [])
        self.assertEqual(db.params, ("alice",))
        self.assertIn("WHERE user_id=%s", db.sql)

    def test_private_thematic_list_filters_owner(self):
        db = DatabaseStub()
        with patch("scripts.account_private_api_v1.current_account", return_value=("alice",)), patch("scripts.account_private_api_v1.connect", return_value=db):
            self.assertEqual(list_batches(object(), ResponseStub()), [])
        self.assertEqual(db.params, ("alice",))
        self.assertIn("WHERE user_id=%s", db.sql)

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
