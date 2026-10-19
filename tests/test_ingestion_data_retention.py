"""Synthetic regression tests for accepted original-reference and source retention."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import zipfile
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v295_eis_originals as originals

REG="32616233661"
TOKEN="synthetic-test-upload-token"

def document(number, **extra):
    uid=f"{number:032X}"
    return {"uid":uid,"name":f"document-{number}.pdf",
            "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+uid,
            **extra}

class IntakeRetention(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        env=patch.dict(os.environ,{"EIS223_DATA_DIR":str(self.root),"EIS223_UPLOAD_TOKEN":TOKEN})
        env.start();self.addCleanup(env.stop)

    def write_manifest(self, docs):
        path=self.root/"eis223_public_browser_intake"/REG/"manifest.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({"source":"public_eis_223_browser","reg_number":REG,"documents":docs}))

    def test_accepted_soap_references_after_first_hundred_survive(self):
        folder=self.root/"eis223_official_archive_inbox"/REG
        folder.mkdir(parents=True)
        for i in range(1,102):
            (folder/f"{i:064x}.json").write_text(json.dumps({
                "source":"official_eis_soap_archive","reg_number":REG,
                "notices":[{"reg_number":REG,"documents":[document(i)]}]}))
        self.assertEqual(len(originals.allowed_documents(REG)),101)

    def test_persisted_browser_history_after_first_hundred_survives(self):
        self.write_manifest([document(i) for i in range(1,102)])
        self.assertEqual(len(originals.allowed_documents(REG)),101)

    def test_invalid_and_foreign_references_remain_rejected(self):
        self.write_manifest([document(1),document(2,url="https://example.com/document.pdf"),
                             document(3,name="malicious.html")])
        self.assertEqual(set(originals.allowed_documents(REG)),{document(1)["uid"]})

    def test_same_hash_official_promotion_keeps_original_source_and_metadata(self):
        path=self.root/"same.pdf";path.write_bytes(b"%PDF-1.7\nsame document")
        first={**document(1),"local":str(path),"status":"checked","published":"2026-10-01",
               "meta":{"source":"legacy_source","protocol_type":"first","identical_sources":[{"name":"older.pdf","url":"https://example.org/older"}]}}
        second={**document(2),"local":str(path),"status":"checked","published":"2026-10-02",
                "meta":{"source":"official_eis_getDocsIP","protocol_type":"second"}}
        async def downloaded(*args):pass
        ns={"app":FastAPI(),"download_all_documents":downloaded}
        originals.install(ns)
        docs=[first,second]
        with patch.object(originals,"allowed_documents",return_value={"known":{}}):
            asyncio.run(ns["download_all_documents"](None,REG,docs))
        self.assertEqual(len(docs),1)
        self.assertEqual(docs[0]["url"],second["url"])
        sources=docs[0]["meta"]["identical_sources"]
        old=next(item for item in sources if item.get("url")==first["url"])
        self.assertEqual(old["published"],"2026-10-01")
        self.assertEqual(old["meta"]["protocol_type"],"first")
        self.assertTrue(any(item.get("url")=="https://example.org/older" for item in sources))

    def test_same_uid_distinct_bytes_keep_both_receipts(self):
        doc=document(1);self.write_manifest([doc])
        ns={"app":FastAPI()};originals.install(ns)
        client=TestClient(ns["app"]);self.addCleanup(client.close)
        blobs=(b"%PDF-1.7\nfirst version",b"%PDF-1.7\nsecond version")
        for blob in blobs:
            response=client.post(f"/api/v295/eis223/original/{REG}/{doc['uid']}",content=blob,
                                 headers={"x-eis223-upload-token":TOKEN})
            self.assertEqual(response.status_code,200,response.text)
        folder=self.root/"eis223_official_originals"/REG/doc["uid"]
        records=[json.loads(path.read_text()) for path in (folder/"versions").glob("*.json")]
        self.assertEqual({item["sha256"] for item in records},
                         {hashlib.sha256(blob).hexdigest() for blob in blobs})
        self.assertEqual(originals.saved_document(REG,doc["uid"])[1]["sha256"],
                         hashlib.sha256(blobs[-1]).hexdigest())

    def test_reference_limit_is_explicit_instead_of_a_truncated_manifest(self):
        self.write_manifest([document(1),document(2)])
        with patch.object(originals,"MAX_KNOWN_DOCUMENTS",1):
            with self.assertRaises(HTTPException) as error:originals.allowed_documents(REG)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(len(originals.allowed_documents(REG)),2)

    def test_receipt_limit_is_explicit_and_keeps_every_saved_receipt(self):
        folder=self.root/"eis223_official_archive_inbox"/REG;folder.mkdir(parents=True)
        for number in (1,2):
            (folder/f"{number}.json").write_text(json.dumps({"reg_number":REG,
                "source":"official_eis_soap_archive",
                "notices":[{"reg_number":REG,"documents":[document(number)]}]}))
        before={path:path.read_bytes() for path in folder.glob("*.json")}
        with patch.object(originals,"MAX_SOURCE_RECEIPTS",1):
            with self.assertRaises(HTTPException):originals.allowed_documents(REG)
        self.assertEqual(before,{path:path.read_bytes() for path in folder.glob("*.json")})

    def test_same_uid_same_hash_is_idempotent(self):
        doc={**document(1),"ext":".pdf"};data=b"%PDF-1.7\nsame version"
        for _ in range(3):originals._store_original_version(REG,doc["uid"],doc,data)
        folder=self.root/"eis223_official_originals"/REG/doc["uid"]
        self.assertEqual(len(list((folder/"versions").glob("*.json"))),1)
        self.assertEqual(len(list(folder.glob("*.pdf"))),1)

    def test_version_limit_rejects_without_replacing_current_or_losing_bytes(self):
        doc={**document(1),"ext":".pdf"};first=b"%PDF-1.7\nfirst version"
        originals._store_original_version(REG,doc["uid"],doc,first)
        folder=self.root/"eis223_official_originals"/REG/doc["uid"]
        before={path:path.read_bytes() for path in folder.rglob("*") if path.is_file()}
        with patch.object(originals,"MAX_ORIGINAL_VERSIONS",1):
            with self.assertRaises(HTTPException) as error:
                originals._store_original_version(REG,doc["uid"],doc,b"%PDF-1.7\nnext version")
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(before,{path:path.read_bytes() for path in folder.rglob("*") if path.is_file()})
        self.assertEqual(originals.saved_document(REG,doc["uid"])[0].read_bytes(),first)

    def test_existing_legacy_receipt_is_retained_before_replacement(self):
        doc={**document(1),"ext":".pdf"};first=b"%PDF-1.7\nlegacy version"
        digest=hashlib.sha256(first).hexdigest()
        folder=self.root/"eis223_official_originals"/REG/doc["uid"];folder.mkdir(parents=True)
        (folder/(digest+".pdf")).write_bytes(first)
        legacy={"reg_number":REG,"uid":doc["uid"],"extension":".pdf","bytes":len(first),
                "sha256":digest,"legacy_provenance":"retain exactly"}
        (folder/"receipt.json").write_text(json.dumps(legacy))
        originals._store_original_version(REG,doc["uid"],doc,b"%PDF-1.7\nnew version")
        self.assertEqual(json.loads((folder/"versions"/(digest+".pdf.json")).read_text()),legacy)

    def test_concurrent_same_uid_replacements_preserve_each_distinct_version(self):
        doc={**document(1),"ext":".pdf"}
        blobs=[b"%PDF-1.7\nversion "+str(number).encode() for number in range(12)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda data:originals._store_original_version(REG,doc["uid"],doc,data),blobs))
        folder=self.root/"eis223_official_originals"/REG/doc["uid"]
        expected={hashlib.sha256(blob).hexdigest() for blob in blobs}
        self.assertEqual({json.loads(path.read_text())["sha256"]
                          for path in (folder/"versions").glob("*.json")},expected)
        self.assertIn(originals.saved_document(REG,doc["uid"])[1]["sha256"],expected)
        self.assertEqual({path.read_bytes() for path in folder.glob("*.pdf")},set(blobs))

    def test_unauthenticated_original_never_writes_receipt_history(self):
        doc=document(1);self.write_manifest([doc])
        ns={"app":FastAPI()};originals.install(ns)
        with TestClient(ns["app"]) as client:
            response=client.post(f"/api/v295/eis223/original/{REG}/{doc['uid']}",content=b"%PDF-1.7\nnot authorized")
        self.assertEqual(response.status_code,401)
        self.assertFalse((self.root/"eis223_official_originals").exists())

    def workbook(self, content_type="application/vnd.ms-excel.sheet.macroEnabled.main+xml", workbook=True):
        output=io.BytesIO()
        with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                             '<Override PartName="/xl/workbook.xml" ContentType="'+content_type+'"/></Types>')
            if workbook:archive.writestr("xl/workbook.xml",'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
        return output.getvalue()

    def test_macro_enabled_workbook_reference_and_package_are_accepted_without_execution(self):
        self.write_manifest([document(1,name="prices.xlsm")])
        self.assertEqual(originals.allowed_documents(REG)[document(1)["uid"]]["ext"],".xlsm")
        originals.assert_format(self.workbook(),".xlsm")

    def test_macro_workbook_missing_parts_or_renamed_other_package_is_rejected(self):
        for raw in (self.workbook(workbook=False),self.workbook(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
                    b"%PDF-1.7\nrenamed PDF"):
            with self.subTest(bytes=len(raw)):
                with self.assertRaises(ValueError):originals.assert_format(raw,".xlsm")

    def test_same_hash_provenance_captures_keep_middle_observation(self):
        doc={**document(1),"ext":".pdf"};data=b"%PDF-1.7\nsame original"
        for name in ("first.pdf","middle.pdf","current.pdf"):
            originals._store_original_version(REG,doc["uid"],{**doc,"name":name},data)
        folder=self.root/"eis223_official_originals"/REG/doc["uid"]
        files=list((folder/"versions").glob("*.json"));self.assertEqual(len(files),1)
        history=json.loads(files[0].read_text())
        self.assertEqual({item["name"] for item in history["provenance"]},{"first.pdf","middle.pdf","current.pdf"})
        self.assertEqual(originals.saved_document(REG,doc["uid"])[1]["name"],"current.pdf")

    def test_corrupted_hash_target_is_quarantined_before_repair_and_reported_changed(self):
        doc={**document(1),"ext":".pdf"};data=b"%PDF-1.7\nvalid version"
        originals._store_original_version(REG,doc["uid"],doc,data)
        path=originals.saved_document(REG,doc["uid"])[0]
        corrupt=b"!"*len(data);path.write_bytes(corrupt)
        receipt,changed=originals._store_original_version(REG,doc["uid"],doc,data)
        self.assertTrue(changed)
        self.assertEqual(path.read_bytes(),data)
        quarantine=path.parent/"quarantine"
        self.assertEqual(next(quarantine.glob("*.pdf")).read_bytes(),corrupt)
        self.assertEqual(json.loads(next(quarantine.glob("*.json")).read_text())["state"],"unverified_corrupt_original")
        self.assertEqual(receipt["sha256"],hashlib.sha256(data).hexdigest())

    def test_quarantine_overflow_rejects_without_overwriting_corrupt_bytes(self):
        doc={**document(1),"ext":".pdf"};data=b"%PDF-1.7\nvalid version"
        originals._store_original_version(REG,doc["uid"],doc,data)
        path=originals.saved_document(REG,doc["uid"])[0]
        corrupt=b"!"*len(data);path.write_bytes(corrupt)
        with patch.object(originals,"MAX_QUARANTINED_ORIGINALS",0):
            with self.assertRaises(HTTPException):originals._store_original_version(REG,doc["uid"],doc,data)
        self.assertEqual(path.read_bytes(),corrupt)

    def test_unreadable_legacy_original_preserves_receipt_provenance_during_repair(self):
        doc={**document(1),"ext":".pdf"};data=b"%PDF-1.7\nlegacy version"
        digest=hashlib.sha256(data).hexdigest()
        folder=self.root/"eis223_official_originals"/REG/doc["uid"];folder.mkdir(parents=True)
        path=folder/(digest+".pdf");path.write_bytes(b"truncated")
        legacy={"reg_number":REG,"uid":doc["uid"],"extension":".pdf","bytes":len(data),
                "sha256":digest,"legacy_provenance":"precious receipt metadata"}
        (folder/"receipt.json").write_text(json.dumps(legacy))
        originals._store_original_version(REG,doc["uid"],doc,data)
        history=json.loads((folder/"versions"/(digest+".pdf.json")).read_text())
        self.assertEqual(history["legacy_provenance"],"precious receipt metadata")
        self.assertEqual(history["retained_file_state"],"unavailable_or_unverified")
        self.assertEqual(path.read_bytes(),data)

    def test_quarantine_retains_each_expected_original_for_identical_corruption(self):
        doc={**document(1),"ext":".pdf"}
        expected=set()
        for data in (b"%PDF-1.7\nfirst version",b"%PDF-1.7\nsecond version"):
            originals._store_original_version(REG,doc["uid"],doc,data)
            path=originals.saved_document(REG,doc["uid"])[0];path.write_bytes(b"!")
            originals._store_original_version(REG,doc["uid"],doc,data)
            expected.add(hashlib.sha256(data).hexdigest())
        quarantine=path.parent/"quarantine"
        self.assertEqual({json.loads(item.read_text())["expected_sha256"] for item in quarantine.glob("*.json")},expected)

if __name__=="__main__":unittest.main()
