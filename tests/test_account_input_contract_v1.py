import unittest
from scripts.account_security_v1 import normalize_email
from scripts.account_gate_v1 import origin_matches


class InputContractTests(unittest.TestCase):
    def test_email_normalization(self):
        self.assertEqual(normalize_email(' TEST@Example.COM '), 'test@example.com')

    def test_origin_validation(self):
        self.assertTrue(origin_matches('https://example.org', 'https://example.org'))
        self.assertFalse(origin_matches('http://example.org', 'https://example.org'))
        self.assertFalse(origin_matches('https://other.example.org', 'https://example.org'))


if __name__ == '__main__':
    unittest.main()
