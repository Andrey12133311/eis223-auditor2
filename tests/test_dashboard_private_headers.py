"""Retiring a counter override must not make account data cacheable."""
import unittest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
import v270_count_display


class PrivateDashboardHeaders(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.body = b'{"purchases":[{"reg_number":"32616448060","violations":2}]}'
        @self.app.get('/api/v257/dashboard')
        def dashboard():
            return Response(self.body, media_type='application/json')
        @self.app.get('/api/v204/dashboard')
        def warming():
            return Response(b'not ready', status_code=503, headers={'Retry-After': '10'})
        @self.app.get('/unrelated')
        def other():
            return Response(b'asset', headers={'Cache-Control': 'public, max-age=300'})
        v270_count_display.install({'app': self.app})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_saved_episode_counts_and_body_are_never_rewritten(self):
        response = self.client.get('/api/v257/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.body)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['content-length'], str(len(self.body)))

    def test_error_status_and_retry_after_are_preserved(self):
        response = self.client.get('/api/v204/dashboard')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b'not ready')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['retry-after'], '10')

    def test_unrelated_paths_keep_their_original_headers(self):
        response = self.client.get('/unrelated')
        self.assertEqual(response.content, b'asset')
        self.assertEqual(response.headers['cache-control'], 'public, max-age=300')


if __name__ == '__main__':
    unittest.main()
