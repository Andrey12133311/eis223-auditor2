"""Exercise actual memo routes through the production account boundary."""
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID
from xml.etree import ElementTree as ET
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts.account_production_v1 import ProductionAccountGate
from scripts import v367_private_features as private
import v304_official_memo_odt as renderer
from memo_fixture import memo_template


REG = "32616474277"
NO_EVIDENCE = "32616474278"
MISSING = "32616474279"
ARCHIVE_ID = UUID("00000000-0000-0000-0000-000000000001")


class IdentityStore:
    """Only owner-filtered SELECTs are permitted during a document download."""

    def __init__(self, archived_odt):
        self.fields = {
            ("alice", REG): ("Получатель Алисы", "Исполнитель Алисы", None),
            ("bob", REG): ("Получатель Бориса", "Исполнитель Бориса", None),
        }
        self.archived_odt = archived_odt
        self.queries = []
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if "SELECT recipient,executor,phone FROM account_memo_requisites" in sql:
            if "WHERE user_id=%s AND reg_number=%s" not in sql:
                raise AssertionError("Memo requisites must be scoped to the owner")
            self.row = self.fields.get(params)
        elif "SELECT m.reg_number,b.odt,b.pdf FROM account_memos m" in sql:
            if "WHERE m.id=%s AND m.user_id=%s" not in sql:
                raise AssertionError("Archived memos must be scoped to the owner")
            self.row = ((REG, self.archived_odt, None)
                        if params == (ARCHIVE_ID, "alice") else None)
        else:
            raise AssertionError("Unexpected query or write during download: " + sql)
        return self

    def fetchone(self):
        return self.row


class MemoDownloadAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eis-memo-access-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.template = directory / "template.odt"
        self.original_template = memo_template()
        self.template.write_bytes(self.original_template)
        database = directory / "eis.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript("""
                CREATE TABLE purchases (
                    reg_number TEXT PRIMARY KEY, title TEXT, customer TEXT,
                    customer_inn TEXT, method TEXT);
                CREATE TABLE findings (
                    id INTEGER PRIMARY KEY, reg_number TEXT, kind TEXT,
                    title TEXT, evidence TEXT, law TEXT, document TEXT);
            """)
            db.executemany("INSERT INTO purchases VALUES(?,?,?,?,?)", [
                (reg, "Поставка оборудования", 'ООО «Пример»', "7701234567", "Аукцион")
                for reg in (REG, NO_EVIDENCE)
            ])
            db.execute("INSERT INTO findings VALUES(1,?,?,?,?,?,?)", (
                REG, "violation", "Нарушение срока размещения протокола",
                "Протокол подписан 01.10.2026, размещён 15.10.2026.",
                "223-ФЗ, ст. 4 ч. 12", "Протокол.pdf"))
        self.identity = IdentityStore(self.original_template)
        self.account = ("alice", "user", "approved")
        for mocked in (
            patch.dict(os.environ, {"EIS223_AUTH_ENFORCE": "true"}),
            patch.object(renderer, "template_path", return_value=self.template),
            patch.object(renderer, "storage", return_value=directory),
            patch.object(renderer, "_rusprofile_company", new=AsyncMock(return_value=None)),
            patch.object(private, "connect", return_value=self.identity),
            patch.object(private, "current_account", side_effect=lambda request: (
                request.scope["eis223_account_id"],
                request.scope["eis223_account_role"], "approved")),
            patch("scripts.account_production_v1.user_session",
                  side_effect=lambda *args: self.account),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        app = FastAPI()
        renderer.install({"app": app, "conn": lambda: sqlite3.connect(
            "file:" + str(database) + "?mode=ro", uri=True)})
        app.include_router(private.router)
        app.add_middleware(ProductionAccountGate)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def odt_text(self, response):
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertIn("application/vnd.oasis.opendocument.text",
                      response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("no-store", response.headers["cache-control"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.read("mimetype"),
                             b"application/vnd.oasis.opendocument.text")
            return " ".join(ET.fromstring(archive.read("content.xml")).itertext())

    def test_user_and_manager_download_real_odt_with_only_own_requisites(self):
        for owner, role, expected, forbidden in (
            ("alice", "user", "Алисы", "Бориса"),
            ("bob", "manager", "Бориса", "Алисы"),
        ):
            with self.subTest(role=role):
                self.account = (owner, role, "approved")
                response = self.client.get(
                    "/api/v304/memo/" + REG + ".odt?user_id=alice&owner=bob")
                text = self.odt_text(response)
                self.assertIn("Получатель " + expected, text)
                self.assertIn("Исполнитель " + expected, text)
                self.assertNotIn(forbidden, text)
                self.assertIn(REG, text)
                self.assertIn("Нарушение срока размещения протокола", text)
        self.assertEqual(self.template.read_bytes(), self.original_template)

    def test_template_status_is_readable_for_approved_user(self):
        response = self.client.get("/api/v304/memo-template/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["template_installed"])
        self.assertIn("no-store", response.headers["cache-control"])

    def test_anonymous_cannot_download_or_read_template_status(self):
        self.account = None
        for path in ("/api/v304/memo/" + REG + ".odt?user_id=alice",
                     "/api/v304/memo-template/status"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.identity.queries, [])

    def test_download_permission_does_not_allow_writes_or_template_administration(self):
        for method, path in (
            ("POST", "/api/v304/memo-template"),
            ("GET", "/api/v304/memo-template/upload"),
            ("POST", "/api/v304/memo/" + REG + ".odt"),
            ("POST", "/api/v218/memo/" + REG),
            ("GET", "/api/v304/memo/not-a-procurement.odt"),
            ("GET", "/api/v304/memo/" + REG + ".pdf"),
            ("POST", "/api/purchases/" + REG + "/recheck"),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.template.read_bytes(), self.original_template)
        self.assertEqual(self.identity.queries, [])

    def test_missing_purchase_and_missing_evidence_keep_actionable_errors(self):
        for reg, status, detail in (
            (MISSING, 404, "Закупка не найдена"),
            (NO_EVIDENCE, 409, "нет нарушения с документальными основаниями"),
        ):
            with self.subTest(reg=reg):
                response = self.client.get("/api/v304/memo/" + reg + ".odt")
                self.assertEqual(response.status_code, status)
                self.assertIn(detail, response.json()["detail"])

    def test_private_archive_still_requires_matching_owner(self):
        path = "/me/memo-archive/" + str(ARCHIVE_ID) + "/odt"
        self.odt_text(self.client.get(path))
        self.account = ("bob", "manager", "approved")
        response = self.client.get(path + "?user_id=alice")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Служебная записка не найдена")


if __name__ == "__main__":
    unittest.main()
