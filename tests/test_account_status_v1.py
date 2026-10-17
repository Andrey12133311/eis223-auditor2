import unittest
from scripts.account_status_v1 import registration_status, can_sign_in, decide


class AccountStatusTests(unittest.TestCase):
    def test_registration_starts_pending(self):
        self.assertEqual(registration_status(), 'pending')
        self.assertFalse(can_sign_in(registration_status()))

    def test_only_approved_signs_in(self):
        for status in ('pending', 'rejected', 'blocked'):
            self.assertFalse(can_sign_in(status))
        self.assertTrue(can_sign_in('approved'))

    def test_admin_approval(self):
        self.assertEqual(decide('admin', 'approved', 'pending', 'approve'), 'approved')
        self.assertEqual(decide('admin', 'approved', 'pending', 'reject'), 'rejected')

    def test_no_user_approval(self):
        with self.assertRaises(PermissionError):
            decide('user', 'approved', 'pending', 'approve')
        with self.assertRaises(PermissionError):
            decide('admin', 'blocked', 'pending', 'approve')
        with self.assertRaises(PermissionError):
            decide('admin', 'approved', 'approved', 'approve')


if __name__ == '__main__':
    unittest.main()
