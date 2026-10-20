import unittest
from scripts.account_session_policy_v1 import cookie_options, no_store_headers, SESSION_MAX_AGE


class SessionPolicyTests(unittest.TestCase):
    def test_cookie_is_secure(self):
        options = cookie_options()
        self.assertTrue(options['httponly'])
        self.assertTrue(options['secure'])
        self.assertEqual(options['samesite'], 'strict')
        self.assertEqual(options['path'], '/')
        self.assertNotIn('max_age', options)
        self.assertNotIn('expires', options)
        self.assertEqual(SESSION_MAX_AGE, 2 * 60 * 60)

    def test_account_responses_must_not_be_cached(self):
        self.assertEqual(no_store_headers()['Cache-Control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
