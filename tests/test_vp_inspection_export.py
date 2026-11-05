import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from fastapi import FastAPI
from fastapi.testclient import TestClient
import vp_inspection_export as export
from vp_inspection_criteria import CRITERIA


def snapshot():
    rows = [{"key": c.key, "criterion": c.title, "law": c.law,
             "has_violation": "✓" if i == 0 else "",
             "no_violation": "", "koap": "", "comment": "MUST NEVER LEAK",
             "evidence": "Оплата производится в течение 30 рабочих дней." if i == 0 else "",
             "document": "Проект договора.pdf" if i == 0 else "",
             "reason": "Недостаточно данных" if i else ""} for i, c in enumerate(CRITERIA)]
    return {"revision": export.REVISION, "legal_engine_signature": export.engine_signature(), "reg_number": "32616411351", "generated_at": "2026-11-05T12:00:00Z",
            "fields": export.metadata({"reg_number": "32616411351", "customer": "ООО ТЕСТ", "title": "Реальная закупка", "price": "100000", "method": "Аукцион", "published": "2026-11-01"}),
            "rows": rows}


class FormTests(unittest.TestCase):
    def test_metadata_clears_unknown_complaint_and_does_not_assume_framework(self):
        fields = export.metadata({"reg_number": "32616411351", "customer": "ООО ТЕСТ", "price": 3000000})
        self.assertEqual(fields["CASE_NUMBER"], "")
        self.assertEqual(fields["COMPLAINANT"], "")
        self.assertNotIn("рамочный", fields["PRICE"])
        self.assertEqual(fields["STAGE"], "")

    def test_real_procurement_adapter_registration_number(self):
        from v332_all_procurements_audit import as_procurement
        p = as_procurement({"reg_number": "32616411351", "customer": "ООО ТЕСТ"})
        self.assertEqual(export.metadata(p)["REG_NUMBER"], "32616411351")

    def test_appendix_keeps_multiple_result_episodes(self):
        value = snapshot()
        value["rows"][0]["results"] = [{"evidence": "Первый эпизод", "document": "one.pdf"}, {"evidence": "Второй эпизод", "document": "two.pdf"}]
        text = " ".join(export._appendix(value))
        self.assertIn("Первый эпизод", text)
        self.assertIn("Второй эпизод", text)

    def test_template_and_both_formats_have_19_criteria_and_blank_comments(self):
        for extension in ("docx", "odt"):
            with self.subTest(extension=extension):
                rendered = export.render(snapshot(), extension)
                with ZipFile(BytesIO(rendered)) as z:
                    name = "word/document.xml" if extension == "docx" else "content.xml"
                    body = z.read(name)
                tree = ET.fromstring(body)
                text = " ".join(tree.itertext())
                for sample in ("ЮПИТЕР", "Курчатов", "260001266600268", "АСТ ГОЗ", "MUST NEVER LEAK", "переторжка", "Подано", "Допущено", "{{"):
                    self.assertNotIn(sample, text)
                self.assertIn("ООО ТЕСТ", text)
                self.assertIn("Реальная закупка", text)
                if extension == "docx":
                    table = tree.find(export.W + "body").findall(export.W + "tbl")[1]
                    rows = table.findall(export.W + "tr")
                    cells = [r.findall(export.W + "tc") for r in rows]
                else:
                    table = tree.findall(".//" + export.OT + "table")[1]
                    rows = table.findall(".//" + export.OT + "table-row")
                    cells = [r.findall(export.OT + "table-cell") for r in rows]
                self.assertEqual(len(rows), 21)
                self.assertEqual("".join(cells[1][1].itertext()), "✓")
                for row in cells[1:20]:
                    self.assertEqual("".join(row[4].itertext()).strip(), "")
                self.assertIn("Основания результатов проверки", text)

    def test_missing_snapshot_returns_409(self):
        with TemporaryDirectory() as tmp, patch.object(export, "root", return_value=Path(tmp)):
            with self.assertRaises(export.HTTPException) as cm:
                export.read_snapshot("32616411351")
            self.assertEqual(cm.exception.status_code, 409)

    def test_read_rejects_stale_or_wrong_procurement(self):
        with TemporaryDirectory() as tmp, patch.object(export, "root", return_value=Path(tmp)):
            path = export.snapshot_path("32616411351")
            path.parent.mkdir()
            value = snapshot()
            value["reg_number"] = "32616417059"
            path.write_text(json.dumps(value))
            with self.assertRaises(export.HTTPException):
                export.read_snapshot("32616411351")

    def test_outdated_legal_engine_snapshot_requires_recheck(self):
        with TemporaryDirectory() as tmp, patch.object(export, "root", return_value=Path(tmp)):
            path = export.snapshot_path("32616411351")
            path.parent.mkdir()
            value = snapshot()
            value["legal_engine_signature"] = "old-engine"
            path.write_text(json.dumps(value))
            with self.assertRaises(export.HTTPException) as cm:
                export.read_snapshot("32616411351")
            self.assertEqual(cm.exception.status_code, 409)

    def test_saved_json_strips_comments(self):
        with TemporaryDirectory() as tmp, patch.object(export, "root", return_value=Path(tmp)):
            path = export.snapshot_path("32616411351")
            path.parent.mkdir()
            path.write_text(json.dumps(snapshot()))
            self.assertTrue(all(row["comment"] == "" for row in export.read_snapshot("32616411351")["rows"]))

    def test_route_requires_account_when_auth_enforced(self):
        app = FastAPI()
        export.install({"app": app})
        with patch.dict("os.environ", {"EIS223_AUTH_ENFORCE": "true"}):
            client = TestClient(app)
            self.assertEqual(client.get("/api/inspection/32616411351.odt").status_code, 401)

    def test_export_does_not_trigger_analysis_or_file_extraction(self):
        app = FastAPI()
        ns = {"app": app}
        export.install(ns)
        with patch.dict("os.environ", {"EIS223_AUTH_ENFORCE": "false"}), patch.object(export, "read_snapshot", return_value=snapshot()):
            client = TestClient(app)
            response = client.get("/api/inspection/32616411351.odt")
            self.assertEqual(response.status_code, 200)
            self.assertIn("application/vnd.oasis.opendocument.text", response.headers["content-type"])
        self.assertTrue(callable(ns["_vp_save_snapshot"]))


class LegacyDownloadTests(unittest.TestCase):
    def app(self):
        from fastapi.responses import Response
        from scripts.account_production_v1 import ProductionAccountGate
        app = FastAPI()

        @app.middleware("http")
        async def old_inspection_template(request, call_next):
            if request.url.path.startswith("/api/purchases/") and "/inspection" in request.url.path:
                return Response(b"STALE-TEMPLATE", media_type="text/plain")
            return await call_next(request)

        @app.get("/api/v304/memo/{reg}.odt")
        async def memo(reg):
            return Response(b"EXISTING-MEMO", media_type="application/vnd.oasis.opendocument.text")

        @app.get("/api/v234/inspection-card/{reg}")
        async def card(reg):
            return Response(b"EXISTING-CARD", media_type="text/html")

        export.install({"app": app})
        app.add_middleware(ProductionAccountGate)
        return app

    def request(self, path, *, account=("account", "user", "approved"), method="GET"):
        with patch.dict("os.environ", {"EIS223_AUTH_ENFORCE": "true"}), \
             patch("scripts.account_production_v1.user_session", return_value=account), \
             patch.object(export, "read_snapshot", return_value=snapshot()) as reader:
            result = TestClient(self.app()).request(method, path, follow_redirects=False)
            return result, reader.call_count

    def test_existing_buttons_use_current_saved_report_before_old_middleware(self):
        for base in ("inspection", "inspection-sheet"):
            for extension in ("", ".odt", ".docx", ".json"):
                with self.subTest(base=base, extension=extension):
                    result, count = self.request("/api/purchases/32616411351/" + base + extension)
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(count, 1)
                    self.assertNotIn(b"STALE-TEMPLATE", result.content)
                    if extension == ".json":
                        self.assertEqual(result.json()["reg_number"], "32616411351")
                    else:
                        self.assertTrue(result.content.startswith(b"PK"))

    def test_legacy_pdf_is_authenticated_redirect_to_odt(self):
        for base in ("inspection", "inspection-sheet"):
            path = "/api/purchases/32616411351/" + base + ".pdf"
            result, count = self.request(path)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.headers["location"], "/api/inspection/32616411351.odt")
            self.assertEqual(count, 0)
            guest, count = self.request(path, account=None)
            self.assertEqual(guest.status_code, 401)
            self.assertEqual(count, 0)

    def test_alias_downloads_preserve_login_and_method_boundaries(self):
        path = "/api/purchases/32616411351/inspection-sheet.odt"
        guest, count = self.request(path, account=None)
        self.assertEqual(guest.status_code, 401)
        self.assertEqual(count, 0)
        post, count = self.request(path, method="POST")
        self.assertEqual(post.status_code, 403)
        self.assertEqual(count, 0)
        admin_post, count = self.request(path, method="POST", account=("admin", "admin", "approved"))
        self.assertEqual(admin_post.content, b"STALE-TEMPLATE")
        self.assertEqual(count, 0)
        head, count = self.request(path, method="HEAD")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(count, 1)

    def test_unknown_formats_do_not_fall_back_to_old_template(self):
        for extension in ("xlsx", "csv", "exe"):
            result, count = self.request("/api/purchases/32616411351/inspection." + extension,
                                         account=("admin", "admin", "approved"))
            self.assertEqual(result.status_code, 404)
            self.assertEqual(count, 0)
        invalid, count = self.request("/api/purchases/not-a-number/inspection.odt",
                                      account=("admin", "admin", "approved"))
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(count, 0)

    def test_other_memo_and_inspection_card_routes_are_untouched(self):
        memo, count = self.request("/api/v304/memo/32616411351.odt")
        self.assertEqual(memo.content, b"EXISTING-MEMO")
        self.assertEqual(count, 0)
        card, count = self.request("/api/v234/inspection-card/32616411351",
                                   account=("admin", "admin", "approved"))
        self.assertEqual(card.content, b"EXISTING-CARD")
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
