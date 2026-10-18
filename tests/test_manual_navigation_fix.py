"""Legacy links must open the authenticated owner's manual workspace."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scripts import account_page_routes_v1 as pages
from scripts.account_production_v1 import ProductionAccountGate


class ManualNavigationTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'EIS223_AUTH_ENFORCE':'true'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.account=('test-owner','user','approved')
        self.legacy_calls=[]
        app=FastAPI()
        app.include_router(pages.router)

        @app.get('/manual-purchases')
        def legacy():
            self.legacy_calls.append(True)
            return {'global_manual_history':'must not be served'}

        @app.get('/api/v257/manual-purchases')
        def legacy_api():
            return {'global_manual_history':'must not be served'}

        app.add_middleware(ProductionAccountGate)
        for target in ('scripts.account_production_v1.user_session',
                       'scripts.account_page_routes_v1.current_account'):
            p=patch(target,side_effect=lambda *a,**k:self.account)
            p.start()
            self.addCleanup(p.stop)
        self.client=TestClient(app)
        self.addCleanup(self.client.close)

    def test_every_role_follows_legacy_link_into_personal_page(self):
        for role in ('user','manager','admin'):
            self.account=('test-owner',role,'approved')
            response=self.client.get('/manual-purchases',follow_redirects=False)
            self.assertEqual(response.status_code,303)
            self.assertEqual(response.headers['location'],'/account/home')
            page=self.client.get(response.headers['location'])
            self.assertEqual(page.status_code,200)
            self.assertIn('id="manual-add"',page.text)
            self.assertIn('Проверить по ИНН',page.text)
            self.assertIn("fetch('/me/manual-checks'",page.text)
            self.assertIn('no-store',page.headers['cache-control'])
        self.assertEqual(self.legacy_calls,[])

    def test_valid_inn_shortcut_is_preserved_without_external_redirect(self):
        response=self.client.get('/manual-purchases/?inn=7701234567&next=https://evil.invalid',follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/account/home?inn=7701234567')
        invalid=self.client.get('/manual-purchases?inn=bad',follow_redirects=False)
        self.assertEqual(invalid.headers['location'],'/account/home')

    def test_signed_out_legacy_link_shows_login(self):
        self.account=None
        response=self.client.get('/manual-purchases',follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/account/login')
        self.assertEqual(self.legacy_calls,[])

    def test_redirect_does_not_open_shared_manual_api_to_users(self):
        response=self.client.get('/api/v257/manual-purchases')
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.legacy_calls,[])


if __name__=='__main__':
    unittest.main()
