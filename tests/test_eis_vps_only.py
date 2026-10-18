"""VPS-only cutover: stale browsers cannot upload or steal queued work."""
import asyncio
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eis_server_worker as server
import eis_vps_only as policy
import v301_search_jobs as jobs
from scripts.account_production_v1 import ProductionAccountGate

KEY = 'synthetic-vps-only-key-with-at-least-24-characters'
REG = '32616233661'


class VpsOnlyIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {'EIS223_DATA_DIR': str(self.home),
            'EIS223_UPLOAD_TOKEN': KEY, 'EIS223_AUTH_ENFORCE': 'true',
            'EIS223_SOURCE_MODE': 'OFFICIAL_EIS_VPS_ONLY'})
        env.start()
        self.addCleanup(env.stop)
        self.remote = AsyncMock(side_effect=AssertionError('Remote fetch called'))
        self.ns = {'app': FastAPI(), 'conn': lambda: sqlite3.connect(self.home / 'jobs.sqlite'),
                   'eis_request': self.remote, 'fetch_binary': self.remote}
        jobs.install(self.ns)
        server.install(self.ns)
        with self.ns['conn']() as db:
            db.execute('DELETE FROM v301_search_jobs')
        self.received = []

        async def intake(request: Request):
            self.received.append(await request.body())
            return {'accepted': True}

        for path in ('/api/v294/eis223/archive', '/api/v295/eis223/original/{reg}/{uid}'):
            self.ns['app'].add_api_route(path, intake, methods=['POST'])
        self.ns['app'].add_middleware(ProductionAccountGate)
        policy.install(self.ns)
        session = patch('scripts.account_production_v1.user_session', return_value=None)
        session.start()
        self.addCleanup(session.stop)
        self.client = TestClient(self.ns['app'])
        self.addCleanup(self.client.close)
        self.headers = {'x-eis223-upload-token': KEY}

    def test_browser_uploads_are_rejected_before_body_or_handler(self):
        paths = ('/api/v294/eis223/archive', '/api/v295/eis223/original/' + REG + '/' + 'A'*32)
        for path in paths:
            for browser in ({'origin': 'https://zakupki.gov.ru'},
                            {'origin': 'https://web-production-1c2ea.up.railway.app'},
                            {'sec-fetch-site': 'same-origin'},
                            {'x-eis223-collector-version': '377'}):
                response = self.client.post(path, content=b'old-browser-bytes',
                                            headers={**self.headers, **browser})
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()['code'], 'EIS223_VPS_ONLY')
        self.assertEqual(self.received, [])
        self.assertEqual(self.client.post(paths[0], content=b'no-key').status_code, 401)
        self.assertEqual(self.received, [])
        for path in paths:
            response = self.client.post(path, content=b'worker-bytes', headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.received, [b'worker-bytes', b'worker-bytes'])

    def test_old_browser_endpoints_disabled_even_with_valid_token(self):
        paths = policy.DISABLED_PATHS | {
            '/api/v299/eis223/public-manifest', '/api/v299/eis223/known',
            '/api/v299/eis223/heartbeat', '/api/v301/bridge/report/' + '0'*24,
        }
        for path in paths:
            for method in ('GET', 'POST', 'OPTIONS'):
                response = self.client.request(method, path, headers=self.headers)
                self.assertEqual(response.status_code, 410, (path, method, response.text))
        self.assertEqual(self.received, [])

    def test_legacy_low_level_fetch_is_blocked_without_network(self):
        for name in ('eis_request', 'fetch_binary'):
            with self.assertRaisesRegex(RuntimeError, 'EIS223_VPS_ONLY'):
                asyncio.run(self.ns[name](None, 'https://zakupki.gov.ru/'))
        self.remote.assert_not_called()

    def test_script_links_only_stop_and_public_mode_confirms_policy(self):
        for path in policy.SCRIPT_PATHS:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertIn('__eis223_five_minutes?.stop()', response.text)
            for active_code in ('fetch(', 'prompt(', 'setInterval(', 'setTimeout('):
                self.assertNotIn(active_code, response.text)
        version = self.client.get('/eis223/worker-version').json()
        self.assertEqual(version['intake_mode'], 'vps_only')
        self.assertFalse(version['browser_collector_enabled'])

    def test_cutover_requeues_browser_work_preserves_vps_lease_and_is_idempotent(self):
        jobs._enqueue(self.ns, 'reg', REG)
        browser = jobs._claim(self.ns, 378)
        jobs._enqueue(self.ns, 'reg', '32610000001')
        identity = str(uuid.uuid4())
        running = server.claim(self.ns, identity, 1)[0]
        self.assertEqual(policy.release_browser_leases(self.ns), 1)
        self.assertEqual(policy.release_browser_leases(self.ns), 0)
        with self.ns['conn']() as db:
            state = db.execute('SELECT status,attempts,lease_owner FROM v301_search_jobs WHERE id=?',
                               (browser['id'],)).fetchone()
            live = db.execute('SELECT status,lease_owner,lease_token FROM v301_search_jobs WHERE id=?',
                              (running['id'],)).fetchone()
        self.assertEqual(state, ('queued', 0, 'server-retry'))
        self.assertEqual(live, ('leased', identity, running['lease_token']))
        claimed = server.claim(self.ns, identity, 2)
        self.assertEqual({job['id'] for job in claimed}, {browser['id'], running['id']})

    def test_old_inn_search_gets_explicit_outcome_without_deleting_history(self):
        previous, _ = jobs._enqueue(self.ns, 'inn', '1234567890')
        policy.release_browser_leases(self.ns)
        with self.ns['conn']() as db:
            result = db.execute('SELECT status,detail,target FROM v301_search_jobs WHERE id=?',
                                (previous['id'],)).fetchone()
        self.assertEqual(result, ('error', 'В режиме VPS укажите номер закупки ЕИС.', '1234567890'))


if __name__ == '__main__':
    unittest.main()
