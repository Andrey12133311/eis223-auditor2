import unittest
from scripts.account_gate_v1 import origin_matches


class OriginEdgeCases(unittest.TestCase):
    def test_reject_embedded_credentials(self):
        self.assertFalse(origin_matches('https://user:pass@example.org', 'https://example.org'))

    def test_reject_non_https(self):
        self.assertFalse(origin_matches('http://example.org', 'https://example.org'))

    def test_accept_exact_https_origin(self):
        self.assertTrue(origin_matches('https://example.org', 'https://example.org'))


if __name__ == '__main__':
    unittest.main()
