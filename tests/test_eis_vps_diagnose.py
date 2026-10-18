"""Protocol and security regression tests without connecting to EIS."""
import contextlib
import io
import os
import socket
import ssl
import sys
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eis_vps_diagnose as diag
import v292_eis_soap_probe as v292

TOKEN = "fixture-token-do-not-print"
REG = "32616440427"


def getpass_warning(*args):
    diag.warnings.warn("echo fallback", diag.getpass.GetPassWarning)
    raise AssertionError("warning must prevent echo fallback")


def soap(data):
    return (f'<s:Envelope xmlns:s="{diag.SOAP}" xmlns:ip="{diag.NS}">'
            '<s:Body><ip:getDocsByReestrNumberResponse><index/>'
            f'<dataInfo>{data}</dataInfo></ip:getDocsByReestrNumberResponse>'
            '</s:Body></s:Envelope>').encode()


class DiagnoseTests(unittest.TestCase):
    def test_request_matches_xsd_and_contains_no_token(self):
        root = ET.fromstring(diag.build_request(REG))
        request = root.find(f"{{{diag.SOAP}}}Body/{{{diag.NS}}}getDocsByReestrNumberRequest")
        self.assertEqual(request.findtext("selectionParams/subsystemType"), "RI223")
        self.assertEqual(request.findtext("selectionParams/reestrNumber"), REG)
        self.assertEqual(request.findtext("index/mode"), "PROD")
        self.assertNotEqual(request.findtext("index/id"),
            ET.fromstring(diag.build_request(REG)).findtext(f".//{{{diag.NS}}}getDocsByReestrNumberRequest/index/id"))
        self.assertNotIn(b"token", diag.build_request(REG))
        with self.assertRaises(ValueError):
            diag.build_request("<bad>")

    def test_only_expected_soap_response_can_confirm_no_documents(self):
        self.assertEqual(diag.classify(200, soap("<noData>true</noData>"))["state"], "NO_DOCUMENTS")
        for status, content in ((200, b"<html>OK</html>"), (200, b"OK"),
                                (200, b"<dataInfo><noData>true</noData></dataInfo>"),
                                (500, soap("<noData>true</noData>"))):
            self.assertNotIn(diag.classify(status, content)["state"], ("NO_DOCUMENTS", "ARCHIVE_LINKS"))

    def test_archive_links_do_not_claim_download_and_empty_urls_fail(self):
        result = diag.classify(200, soap("<archiveUrl>https://int.zakupki.gov.ru/archive?id=1</archiveUrl>"))
        self.assertEqual(result["state"], "ARCHIVE_LINKS")
        self.assertEqual(result["archive_count"], 1)
        for value in ("", "javascript:alert(1)"):
            self.assertEqual(diag.classify(200, soap(f"<archiveUrl>{value}</archiveUrl>"))["state"], "UNEXPECTED_XML")

    def test_eis_error_codes_and_secret_redaction(self):
        for code, expected in ((5, "AUTH_ERROR"), (24, "AUTH_ERROR"), (34, "AUTH_ERROR"),
                               (8, "PREPARING"), (13, "RATE_LIMIT"), (28, "EIS_ERROR")):
            content = soap(f"<errorInfo><code>{code}</code><message>token {TOKEN}</message></errorInfo>")
            result = diag.classify(500, content, TOKEN)
            self.assertEqual(result["state"], expected)
            self.assertEqual(result["code"], str(code))
            self.assertNotIn(TOKEN, str(result))

    def test_fault_nonxml_auth_and_large_responses_are_not_success(self):
        content = (f'<s:Envelope xmlns:s="{diag.SOAP}"><s:Body><s:Fault>'
                   f'<faultstring>{TOKEN}</faultstring></s:Fault></s:Body></s:Envelope>').encode()
        result = diag.classify(500, content, TOKEN)
        self.assertEqual(result["state"], "SOAP_FAULT")
        self.assertNotIn(TOKEN, str(result))
        self.assertEqual(diag.classify(403, b"Forbidden")["state"], "HTTP_AUTH_ERROR")
        self.assertEqual(diag.classify(200, b"x" * (diag.MAX_BODY + 1))["state"], "RESPONSE_TOO_LARGE")
        self.assertEqual(diag.classify(200, b'<!DOCTYPE foo><foo/>')["state"], "UNEXPECTED_XML")

    def test_transport_classification_and_redirect_is_blocked(self):
        certificate_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        certificate_error.reason = "CERTIFICATE_VERIFY_FAILED"
        self.assertEqual(diag.transport_state(certificate_error), "TLS_ERROR")
        self.assertEqual(diag.transport_state(urllib.error.URLError(certificate_error)), "TLS_ERROR")
        for error, expected in ((ssl.SSLError("handshake failure"), "TLS_ERROR"),
                               (TimeoutError(), "TIMEOUT"), (socket.gaierror(), "DNS_ERROR"),
                               (OSError("unreachable"), "NETWORK_ERROR")):
            self.assertEqual(diag.transport_state(urllib.error.URLError(error)), expected)
        self.assertIsNone(diag.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))
        order, output = [], io.StringIO()
        def prompt(*args):
            order.append("token")
            return TOKEN
        def connect(*args, **kwargs):
            order.append("tls")
            raise ssl.SSLError(TOKEN)
        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(diag.getpass, "getpass", side_effect=prompt), \
             patch.object(diag.socket, "create_connection", side_effect=connect), \
             contextlib.redirect_stdout(output):
            self.assertEqual(diag.main([]), 2)
        self.assertEqual(order, ["token", "tls"])
        self.assertNotIn(TOKEN, output.getvalue())
        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(diag.getpass, "getpass", side_effect=getpass_warning), \
             patch.object(diag.socket, "create_connection") as connection, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(diag.main([]), 2)
            connection.assert_not_called()

    def test_diag_http_header_and_bounded_response_read(self):
        content = soap("<noData>true</noData>")
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, limit):
                self.limit = limit
                return content
        response = Response()
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return response
        opener = Opener()
        with patch.object(diag.urllib.request, "build_opener", return_value=opener):
            result = diag.send_probe(TOKEN, REG)
        headers = dict((k.lower(), v) for k, v in opener.request.header_items())
        self.assertEqual(headers["individualperson_token"], TOKEN)
        self.assertEqual(headers["soapaction"], '"' + diag.SOAP_ACTION + '"')
        self.assertNotIn(TOKEN.encode(), opener.request.data)
        self.assertEqual(result["state"], "NO_DOCUMENTS")
        self.assertEqual(response.limit, diag.MAX_BODY + 1)

    def test_v292_token_http_only_and_ri223(self):
        calls = []
        class Client:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def post(self, endpoint, content, headers):
                calls.append((endpoint, content, headers))
                return SimpleNamespace(status_code=200, content=soap("<noData>true</noData>"))
        output = io.StringIO()
        with patch.dict(os.environ, {"EIS_TOKEN": TOKEN}, clear=True), \
             patch("httpx.Client", Client), contextlib.redirect_stdout(output):
            v292.probe()
        endpoint, body, headers = calls[0]
        self.assertEqual(endpoint, diag.ENDPOINT)
        self.assertEqual(headers["individualPerson_token"], TOKEN)
        self.assertEqual(headers["SOAPAction"], '"' + diag.SOAP_ACTION + '"')
        self.assertNotIn(TOKEN.encode(), body)
        self.assertNotIn(TOKEN, output.getvalue())
        root = ET.fromstring(body)
        self.assertEqual(root.findtext(f".//{{{diag.NS}}}getDocsByReestrNumberRequest/selectionParams/subsystemType"), "RI223")


if __name__ == "__main__":
    unittest.main()
