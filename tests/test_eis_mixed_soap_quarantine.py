"""Supported evidence survives unknown siblings; unsupported bytes prove no facts.

The unknown type is invented to reproduce the observed unsupported_xml_type
failure. It does not claim that an unverified schema is officially supported.
"""
import asyncio
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
import uuid
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import eis223_soap_xml as xml_reader
import eis_server_worker as worker
import v294_eis_bridge as bridge
import v295_eis_originals as originals
import v298_eis_only as offline
import v301_search_jobs as queue

REG = "32616233661"
OTHER = "32616233662"
KNOWN = "purchaseNoticeKESMBO"
UNKNOWN = "purchaseNoticeUnimplemented"
UID = "A"*32
UNKNOWN_UID = "B"*32
TOKEN = "synthetic-mixed-soap-test-key-only-0000"
NS = xml_reader.PURCHASE_NS
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def fixture(kind=KNOWN, reg=REG, identity=None, url=None, version=1):
    identity = f"<registrationNumber>{reg}</registrationNumber>" if identity is None else identity
    url = URL+(UID if kind == KNOWN else UNKNOWN_UID) if url is None else url
    raw = (f'<{kind} xmlns="{NS}"><body><item><{kind}Data>{identity}'
           f'<version>{version}</version><publicationDateTime>2026-10-01T12:00:00+03:00</publicationDateTime>'
           '<name>Supported fixture procurement</name><submissionCloseDateTime>2026-11-01</submissionCloseDateTime>'
           '<attachments><document><guid>fixture-guid</guid><fileName>original.pdf</fileName>'
           f'<url>{url}</url></document></attachments></{kind}Data></item></body></{kind}>').encode()
    return f"{kind}_{reg}_{version}_fixture.xml", raw


def archive(*files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as zipped:
        for name, raw in files:
            zipped.writestr(name, raw)
    return output.getvalue()


class MixedSoapQuarantineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        env = patch.dict(os.environ, {"EIS223_DATA_DIR": temp.name, "EIS223_UPLOAD_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)
        app = FastAPI()
        bridge.install({"app": app})
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self, raw):
        return self.client.post("/api/v294/eis223/archive", content=raw,
                                headers={"x-eis223-upload-token": TOKEN})

    def accepted(self, *files):
        raw = archive(*(files or (fixture(), fixture(UNKNOWN))))
        reply = self.upload(raw)
        self.assertEqual(reply.status_code, 200, reply.text)
        sha = hashlib.sha256(raw).hexdigest()
        return raw, sha, reply.json()

    def original(self):
        raw = b"%PDF-1.7\nactual supported original bytes\n%%EOF"
        digest = hashlib.sha256(raw).hexdigest()
        folder = self.folder/"eis223_official_originals"/REG/UID
        folder.mkdir(parents=True, exist_ok=True)
        (folder/(digest+".pdf")).write_bytes(raw)
        (folder/"receipt.json").write_text(json.dumps({
            "reg_number": REG, "uid": UID, "sha256": digest, "bytes": len(raw),
            "extension": ".pdf", "source_url": URL+UID}))
        return folder/(digest+".pdf"), digest

    def report(self, sha, *, terminal=False, metadata_only=False):
        ns = {"conn": lambda: sqlite3.connect(self.folder/"queue.sqlite", timeout=5)}
        queue._init(ns)
        worker.init(ns)
        with ns["conn"]() as db:
            db.execute("DELETE FROM v301_search_jobs")
        queue._enqueue(ns, "reg", REG, parent_id="v341-card-facts" if metadata_only else "")
        identity = str(uuid.uuid4())
        job = worker.claim(ns, identity, 1)[0]
        if terminal:
            with ns["conn"]() as db:
                db.execute("UPDATE v301_search_jobs SET attempts=?,accepted=1 WHERE id=?", (worker.MAX_ATTEMPTS, job["id"]))
        if metadata_only:
            ns["_v379_import_soap_metadata"] = lambda reg: {"stored": True}
        result = worker.report(ns, job["id"], {
            "worker_id": identity, "lease_token": job["lease_token"], "reg_number": REG,
            "status": "completed", "archive_sha256s": [sha],
            "unsupported_xml_count": 0, "originals_expected": 99, "originals_received": 99})
        with ns["conn"]() as db:
            stored = json.loads(db.execute("SELECT result_json FROM eis_server_reports WHERE job_id=?", (job["id"],)).fetchone()[0])
        self.assertEqual(stored, result)
        return result

    def test_mixed_archive_recovers_supported_xml_and_preserves_every_original_byte(self):
        files = (fixture(UNKNOWN), fixture())
        raw, sha, reply = self.accepted(*files)
        saved = self.folder/"eis223_official_archive_inbox"/REG/(sha+".zip")
        self.assertEqual(saved.read_bytes(), raw)
        with zipfile.ZipFile(saved) as zipped:
            self.assertEqual({name: zipped.read(name) for name in zipped.namelist()}, dict(files))
        records = offline.official_xml_documents(REG)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0]["xml_type"], KNOWN)
        self.assertEqual(offline.field(offline.official_notice(REG), "name"), "Supported fixture procurement")
        self.assertEqual(set(originals.allowed_documents(REG)), {UID})
        self.assertEqual(reply["notice_xmls"], 1)

    def test_unknown_sibling_has_no_dates_facts_or_download_references(self):
        self.accepted()
        rows = bridge.parse_notices(archive(fixture(), fixture(UNKNOWN)))
        unknown = rows[1]
        self.assertEqual(unknown["xml_family"], "unsupported")
        self.assertEqual(unknown["status"], "unsupported_xml")
        self.assertEqual(unknown["documents"], [])
        self.assertNotIn("publication", unknown)
        self.assertNotIn("version", unknown)
        self.assertEqual(unknown["unresolved_attachment_count"], 0)
        name, raw = fixture(UNKNOWN)
        with self.assertRaises(xml_reader.UnsupportedXmlType):
            xml_reader.parse_document(raw, name)

    def test_partial_receipt_keeps_unknown_xml_separate_from_original_counts(self):
        raw, sha, reply = self.accepted()
        receipt = json.loads((self.folder/"eis223_official_archive_inbox"/REG/(sha+".json")).read_text())
        for info in (reply, receipt):
            self.assertEqual(info["status"], "metadata_partial")
            self.assertEqual(info["unsupported_xml_count"], 1)
            self.assertEqual(info["unresolved_attachment_count"], 0)
            self.assertEqual(info["unsupported_attachment_count"], 0)
            self.assertEqual(info["audited_documents_increment"], 0)
        self.assertEqual(receipt["total_attachment_count"], 1)
        self.assertEqual(reply["document_links"], 1)

    def test_supported_archive_keeps_previous_metadata_and_completion_behavior(self):
        _, sha, reply = self.accepted(fixture())
        self.assertEqual(reply["status"], "metadata_only")
        self.assertEqual(reply["unsupported_xml_count"], 0)
        self.original()
        proof = worker.evidence(REG, [sha])
        self.assertTrue(proof["complete"])
        self.assertEqual((proof["expected"], proof["received"], proof["unsupported_xml_count"]), (1, 1, 0))
        self.assertEqual(self.report(sha)["status"], "done")

    def test_unknown_only_archive_still_rejected(self):
        response = self.upload(archive(fixture(UNKNOWN)))
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.json()["detail"].startswith("Unsupported official EIS XML type: "))
        self.assertFalse((self.folder/"eis223_official_archive_inbox").exists())

    def test_foreign_or_missing_registry_cannot_be_quarantined_as_this_purchase(self):
        bad = [fixture(UNKNOWN, reg=OTHER), fixture(UNKNOWN, identity=f"<registrationNumber>{OTHER}</registrationNumber>"),
               fixture(UNKNOWN, identity=""), fixture(UNKNOWN, identity=f"<registrationNumber>{REG}-01</registrationNumber>")]
        for entry in bad:
            with self.subTest(name=entry[0], digest=hashlib.sha256(entry[1]).hexdigest()):
                self.assertEqual(self.upload(archive(fixture(), entry)).status_code, 422)

    def test_duplicate_or_conflicting_registry_nodes_still_rejected(self):
        identities = [f"<registrationNumber>{REG}</registrationNumber>"*2,
                      f"<registrationNumber>{REG}</registrationNumber><purchaseInfo><purchaseNoticeNumber>{OTHER}</purchaseNoticeNumber></purchaseInfo>",
                      "<purchaseInfo>"+f"<purchaseNoticeNumber>{REG}</purchaseNoticeNumber>"*2+"</purchaseInfo>"]
        for identity in identities:
            self.assertEqual(self.upload(archive(fixture(), fixture(UNKNOWN, identity=identity))).status_code, 422)

    def test_unknown_protocol_shape_needs_its_explicit_parent_procurement(self):
        identity = (f"<registrationNumber>{REG}-01</registrationNumber><purchaseInfo>"
                    f"<purchaseNoticeNumber>{REG}</purchaseNoticeNumber></purchaseInfo>")
        _, _, reply = self.accepted(fixture(), fixture("purchaseProtocolUnimplemented", identity=identity))
        self.assertEqual(reply["unsupported_xml_count"], 1)
        self.assertEqual(reply["protocol_xmls"], 0)

    def test_malformed_unknown_xml_rejects_whole_upload(self):
        name, raw = fixture(UNKNOWN)
        self.assertEqual(self.upload(archive(fixture(), (name, raw[:-5]))).status_code, 422)

    def test_foreign_namespaces_and_hidden_or_duplicate_payloads_rejected(self):
        name, raw = fixture(UNKNOWN)
        foreign = b"https://untrusted.example/223fz/purchase/1"
        invalid = [raw.replace(NS.encode(), foreign),
                   raw.replace(("<"+UNKNOWN+"Data>").encode(), ("<"+UNKNOWN+'Data xmlns="'+foreign.decode()+'">').encode()),
                   raw.replace(b"<body>", b"<extension>").replace(b"</body>", b"</extension>"),
                   raw.replace(("</"+UNKNOWN+"Data>").encode(), ("</"+UNKNOWN+"Data><"+UNKNOWN+"Data/>").encode()),
                   raw.replace(b"<registrationNumber>", b'<registrationNumber xmlns="https://untrusted.example/">')]
        for bad in invalid:
            self.assertEqual(self.upload(archive(fixture(), (name, bad))).status_code, 422)

    def test_doctype_and_utf16_entities_rejected_before_any_storage(self):
        name, raw = fixture(UNKNOWN)
        for bad in (("<!DOCTYPE x [<!ENTITY e 'text'>]>"+raw.decode()).encode(),
                    ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY e "text">]>'+raw.decode()).encode("utf-16")):
            self.assertEqual(self.upload(archive(fixture(), (name, bad))).status_code, 422)
        self.assertFalse((self.folder/"eis223_official_archive_inbox").exists())

    def test_duplicate_and_traversal_names_rejected(self):
        unknown = fixture(UNKNOWN)
        with self.assertWarns(UserWarning):
            duplicate = archive(fixture(), unknown, unknown)
        self.assertEqual(self.upload(duplicate).status_code, 422)
        self.assertEqual(self.upload(archive(fixture(), ("../unknown.xml", unknown[1]))).status_code, 422)

    def test_external_original_urls_rejected_in_known_and_unknown_members(self):
        for kind in (KNOWN, UNKNOWN):
            with self.subTest(kind=kind):
                invalid = fixture(kind, url="https://untrusted.example/original.pdf", version=2)
                self.assertEqual(self.upload(archive(fixture(), invalid)).status_code, 422)

    def test_zip_upload_expansion_member_and_count_limits_unchanged(self):
        raw = archive(fixture(), fixture(UNKNOWN))
        for name, value in (("MAX_UPLOAD", len(raw)-1), ("MAX_UNCOMPRESSED", 10), ("MAX_XML", 10)):
            with self.subTest(limit=name), patch.object(bridge, name, value):
                self.assertIn(self.upload(raw).status_code, (413, 422))
        too_many = archive(*(fixture(version=index+1) for index in range(101)))
        self.assertEqual(self.upload(too_many).status_code, 422)

    def test_bad_zip_and_crc_rejected(self):
        self.assertEqual(self.upload(b"not a zip").status_code, 422)
        raw = archive(fixture(), fixture(UNKNOWN))
        damaged = raw.replace(b"Supported fixture procurement", b"Unsupported corrupt content!", 1)
        self.assertEqual(self.upload(damaged).status_code, 422)

    def test_server_reparses_bytes_when_receipt_claims_unknown_count_zero(self):
        _, sha, _ = self.accepted()
        self.original()
        path = self.folder/"eis223_official_archive_inbox"/REG/(sha+".json")
        receipt = json.loads(path.read_text())
        receipt["unsupported_xml_count"] = 0
        receipt["notices"] = receipt["notices"][:1]
        path.write_text(json.dumps(receipt))
        proof = worker.evidence(REG, [sha])
        self.assertFalse(proof["complete"])
        self.assertTrue(proof["metadata_received"])
        self.assertEqual((proof["expected"], proof["received"], proof["unresolved"], proof["unsupported_xml_count"]), (1, 1, 0, 1))
        self.assertTrue(bridge.soap_metadata_incomplete(REG))

    def test_worker_completed_report_cannot_complete_partially_supported_archive(self):
        _, sha, _ = self.accepted()
        self.original()
        result = self.report(sha)
        self.assertEqual(result["status"], "queued")
        self.assertFalse(result["download_complete"])
        self.assertEqual(result["unsupported_xml_count"], 1)
        self.assertEqual(result["error_code"], "unsupported_xml_type")
        self.assertEqual((result["originals_expected"], result["originals_received"]), (1, 1))

    def test_terminal_worker_report_preserves_partial_state(self):
        _, sha, _ = self.accepted()
        self.original()
        result = self.report(sha, terminal=True)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["download_complete"])

    def test_metadata_only_report_does_not_hide_unknown_xml_even_when_card_saved(self):
        _, sha, _ = self.accepted()
        result = self.report(sha, metadata_only=True)
        self.assertTrue(result["metadata_stored"])
        self.assertFalse(result["download_complete"])
        self.assertEqual(result["unsupported_xml_count"], 1)

    def test_overlapping_archives_do_not_duplicate_unknown_xml_count(self):
        _, first, _ = self.accepted()
        _, second, _ = self.accepted(fixture(version=2), fixture(UNKNOWN))
        proof = worker.evidence(REG, [first, second])
        self.assertEqual(proof["unsupported_xml_count"], 1)
        self.assertEqual(proof["expected"], 1)

    def test_changed_archive_bytes_cannot_supply_complete_or_authoritative_evidence(self):
        _, sha, _ = self.accepted()
        path = self.folder/"eis223_official_archive_inbox"/REG/(sha+".zip")
        path.write_bytes(archive(fixture(version=9)))
        proof = worker.evidence(REG, [sha])
        self.assertFalse(proof["complete"])
        self.assertFalse(proof["metadata_received"])
        self.assertIsNone(offline.official_notice(REG))

    def test_shared_audit_collector_remains_incomplete_when_all_known_originals_are_read(self):
        import v332_all_procurements_audit as general
        self.accepted()
        path, digest = self.original()
        rows = [{"id": 1, "name": "original.pdf", "local_path": str(path), "url": URL+UID,
                 "metadata_json": json.dumps({"source": "official_eis_original", "source_url": URL+UID,
                                               "sha256": digest}), "status": "checked"}]
        source = asyncio.run(general.collect_saved_evidence(
            {"extract_text": lambda path: "Readable supported original"}, REG, rows))
        self.assertEqual(len(source["docs"]), 1)
        self.assertEqual(source["unreadable"], 0)
        self.assertEqual((source["original_expected"], source["original_received"]), (1, 1))
        self.assertTrue(source["source_incomplete"])
        self.assertIn("XML", source["source_error"])


if __name__ == "__main__":
    unittest.main()
