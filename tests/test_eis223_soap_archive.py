"""Synthetic fixtures reconstructed from supplied official TFF 223-FZ 16.3.

These are NOT captured production procurement XML. They reproduce the actual
KESMBO/protocol envelope and scoped registry fields that the old parser lost.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import eis223_soap_xml as xml_reader
import v294_eis_bridge as bridge
import v295_eis_originals as originals
import v298_eis_only as offline

REG="32616233661"
TOKEN="synthetic-test-upload-key-only-000000"
NS=xml_reader.PURCHASE_NS


def fixture(kind="purchaseNoticeKESMBO",version=1,reg=REG,url=None,extra=""):
    """Official shape, invented contents, no real personal data or credentials."""
    url=url or ("https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+
                format(version,"032X"))
    identity=(f"<registrationNumber>{reg}</registrationNumber>" if kind in xml_reader.NOTICE_TYPES
              else f"<registrationNumber>{reg}-01</registrationNumber><purchaseInfo>"
                   f"<purchaseNoticeNumber>{reg}</purchaseNoticeNumber></purchaseInfo>")
    raw=(f'<{kind} xmlns="{NS}"><header><createDateTime>2026-10-18T10:00:00+03:00</createDateTime>'
         f'</header><body><item><{kind}Data>{identity}<version>{version}</version>'
         f'<publicationDateTime>2026-10-{min(version,28):02d}T12:00:00+03:00</publicationDateTime>'
         f'<name>Поставка тестовых материалов, редакция {version}</name>'
         '<customer><mainInfo><fullName>Общество с ограниченной ответственностью «Тест»</fullName>'
         '<inn>7707083893</inn></mainInfo></customer><purchaseCodeName>Конкурс в электронной форме</purchaseCodeName>'
         '<applSubmisionStartDate>2026-10-01</applSubmisionStartDate>'
         '<submissionCloseDateTime>2026-11-01T12:00:00+03:00</submissionCloseDateTime>'
         '<lots><lot><lotData><initialSum>125000.00</initialSum></lotData></lot></lots>'
         '<attachments><document><guid>synthetic-guid</guid><fileName>Документация.pdf</fileName>'
         f'<url>{url}</url><registrationNumber>12345678901234567890123</registrationNumber>'
         '</document></attachments>'+extra+
         f'</{kind}Data></item></body></{kind}>').encode()
    return f"{kind}_{reg}_{version}_synthetic.xml",raw


def archive(*files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as z:
        for name,raw in files:z.writestr(name,raw)
    return output.getvalue()


class OfficialSoapArchiveTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        env=patch.dict(os.environ,{"EIS223_DATA_DIR":str(self.root),"EIS223_UPLOAD_TOKEN":TOKEN})
        env.start();self.addCleanup(env.stop)
        self.dbfile=self.root/"test.sqlite"
        with self.connect() as db:
            db.executescript('''CREATE TABLE purchases(
                reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,customer_inn TEXT,
                method TEXT,price TEXT,price_num REAL,published_at TEXT,submission_start TEXT,
                submission_end TEXT,deadline TEXT,source TEXT,last_seen TEXT,url TEXT,
                card_fields_json TEXT,card_source_status TEXT,violations INTEGER DEFAULT 0,
                documents_checked INTEGER DEFAULT 0,documents_found INTEGER DEFAULT 0,
                manual INTEGER DEFAULT 0);
                CREATE TABLE findings(reg_number TEXT,evidence TEXT);
                CREATE TABLE docs(reg_number TEXT,name TEXT);
            ''')
        app=FastAPI();self.ns={"app":app,"conn":self.connect}
        self.ns["_v379_import_soap_metadata"]=lambda reg:offline.import_soap_metadata(self.ns,reg)
        bridge.install(self.ns)
        self.client=TestClient(app)
        self.addCleanup(self.client.close)

    def connect(self):
        db=sqlite3.connect(self.dbfile);db.row_factory=sqlite3.Row
        return db

    def upload(self,data):
        return self.client.post("/api/v294/eis223/archive",content=data,
                                headers={"x-eis223-upload-token":TOKEN,"content-type":"application/zip"})

    def test_mixed_kesmbo_versions_and_protocols_survive_and_latest_is_used(self):
        files=[fixture(version=v) for v in (3,1,2)]
        files += [fixture(kind=k,version=i+1) for i,k in enumerate((
            "purchaseProtocolRZ1KESMBO","purchaseProtocolRZ2KESMBO",
            "purchaseProtocolSummingupKESMBO","purchaseProtocolEvasionKESMBO"))]
        raw=archive(*files);reply=self.upload(raw)
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual((reply.json()["notice_xmls"],reply.json()["protocol_xmls"]),(3,4))
        self.assertEqual(reply.json()["metadata_import"],"stored")
        paths=list((self.root/"eis223_official_archive_inbox"/REG).glob("*.zip"))
        self.assertEqual(paths[0].read_bytes(),raw)
        with zipfile.ZipFile(paths[0]) as z:
            self.assertEqual({n:z.read(n) for n in z.namelist()},dict(files))
        self.assertEqual(offline.field(offline.official_notice(REG),"version"),"3")
        self.assertEqual(len(offline.official_notice_revisions(REG)),3)
        self.assertEqual(len(offline.official_xml_documents(REG)),7)
        self.assertEqual(len(originals.allowed_documents(REG)),4)
        with self.connect() as db:
            row=dict(db.execute("SELECT * FROM purchases").fetchone())
        self.assertIn("редакция 3",row["title"])
        self.assertEqual(row["price_num"],125000)
        self.assertEqual((row["documents_checked"],row["violations"],row["manual"]),(0,0,0))
        self.assertEqual(row["card_source_status"],"metadata_only")

    def test_repeated_archive_and_overlapping_versions_are_idempotent(self):
        raw=archive(fixture(version=1),fixture(version=2))
        self.assertFalse(self.upload(raw).json()["already_received"])
        self.assertTrue(self.upload(raw).json()["already_received"])
        self.upload(archive(fixture(version=2),fixture(version=3)))
        self.assertEqual(len(offline.official_notice_revisions(REG)),3)
        self.assertEqual(len(list((self.root/"eis223_official_archive_inbox"/REG).glob("*.zip"))),2)

    def test_metadata_update_preserves_all_audit_and_manual_state(self):
        with self.connect() as db:
            db.execute("INSERT INTO purchases(reg_number,title,violations,documents_checked,manual) VALUES(?,?,7,4,1)",
                       (REG,"Старое название"))
            db.execute("INSERT INTO findings VALUES(?,?)",(REG,"existing evidence"))
            db.execute("INSERT INTO docs VALUES(?,?)",(REG,"existing.pdf"))
        self.upload(archive(fixture(version=4)))
        self.upload(archive(fixture(version=2)))
        with self.connect() as db:
            row=dict(db.execute("SELECT * FROM purchases").fetchone())
            self.assertEqual(tuple(db.execute("SELECT * FROM findings").fetchone()),(REG,"existing evidence"))
            self.assertEqual(tuple(db.execute("SELECT * FROM docs").fetchone()),(REG,"existing.pdf"))
        self.assertIn("редакция 4",row["title"])
        self.assertEqual((row["violations"],row["documents_checked"],row["manual"]),(7,4,1))

    def test_all_confirmed_notice_and_protocol_variants_accept_scoped_identity(self):
        for kind in sorted(xml_reader.NOTICE_TYPES|xml_reader.PROTOCOL_TYPES):
            with self.subTest(kind=kind):
                row=bridge.parse_notices(archive(fixture(kind=kind)))[0]
                self.assertEqual(row["reg_number"],REG)

    def test_unsupported_and_content_uid_only_are_preserved_but_not_complete(self):
        name,raw=fixture(extra='<attachments><documentFS><guid>fs-guid</guid>'
                               '<contentUid>unresolved-content</contentUid></documentFS>'
                               '<prevDocument><guid>old-guid</guid></prevDocument></attachments>',
                         url="https://int.zakupki.gov.ru/unknown/download")
        reply=self.upload(archive((name,raw)))
        self.assertEqual(reply.status_code,200)
        self.assertEqual(reply.json()["unresolved_attachment_count"],1)
        self.assertEqual(reply.json()["unsupported_attachment_count"],1)
        record=bridge.parse_notices(archive((name,raw)))[0]
        self.assertEqual(record["previous_document_guids"],["old-guid"])
        self.assertEqual(originals.allowed_documents(REG),{})

    def test_no_urls_from_non_attachment_fields_are_invented(self):
        name,raw=fixture(extra="<extra><document><fileName>not-an-attachment.pdf</fileName>"
                               "<url>https://example.org/link</url></document></extra>")
        self.assertEqual(len(bridge.parse_notices(archive((name,raw)))[0]["documents"]),1)

    def test_mixed_procurements_duplicate_members_and_foreign_namespaces_rejected(self):
        first=fixture()
        second=fixture(reg="32616233662")
        invalid=[archive(first,second),archive(("../bad.xml",first[1])),
                 archive((first[0],first[1].replace(NS.encode(),b"https://attacker.example/223fz/purchase/1"))),
                 archive((first[0],first[1].replace(b"<version>1</version>",
                    b"</purchaseNoticeKESMBOData><purchaseNoticeKESMBOData>")))]
        for data in invalid:
            with self.subTest(digest=hashlib.sha256(data).hexdigest()):
                self.assertEqual(self.upload(data).status_code,422)
        with self.assertWarns(UserWarning):duplicate=archive(first,first)
        self.assertEqual(self.upload(duplicate).status_code,422)

    def test_utf16_entity_declaration_and_unknown_root_are_rejected(self):
        name,raw=fixture()
        dangerous=('<?xml version="1.0" encoding="UTF-16"?>'
                   '<!DOCTYPE x [<!ENTITY e "EXPANDED">]>'+raw.decode()).encode("utf-16")
        self.assertEqual(self.upload(archive((name,dangerous))).status_code,422)
        unknown=raw.replace(b"purchaseNoticeKESMBO",b"purchaseNoticeInvented")
        self.assertEqual(self.upload(archive((name.replace("KESMBO","Invented"),unknown))).status_code,422)

    def test_changed_archive_bytes_cannot_supply_verified_metadata(self):
        self.upload(archive(fixture()))
        path=next((self.root/"eis223_official_archive_inbox"/REG).glob("*.zip"))
        path.write_bytes(archive(fixture(version=9)))
        self.assertIsNone(offline.official_notice(REG))

    def test_archive_key_and_browser_origin_protection_unchanged(self):
        raw=archive(fixture(kind="purchaseNotice"))
        self.assertEqual(self.client.post("/api/v294/eis223/archive",content=raw).status_code,401)
        self.assertEqual(self.client.post("/api/v294/eis223/archive",content=raw,
            headers={"x-eis223-upload-token":TOKEN,"origin":"https://attacker.example"}).status_code,403)
        self.assertEqual(self.upload(raw).status_code,200)

    def test_server_mode_reports_observed_worker_heartbeat(self):
        offline.install(self.ns)
        with patch("eis_server_worker.health",return_value={"active":False,"last_seen":None,"status":"not_connected"}):
            initial=self.client.get("/api/v298/eis-only/status").json()
        self.assertFalse(initial["vps_eis_requests"])
        self.assertTrue(initial["server_collector_configured"])
        with patch("eis_server_worker.health",return_value={"active":True,"last_seen":123,"status":"idle"}):
            active=self.client.get("/api/v298/eis-only/status").json()
        self.assertTrue(active["vps_eis_requests"])
        self.assertEqual(active["mode"],"official_eis_server_and_browser")
        self.assertFalse(active["direct_eis_server_requests"])


if __name__=="__main__":unittest.main()
