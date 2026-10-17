import unittest
from unittest.mock import patch
from fastapi import HTTPException
from email_validator import EmailNotValidError
from scripts.account_api_v1 import register, Credentials


class RequestStub:
    headers = {"origin": "https://example.org"}


class RegistrationEmailTests(unittest.TestCase):
    def test_nonexistent_domain_rejected_before_password_hash(self):
        with patch("scripts.account_api_v1.require_origin"), patch(
            "scripts.account_api_v1.connect"
        ) as connect, patch(
            "scripts.account_api_v1.request_allowed", return_value=True
        ), patch(
            "scripts.account_api_v1.validate_email",
            side_effect=EmailNotValidError("Domain has no mail servers")
        ), patch("scripts.account_api_v1.hash_password") as hash_password:
            connect.return_value.__enter__.return_value = object()
            with self.assertRaises(HTTPException) as caught:
                register(Credentials(email="someone@invalid.example", password="a-strong-password"), RequestStub())
            self.assertEqual(caught.exception.status_code, 422)
            hash_password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
