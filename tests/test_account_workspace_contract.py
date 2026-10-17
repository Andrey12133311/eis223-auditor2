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
    def test_no_email_confirmation_required(self):
        a=(ROOT/'scripts/account_api_v1.py').read_text()
        b=(ROOT/'scripts/account_repository_v1.py').read_text()
        self.assertNotIn('send_verification_email(',a)
        self.assertNotIn('email_verified_at IS NOT NULL',b)
if __name__=='__main__': unittest.main()
