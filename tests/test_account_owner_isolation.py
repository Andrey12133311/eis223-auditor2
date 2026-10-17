"""Behavioral tests for cross-account isolation without a live database."""
import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException, Response
from scripts import account_private_api_v1 as private
from scripts import account_workspace_v1 as workspace


class FakeDB:
    def __init__(self):
        self.records = []
        self.calls = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params):
        self.calls.append((sql, params))
        self.records = []
        if 'FROM account_thematic_batches' in sql and 'SELECT' in sql:
            self.records = [('queue-a',)] if params == (OWNER_A,) else [('queue-b',)]
        if 'FROM account_memos' in sql and 'SELECT id,' in sql:
            self.records = [(uuid4(), '32616412317', 'private-key', CREATED)] if params == (OWNER_A,) else []
        if 'FROM account_procurements' in sql and 'SELECT reg_number' in sql:
            self.records = [('32616412317', CREATED)] if params == (OWNER_A,) else []
        return self
    def fetchall(self): return self.records


from datetime import datetime, timezone
OWNER_A = uuid4()
OWNER_B = uuid4()
CREATED = datetime.now(timezone.utc)


class OwnerIsolationTests(unittest.TestCase):
    def test_private_lists_filter_by_signed_in_owner(self):
        db = FakeDB()
        for owner in (OWNER_A, OWNER_B):
            with patch.object(private, 'connect', return_value=db), patch.object(private, 'current_account', return_value=(owner, 'user', 'approved')):
                batches = private.list_batches(object(), Response())
                memos = private.list_memos(object(), Response())
            self.assertEqual(batches, [{'batch_id': 'queue-a' if owner == OWNER_A else 'queue-b'}])
            self.assertEqual(len(memos), 1 if owner == OWNER_A else 0)
        self.assertTrue(all(params in ((OWNER_A,), (OWNER_B,)) for sql, params in db.calls))

    def test_procurement_list_uses_current_owner(self):
        db = FakeDB()
        with patch.object(workspace, 'connect', return_value=db), patch.object(workspace, 'current_account', return_value=(OWNER_B, 'user', 'approved')):
            self.assertEqual(workspace.my_procurements(object(), Response()), [])
        self.assertEqual(db.calls[-1][1], (OWNER_B,))

    def test_new_personal_batch_is_owned_and_not_running(self):
        db = FakeDB()
        with patch.object(private, 'connect', return_value=db), patch.object(private, 'current_account', return_value=(OWNER_A, 'user', 'approved')), patch.object(private, 'require_origin'):
            result = private.create_personal_batch(object(), Response())
        self.assertTrue(result['batch_id'].startswith('personal-'))
        self.assertFalse(result['execution_started'])
        self.assertEqual(db.calls[-1][1], (OWNER_A, result['batch_id']))

    def test_manual_personal_id_claim_is_rejected(self):
        db = FakeDB()
        batch_id = 'personal-' + uuid4().hex
        with patch.object(private, 'connect', return_value=db), patch.object(private, 'current_account', return_value=(OWNER_B, 'user', 'approved')), patch.object(private, 'require_origin'):
            with self.assertRaises(HTTPException) as caught:
                private.save_batch(batch_id, object(), Response())
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(db.calls, [])

    def test_owned_personal_batch_status_does_not_start_engine(self):
        db = FakeDB()
        batch_id = 'personal-' + uuid4().hex
        with patch.object(private, 'connect', return_value=db), patch.object(private, 'current_account', return_value=(OWNER_A, 'user', 'approved')), patch.object(private, 'owns_batch', return_value=True):
            result = private.batch_details(batch_id, object(), Response())
        self.assertEqual(result['status'], 'saved')
        self.assertFalse(result['execution_started'])
        self.assertFalse(result['results_available'])

    def test_unowned_personal_batch_is_hidden(self):
        db = FakeDB()
        batch_id = 'personal-' + uuid4().hex
        with patch.object(private, 'connect', return_value=db), patch.object(private, 'current_account', return_value=(OWNER_B, 'user', 'approved')), patch.object(private, 'owns_batch', return_value=False):
            with self.assertRaises(HTTPException) as caught:
                private.batch_details(batch_id, object(), Response())
        self.assertEqual(caught.exception.status_code, 404)

    def test_unowned_memo_download_returns_not_found(self):
        db = FakeDB()
        with patch.object(private, 'connect', return_value=db), patch.object(private, 'current_account', return_value=(OWNER_B, 'user', 'approved')), patch.object(private, 'owned_memo_key', return_value=None) as lookup:
            with self.assertRaises(HTTPException) as caught:
                private.memo_download(uuid4(), object(), Response())
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(lookup.call_args.args[2], OWNER_B)


if __name__ == '__main__':
    unittest.main()
