"""Staging startup must reject configurations that expose personal data."""
import os
import unittest
from unittest.mock import patch
from scripts.account_staging_app import startup


class StagingConfigurationTests(unittest.TestCase):
    def test_rejects_disabled_authentication(self):
        with patch.dict(os.environ, {'EIS223_AUTH_ENFORCE':'false'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'EIS223_AUTH_ENFORCE'):
                startup()

    def test_requires_separate_identity_database(self):
        with patch.dict(os.environ, {'EIS223_AUTH_ENFORCE':'true'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'identity database'):
                startup()

    def test_rejects_https_prefix_spoofing(self):
        env={'EIS223_AUTH_ENFORCE':'true','EIS223_IDENTITY_DATABASE_URL':'postgresql://test','EIS223_PUBLIC_ORIGIN':'https://trusted.example@evil.example'}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'HTTPS'):
                startup()

    def test_requires_https_origin(self):
        with patch.dict(os.environ, {'EIS223_AUTH_ENFORCE':'true', 'EIS223_IDENTITY_DATABASE_URL':'postgresql://test'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'HTTPS'):
                startup()


if __name__ == '__main__':
    unittest.main()
