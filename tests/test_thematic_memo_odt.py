"""Thematic downloads use the official ODT and the current owner's campaign."""
import io
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts.account_production_v1 import ProductionAccountGate
from scripts import v367_private_features as private
import v304_official_memo_odt as renderer
from memo_fixture import memo_template


REG = "32616474277"
OTHER_REG = "32616474278"
ALICE_BATCH = "a" * 24
BOB_BATCH = "b" * 24
EMPTY_BATCH = "c" * 24
THEMATIC_TITLE = "Тематическое нарушение срока размещения протокола"
GENERAL_TITLE = "Только общее нарушение срока разъяснений"
OTHER_CATEGORY = "Нарушение вне выбранного направления"
BOB_TITLE = "Нарушение обеспечения из другой тематической проверки"
MSP_TITLE = "Размер обеспечения заявки превышает 2% НМЦД в закупке только для МСП"


class Selection:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class IdentityStore:
    """Fail on any unscoped identity read or write during an ODT download."""
    def __init__(self):
        self.owners = {("alice", ALICE_BATCH), ("alice", EMPTY_BATCH), ("bob", BOB_BATCH)}
        self.fields = {
            ("alice", REG): ("Получатель Алисы", "Исполнитель Алисы", "+7 000 000-00-01"),
            ("bob", REG): ("Получатель Бориса", "Исполнитель Бориса", "+7 000 000-00-02"),
        }
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if "SELECT 1 FROM account_thematic_batches" in sql:
            if "WHERE user_id=%s AND batch_id=%s" not in sql:
                raise AssertionError("Campaign reads must use the authenticated owner")
            return Selection((1,) if tuple(params) in self.owners else None)
        if "SELECT recipient,executor,phone FROM account_memo_requisites" in sql:
            if "WHERE user_id=%s AND reg_number=%s" not in sql:
                raise AssertionError("Memo requisites must use the authenticated owner")
            return Selection(self.fields.get(tuple(params)))
        raise AssertionError("Unexpected query or write during download: " + sql)


class ThematicMemoOdtTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eis-thematic-memo-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.template = self.directory / "template.odt"
        self.original_template = memo_template()
        self.template.write_bytes(self.original_template)
        self.database = self.directory / "audit.sqlite"
        with sqlite3.connect(self.database) as db:
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,title TEXT,
                    customer TEXT,customer_inn TEXT,method TEXT);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,
                    kind TEXT,title TEXT,evidence TEXT,law TEXT,document TEXT);
                CREATE TABLE thematic_batches(id TEXT PRIMARY KEY,categories TEXT);
                CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,PRIMARY KEY(batch_id,reg));
                CREATE TABLE thematic_findings(batch_id TEXT,reg TEXT,category TEXT,
                    title TEXT,law TEXT,evidence TEXT,code TEXT,document TEXT,
                    source_url TEXT,law_url TEXT);
            """)
            db.executemany("INSERT INTO purchases VALUES(?,?,?,?,?)", [
                (reg, "Поставка оборудования", 'ООО «Пример»', "7701234567", "Аукцион")
                for reg in (REG, OTHER_REG)
            ])
            db.executemany("INSERT INTO thematic_batches VALUES(?,?)", [
                (ALICE_BATCH, '["dates"]'), (BOB_BATCH, '["security"]'),
                (EMPTY_BATCH, '["dates"]'),
            ])
            db.executemany("INSERT INTO thematic_items VALUES(?,?)", [
                (batch, REG) for batch in (ALICE_BATCH, BOB_BATCH, EMPTY_BATCH)
            ])
            db.executemany("INSERT INTO thematic_findings VALUES(?,?,?,?,?,?,?,?,?,?)", [
                self.finding(ALICE_BATCH, "dates", THEMATIC_TITLE),
                self.finding(ALICE_BATCH, "security", OTHER_CATEGORY),
                self.finding(BOB_BATCH, "security", BOB_TITLE),
            ])
        self.identity = IdentityStore()
        self.account = ("alice", "user", "approved")
        for mocked in (
            patch.dict(os.environ, {"EIS223_AUTH_ENFORCE": "true", "EIS223_ACCOUNTS_MOUNT": "true"}),
            patch.object(renderer, "template_path", return_value=self.template),
            patch.object(renderer, "storage", return_value=self.directory),
            patch.object(renderer, "_rusprofile_company", new=AsyncMock(return_value=None)),
            patch.object(private, "connect", return_value=self.identity),
            patch("scripts.account_api_v1.connect", return_value=self.identity),
            patch("scripts.account_production_v1.user_session", side_effect=lambda *args: self.account),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.app = FastAPI()
        renderer.install({"app": self.app, "conn": self.read_only_connection})
        self.app.add_middleware(ProductionAccountGate)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def read_only_connection(self):
        return sqlite3.connect("file:" + str(self.database) + "?mode=ro", uri=True)

    def finding(self, batch, category, title, number=1):
        return (batch, REG, category, title, "223-ФЗ, ст. 4 ч. 12",
                f"Протокол № {number} подписан 01.10.2026, размещён 15.10.2026.",
                "223-4-12-PROTOCOL-DELAY", f"Протокол № {number}.pdf",
                f"https://zakupki.gov.ru/223/notice/public/fixture/{number}", "")

    def path(self, batch=ALICE_BATCH, reg=REG):
        return f"/api/v304/thematic-memo/{batch}/{reg}.odt"

    def text_from_odt(self, response, batch=ALICE_BATCH):
        self.assertEqual(response.status_code, 200, response.text[:400])
        self.assertEqual(response.headers["content-type"], "application/vnd.oasis.opendocument.text")
        self.assertEqual(response.headers["content-disposition"],
            f'attachment; filename="sluzhebnaya_zapiska_thematic_{batch}_{REG}.odt"')
        self.assertIn("no-store", response.headers["cache-control"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.infolist()[0].filename, "mimetype")
            self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/vnd.oasis.opendocument.text")
            self.assertIn("styles.xml", archive.namelist())
            text = " ".join(ET.fromstring(archive.read("content.xml")).itertext())
        return text.replace("\u00a0", " ")

    def add_general_finding(self):
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO findings VALUES(1,?,?,?,?,?,?)", (
                REG, "violation", GENERAL_TITLE, "Подтверждённые общие обстоятельства",
                "223-ФЗ, ст. 3.2 ч. 3", "Общий документ.pdf"))

    def test_thematic_evidence_without_general_findings_downloads_official_odt(self):
        with self.read_only_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)
            before = list(db.iterdump())
        text = self.text_from_odt(self.client.get(self.path() + "?user_id=bob&owner=bob"))
        for value in ("СЛУЖЕБНАЯ ЗАПИСКА", REG, THEMATIC_TITLE,
                      "Получатель Алисы", "Исполнитель Алисы", "+7 000 000-00-01"):
            self.assertIn(value, text)
        self.assertNotIn("Бориса", text)
        self.assertEqual(self.template.read_bytes(), self.original_template)
        with self.read_only_connection() as db:
            self.assertEqual(list(db.iterdump()), before)

    def test_only_chosen_batch_categories_enter_memo_without_general_fallback(self):
        self.add_general_finding()
        text = self.text_from_odt(self.client.get(self.path() + "?batch=" + BOB_BATCH))
        self.assertIn(THEMATIC_TITLE, text)
        for value in (GENERAL_TITLE, OTHER_CATEGORY, BOB_TITLE):
            self.assertNotIn(value, text)

    def test_each_owner_gets_only_own_campaign_and_requisites(self):
        self.account = ("bob", "manager", "approved")
        denied = self.client.get(self.path() + "?owner=alice&user_id=alice")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(self.identity.queries[-1][1], ("bob", ALICE_BATCH))
        text = self.text_from_odt(self.client.get(self.path(BOB_BATCH)), BOB_BATCH)
        self.assertIn(BOB_TITLE, text)
        self.assertIn("Получатель Бориса", text)
        self.assertIn("Исполнитель Бориса", text)
        self.assertNotIn(THEMATIC_TITLE, text)
        self.assertNotIn("Алисы", text)

    def test_anonymous_has_no_new_document_access(self):
        self.account = None
        for method in ("GET", "HEAD"):
            response = self.client.request(method, self.path() + "?owner=alice")
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.identity.queries, [])

    def test_missing_membership_or_thematic_evidence_cannot_use_general_finding(self):
        self.add_general_finding()
        for path, status in ((self.path(reg=OTHER_REG), 404),
                             (self.path(EMPTY_BATCH), 409),
                             (self.path("d" * 24), 404)):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, status)
                self.assertNotIn("application/vnd.oasis.opendocument.text", response.headers["content-type"])

    def test_new_permission_is_only_authenticated_thematic_odt_read(self):
        for method, path in (("POST", self.path()), ("DELETE", self.path()),
                             ("GET", self.path().replace(".odt", ".pdf")),
                             ("GET", self.path().replace(".odt", ".docx")),
                             ("GET", self.path("invalid")),
                             ("GET", self.path(reg="invalid"))):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path).status_code, 403)
        self.assertEqual(self.identity.queries, [])
        # The existing shared route still reads only its own general source.
        response = self.client.get(f"/api/v304/memo/{REG}.odt?thematic_batch={ALICE_BATCH}")
        self.assertEqual(response.status_code, 409)

    def test_all_selected_thematic_episodes_survive_without_four_item_cutoff(self):
        extra = [self.finding(ALICE_BATCH, "dates", f"Отдельный тематический эпизод № {n}", n)
                 for n in range(2, 7)]
        with sqlite3.connect(self.database) as db:
            db.executemany("INSERT INTO thematic_findings VALUES(?,?,?,?,?,?,?,?,?,?)", extra)
        text = self.text_from_odt(self.client.get(self.path()))
        self.assertIn(THEMATIC_TITLE, text)
        for row in extra:
            self.assertIn(row[3], text)

    def reassess_msp(self, state, *, only_msp=False):
        from scripts import v385_msp_reassessment as reassessment
        from v384_thematic_findings import thematic_items
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY,reg_number TEXT)")
            db.execute("UPDATE thematic_batches SET categories=? WHERE id=?",
                       ('["dates","security"]', ALICE_BATCH))
            db.execute("DELETE FROM thematic_findings WHERE batch_id=? AND category='security'",
                       (ALICE_BATCH,))
            if only_msp:
                db.execute("DELETE FROM thematic_findings WHERE batch_id=? AND category='dates'",
                           (ALICE_BATCH,))
            db.execute("INSERT INTO thematic_findings VALUES(?,?,?,?,?,?,?,?,?,?)", (
                ALICE_BATCH, REG, "security", MSP_TITLE, "ПП РФ № 1352, п. 23",
                "В документации предусмотрено обеспечение заявки в размере 5% НМЦД.",
                reassessment.ELIGIBLE_CODE, "Извещение.pdf",
                "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32, ""))
        ns = {"conn": lambda: sqlite3.connect(self.database)}
        item = next(row for row in thematic_items(ns, ALICE_BATCH, REG)
                    if row["code"] == reassessment.ELIGIBLE_CODE)
        purchase = {"reg": REG, "_msp_source_token": reassessment.read_source_token(ns, REG)}
        if state != "unknown":
            purchase["fact_evidence"] = {"sme_only": {"value": state == "yes",
                "source": "official_eis_card", "evidence": "Только МСП: " + state}}
        docs = []
        if state == "conflict":
            text = "Участниками закупки могут быть только субъекты малого и среднего предпринимательства: Да"
            docs = [{"name": "Извещение.pdf", "status": "checked", "text": text,
                     "meta": {"source": "official_eis_original",
                              "source_url": item["source_url"],
                              "sha256": hashlib.sha256(text.encode()).hexdigest()}}]
        result = reassessment.record_reassessment(ns, "alice", ALICE_BATCH, REG,
                                                  item["finding_key"], purchase, docs)
        self.assertEqual(result["reassessment"]["scope"]["state"], state)
        return result

    def test_revised_only_cannot_generate_false_memo_or_fall_back_to_general(self):
        self.add_general_finding()
        for state in ("no", "unknown", "conflict"):
            with self.subTest(state=state):
                self.reassess_msp(state, only_msp=True)
                with self.read_only_connection() as db:
                    before = list(db.iterdump())
                response = self.client.get(self.path())
                self.assertEqual(response.status_code, 409, response.text)
                self.assertNotIn("application/vnd.oasis.opendocument.text", response.headers["content-type"])
                with self.read_only_connection() as db:
                    self.assertEqual(list(db.iterdump()), before)

    def test_revised_findings_are_excluded_but_remaining_confirmed_enter_memo(self):
        for state in ("no", "unknown", "conflict"):
            with self.subTest(state=state):
                self.reassess_msp(state)
                with self.read_only_connection() as db:
                    before = list(db.iterdump())
                text = self.text_from_odt(self.client.get(self.path()))
                self.assertIn(THEMATIC_TITLE, text)
                self.assertNotIn(MSP_TITLE, text)
                self.assertNotIn("5% НМЦД", text)
                with self.read_only_connection() as db:
                    self.assertEqual(list(db.iterdump()), before)

    def test_confirmed_msp_reassessment_still_enters_official_memo(self):
        self.reassess_msp("yes", only_msp=True)
        text = self.text_from_odt(self.client.get(self.path()))
        self.assertIn(MSP_TITLE, text)
        self.assertIn("Извещение.pdf", text)

    def test_stale_confirmed_reassessment_cannot_generate_false_memo(self):
        self.reassess_msp("yes", only_msp=True)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE purchases SET method='Изменённый способ' WHERE reg_number=?", (REG,))
        response = self.client.get(self.path())
        self.assertEqual(response.status_code, 409, response.text)


class OfficialTemplateSafetyTests(unittest.TestCase):
    def source(self, change=None):
        with zipfile.ZipFile(io.BytesIO(memo_template())) as archive:
            items = {name: archive.read(name) for name in archive.namelist()}
        content = ET.fromstring(items["content.xml"])
        styles = ET.fromstring(items["styles.xml"])
        if change:
            change(content, styles)
        items["content.xml"] = ET.tostring(content, encoding="utf-8")
        items["styles.xml"] = ET.tostring(styles, encoding="utf-8")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, binary in items.items():
                archive.writestr(name, binary)
        return output.getvalue()

    def render(self, source, fields=None):
        return renderer._render_odt(source,
            {"reg_number": REG, "title": "Новый предмет закупки"},
            [{"title": THEMATIC_TITLE, "law": "223-ФЗ, ст. 4 ч. 12",
              "evidence": "Протокол размещён после установленного срока.",
              "document": "Протокол.pdf"}],
            'ООО «Пример»', fields or {})

    def test_canonical_template_is_accepted_but_shifted_paragraphs_fail_closed(self):
        source = memo_template()
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            self.assertTrue(renderer._compatible_template(archive.read("content.xml")))
        self.render(renderer._safe_odt(source))
        for keep_count in (False, True):
            def shift(content, styles):
                body = content.find(".//{" + renderer.OO + "}text")
                paragraphs = list(body.iter("{" + renderer.TX + "}p"))
                position = list(body).index(paragraphs[20])
                body.insert(position, ET.Element("{" + renderer.TX + "}p"))
                if keep_count:
                    # Keep all the old corpus phrases and even the old count;
                    # positional anchors must still detect the displaced fields.
                    body.remove(paragraphs[34])
            with self.subTest(keep_count=keep_count):
                shifted = self.source(shift)
                with zipfile.ZipFile(io.BytesIO(shifted)) as archive:
                    xml = archive.read("content.xml")
                    self.assertFalse(renderer._compatible_template(xml))
                with self.assertRaises(ValueError):
                    renderer._safe_odt(shifted)
                with self.assertRaises(HTTPException) as failure:
                    self.render(shifted)
                self.assertEqual(failure.exception.status_code, 503)
                self.assertIn("исходной служебной записке", failure.exception.detail)

    def test_source_number_is_cleared_from_content_and_style_fields(self):
        old_number = "2026-777777"
        tx, office = "{" + renderer.TX + "}", "{" + renderer.OO + "}"
        def add_number(content, styles):
            body = content.find(".//" + office + "text")
            paragraphs = list(body.iter(tx + "p"))
            for node in (body, styles):
                declarations = ET.SubElement(node, tx + "user-field-decls")
                ET.SubElement(declarations, tx + "user-field-decl", {
                    tx + "name": "ProjectNumber", office + "value-type": "string",
                    office + "string-value": old_number})
            for parent in (paragraphs[17], styles, styles):
                field = ET.SubElement(parent, tx + "user-field-get", {
                    tx + "name": "ProjectNumber"})
                field.text = old_number
                field.tail = " 2026-139205"
        source = self.source(add_number)
        with zipfile.ZipFile(io.BytesIO(self.render(source))) as archive:
            for name in ("content.xml", "styles.xml"):
                data = archive.read(name)
                self.assertNotIn(old_number.encode(), data)
                self.assertNotIn(b"2026-139205", data)
                fields = [element for element in ET.fromstring(data).iter()
                          if element.get(tx + "name") == "ProjectNumber"]
                self.assertTrue(fields)
                for field in fields:
                    self.assertEqual(field.get(office + "string-value", ""), "")
                    self.assertFalse("".join(field.itertext()).strip())

    def test_current_fields_replace_original_case_and_recipient_without_losing_tab(self):
        old_values = ("32616324151", "Исходный предмет", "Исходный адресат",
                      "Исходное обращение", "Исходный исполнитель", "Исходный телефон")
        def fill_source(content, styles):
            paragraphs = list(content.find(".//{" + renderer.OO + "}text").iter("{" + renderer.TX + "}p"))
            for index, value in ((13, old_values[2]), (18, old_values[3]),
                                 (55, old_values[4]), (56, old_values[5])):
                paragraphs[index].text = value
            intro = paragraphs[20]
            intro.text = ""
            span = ET.SubElement(intro, "{" + renderer.TX + "}span")
            tab = ET.SubElement(span, "{" + renderer.TX + "}tab")
            tab.tail = "Управление проверило " + old_values[1] + " № " + old_values[0]
        fields = {"recipient": "Новый адресат", "executor": "Новый исполнитель",
                  "phone": "+7 000 111-22-33"}
        with zipfile.ZipFile(io.BytesIO(self.render(self.source(fill_source), fields))) as archive:
            content = ET.fromstring(archive.read("content.xml"))
        text = " ".join(content.itertext()).replace("\u00a0", " ")
        for value in (*fields.values(), REG, "Новый предмет закупки"):
            self.assertIn(value, text)
        for value in old_values:
            self.assertNotIn(value, text)
        intro = next(paragraph for paragraph in content.iter("{" + renderer.TX + "}p")
                     if REG in "".join(paragraph.itertext()))
        from v321_memo_layout import _leading_tabs
        self.assertEqual(_leading_tabs(intro), 1)

    def test_invalid_supplied_requisite_rejects_instead_of_keeping_original(self):
        for field in ("recipient", "executor", "phone"):
            with self.subTest(field=field):
                with self.assertRaises(HTTPException) as failure:
                    self.render(memo_template(), {field: "x" * 200})
                self.assertEqual(failure.exception.status_code, 422)

    def test_statute_link_has_word_separator_after_template_tab(self):
        def indent_law(content, styles):
            paragraphs = list(content.find(".//{" + renderer.OO + "}text").iter("{" + renderer.TX + "}p"))
            law = paragraphs[24]
            old_text = law.text
            law.text = ""
            span = ET.SubElement(law, "{" + renderer.TX + "}span")
            ET.SubElement(span, "{" + renderer.TX + "}tab").tail = old_text
        with zipfile.ZipFile(io.BytesIO(self.render(self.source(indent_law)))) as archive:
            content = ET.fromstring(archive.read("content.xml"))
        law = next(paragraph for paragraph in content.iter("{" + renderer.TX + "}p")
                   if "Правовое основание выявленного нарушения" in "".join(paragraph.itertext()))
        self.assertRegex("".join(law.itertext()), r"\.\s+КонсультантПлюс:")
        self.assertTrue(list(law.iter("{" + renderer.TX + "}a")))


if __name__ == "__main__":
    unittest.main()
