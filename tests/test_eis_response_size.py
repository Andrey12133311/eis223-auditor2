"""Bounded 26 MiB synthetic originals, never live EIS files or real tokens."""
import asyncio
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eis_server_worker as server
import eis_vps_worker as worker
import v295_eis_originals as originals

REG = '32616233661'
UID = 'A' * 32
URL = 'https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=' + UID
SITE = 'https://site.example'
TOKEN = 'synthetic-response-size-key-only'


class Response(io.BytesIO):
    def __init__(self, data=b'', advertised=None):
        super().__init__(data)
        self.headers = {} if advertised is None else {'Content-Length': str(advertised)}
        self.read_calls = 0

    def geturl(self):
        return URL

    def read(self, count=-1):
        self.read_calls += 1
        return super().read(count)


def transport(response):
    result = worker.Transport.__new__(worker.Transport)
    result.config = {'site_url': SITE, 'eis_token': TOKEN,
                     'site_token': TOKEN, 'timeout_seconds': 30}
    result.eis_opener = Mock()
    result.site_opener = Mock()
    result.eis_opener.open.return_value = response
    result.site_opener.open.return_value = response
    result.last_file_at = 0
    return result


class ResponseLimitTests(unittest.TestCase):
    def test_worker_and_receiver_share_finite_limit_without_relaxing_metadata_or_expansion(self):
        self.assertEqual(worker.MAX_FILE, 64 * 1024 * 1024)
        self.assertEqual(worker.MAX_FILE, originals.MAX_BYTES)
        self.assertEqual(worker.MAX_ZIP, 15 * 1024 * 1024)
        self.assertEqual(worker.MAX_XML, 2 * 1024 * 1024)
        self.assertEqual(originals.MAX_EXPANDED, 80 * 1024 * 1024)

    def test_26_mib_original_crosses_old_boundary_and_remains_byte_exact(self):
        raw = b'%PDF-1.7\n' + b'0' * (26 * 1024 * 1024) + b'\n%%EOF'
        r = Response(raw, len(raw))
        received = transport(r).download(URL, worker.MAX_FILE)
        self.assertEqual(hashlib.sha256(received).digest(), hashlib.sha256(raw).digest())
        self.assertEqual(len(received), len(raw))
        self.assertGreater(r.read_calls, 100)

    def test_oversized_advertised_original_stops_before_body_read(self):
        r = Response(b'never read', worker.MAX_FILE + 1)
        with self.assertRaises(worker.ResponseSizeLimit) as caught:
            transport(r).download(URL, worker.MAX_FILE)
        self.assertEqual(r.read_calls, 0)
        self.assertEqual(caught.exception.code, 'response_size_limit')
        self.assertEqual(caught.exception.context, {'kind': 'eis_original',
            'limit_bytes': worker.MAX_FILE, 'observed_bytes': worker.MAX_FILE + 1})
        self.assertNotIn(URL, str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_missing_length_cannot_bypass_stream_limit(self):
        with self.assertRaises(worker.ResponseSizeLimit) as caught:
            transport(Response(b'x' * 9)).request(URL, eis=True, limit=8, response_kind='eis_original')
        self.assertEqual(caught.exception.context['observed_bytes'], 9)

    def test_short_body_is_never_accepted_as_complete(self):
        with self.assertRaises(worker.WorkerError) as caught:
            transport(Response(b'%PDF-truncated', 200)).download(URL, worker.MAX_FILE)
        self.assertEqual(caught.exception.code, 'response_length_mismatch')

    def test_archive_and_site_response_limits_are_not_original_limits(self):
        for eis, kind, limit in [(True, 'eis_archive', worker.MAX_ZIP),
                                 (False, 'site_response', worker.MAX_XML)]:
            r = Response(b'', limit + 1)
            with self.subTest(kind=kind), self.assertRaises(worker.ResponseSizeLimit) as caught:
                t = transport(r)
                if eis:
                    t.download(URL, limit)
                else:
                    t.site('/api/eis-worker/status')
            self.assertEqual(caught.exception.context['kind'], kind)
            self.assertEqual(r.read_calls, 0)

    def test_context_is_sanitized_and_never_becomes_delivery_evidence(self):
        value = {'kind': 'eis_original', 'limit_bytes': 10, 'observed_bytes': 11,
                 'uid': UID, 'token': TOKEN, 'url': URL, 'download_complete': True}
        clean = server._size_limit_context(value)
        self.assertEqual(set(clean), {'kind', 'limit_bytes', 'observed_bytes', 'uid'})
        result = server._report_telemetry({'error_code': 'response_size_limit',
            'size_limit': value, 'reg_number': REG, 'download_complete': False,
            'originals_expected': 5, 'originals_received': 4})
        self.assertFalse(result['download_complete'])
        self.assertEqual(result['originals_received'], 4)
        self.assertEqual(result['size_limit'], clean)
        self.assertNotIn(TOKEN, json.dumps(result))
        for invalid in (None, {'kind': URL}, {**value, 'observed_bytes': True},
                        {**value, 'observed_bytes': 2**51}, {**value, 'limit_bytes': -1}):
            self.assertEqual(server._size_limit_context(invalid), {})


class LargeOriginalIntakeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {'EIS223_DATA_DIR': folder.name, 'EIS223_UPLOAD_TOKEN': TOKEN})
        env.start()
        self.addCleanup(env.stop)
        self.entry = {'name': 'synthetic.pdf', 'ext': '.pdf', 'uid': UID, 'url': URL}
        path = self.root/'eis223_public_browser_intake'/REG/'manifest.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'source': 'public_eis_223_browser', 'reg_number': REG, 'documents': [self.entry]}))
        app = FastAPI()
        originals.install({'app': app})
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'x-eis223-upload-token': TOKEN}

    def test_26_mib_persists_exactly_preserves_previous_original_and_never_counts_as_audit(self):
        old = b'%PDF-1.7\nprevious synthetic version\n%%EOF'
        originals._store_original_version(REG, UID, self.entry, old)
        old_path, _ = originals.saved_document(REG, UID)
        raw = b'%PDF-1.7\n' + b'0' * (26 * 1024 * 1024) + b'\n%%EOF'
        with patch.object(originals, 'MAX_REVIEW_TASKS', 0):
            reply = self.client.post('/api/v295/eis223/original/' + REG + '/' + UID,
                                     content=raw, headers=self.headers)
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()['audited_documents_increment'], 0)
        path, receipt = originals.saved_document(REG, UID)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(old_path.read_bytes(), old)
        self.assertEqual(len(list((path.parent/'versions').glob('*.json'))), 2)
        manifest = self.client.get('/api/v295/eis223/manifest/' + REG, headers=self.headers).json()
        self.assertEqual(manifest['max_original_bytes'], worker.MAX_FILE)
        self.assertTrue(manifest['documents'][0]['received'])
        self.assertFalse(manifest['counted_as_audited'])

    def test_over_limit_and_unauthorized_requests_never_persist_or_queue(self):
        for headers, expected in [({**self.headers, 'content-length': str(originals.MAX_BYTES + 1)}, 413),
                                  ({'x-eis223-upload-token': 'invalid'}, 401)]:
            with patch.object(originals, '_store_original_version') as save, \
                    patch.object(originals, '_try_execution_lock') as enqueue:
                response = self.client.post('/api/v295/eis223/original/' + REG + '/' + UID,
                                            content=b'%PDF-1.7', headers=headers)
                self.assertEqual(response.status_code, expected)
                save.assert_not_called()
                enqueue.assert_not_called()

    def test_complete_asgi_message_with_short_content_length_never_replaces_prior(self):
        old = b'%PDF-1.7\nretained version'
        originals._store_original_version(REG, UID, self.entry, old)
        messages = []
        raw = b'%PDF-1.7\ntruncated incoming version'
        async def receive():
            return {'type': 'http.request', 'body': raw, 'more_body': False}
        async def send(message):
            messages.append(message)
        path = '/api/v295/eis223/original/' + REG + '/' + UID
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'POST', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                 'query_string': b'', 'root_path': '', 'server': ('testserver', 80),
                 'client': ('fixture', 123), 'headers': [(b'x-eis223-upload-token', TOKEN.encode()),
                                                       (b'content-length', b'1000')]}
        with patch.object(originals, '_store_original_version') as save:
            asyncio.run(self.app(scope, receive, send))
            save.assert_not_called()
        self.assertEqual(messages[0]['status'], 400)
        retained, _ = originals.saved_document(REG, UID)
        self.assertEqual(retained.read_bytes(), old)


if __name__ == '__main__':
    unittest.main()
