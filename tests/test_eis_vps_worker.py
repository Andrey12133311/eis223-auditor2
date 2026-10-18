"""Offline worker safety, resume, and delivery contract tests; no real credentials."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

from scripts import eis_vps_worker as w

REG = '32616233661'
TOKEN = 'fixture-eis-secret'
UID = 'A' * 32
URL = 'https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=' + UID
ARCHIVE_URL = 'https://int.zakupki.gov.ru/dstore/common/download/compound?docRequestUid=fixture'


def zipped(name='purchaseNoticeKESMBO_32616233661_1.xml'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr(name, '<purchaseNoticeKESMBO/>')
    return out.getvalue()


def response(inner):
    return ('<e:Envelope xmlns:e="' + w.SOAP_NS + '" xmlns:a="' + w.API_NS + '">'
            '<e:Body><a:getDocsByReestrNumberResponse><dataInfo>' + inner +
            '</dataInfo></a:getDocsByReestrNumberResponse></e:Body></e:Envelope>').encode()


class FakeTransport:
    def __init__(self):
        self.received = False
        self.calls = []
        self.fail_original = False
        self.fail_report = False
        self.fail_discovery = False
        self.unresolved = 0
        self.jobs = []
        self.last_response_url = w.SEARCH_URL

    def site(self, path, data=None, method=None):
        self.calls.append(('site', path, data))
        if path.startswith('/api/v295/eis223/manifest/'):
            return {'documents': [{'uid': UID, 'url': URL, 'received': self.received}],
                    'unresolved_attachment_count': self.unresolved}
        if path.endswith('/report'):
            if self.fail_report:
                raise w.WorkerError('site_connection_error')
            return {'received': True, 'status': 'done' if self.received else 'queued', 'download_complete': self.received}
        if path.startswith('/api/eis-worker/jobs/next'):
            jobs, self.jobs = self.jobs, []
            return {'jobs': jobs}
        if '/recheck/' in path:
            return {'status': 'queued'}
        return {}

    def soap(self, reg):
        self.calls.append(('soap', reg))
        return [ARCHIVE_URL]

    def download(self, url, limit):
        self.calls.append(('download', url))
        return zipped() if url == ARCHIVE_URL else b'%PDF-fixture'

    def upload(self, path, data, content_type):
        self.calls.append(('upload', path))
        if '/original/' in path:
            if self.fail_original:
                raise w.WorkerError('site_connection_error')
            self.received = True
        return {'accepted': True, 'unresolved_attachment_count': self.unresolved}

    def request(self, url, **kwargs):
        self.calls.append(('request', url))
        if self.fail_discovery:
            raise w.WorkerError('eis_http_403', 1200)
        return b'<a href="/epz/order/notice/notice223/common-info.html?regNumber=32616233661">Link</a>'


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = w.State(self.temp.name)
        self.http = FakeTransport()
        self.worker = w.Worker({'interval_seconds': 300}, self.state, self.http)
        self.job = {'id': 'job-1', 'reg_number': REG, 'lease_token': 'lease-fixture'}

    def tearDown(self):
        self.state.db.close()
        self.temp.cleanup()

    def test_soap_fields_are_unqualified_and_token_escaped(self):
        xml = w.soap_request(REG, TOKEN + '&<')
        root = ET.fromstring(xml)
        request = root.find('.//{' + w.API_NS + '}getDocsByReestrNumberRequest')
        self.assertIsNotNone(request.find('index/id'))
        self.assertEqual(request.find('selectionParams/reestrNumber').text, REG)
        self.assertEqual(request.find('selectionParams/subsystemType').text, 'RI223')
        self.assertEqual(root.find('.//individualPerson_token').text, TOKEN + '&<')
        self.assertIsNone(request.find('{' + w.API_NS + '}index'))

    def test_no_data_requires_explicit_true_and_http200_errors_are_not_empty(self):
        self.assertEqual(w.archive_urls(response('<noData>true</noData>')), [])
        self.assertEqual(w.archive_urls(response('<noData>1</noData>')), [])
        for inner in ('', '<noData>false</noData>', '<errorInfo><code>24</code><message>' + TOKEN + '</message></errorInfo>'):
            with self.assertRaises(w.WorkerError) as error:
                w.archive_urls(response(inner))
            self.assertNotIn(TOKEN, str(error.exception))
        with self.assertRaises(w.WorkerError) as error:
            w.archive_urls(response('<errorInfo><code>13</code></errorInfo>'))
        self.assertEqual(error.exception.retry_after, 1200)

    def test_official_urls_only_and_redirect_cannot_leak_credentials(self):
        self.assertEqual(w.archive_urls(response('<archiveUrl>' + ARCHIVE_URL + '</archiveUrl>')), [ARCHIVE_URL])
        bad = ['http://zakupki.gov.ru/a', 'https://zakupki.gov.ru.evil.test/a',
               'https://evil.test/a', 'https://u:p@zakupki.gov.ru/a',
               'https://zakupki.gov.ru:8443/a', 'https://zakupki.gov.ru/a#x', 'https://127.0.0.1/a']
        request = urllib.request.Request(ARCHIVE_URL, headers={'individualPerson_token': TOKEN})
        for url in bad:
            with self.subTest(url=url), self.assertRaises(w.WorkerError):
                w.OfficialRedirect().redirect_request(request, None, 302, '', {}, url)
        with self.assertRaises(w.WorkerError):
            w.NoRedirect().redirect_request(request, None, 302, '', {}, ARCHIVE_URL)

    def test_xml_entities_rejected_in_utf8_and_utf16(self):
        xml = '<!DOCTYPE root [<!ENTITY a "secret">]><root>&a;</root>'
        for encoding in ('utf-8', 'utf-16', 'utf-32'):
            with self.subTest(encoding=encoding), self.assertRaises(w.WorkerError):
                w.xml_root(xml.encode(encoding))

    def test_archive_path_traversal_and_non_zip_are_rejected(self):
        w.verify_zip(zipped())
        for data in (zipped('../secret.xml'), zipped('nested/file.xml'), zipped('run.py'), b'not a zip'):
            with self.assertRaises(w.WorkerError):
                w.verify_zip(data)

    def test_completed_is_after_server_receipts_and_queues_review(self):
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['originals_received'], 1)
        self.assertEqual(report['review_status'], 'queued')
        self.assertEqual(report['archive_sha256'], hashlib.sha256(zipped()).hexdigest())
        self.assertTrue(any('/recheck/' in c[1] for c in self.http.calls if c[0] == 'site'))
        self.assertFalse(list(Path(self.temp.name).glob('*.part')))

    def test_received_original_is_not_downloaded_or_uploaded_again(self):
        self.http.received = True
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['originals_uploaded'], 0)
        self.assertEqual([c[1] for c in self.http.calls if c[0] == 'download'], [ARCHIVE_URL])

    def test_failed_upload_keeps_cache_and_retries_without_redownloading_original(self):
        self.http.fail_original = True
        first = self.worker.process(self.job)
        self.assertEqual(first['status'], 'partial')
        self.assertEqual(first['originals_failed'], 1)
        self.assertEqual(len(list(Path(self.temp.name).glob('*.part'))), 1)
        self.http.fail_original = False
        self.http.calls.clear()
        second = self.worker.process(self.job)
        self.assertEqual(second['status'], 'completed')
        self.assertNotIn(('download', URL), self.http.calls)

    def test_report_is_durable_until_acknowledged(self):
        report = self.worker.process(self.job)
        self.state.save_report('job-1', report)
        self.http.fail_report = True
        with self.assertRaises(w.WorkerError):
            self.worker.retry_reports()
        self.assertEqual(len(self.state.pending_reports()), 1)
        self.http.fail_report = False
        self.worker.retry_reports()
        self.assertEqual(self.state.pending_reports(), [])

    def test_report_log_uses_authoritative_server_downgrade(self):
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'completed')
        self.state.save_report('job-1', report)
        output = io.StringIO()
        with patch.object(self.http, 'site', return_value={
                'received': True, 'status': 'queued', 'download_complete': False}), contextlib.redirect_stdout(output):
            self.worker.retry_reports()
        event = json.loads(output.getvalue())
        self.assertEqual(event['event'], 'report_ack')
        self.assertEqual(event['status'], 'queued')
        self.assertFalse(event['download_complete'])
        self.assertFalse(event['audit_complete'])

    def test_unresolved_attachment_never_completes_or_queues_review(self):
        self.http.unresolved = 1
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['error_code'], 'unsupported_attachment_links')
        self.assertFalse(any('/recheck/' in c[1] for c in self.http.calls if c[0] == 'site'))

    def test_cycle_respects_backoff_and_search_block_does_not_block_known_jobs(self):
        self.http.fail_discovery = True
        self.http.jobs = [self.job]
        with contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        self.assertIn(('soap', REG), self.http.calls)
        self.assertEqual(self.worker.discovery_status, 'blocked')
        self.state.set('backoff_until', 99999999999)
        self.http.calls.clear()
        self.worker.cycle()
        self.assertFalse(any(c[0] in ('soap', 'request') for c in self.http.calls))

    def test_file_budget_does_not_claim_completed(self):
        self.worker.files_this_cycle = 12
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['originals_uploaded'], 0)

    def test_discovery_only_uses_223_official_anchor_numbers(self):
        html = b'<p>39999999999</p><a href="https://evil.test/223/x?regNumber=31111111111">bad</a><a href="/epz/order/notice/notice223/common-info.html?regNumber=32616233661&amp;x=1">ok</a>'
        self.assertEqual(w.discovery_numbers(html), [REG])
        with self.assertRaises(w.WorkerError):
            w.discovery_numbers(b'<p>captcha</p>')

    def test_token_validation_identifies_bad_field_without_exposing_value(self):
        cases = [
            (None, 'empty'), ('', 'empty'), ('  \r\n ', 'empty'),
            ('секрет', 'invalid_characters'), ('valid\u200btoken', 'invalid_characters'),
            ('valid\r\nInjected:yes', 'invalid_characters'),
            ('valid token', 'invalid_characters'), ('valid\ttoken', 'invalid_characters'),
            ('valid\x7ftoken', 'invalid_characters'), (12345, 'invalid_characters'),
            ('EIS223_UPLOAD_TOKEN', 'variable_name'), ('EIS_TOKEN', 'variable_name'),
            (' eis223_upload_token ', 'variable_name'),
        ]
        for name in ('eis_token', 'site_token'):
            for value, suffix in cases:
                with self.subTest(name=name, value_type=type(value).__name__, suffix=suffix):
                    with self.assertRaises(w.WorkerError) as error:
                        w.validate_token(value, name)
                    self.assertEqual(str(error.exception), name + '_' + suffix)

    def test_opaque_ascii_token_preserved_with_outer_paste_whitespace_trimmed(self):
        value = "opaque+Value/2026:=!?@[]{}#$%&*()-_.~"
        self.assertEqual(w.validate_token(value, 'eis_token'), value)
        self.assertEqual(w.validate_token(' \r\n' + value + '\t\n ', 'site_token'), value)
        self.assertEqual(w.validate_token('short-but-valid', 'site_token'), 'short-but-valid')

    def test_request_rejects_invalid_injected_token_before_header_construction(self):
        http = w.Transport.__new__(w.Transport)
        http.config = {'site_url': 'https://example.test', 'eis_token': 'ЕИС-секрет',
                       'site_token': 'site-secret\u200b', 'timeout_seconds': 30}
        for url, eis, code in ((w.ENDPOINT, True, 'eis_token_invalid_characters'),
                               ('https://example.test/api/eis-worker/status', False, 'site_token_invalid_characters')):
            with self.subTest(eis=eis), patch('urllib.request.Request') as request:
                with self.assertRaises(w.WorkerError) as error:
                    http.request(url, eis=eis)
                self.assertEqual(error.exception.code, code)
                request.assert_not_called()

    def test_config_rejects_literal_env_names_and_non_ascii_by_field(self):
        path = Path(self.temp.name) / 'invalid-token-config.json'
        good = {'eis_token': TOKEN, 'site_token': 'opaque-site-token', 'site_url': 'https://example.test'}
        for name, bad, suffix in (('eis_token', 'секрет', 'invalid_characters'),
                                  ('site_token', 'EIS223_UPLOAD_TOKEN', 'variable_name')):
            path.write_text(json.dumps({**good, name: bad}))
            path.chmod(0o600)
            with self.assertRaises(w.WorkerError) as error:
                w.load_config(path)
            self.assertEqual(error.exception.code, name + '_' + suffix)

    def test_credentials_permissions_and_validation(self):
        p = Path(self.temp.name) / 'config.json'
        p.write_text(json.dumps({'eis_token': TOKEN, 'site_token': 'site-secret',
                                 'site_url': 'https://example.test', 'ca_file': '/tmp/ca'}))
        p.chmod(0o644)
        with self.assertRaises(w.WorkerError):
            w.load_config(p)
        p.chmod(0o640)
        self.assertEqual(w.load_config(p)['interval_seconds'], 300)
        p.write_text(json.dumps({'eis_token': TOKEN, 'site_token': '', 'site_url': 'https://example.test'}))
        with self.assertRaises(w.WorkerError) as error:
            w.load_config(p)
        self.assertNotIn(TOKEN, str(error.exception))


if __name__ == '__main__':
    unittest.main()
