"""Regression tests for account dashboard contracts."""
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from fastapi import Response
from scripts import account_workspace_v1 as workspace
from scripts.account_pages_v1 import HOME, THEMATIC
from scripts.account_site_ui_v1 import site_header
ROOT = Path(__file__).resolve().parents[1]
class WorkspaceContracts(unittest.TestCase):
    def test_no_duplicate_private_routes(self):
        a=(ROOT/'scripts/account_workspace_v1.py').read_text()
        b=(ROOT/'scripts/account_private_api_v1.py').read_text()
        for route in ('/memos','/thematic-batches'):
            self.assertNotIn('@router.get("'+route+'")',a)
            self.assertEqual(b.count('@router.get("'+route+'")'),1)
    def test_dashboard_api_fields(self):
        s=(ROOT/'scripts/account_pages_v1.py').read_text()
        self.assertIn('encodeURIComponent(check.id)', THEMATIC)
        self.assertIn('data.batches', THEMATIC)
        self.assertIn('item.reg_number',s)
    def test_thematic_unlink_is_owner_scoped(self):
        api=(ROOT/'scripts/account_private_api_v1.py').read_text()
        self.assertIn('@router.delete("/thematic-batches/{batch_id}")',api)
        self.assertIn('WHERE user_id=%s AND batch_id=%s RETURNING batch_id',api)
        self.assertIn('require_origin(request)',api)
        pages=(ROOT/'scripts/account_pages_v1.py').read_text()
        self.assertIn("Удалить проверку", THEMATIC)
        self.assertIn("if(!confirm(", THEMATIC)
        self.assertIn("'/api/thematic/'+encodeURIComponent(check.id)", THEMATIC)
    def test_thematic_bookmark_requires_owner_and_origin(self):
        api=(ROOT/'scripts/account_private_api_v1.py').read_text()
        pages=(ROOT/'scripts/account_pages_v1.py').read_text()
        self.assertIn('@router.post("/thematic-batches/{batch_id}"',api)
        self.assertIn('require_origin(request)',api)
        self.assertIn('VALUES (%s,%s) ON CONFLICT DO NOTHING',api)
        self.assertIn("method:'DELETE'",pages)
    def test_personal_summary_is_owner_scoped(self):
        api=(ROOT/'scripts/account_workspace_v1.py').read_text()
        page=(ROOT/'scripts/account_pages_v1.py').read_text()
        self.assertIn('@router.get("/summary")',api)
        self.assertIn('WHERE user_id=%s',api)
        owner = uuid4()
        class SummaryDB:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, params):
                self.sql, self.params = sql, params
                return self
            def fetchone(self): return (2, 3, 5, 7)
        db, response = SummaryDB(), Response()
        with patch.object(workspace, "current_account", return_value=(owner, "user", "approved")), \
             patch.object(workspace, "connect", return_value=db):
            result = workspace.my_summary(object(), response)
        self.assertEqual(db.params, (owner, owner, owner, owner))
        self.assertEqual(db.sql.count("WHERE user_id=%s"), 4)
        self.assertEqual(result, {"procurements": 2, "thematic_batches": 3,
                                  "memos": 5, "manual_checks": 7})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn(b"get('/me/summary')", site_header("user"))
    def test_procurement_list_disables_caching(self):
        api=(ROOT/'scripts/account_workspace_v1.py').read_text()
        self.assertIn('def my_procurements(request: Request, response: Response):',api)
        self.assertIn('response.headers.update(no_store_headers())',api)
        self.assertIn("if not REG_NUMBER.fullmatch(reg_number):",api)
    def test_personal_thematic_queue_controls(self):
        page=(ROOT/'scripts/account_pages_v1.py').read_text()
        api=(ROOT/'scripts/account_private_api_v1.py').read_text()
        # The current page uses real owned history, not the retired manual
        # batch-ID widget. Keep backend legacy ownership assertions below.
        self.assertIn("fetch('/api/thematic/history'", THEMATIC)
        self.assertIn("credentials:'same-origin'", THEMATIC)
        self.assertIn("cache:'no-store'", THEMATIC)
        self.assertIn("response.status===401", THEMATIC)
        self.assertIn("Новая тематическая проверка", THEMATIC)
        self.assertIn("open.textContent='Открыть результаты'", THEMATIC)
        self.assertIn("'/thematic#'+encodeURIComponent(check.id)", THEMATIC)
        self.assertIn("method:'DELETE'", THEMATIC)
        self.assertNotIn("prompt(", THEMATIC)
        self.assertNotIn("fetch('/me/thematic-batches'", THEMATIC)
        self.assertIn('@router.delete("/thematic-batches/{batch_id}/procurements/{reg_number}")',api)
        self.assertIn('@router.get("/thematic-batches/{batch_id}/procurements")',api)
        self.assertIn('@router.post("/thematic-batches/{batch_id}/procurements/{reg_number}"',api)
    def test_identity_schema_contains_no_literal_escaped_newlines(self):
        schema=(ROOT/'scripts/account_schema_v1.py').read_text()
        self.assertNotIn(chr(92)+'n',schema)
        self.assertIn('CREATE TABLE IF NOT EXISTS account_thematic_procurements',schema)
        self.assertIn('ON DELETE CASCADE',schema)
    def test_personal_job_enqueue_is_owner_scoped_and_idempotent(self):
        api=(ROOT/'scripts/account_private_api_v1.py').read_text()
        schema=(ROOT/'scripts/account_schema_v1.py').read_text()
        self.assertIn('@router.post("/thematic-batches/{batch_id}/jobs/{reg_number}"',api)
        self.assertIn('@router.get("/thematic-batches/{batch_id}/jobs")',api)
        self.assertIn('FROM account_thematic_procurements',api)
        self.assertIn('ON CONFLICT DO NOTHING RETURNING status',api)
        self.assertIn('WHERE user_id=%s AND batch_id=%s AND reg_number=%s',api)
        self.assertIn('CREATE TABLE IF NOT EXISTS account_thematic_jobs',schema)
        self.assertIn('PRIMARY KEY(user_id, batch_id, reg_number)',schema)
    def test_no_email_confirmation_required(self):
        a=(ROOT/'scripts/account_api_v1.py').read_text()
        b=(ROOT/'scripts/account_repository_v1.py').read_text()
        self.assertNotIn('send_verification_email(',a)
        self.assertNotIn('email_verified_at IS NOT NULL',b)
if __name__=='__main__': unittest.main()
