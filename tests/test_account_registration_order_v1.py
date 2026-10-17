"""Regression: throttle requests before expensive password hashing."""
import inspect
import unittest
from scripts.account_api_v1 import register


class RegistrationOrderTests(unittest.TestCase):
    def test_rate_limit_precedes_hash(self):
        source = inspect.getsource(register)
        self.assertLess(source.index('request_allowed('), source.index('hash_password('))


if __name__ == "__main__":
    unittest.main()
