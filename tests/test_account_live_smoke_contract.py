"""Probe regressions use fake HTTP replies only; never production credentials."""
import io
import json
import unittest
from email.message import Message
from urllib.error import HTTPError
from scripts.v257_readonly_smoke import (inspect_response, run, PATHS, MAX_BODY,
                                        NoRedirect, ProbeFailure)


class ReadOnlySmokeContract(unittest.TestCase):
    def test_login_redirect_is_not_reported_as_authenticated_dashboard(self):
        self.assertEqual(inspect_response("/", 303, {"Location": "/account/login"}, b""),
                         "authentication_required")
        for status, location in ((200, "/account/login"), (303, "https://foreign.invalid/"),
                                 (302, "/account/login"), (503, "")):
            with self.subTest(status=status, location=location), self.assertRaises(ProbeFailure):
                inspect_response("/", status, {"Location": location}, b"dashboard")

    def test_private_data_must_not_be_anonymously_readable(self):
        path = "/api/v257/dashboard?limit=1"
        self.assertEqual(inspect_response(path, 401, {}, b'{"detail":"Authentication required"}'),
                         "protected_api_denied")
        for status, body in ((200, b'{"purchases":[],"stats":{}}'),
                             (401, b'<html>login</html>'), (401, b'{"detail":"other failure"}'),
                             (429, b'{}'), (503, b'{}')):
            with self.subTest(status=status, body=body), self.assertRaises(ProbeFailure):
                inspect_response(path, status, {}, body)

    def test_health_and_login_require_their_actual_content(self):
        self.assertEqual(inspect_response("/health", 200, {}, b'{"status":"ok"}'), "health_available")
        self.assertEqual(inspect_response("/account/login", 200, {},
                                         b'<main role="dialog"><form action="/auth/login"></form></main>'),
                         "login_form_available")
        for path, status, body in (("/health", 503, b'{}'), ("/health", 200, b'login'),
                                  ("/account/login", 200, b'other app'),
                                  ("/account/login", 500, b'/auth/login role="dialog"')):
            with self.subTest(path=path), self.assertRaises(ProbeFailure):
                inspect_response(path, status, {}, body)
        with self.assertRaises(ProbeFailure):
            inspect_response("/health", 200, {}, b' ' * (MAX_BODY + 1))

    def test_run_only_reads_and_never_follows_redirects_or_sends_credentials(self):
        requests = []
        class FakeOpener:
            def open(self, request, timeout):
                requests.append(request)
                path = request.full_url.removeprefix("https://example.invalid")
                headers = Message()
                if path in ("/", "/manual-purchases"):
                    status, body = 303, b''
                    headers["Location"] = "/account/login"
                elif path == "/health":
                    status, body = 200, b'{"status":"ok"}'
                elif path == "/account/login":
                    status, body = 200, b'role="dialog" /auth/login'
                else:
                    status, body = 401, b'{"detail":"Authentication required"}'
                response = HTTPError(request.full_url, status, "fake", headers, io.BytesIO(body))
                if status != 200:
                    raise response
                return response
        result = run("https://example.invalid", FakeOpener())
        self.assertEqual(len(requests), len(PATHS))
        self.assertTrue(all(request.get_method() == "GET" for request in requests))
        self.assertTrue(all(request.data is None for request in requests))
        self.assertTrue(all(set(key.lower() for key, _ in request.header_items()) == {"user-agent"}
                            for request in requests))
        self.assertFalse(result["authenticated_dashboard_verified"])
        self.assertFalse(result["procurement_processing_verified"])
        self.assertIsNone(NoRedirect().redirect_request(None, None, 303, "", {}, "https://foreign.invalid"))

    def test_origin_must_not_accept_credentials_paths_or_cleartext(self):
        for origin in ("http://example.invalid", "https://user:pass@example.invalid", "https://example.invalid/path",
                       "https://example.invalid?key=secret", "https://example.invalid#frag"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                run(origin)


if __name__ == "__main__":
    unittest.main()
