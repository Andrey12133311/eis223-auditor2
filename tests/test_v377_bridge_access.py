"""The production login gate permits authenticated EIS metadata intake only."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware
from scripts.account_production_v1 import ProductionAccountGate
import v311_verified_customer as buyer
import v341_eis_facts as facts

ORIGIN = 'https://zakupki.gov.ru'
KEY = 'fixture-upload-key-with-at-least-24-characters'
REG = '32610000001'
SOURCE = ORIGIN + '/223/purchase/public/purchase/info/common-info.html?regNumber=' + REG
ROUTES = ('/api/v311/eis223/customer', '/api/v341/eis223/card-facts')


class BridgeAccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'EIS223_AUTH_ENFORCE': 'true', 'EIS223_UPLOAD_TOKEN': KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        session_patch = patch('scripts.account_production_v1.user_session', return_value=None)
        self.session = session_patch.start()
        self.addCleanup(session_patch.stop)
        app = FastAPI()
        buyer.install({'app': app})
        facts.install({'app': app})
        app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN],
            allow_methods=['GET', 'POST', 'OPTIONS'],
            allow_headers=['content-type', 'x-eis223-upload-token',
                           'x-eis223-collector-version'])
        # Match production: authentication wraps existing EIS CORS/handlers.
        app.add_middleware(ProductionAccountGate)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def headers(self, **extra):
        return {'Origin': ORIGIN, 'x-eis223-upload-token': KEY,
                'x-eis223-collector-version': '377', **extra}

    def test_browser_preflight_does_not_require_session_or_secret(self):
        for path in ROUTES:
            response = self.client.options(path, headers={
                'Origin': ORIGIN, 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers':
                    'content-type,x-eis223-upload-token,x-eis223-collector-version'})
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers['access-control-allow-origin'], ORIGIN)
            self.assertIn('x-eis223-upload-token', response.headers['access-control-allow-headers'])
        self.session.assert_not_called()

    def test_authenticated_snapshot_reaches_real_validation_and_save(self):
        with patch.object(buyer, '_apply', return_value=True) as save_buyer, \
             patch('v299_browser_intake._apply_customer', return_value=False), \
             patch.object(facts, 'save_snapshot', return_value={'accepted': True}) as save_facts:
            response = self.client.post(ROUTES[0], headers=self.headers(), json={
                'reg_number': REG, 'source_page': SOURCE,
                'customer': 'Общество с ограниченной ответственностью «Проверка»'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['accepted'])
            save_buyer.assert_called_once()
            response = self.client.post(ROUTES[1], headers=self.headers(), json={
                'reg_number': REG, 'source_page': SOURCE, 'prices': [], 'fields': {}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['accepted'])
            save_facts.assert_called_once()
        self.session.assert_not_called()

    def test_invalid_credentials_cannot_write_metadata(self):
        for path in ROUTES:
            for token in ('', 'wrong-key'):
                response = self.client.post(path, headers=self.headers(**{
                    'x-eis223-upload-token': token}), json={})
                self.assertEqual(response.status_code, 401, (path, token))

    def test_existing_origin_and_payload_checks_are_enforced(self):
        for path in ROUTES:
            response = self.client.post(path, headers=self.headers(Origin='https://example.com'), json={})
            self.assertEqual(response.status_code, 403, path)
            self.assertNotIn('access-control-allow-origin', response.headers)
            response = self.client.post(path, headers=self.headers(), json={})
            self.assertEqual(response.status_code, 422, path)
            self.assertEqual(response.headers['access-control-allow-origin'], ORIGIN)

    def test_preflight_from_other_origin_is_rejected_by_cors(self):
        response = self.client.options(ROUTES[0], headers={
            'Origin': 'https://example.com', 'Access-Control-Request-Method': 'POST'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('access-control-allow-origin', response.headers)

    def test_machine_secret_does_not_open_neighbouring_or_private_routes(self):
        for path in ('/api/v311/eis223/customer/extra', '/api/v341/eis223/card-facts/extra',
                     '/me/profile', '/api/scan-now'):
            for method in ('POST', 'OPTIONS'):
                response = self.client.request(method, path, headers=self.headers())
                self.assertEqual(response.status_code, 401, (path, method))


if __name__ == '__main__':
    unittest.main()
