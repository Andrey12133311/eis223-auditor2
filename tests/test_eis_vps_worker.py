"""Offline worker safety, resume, and delivery contract tests; no real credentials."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import Mock, patch

from scripts import eis_vps_worker as w

REG = '32616233661'
INN = '6501309746'
TOKEN = 'fixture-eis-secret'
UID = 'A' * 32
URL = 'https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=' + UID
ARCHIVE_URL = 'https://int.zakupki.gov.ru/dstore/common/download/compound?docRequestUid=fixture'


def zipped(name='purchaseNoticeKESMBO_32616233661_1.xml'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        # Archive-byte assertions must not depend on the wall-clock ZIP timestamp.
        archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), '<purchaseNoticeKESMBO/>')
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
        self.search_html = None
        self.last_response_url = w.SEARCH_URL

    def site(self, path, data=None, method=None):
        self.calls.append(('site', path, data))
        if path.startswith('/api/v295/eis223/manifest/'):
            return {'documents': [{'uid': UID, 'url': URL, 'received': self.received}],
                    'unresolved_attachment_count': self.unresolved}
        if path.endswith('/report'):
            if self.fail_report:
                raise w.WorkerError('site_connection_error')
            if '/inn-jobs/' in path:
                return {'received': True, 'status': 'done', 'search_complete': True,
                        'candidate_count': len(data.get('reg_numbers', []))}
            return {'received': True, 'status': 'done' if self.received else 'queued', 'download_complete': self.received}
        if path.startswith('/api/eis-worker/jobs/next'):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            limit = int(query.get('limit', ['2'])[0])
            capable = 'inn_search' in query.get('capabilities', [''])[0]
            jobs = [job for job in self.jobs if job.get('kind') != 'inn' or capable][:limit]
            self.jobs = [job for job in self.jobs if job not in jobs]
            return {'jobs': jobs}
        if path == '/api/eis-worker/discovered':
            return {'accepted': True}
        if '/recheck/' in path:
            return {'status': 'queued'}
        return {}

    def soap(self, reg):
        self.calls.append(('soap', reg))
        return [ARCHIVE_URL]

    def download(self, url, limit, *, cancel=None):
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
        self.last_response_url = url
        if self.fail_discovery:
            raise w.WorkerError('eis_http_403', 1200)
        return self.search_html or b'<a href="/epz/order/notice/notice223/common-info.html?regNumber=32616233661">Link</a>'


class MultiOriginalTransport(FakeTransport):
    def __init__(self, count=4):
        super().__init__()
        self.documents = [{'uid': f'{i:032X}', 'url': URL[:-32] + f'{i:032X}'}
                          for i in range(1, count + 1)]
        self.receipts = set()
        self.active_downloads = 0
        self.max_active_downloads = 0
        self.download_started = threading.Event()
        self.upload_started = threading.Event()
        self.wait_for_overlap = False
        self.second_error = None

    def site(self, path, data=None, method=None):
        if '/manifest/' in path:
            self.calls.append(('site', path, data))
            return {'documents': [{**item, 'received': item['uid'] in self.receipts}
                                  for item in self.documents]}
        return super().site(path, data, method)

    def download(self, url, limit, *, cancel=None):
        if url == ARCHIVE_URL:
            return super().download(url, limit, cancel=cancel)
        self.calls.append(('download', url))
        self.active_downloads += 1
        self.max_active_downloads = max(self.max_active_downloads, self.active_downloads)
        try:
            if len(self.documents) > 1 and url == self.documents[1]['url']:
                self.download_started.set()
                if self.wait_for_overlap and not self.upload_started.wait(2):
                    raise AssertionError('The second download did not overlap the first upload')
                if self.second_error:
                    raise self.second_error
            return b'%PDF-synthetic-original'
        finally:
            self.active_downloads -= 1

    def upload(self, path, data, content_type):
        if '/original/' not in path:
            return super().upload(path, data, content_type)
        self.calls.append(('upload', path))
        if self.wait_for_overlap and path.endswith('/' + self.documents[0]['uid']):
            self.upload_started.set()
            if not self.download_started.wait(2):
                raise AssertionError('The first upload did not overlap the second download')
        if self.fail_original:
            raise w.WorkerError('site_connection_error')
        self.receipts.add(path.rsplit('/', 1)[-1])
        return {'accepted': True}


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

    def test_rejected_original_does_not_block_following_files_and_bad_cache_is_evicted(self):
        self.http = MultiOriginalTransport(3)
        self.worker.http = self.http
        first_uid = self.http.documents[0]['uid']
        original_upload = self.http.upload
        def reject_first(path, data, content_type):
            if path.endswith('/' + first_uid):
                raise w.WorkerError('site_http_422')
            return original_upload(path, data, content_type)
        with patch.object(self.http, 'upload', side_effect=reject_first), contextlib.redirect_stdout(io.StringIO()):
            report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['originals_uploaded'], 2)
        self.assertEqual(report['originals_failed'], 1)
        self.assertNotIn(first_uid, self.http.receipts)
        self.assertEqual(len(self.http.receipts), 2)
        rejected_key = w.cache_key(REG, first_uid, self.http.documents[0]['url'])
        self.assertIsNone(self.state.cached(rejected_key))
        self.assertFalse((self.state.root / (rejected_key + '.part')).exists())
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM deliveries WHERE kind='original'").fetchone()[0], 2)
        self.http.calls.clear()
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'completed')
        downloads = [c[1] for c in self.http.calls if c[0] == 'download']
        self.assertIn(self.http.documents[0]['url'], downloads)
        for item in self.http.documents[1:]:
            self.assertNotIn(item['url'], downloads)

    def test_rejected_archive_allows_other_archives_but_never_claims_complete(self):
        rejected_url = ARCHIVE_URL + '-rejected'
        bad_archive = zipped('rejected.xml')
        accepted_archive = zipped()
        original_upload = self.http.upload
        original_download = self.http.download
        def download(url, limit, *, cancel=None):
            if url == rejected_url:
                self.http.calls.append(('download', url))
                return bad_archive
            return original_download(url, limit, cancel=cancel)
        def upload(path, data, content_type):
            if '/archive' in path and data == bad_archive:
                raise w.WorkerError('site_http_422')
            return original_upload(path, data, content_type)
        with patch.object(self.http, 'soap', return_value=[rejected_url, ARCHIVE_URL]), \
                patch.object(self.http, 'download', side_effect=download), \
                patch.object(self.http, 'upload', side_effect=upload), contextlib.redirect_stdout(io.StringIO()):
            report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['archives_rejected'], 1)
        self.assertEqual(report['archives_uploaded'], 1)
        self.assertEqual(report['originals_uploaded'], 1)
        self.assertEqual(report['archive_sha256s'], [hashlib.sha256(accepted_archive).hexdigest()])
        self.assertIsNone(self.state.cached(w.cache_key(REG, 'archive', rejected_url)))
        self.assertFalse(any('/recheck/' in c[1] for c in self.http.calls if c[0] == 'site'))
        original_site = self.http.site
        def failed_manifest(path, data=None, method=None):
            if '/manifest/' in path:
                raise w.WorkerError('site_connection_error')
            return original_site(path, data, method)
        with patch.object(self.http, 'soap', return_value=[rejected_url, ARCHIVE_URL]), \
                patch.object(self.http, 'download', side_effect=download), \
                patch.object(self.http, 'upload', side_effect=upload), \
                patch.object(self.http, 'site', side_effect=failed_manifest), contextlib.redirect_stdout(io.StringIO()):
            later_failure = self.worker.process(self.job)
        self.assertEqual(later_failure['archives_rejected'], 1)
        self.assertEqual(later_failure['archives_uploaded'], 1)
        self.assertEqual(later_failure['status'], 'error')
        self.assertEqual(later_failure['error_code'], 'site_connection_error')

    def test_scoped_discovery_backpressure_keeps_queue_draining(self):
        original_site = self.http.site
        def site(path, data=None, method=None):
            if path.endswith('/discovered'):
                raise w.WorkerError('site_discovery_backpressure', 60)
            return original_site(path, data, method)
        self.http.jobs = [self.job]
        with patch.object(self.http, 'site', side_effect=site), contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        self.assertIn(('soap', REG), self.http.calls)
        self.assertTrue(self.http.received)
        self.assertIsNotNone(self.state.pending_discovery())
        self.assertEqual(self.state.discovery_cursor(), 0)
        self.assertEqual(float(self.state.get('backoff_until', '0')), 0)
        self.assertEqual(float(self.state.get('site_backoff_until', '0')), 0)
        self.assertEqual(len([c for c in self.http.calls if c[0] == 'request']), 1)
        self.assertTrue(any(c[0] == 'site' and c[1].endswith('/report') for c in self.http.calls))

    def test_backpressure_scope_only_applies_to_exact_site_discovery_endpoint(self):
        transport = w.Transport.__new__(w.Transport)
        transport.config = {'site_url': 'https://example.test', 'eis_token': TOKEN,
                            'site_token': 'site-secret', 'timeout_seconds': 10}
        transport.site_opener = Mock()
        transport.eis_opener = Mock()
        scoped = {'retry-after': '60', 'x-eis223-retry-scope': 'discovery'}
        for status in (429, 503):
            for url, eis, expected in [('https://example.test/api/eis-worker/discovered', False, 'site_discovery_backpressure'),
                                       ('https://example.test/api/eis-worker/heartbeat', False, 'site_http_' + str(status)),
                                       (w.SEARCH_URL, True, 'eis_http_' + str(status))]:
                opener = transport.eis_opener if eis else transport.site_opener
                opener.open.side_effect = urllib.error.HTTPError(url, status, 'busy', scoped, None)
                with self.subTest(url=url, status=status), self.assertRaises(w.WorkerError) as error:
                    transport.request(url, 'POST', b'{}', eis=eis)
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(error.exception.retry_after, 60 if expected == 'site_discovery_backpressure' else
                                 1200 if status == 429 else 300)

    def test_general_search_rotates_fifty_record_pages_and_survives_restart(self):
        html = ''.join(f'<a href="/epz/order/notice/notice223/common-info.html?regNumber={32616233000+i}">Notice</a>' for i in range(125))
        self.http.search_html = html.encode()
        observed = []
        for index in range(19):
            # Simulate the scheduler's ready capable claim finding no INN.
            self.state.set('inn_opportunity_pending', '0')
            with patch.object(w.time, 'time', return_value=1000 + index * 60), contextlib.redirect_stdout(io.StringIO()):
                self.worker.discover()
            payload = [c[2] for c in self.http.calls if c[0] == 'site' and c[1].endswith('/discovered')][-1]
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(payload['source_url']).query)
            observed.append(int(query['pageNumber'][0]))
            self.assertEqual(query['recordsPerPage'], ['_50'])
            self.assertEqual(len(payload['reg_numbers']), 100)
            self.assertEqual(len(set(payload['reg_numbers'])), 100)
            if index == 2:
                reopened = w.State(self.temp.name)
                self.assertEqual(reopened.discovery_cursor(), 3)
                reopened.db.close()
        self.assertEqual(observed, [1, 2, 1, 3, 1, 4, 1, 5, 1, 6, 1, 7, 1, 8, 1, 9, 1, 10, 1])

    def test_discovery_delivery_is_durable_without_refetch_or_source_cooldown_extension(self):
        original_site = self.http.site
        attempts = []

        def fail_delivery(path, data=None, method=None):
            if path.endswith('/discovered'):
                attempts.append(data)
                raise w.WorkerError('site_connection_error', 120)
            return original_site(path, data, method)

        with patch.object(self.http, 'site', side_effect=fail_delivery), \
                patch.object(w.time, 'time', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            self.worker.discover()
        self.assertIsNotNone(self.state.pending_discovery())
        self.assertEqual(self.state.discovery_cursor(), 0)
        self.assertEqual(float(self.state.get('search_not_before')), 1060)
        self.assertEqual(len([c for c in self.http.calls if c[0] == 'request']), 1)
        restarted = w.State(self.temp.name)
        try:
            other = w.Worker({'search_interval_seconds': 30}, restarted, self.http)
            restarted.set('backoff_until', 5000)
            restarted.set('search_not_before', 5000)
            with patch.object(w.time, 'time', return_value=1120), contextlib.redirect_stdout(io.StringIO()):
                other.cycle()
            delivery = [c[2] for c in self.http.calls if c[0] == 'site' and c[1].endswith('/discovered')]
            self.assertEqual(delivery, attempts)
            self.assertIsNone(restarted.pending_discovery())
            self.assertEqual(restarted.discovery_cursor(), 1)
            self.assertEqual(len([c for c in self.http.calls if c[0] == 'request']), 1)
            self.assertEqual(float(restarted.get('search_not_before')), 5000)
        finally:
            restarted.db.close()

    def test_cursor_waits_for_explicit_acceptance_and_empty_deep_page_resets(self):
        original_site = self.http.site
        def unaccepted(path, data=None, method=None):
            if path.endswith('/discovered'):
                return {'accepted': False}
            return original_site(path, data, method)
        with patch.object(self.http, 'site', side_effect=unaccepted), \
                patch.object(w.time, 'time', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            self.worker.discover()
        self.assertEqual(self.state.discovery_cursor(), 0)
        self.assertIsNotNone(self.state.pending_discovery())
        with patch.object(w.time, 'time', return_value=1300), contextlib.redirect_stdout(io.StringIO()):
            self.worker.retry_discovery()
        self.assertEqual(self.state.discovery_cursor(), 1)
        self.http.search_html = '<p>По вашему запросу ничего не найдено</p>'.encode()
        self.state.set('inn_opportunity_pending', '0')
        with patch.object(w.time, 'time', return_value=1360), contextlib.redirect_stdout(io.StringIO()):
            self.worker.discover()
        self.assertEqual(self.state.discovery_cursor(), 0)
        self.assertEqual(self.worker.discovery_status, 'ok')
        payload = [c[2] for c in self.http.calls if c[0] == 'site' and c[1].endswith('/discovered')][-1]
        self.assertEqual(payload['reg_numbers'], [])

    def test_redirect_to_first_page_cannot_advance_deep_page(self):
        self.state.set('discovery_cursor', 1)
        original_request = self.http.request
        def redirected(url, **kwargs):
            data = original_request(url, **kwargs)
            self.http.last_response_url = w.SEARCH_URL
            return data
        with patch.object(self.http, 'request', side_effect=redirected), \
                patch.object(w.time, 'time', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            self.worker.discover()
        self.assertEqual(self.state.discovery_cursor(), 1)
        self.assertIsNone(self.state.pending_discovery())
        self.assertEqual(self.worker.discovery_error, 'eis_discovery_page_mismatch')
        self.assertFalse(any(c[0] == 'site' and c[1].endswith('/discovered') for c in self.http.calls))

    def test_shared_sixty_second_slots_alternate_under_continuous_inn_demand(self):
        for cycle in range(4):
            self.http.jobs.append({**self.inn_job(), 'id': 'inn-' + str(cycle)})
            with patch.object(w.time, 'time', return_value=1000 + cycle * 60), contextlib.redirect_stdout(io.StringIO()):
                self.worker.cycle()
        requests = [urllib.parse.parse_qs(urllib.parse.urlsplit(c[1]).query)
                    for c in self.http.calls if c[0] == 'request']
        self.assertEqual([r.get('customerInn') for r in requests], [[INN], None, [INN], None])
        self.assertEqual([r['recordsPerPage'][0] for r in requests], ['_20', '_50', '_20', '_50'])
        self.assertEqual([r['pageNumber'][0] for r in requests if 'customerInn' not in r], ['1', '2'])

    def test_general_discovery_precedes_long_documents_and_never_overlaps_downloads(self):
        self.http = MultiOriginalTransport(4)
        self.worker.http = self.http
        self.http.jobs = [self.job]
        now = [1800000000]
        original_upload = self.http.upload
        original_request = self.http.request
        def upload(path, data, content_type):
            result = original_upload(path, data, content_type)
            if '/original/' in path:
                now[0] += 61
            return result
        def request(url, **kwargs):
            self.assertEqual(self.http.active_downloads, 0)
            return original_request(url, **kwargs)
        with patch.object(self.http, 'upload', side_effect=upload), \
                patch.object(self.http, 'request', side_effect=request), \
                patch.object(w.time, 'time', side_effect=lambda: now[0]), contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        calls = self.http.calls
        first_search = next(i for i, c in enumerate(calls) if c[0] == 'request')
        first_soap = next(i for i, c in enumerate(calls) if c[0] == 'soap')
        self.assertLess(first_search, first_soap)
        self.assertEqual(len([c for c in calls if c[0] == 'request']), 2)
        self.assertEqual(self.http.max_active_downloads, 1)

    def test_long_registry_backlog_cannot_starve_queued_inn_search(self):
        self.http = MultiOriginalTransport(4)
        self.worker.http = self.http
        now = [1800000000]
        original_soap = self.http.soap
        original_upload = self.http.upload
        def soap(reg):
            self.http.receipts.clear()
            return original_soap(reg)
        def upload(path, data, content_type):
            result = original_upload(path, data, content_type)
            if '/original/' in path:
                now[0] += 61
            return result
        with patch.object(self.http, 'soap', side_effect=soap), \
                patch.object(self.http, 'upload', side_effect=upload), \
                patch.object(w.time, 'time', side_effect=lambda: now[0]), contextlib.redirect_stdout(io.StringIO()):
            self.worker.discover()
            now[0] += 1
            self.http.jobs = [self.inn_job()] + [{**self.job, 'id': 'reg-' + str(i),
                                               'reg_number': str(32616233000 + i)} for i in range(8)]
            for _ in range(3):
                self.worker.cycle()
        public_requests = [urllib.parse.parse_qs(urllib.parse.urlsplit(c[1]).query)
                           for c in self.http.calls if c[0] == 'request']
        self.assertTrue(any(query.get('customerInn') == [INN] for query in public_requests))
        self.assertFalse(any(job.get('kind') == 'inn' for job in self.http.jobs))
        self.assertTrue(any('customerInn' not in query for query in public_requests))
        self.assertLess(len(public_requests), 10)

    def test_reserved_inn_opportunity_survives_restart(self):
        with patch.object(w.time, 'time', return_value=1800000000), contextlib.redirect_stdout(io.StringIO()):
            self.worker.discover()
        restarted = w.State(self.temp.name)
        try:
            other = w.Worker({'search_interval_seconds': 60}, restarted, self.http)
            self.http.calls.clear()
            self.http.jobs = [self.inn_job()]
            with patch.object(w.time, 'time', return_value=1800000060), contextlib.redirect_stdout(io.StringIO()):
                other.discover()  # Cannot steal the reserved next slot.
                self.assertEqual(self.http.calls, [])
                other.cycle()
            requests = [c[1] for c in self.http.calls if c[0] == 'request']
            self.assertEqual(requests, [w.inn_search_url(INN, 1)])
        finally:
            restarted.db.close()

    def test_new_search_interval_never_shortens_stored_cooldown_or_remote_pause(self):
        with patch.object(w.time, 'time', return_value=1000):
            self.state.take_search_slot(300)
        faster = w.Worker({'search_interval_seconds': 30}, self.state, self.http)
        with patch.object(w.time, 'time', return_value=1060), contextlib.redirect_stdout(io.StringIO()):
            faster.discover()
        self.assertEqual(self.http.calls, [])
        self.http.fail_discovery = True
        with patch.object(w.time, 'time', return_value=1300), contextlib.redirect_stdout(io.StringIO()):
            faster.discover()
        self.assertEqual(float(self.state.get('search_not_before')), 2500)
        self.http.fail_discovery = False
        self.http.calls.clear()
        with patch.object(w.time, 'time', return_value=1360), contextlib.redirect_stdout(io.StringIO()):
            w.Worker({'search_interval_seconds': 30}, self.state, self.http).discover()
        self.assertEqual(self.http.calls, [])

    def test_search_config_defaults_and_bounds(self):
        p = Path(self.temp.name) / 'search-config.json'
        base = {'eis_token': TOKEN, 'site_token': 'site-secret', 'site_url': 'https://example.test'}
        for value, expected in [(None, 60), (1, 30), (30, 30), (300, 300), (7200, 3600)]:
            config = dict(base)
            if value is not None:
                config['search_interval_seconds'] = value
            p.write_text(json.dumps(config))
            p.chmod(0o600)
            self.assertEqual(w.load_config(p)['search_interval_seconds'], expected)

    def test_retry_after_seconds_dates_and_invalid_values(self):
        now = 1800000000
        for value, expected in [('7200', 7200), ('5', 1200), ('garbage', 1200),
                                ('-500', 1200), (None, 1200)]:
            with self.subTest(value=value), patch.object(w.time, 'time', return_value=now):
                self.assertEqual(w.retry_after_delay(value, 1200), expected)
        from email.utils import formatdate
        with patch.object(w.time, 'time', return_value=now):
            self.assertEqual(w.retry_after_delay(formatdate(now + 5400, usegmt=True), 1200), 5400)
            self.assertEqual(w.retry_after_delay(formatdate(now - 10, usegmt=True), 1200), 1200)
        transport = w.Transport.__new__(w.Transport)
        transport.config = {'site_url': 'https://example.test', 'eis_token': TOKEN,
                            'site_token': 'site-secret', 'timeout_seconds': 10}
        transport.eis_opener = Mock()
        transport.eis_opener.open.side_effect = urllib.error.HTTPError(URL, 429, 'limited',
                                                                       {'Retry-After': '7200'}, None)
        with self.assertRaises(w.WorkerError) as error:
            transport.request(URL, eis=True)
        self.assertEqual(error.exception.retry_after, 7200)
        self.assertEqual(error.exception.code, 'eis_http_429')

    def test_long_remote_backoff_survives_restart_and_wire_limit(self):
        self.http = MultiOriginalTransport()
        self.http.second_error = w.WorkerError('eis_http_429', 172800)
        self.worker.http = self.http
        self.http.jobs = [self.job]
        with patch.object(w.time, 'time', return_value=1800000000), contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        reports = [c[2] for c in self.http.calls if c[0] == 'site' and c[1].endswith('/report')]
        self.assertEqual(reports[0]['retry_after_seconds'], 86400)
        self.assertEqual(float(self.state.get('backoff_until')), 1800172800)
        restarted = w.State(self.temp.name)
        try:
            other = w.Worker({'interval_seconds': 30}, restarted, self.http)
            self.http.calls.clear()
            with patch.object(w.time, 'time', return_value=1800086500):
                other.cycle()
            self.assertFalse(any(c[0] in ('soap', 'download', 'request') for c in self.http.calls))
        finally:
            restarted.db.close()

    def test_heartbeat_rate_limit_is_persisted_before_inn_report_retry(self):
        self.http.jobs = [self.inn_job()]
        original_site = self.http.site
        attempts = []

        def site(path, data=None, method=None):
            if path.endswith('/heartbeat') and data.get('current_job_id'):
                attempts.append(path)
                raise w.WorkerError('site_http_429', 7200)
            return original_site(path, data, method)

        with patch.object(self.http, 'site', side_effect=site), \
                patch.object(w.time, 'time', return_value=1800000000), contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(float(self.state.get('site_backoff_until')), 1800007200)
        self.assertTrue(self.state.pending_reports())
        self.assertFalse(any(c[0] == 'site' and c[1].endswith('/report') for c in self.http.calls))

    def test_site_retry_after_defers_reports_and_heartbeats_across_restart(self):
        self.http.jobs = [self.job]
        original_upload = self.http.upload

        def limited_upload(path, data, content_type):
            if '/original/' in path:
                raise w.WorkerError('site_http_429', 7200)
            return original_upload(path, data, content_type)

        with patch.object(self.http, 'upload', side_effect=limited_upload), \
                patch.object(w.time, 'time', return_value=1800000000), contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        self.assertTrue(self.state.pending_reports())
        self.assertFalse(any(c[0] == 'site' and c[1].endswith('/report') for c in self.http.calls))
        restarted = w.State(self.temp.name)
        try:
            other = w.Worker({'interval_seconds': 30}, restarted, self.http)
            self.http.calls.clear()
            with patch.object(w.time, 'time', return_value=1800000030):
                other.cycle()
                other.heartbeat(force=True)
            self.assertEqual(self.http.calls, [])
            with patch.object(w.time, 'time', return_value=1800007201), contextlib.redirect_stdout(io.StringIO()):
                other.cycle()
            self.assertTrue(any(c[0] == 'site' and c[1].endswith('/report') for c in self.http.calls))
        finally:
            restarted.db.close()

    def test_second_job_is_claimed_only_after_first_report_ack(self):
        second = {**self.job, 'id': 'job-2', 'reg_number': '32616233662'}
        self.http.jobs = [self.job, second]
        clock = [1800000000]
        original_process = self.worker.process

        def slow_process(job):
            result = original_process(job)
            clock[0] += 1000  # Longer than a normal lease; only current job is held.
            return result

        claim_times = []
        original_site = self.http.site

        def site(path, data=None, method=None):
            if '/jobs/next?' in path:
                claim_times.append(clock[0])
                self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['limit'], ['1'])
            return original_site(path, data, method)

        with patch.object(self.worker, 'process', side_effect=slow_process), \
                patch.object(self.http, 'site', side_effect=site), \
                patch.object(w.time, 'time', side_effect=lambda: clock[0]), contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        self.assertEqual(claim_times, [1800000000, 1800001000])
        calls = self.http.calls
        claims = [i for i, call in enumerate(calls) if call[0] == 'site' and '/jobs/next?' in call[1]]
        first_ack = next(i for i, call in enumerate(calls) if call[0] == 'site' and call[1].endswith('/job-1/report'))
        self.assertLess(first_ack, claims[1])
        self.assertIn(('soap', second['reg_number']), calls)

    def test_full_original_budget_leaves_next_job_unleased(self):
        self.http = MultiOriginalTransport(50)
        self.worker.http = self.http
        self.http.jobs = [self.job, {**self.job, 'id': 'job-2'}]
        with contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        claims = [call for call in self.http.calls if call[0] == 'site' and '/jobs/next?' in call[1]]
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(self.http.jobs), 1)

    def test_queue_interval_uses_fast_default_and_preserves_explicit_slow_rate(self):
        p = Path(self.temp.name) / 'rate-config.json'
        config = {'eis_token': TOKEN, 'site_token': 'site-secret', 'site_url': 'https://example.test'}
        for interval, expected in [(None, 30), (1, 30), (30, 30), (300, 300), (900, 900)]:
            value = dict(config)
            if interval is not None:
                value['interval_seconds'] = interval
            p.write_text(json.dumps(value))
            p.chmod(0o600)
            self.assertEqual(w.load_config(p)['interval_seconds'], expected)

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

    def test_metadata_only_delivers_archives_without_consuming_original_budget(self):
        report = self.worker.process({**self.job, 'metadata_only': True})
        self.assertEqual(report['archives_uploaded'], 1)
        self.assertEqual(report['originals_uploaded'], 0)
        self.assertEqual(self.worker.files_this_cycle, 0)
        self.assertNotEqual(report['status'], 'completed')
        self.assertEqual([c[1] for c in self.http.calls if c[0] == 'download'], [ARCHIVE_URL])
        self.assertFalse(any('/manifest/' in c[1] or '/recheck/' in c[1]
                             for c in self.http.calls if c[0] == 'site'))

    def test_pipeline_overlaps_upload_but_never_parallelizes_eis_downloads(self):
        self.http = MultiOriginalTransport()
        self.http.wait_for_overlap = True
        self.worker.http = self.http
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['originals_uploaded'], 4)
        self.assertEqual(self.http.max_active_downloads, 1)
        self.assertTrue(self.http.download_started.is_set())
        self.assertTrue(self.http.upload_started.is_set())
        self.assertFalse(list(Path(self.temp.name).glob('*.part')))

    def test_pipeline_caches_prefetched_bytes_after_failed_upload(self):
        self.http = MultiOriginalTransport()
        self.http.wait_for_overlap = True
        self.http.fail_original = True
        self.worker.http = self.http
        report = self.worker.process(self.job)
        self.assertEqual(report['error_code'], 'site_connection_error')
        self.assertEqual(report['originals_failed'], 1)
        self.assertEqual(len(list(Path(self.temp.name).glob('*.part'))), 2)
        first_downloads = [c for c in self.http.calls if c[0] == 'download']
        self.assertEqual(len(first_downloads), 3)  # archive plus two originals
        self.http.fail_original = False
        self.http.calls.clear()
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'completed', report)
        for item in self.http.documents[:2]:
            self.assertNotIn(('download', item['url']), self.http.calls)

    def test_pipeline_stops_after_prefetched_rate_limit(self):
        self.http = MultiOriginalTransport()
        self.http.second_error = w.WorkerError('eis_http_429', 1200)
        self.worker.http = self.http
        report = self.worker.process(self.job)
        self.assertEqual(report['error_code'], 'eis_http_429')
        self.assertEqual(report['retry_after_seconds'], 1200)
        self.assertEqual(report['originals_uploaded'], 1)
        self.assertEqual(report['originals_failed'], 1)
        self.assertEqual(self.worker.files_this_cycle, 2)
        self.assertNotIn(('download', self.http.documents[2]['url']), self.http.calls)

    def test_upload_failure_does_not_hide_prefetch_global_backoff(self):
        self.http = MultiOriginalTransport()
        self.http.wait_for_overlap = True
        self.http.fail_original = True
        self.http.second_error = w.WorkerError('eis_http_429', 1200)
        self.worker.http = self.http
        with patch.object(w.time, 'time', return_value=1800000000):
            report = self.worker.process(self.job)
        self.assertEqual(report['error_code'], 'eis_http_429')
        self.assertEqual(report['retry_after_seconds'], 1200)
        self.assertEqual(float(self.state.get('backoff_until')), 1800001200)
        self.assertNotIn(('download', self.http.documents[2]['url']), self.http.calls)

    def test_pipeline_bounds_forty_eight_attempts_with_one_download(self):
        self.http = MultiOriginalTransport(51)
        self.worker.http = self.http
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'partial', report)
        self.assertEqual(report['originals_uploaded'], 48)
        self.assertEqual(self.worker.files_this_cycle, 48)
        self.assertEqual(len([c for c in self.http.calls if c[0] == 'download']), 49)
        self.assertEqual(self.http.max_active_downloads, 1)
        self.assertNotIn(('download', self.http.documents[48]['url']), self.http.calls)

    def test_limiter_preserves_spacing_and_cancels_before_network(self):
        transport = w.Transport.__new__(w.Transport)
        transport.last_file_at = 97.5
        starts = []
        now = [100.0]

        def sleep(seconds):
            now[0] += seconds

        def request(*args, **kwargs):
            starts.append(now[0])
            return b'fixture'

        transport.request = request
        with patch.object(w.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(w.time, 'sleep', side_effect=sleep):
            transport.download(URL, w.MAX_FILE)
            transport.download(URL, w.MAX_FILE)
            self.assertEqual(starts, [100.0, 102.5])
            cancel = threading.Event()
            with patch.object(cancel, 'wait', side_effect=lambda seconds: cancel.set()):
                with self.assertRaises(w.WorkerError) as error:
                    transport.download(URL, w.MAX_FILE, cancel=cancel)
            self.assertEqual(error.exception.code, 'download_cancelled')
            self.assertEqual(starts, [100.0, 102.5])

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
        self.worker.files_this_cycle = 48
        report = self.worker.process(self.job)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['originals_uploaded'], 0)

    def test_discovery_only_uses_223_official_anchor_numbers(self):
        html = b'<p>39999999999</p><a href="https://evil.test/223/x?regNumber=31111111111">bad</a><a href="/epz/order/notice/notice223/common-info.html?regNumber=32616233661&amp;x=1">ok</a>'
        self.assertEqual(w.discovery_numbers(html), [REG])
        with self.assertRaises(w.WorkerError):
            w.discovery_numbers(b'<p>captcha</p>')

    def inn_job(self, page=1):
        return {'id': 'inn-job-1', 'kind': 'inn', 'target': INN,
                'page': page, 'lease_token': 'inn-lease-fixture'}

    def test_inn_query_uses_customer_filter_and_bounded_ten_page_protocol(self):
        url = w.inn_search_url(INN, 10)
        values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)
        self.assertEqual(values['customerInn'], [INN])
        self.assertEqual(values['searchString'], [''])
        self.assertEqual(values['recordsPerPage'], ['_20'])
        self.assertEqual(values['pageNumber'], ['10'])
        for page in (0, 11, True, '1'):
            with self.subTest(page=page), self.assertRaises(w.WorkerError):
                w.inn_search_url(INN, page)

    def test_inn_candidates_accept_visible_notice_ids_not_arbitrary_digits(self):
        html = (f'<input name="customerInn" value="{INN}"><p>39999999999</p>'
                '<a href="https://evil.test/epz/order/notice/notice223/common-info.html?regNumber=31111111111">bad</a>'
                '<a href="/223/filestore/file.html?regNumber=32222222222">not a notice</a>'
                '<a hidden href="/epz/order/notice/notice223/common-info.html?regNumber=33333333333">hidden</a>'
                f'<div class="search-registry-entry-block"><a href="/epz/order/notice/notice223/common-info.html?noticeId=888"><span>№ {REG}</span></a></div>'
                '<div class="registry-entry"><a href="/epz/order/notice/notice223/common-info.html?noticeInfoId=999">Карточка</a><span>№ 32616467074</span></div>')
        candidates, more = w.inn_candidates(html.encode(), INN, 1, w.inn_search_url(INN, 1))
        self.assertEqual(candidates, [REG, '32616467074'])
        self.assertFalse(more)

    def test_inn_filter_and_empty_response_are_never_buyer_proof_or_false_no_data(self):
        url = w.inn_search_url(INN, 1)
        mismatch = b'<input name="customerInn" value="1111111111"><a href="/epz/order/notice/notice223/common-info.html?regNumber=32616233661">Notice</a>'
        with self.assertRaises(w.WorkerError) as error:
            w.inn_candidates(mismatch, INN, 1, url)
        self.assertEqual(error.exception.code, 'eis_inn_filter_mismatch')
        for html in (f'<input name="customerInn" value="{INN}">',
                     f'<input name="customerInn" value="{INN}"><div hidden>По вашему запросу ничего не найдено</div>',
                     f'<input name="customerInn" value="{INN}"><script>По вашему запросу ничего не найдено</script>',
                     '<input name="customerInn" value=""><p>По вашему запросу ничего не найдено</p>',
                     '<p>По вашему запросу ничего не найдено</p>'):
            with self.subTest(html=html), self.assertRaises(w.WorkerError):
                w.inn_candidates(html.encode(), INN, 1, url)
        valid_zero = f'<input name="customerInn" value="{INN}"><p>По вашему запросу ничего не найдено</p>'
        self.assertEqual(w.inn_candidates(valid_zero.encode(), INN, 1, url), ([], False))
        with self.assertRaises(w.WorkerError):
            w.inn_candidates(valid_zero.encode(), INN, 1, w.inn_search_url('1111111111', 1))
        with self.assertRaises(w.WorkerError):
            w.inn_candidates(b'<p>captcha</p>', INN, 1, url)

    def test_captcha_script_reference_does_not_hide_real_search_results(self):
        html = (f'<script src="/captcha-library.js">var captchaEnabled = true;</script>'
                f'<a href="/epz/order/notice/notice223/common-info.html?regNumber={REG}">Notice</a>')
        self.assertEqual(w.inn_candidates(html.encode(), INN, 1, w.inn_search_url(INN, 1)), ([REG], False))

    def test_inn_pagination_remains_explicitly_limited_at_tenth_page(self):
        html = ''.join(f'<a href="/epz/order/notice/notice223/common-info.html?regNumber={32616233000+i}">Notice</a>' for i in range(25))
        candidates, more = w.inn_candidates(html.encode(), INN, 10, w.inn_search_url(INN, 10))
        self.assertEqual(len(candidates), 20)
        self.assertTrue(more)
        next_link = (f'<a href="/epz/order/notice/notice223/common-info.html?regNumber={REG}">Notice</a>'
                     f'<a href="{w.inn_search_url(INN, 2)}">Далее</a>')
        self.assertTrue(w.inn_candidates(next_link.encode(), INN, 1, w.inn_search_url(INN, 1))[1])

    def test_shared_search_budget_survives_worker_restart(self):
        with patch('scripts.eis_vps_worker.time.time', return_value=1000):
            self.state.take_search_slot()
        restarted = w.State(self.temp.name)
        try:
            with patch('scripts.eis_vps_worker.time.time', return_value=1001):
                with self.assertRaises(w.WorkerError) as error:
                    restarted.take_search_slot()
                self.assertEqual(error.exception.code, 'eis_search_cooldown')
            with patch('scripts.eis_vps_worker.time.time', return_value=1300):
                restarted.take_search_slot()
        finally:
            restarted.db.close()

    def test_inn_priority_capability_and_shared_discovery_budget(self):
        self.http.jobs = [self.inn_job(), self.job]
        with contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        requests = [call for call in self.http.calls if call[0] == 'request']
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][1], w.inn_search_url(INN, 1))
        claim = next(c for c in self.http.calls if c[0] == 'site' and '/jobs/next?' in c[1])
        self.assertIn('capabilities=inn_search', claim[1])
        heartbeat = next(c for c in self.http.calls if c[0] == 'site' and c[1].endswith('/heartbeat'))
        self.assertEqual(heartbeat[2]['capabilities'], ['inn_search'])
        reported = next(c[2] for c in self.http.calls if c[0] == 'site' and '/inn-jobs/' in c[1])
        self.assertEqual(reported['reg_numbers'], [REG])
        self.assertNotIn('customer_inn', reported)
        self.assertNotIn('candidates', reported)
        self.assertIn(('soap', REG), self.http.calls)

    def test_inn_public_search_block_does_not_block_soap_jobs(self):
        self.http.fail_discovery = True
        self.http.jobs = [self.inn_job(), self.job]
        with contextlib.redirect_stdout(io.StringIO()):
            self.worker.cycle()
        self.assertIn(('soap', REG), self.http.calls)
        self.assertEqual(len([c for c in self.http.calls if c[0] == 'request']), 1)
        self.assertGreater(float(self.state.get('search_not_before')), w.time.time() + 1100)
        self.assertEqual(self.state.get('backoff_until', '0'), '0')

    def test_inn_report_is_durable_and_uses_separate_endpoint(self):
        report = self.worker.process_inn(self.inn_job())
        self.assertEqual(report['status'], 'completed')
        self.assertFalse(any(c[0] in ('soap', 'download', 'upload') for c in self.http.calls))
        self.state.save_report('inn-job-1', report)
        self.http.fail_report = True
        with self.assertRaises(w.WorkerError):
            self.worker.retry_reports()
        self.assertEqual(self.state.pending_reports()[0][1]['kind'], 'inn')
        restarted = w.State(self.temp.name)
        self.http.fail_report = False
        try:
            worker = w.Worker({'interval_seconds': 300}, restarted, self.http)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                worker.retry_reports()
            self.assertEqual(json.loads(output.getvalue())['event'], 'inn_report_ack')
            self.assertEqual(restarted.pending_reports(), [])
        finally:
            restarted.db.close()
        self.assertTrue(any(c[0] == 'site' and c[1] == '/api/eis-worker/inn-jobs/inn-job-1/report' for c in self.http.calls))

    def test_cooldown_defers_inn_without_request_and_no_data_requires_explicit_page(self):
        self.state.take_search_slot()
        report = self.worker.process_inn(self.inn_job())
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['error_code'], 'eis_search_cooldown')
        self.assertFalse(any(c[0] == 'request' for c in self.http.calls))
        self.state.set('search_not_before', 0)
        self.http.search_html = f'<input name="customerInn" value="{INN}"><p>По вашему запросу ничего не найдено</p>'.encode()
        report = self.worker.process_inn(self.inn_job())
        self.assertEqual(report['status'], 'no_data')
        self.assertEqual(report['reg_numbers'], [])
        self.assertFalse(report['has_more'])

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
        self.assertEqual(w.load_config(p)['interval_seconds'], 30)
        p.write_text(json.dumps({'eis_token': TOKEN, 'site_token': '', 'site_url': 'https://example.test'}))
        with self.assertRaises(w.WorkerError) as error:
            w.load_config(p)
        self.assertNotIn(TOKEN, str(error.exception))


if __name__ == '__main__':
    unittest.main()
