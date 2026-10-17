"""Regression tests for account dashboard contracts."""
import unittest
from pathlib import Path
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
        self.assertIn('item.batch_id',s)
        self.assertIn('item.reg_number',s)
    def test_thematic_unlink_is_owner_scoped(self):
        api=(ROOT/'scripts/account_private_api_v1.py').read_text()
        self.assertIn('@router.delete("/thematic-batches/{batch_id}")',api)
        self.assertIn('WHERE user_id=%s AND batch_id=%s RETURNING batch_id',api)
        self.assertIn('require_origin(request)',api)
        pages=(ROOT/'scripts/account_pages_v1.py').read_text()
        self.assertIn("Убрать из кабинета",pages)
    def test_thematic_bookmark_requires_owner_and_origin(self):
        api=(ROOT/'scripts/account_private_api_v1.py').read_text()
        pages=(ROOT/'scripts/account_pages_v1.py').read_text()
        self.assertIn('@router.post("/thematic-batches/{batch_id}"',api)
        self.assertIn('require_origin(request)',api)
        self.assertIn('VALUES (%s,%s) ON CONFLICT DO NOTHING',api)
        self.assertIn("method:'DELETE'",pages)
    def test_no_email_confirmation_required(self):
        a=(ROOT/'scripts/account_api_v1.py').read_text()
        b=(ROOT/'scripts/account_repository_v1.py').read_text()
        self.assertNotIn('send_verification_email(',a)
        self.assertNotIn('email_verified_at IS NOT NULL',b)
if __name__=='__main__': unittest.main()
