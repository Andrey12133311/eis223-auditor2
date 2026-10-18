"""Storage errors stay explicit/readable and recover without changing saved data."""
import errno
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware
from scripts import v357_storage_guard as guard

ORIGIN = "https://zakupki.gov.ru"


class StorageGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.calls = []

        @self.app.api_route('/{path:path}', methods=['GET', 'POST'])
        async def endpoint(path: str):
            self.calls.append(path)
            if path == 'disk-error':
                raise OSError(errno.ENOSPC, 'No space left on device')
            if path == 'sqlite-error':
                raise sqlite3.OperationalError('database or disk is full')
            if path == 'unrelated-error':
                raise sqlite3.OperationalError('database is locked')
            return {'accepted': True}

        # Match production order: storage middleware wraps existing CORS.
        self.app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN],
            allow_methods=['GET', 'POST', 'OPTIONS'],
            allow_headers=['content-type', 'x-eis223-upload-token',
                           'x-eis223-collector-version'])
        guard.install({'app': self.app})
        self.client = TestClient(self.app)

    def assert_storage_error(self, response):
        self.assertEqual(response.status_code, 507)
        self.assertEqual(response.json()['code'], 'EIS223_STORAGE_FULL')
        self.assertEqual(response.json()['retry_after_seconds'], 60)
        self.assertEqual(response.headers['retry-after'], '60')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['access-control-allow-origin'], ORIGIN)
        self.assertIn('retry-after', response.headers['access-control-expose-headers'].lower())

    def test_all_bridge_routes_are_guarded(self):
        with patch.object(guard, 'free_bytes', return_value=0):
            for route in guard.BRIDGE_PREFIXES:
                with self.subTest(route=route):
                    response = self.client.post(route, headers={'Origin': ORIGIN})
                    self.assert_storage_error(response)
                    self.assertEqual(response.json()['free_bytes'], 0)
        self.assertEqual(self.calls, [])
        self.assertIn('/api/v311/eis223/customer', guard.BRIDGE_PREFIXES)
        self.assertIn('/api/v341/eis223/card-facts', guard.BRIDGE_PREFIXES)

    def test_preflight_succeeds_while_full(self):
        with patch.object(guard, 'free_bytes', return_value=0) as free:
            response = self.client.options('/api/v341/eis223/card-facts', headers={
                'Origin': ORIGIN,
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers':
                    'content-type,x-eis223-upload-token,x-eis223-collector-version'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['access-control-allow-origin'], ORIGIN)
            self.assertIn('POST', response.headers['access-control-allow-methods'])
            self.assertIn('x-eis223-upload-token', response.headers['access-control-allow-headers'])
            free.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_recovers_immediately_after_free_space_restored(self):
        with patch.object(guard, 'free_bytes', side_effect=[0, guard.MIN_FREE]):
            self.assert_storage_error(self.client.get('/api/v301/bridge/next', headers={'Origin': ORIGIN}))
            response = self.client.get('/api/v301/bridge/next', headers={'Origin': ORIGIN})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'accepted': True})
        self.assertEqual(self.calls, ['api/v301/bridge/next'])

    def test_unknown_space_does_not_block(self):
        with patch.object(guard, 'free_bytes', return_value=None):
            self.assertEqual(self.client.get('/api/v301/bridge/next').status_code, 200)

    def test_disk_and_sqlite_exceptions_have_same_retry_contract(self):
        for route in ('/disk-error', '/sqlite-error'):
            with self.subTest(route=route):
                self.assert_storage_error(self.client.get(route, headers={'Origin': ORIGIN}))

    def test_unrelated_errors_propagate(self):
        with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'):
            self.client.get('/unrelated-error')

    def test_unrelated_endpoint_remains_available(self):
        with patch.object(guard, 'free_bytes', return_value=0):
            self.assertEqual(self.client.get('/health').status_code, 200)

    def test_no_cors_grant_to_untrusted_origin(self):
        with patch.object(guard, 'free_bytes', return_value=0):
            response = self.client.post('/api/v341/eis223/card-facts', headers={'Origin': 'https://example.com'})
            self.assertEqual(response.status_code, 507)
            self.assertNotIn('access-control-allow-origin', response.headers)


if __name__ == '__main__':
    unittest.main()
