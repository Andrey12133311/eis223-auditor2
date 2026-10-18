"""Exercise the account gate outside real collector CORS and upload handlers."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v295_eis_originals as originals
import v299_browser_intake as intake
import v311_verified_customer as customers
import v341_eis_facts as facts
from scripts.account_production_v1 import ProductionAccountGate


ORIGIN = "https://zakupki.gov.ru"
TOKEN = "collector-upload-key-for-regression-123456"
REG = "32616412317"
SOURCE = ORIGIN + "/223/purchase/public/purchase/info/common-info.html?regNumber=" + REG
CUSTOMER = "/api/v311/eis223/customer"
PRICE = "/api/v322/eis223/price"
FACTS = "/api/v341/eis223/card-facts"
CARD_ROUTES = (CUSTOMER, PRICE, FACTS)
COLLECTOR_ROUTES = (
    ("/api/v294/eis223/archive", "POST"),
    ("/api/v299/eis223/public-manifest", "POST"),
    ("/api/v299/eis223/heartbeat", "POST"),
    ("/api/v299/eis223/known", "GET"),
    ("/api/v310/eis223/card-metadata", "POST"),
    (CUSTOMER, "POST"), (PRICE, "POST"), (FACTS, "POST"),
    ("/api/v301/bridge/next", "GET"),
    ("/api/v301/bridge/report/" + "a" * 24, "POST"),
    ("/api/v295/eis223/original/" + REG + "/" + "A" * 32, "POST"),
    ("/api/v295/eis223/manifest/" + REG, "GET"),
    ("/api/v295/eis223/recheck/" + REG, "POST"),
    ("/api/v295/eis223/recheck-status/" + REG, "GET"),
)


class CollectorCorsAccountTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        ns = {"app": app}
        # Production installs collector CORS first, then wraps it in the gate.
        originals.install(ns)
        customers.install(ns)
        facts.install(ns)
        app.add_middleware(ProductionAccountGate)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        env = patch.dict(os.environ, {
            "EIS223_AUTH_ENFORCE": "true", "EIS223_UPLOAD_TOKEN": TOKEN,
        })
        env.start()
        self.addCleanup(env.stop)
        session = patch("scripts.account_production_v1.user_session", return_value=None)
        self.session = session.start()
        self.addCleanup(session.stop)
        self.writes = []
        for module, name, result in (
            (customers, "_apply", True),
            (intake, "_apply_customer", True),
            (customers, "_save_verified_price", True),
            (facts, "save_snapshot", {"accepted": True, "reg_number": REG}),
        ):
            writer = patch.object(module, name, return_value=result)
            self.writes.append(writer.start())
            self.addCleanup(writer.stop)

    def preflight(self, path, method="POST", **headers):
        return self.client.options(path, headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers":
                "content-type,x-eis223-upload-token,x-eis223-collector-version",
            **headers,
        })

    def upload(self, path, *, token=TOKEN, origin=ORIGIN, payload=None):
        headers = {"Origin": origin, "x-eis223-collector-version": "376"}
        if token is not None:
            headers["x-eis223-upload-token"] = token
        data = {"reg_number": REG, "source_page": SOURCE}
        if path == CUSTOMER:
            data["customer"] = 'ООО «Проверенный заказчик»'
        elif path == PRICE:
            data["price"] = "1 250 000,00 руб."
        elif path == FACTS:
            data.update(fields={"submission_end": "2026-10-30"},
                        field_evidence={"submission_end": "Окончание подачи заявок: 30.10.2026"})
        return self.client.post(path, headers=headers, json=data if payload is None else payload)

    def assert_no_writes(self):
        for writer in self.writes:
            writer.assert_not_called()

    def test_all_collector_preflights_reach_cors_without_session_or_token(self):
        self.session.side_effect = AssertionError("Preflight must not depend on identity DB")
        for path, method in COLLECTOR_ROUTES:
            with self.subTest(path=path):
                response = self.preflight(path, method)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
                self.assertIn(method, response.headers["access-control-allow-methods"])
                self.assertIn("x-eis223-upload-token", response.headers["access-control-allow-headers"])
        self.session.assert_not_called()
        self.assert_no_writes()

    def test_preflight_keeps_cors_origin_method_and_header_limits(self):
        for overrides in (
            {"Origin": "https://untrusted.example"},
            {"Access-Control-Request-Method": "DELETE"},
            {"Access-Control-Request-Headers": "x-untrusted-header"},
        ):
            with self.subTest(headers=overrides):
                response = self.preflight(FACTS, **overrides)
                self.assertEqual(response.status_code, 400)
                if "Origin" in overrides:
                    self.assertNotIn("access-control-allow-origin", response.headers)
        self.assert_no_writes()

    def test_new_machine_exemptions_are_exact_not_broad_version_prefixes(self):
        for path in (CUSTOMER + "/other", PRICE + "/other", FACTS + "/other",
                     "/api/v311/customer/" + REG, "/api/scan-now", "/me/procurements"):
            with self.subTest(path=path):
                self.assertEqual(self.preflight(path).status_code, 401)
                self.assertEqual(self.upload(path).status_code, 401)
        self.assert_no_writes()

    def test_card_uploads_require_key_even_after_successful_preflight(self):
        for path in CARD_ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self.preflight(path).status_code, 200)
                for token in (None, "wrong-upload-key"):
                    self.assertEqual(self.upload(path, token=token).status_code, 401)
        self.assert_no_writes()

    def test_approved_admin_session_does_not_replace_upload_key(self):
        self.session.return_value = ("id", "admin", "approved")
        for path in CARD_ROUTES:
            with self.subTest(path=path):
                response = self.upload(path, token=None)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"], "Unauthorized")
        self.assert_no_writes()

    def test_valid_card_uploads_reach_real_handlers_without_identity_db(self):
        self.session.side_effect = AssertionError("Valid machine key must bypass identity DB")
        for path in CARD_ROUTES:
            with self.subTest(path=path):
                response = self.upload(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["accepted"])
                self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        self.session.assert_not_called()
        for writer in self.writes:
            writer.assert_called_once()

    def test_card_handlers_still_reject_untrusted_origin_source_and_oversized_payload(self):
        for path in CARD_ROUTES:
            with self.subTest(path=path):
                response = self.upload(path, origin="https://untrusted.example")
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("access-control-allow-origin", response.headers)
                self.assertEqual(self.upload(path, payload={
                    "reg_number": REG, "source_page": "https://untrusted.example/card",
                    "customer": 'ООО «Заказчик»', "price": "1 250 000,00 руб.",
                }).status_code, 422)
                self.assertEqual(self.upload(path, payload={"padding": "x" * 30001}).status_code, 413)
        self.assert_no_writes()


if __name__ == "__main__":
    unittest.main()
