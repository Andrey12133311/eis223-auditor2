"""Already running collectors finish only when trusted stored originals agree."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v301_search_jobs as jobs


class ServerCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{"EIS223_DATA_DIR":str(self.root),
                                      "EIS223_UPLOAD_TOKEN":"fixture-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.review=[]
        self.ns={"conn":lambda:sqlite3.connect(self.root/"queue.sqlite",timeout=0.2),
                 "app":FastAPI(),"_v295_enqueue_review":self.enqueue_review}
        jobs.install(self.ns)
        self.client=TestClient(self.ns["app"])
        self.addCleanup(self.client.close)
        self.reg="32616447983"
        self.add_job("fixture")
        self.manifest_path=self.root/"eis223_public_browser_intake"/self.reg/"manifest.json"
        self.manifest_path.parent.mkdir(parents=True)
        self.documents=[]
        for index in range(1,13):
            uid=f"{index:032X}"
            data=b"%PDF-1.7\nFixture original "+str(index).encode()
            digest=hashlib.sha256(data).hexdigest()
            folder=self.root/"eis223_official_originals"/self.reg/uid
            folder.mkdir(parents=True)
            (folder/(digest+".pdf")).write_bytes(data)
            (folder/"receipt.json").write_text(json.dumps({
                "reg_number":self.reg,"uid":uid,"extension":".pdf",
                "sha256":digest,"bytes":len(data)}))
            self.documents.append({"name":f"document-{index}.pdf",
                "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+uid})
        self.manifest={"source":"public_eis_223_browser","reg_number":self.reg,
                       "declared_attachment_count":12,"documents":self.documents}
        self.write_manifest()

    def enqueue_review(self,reg):
        self.review.append(reg)
        return {"status":"queued"}

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def add_job(self,identifier,kind="reg",parent_id="",status="leased"):
        with self.ns["conn"]() as db:
            db.execute("INSERT INTO v301_search_jobs "
                "(id,kind,target,status,attempts,parent_id,created_at,updated_at) "
                "VALUES (?,?,?,?,1,?,'now','now')",
                (identifier,kind,"32616447983",status,parent_id))

    def stored(self,identifier="fixture"):
        db=jobs._db(self.ns)
        try:return dict(db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(identifier,)).fetchone())
        finally:db.close()

    def report(self,identifier="fixture",**overrides):
        body={"status":"partial","found":1,"accepted":0,
              "detail":"Получено 12 из 12 вложений ЕИС; остальные остаются в очереди"}
        body.update(overrides)
        return self.client.post("/api/v301/bridge/report/"+identifier,json=body,headers={
            "origin":"https://zakupki.gov.ru","x-eis223-upload-token":"fixture-secret",
            "x-eis223-collector-version":"376"})

    def test_running_v376_finishes_and_enqueues_offline_audit(self):
        response=self.report()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["status"],"done")
        row=self.stored()
        self.assertEqual((row["found"],row["accepted"]),(1,0))
        self.assertIn("переданы на проверку",row["detail"])
        self.assertNotIn("получены и проверены",row["detail"])
        self.assertEqual(self.review,[self.reg])

    def test_reported_counts_and_text_cannot_replace_missing_original(self):
        receipt=self.root/"eis223_official_originals"/self.reg/f"{12:032X}"/"receipt.json"
        receipt.unlink()
        self.assertEqual(self.report(accepted=12).json()["status"],"queued")
        self.assertEqual(self.stored()["status"],"queued")

    def test_receipt_requires_real_original_file(self):
        folder=self.root/"eis223_official_originals"/self.reg/f"{12:032X}"
        next(folder.glob("*.pdf")).unlink()
        self.assertFalse(jobs._stored_download_complete(self.reg))
        self.assertEqual(self.report().json()["status"],"queued")

    def test_declared_unknown_attachment_keeps_job_incomplete(self):
        self.manifest["declared_attachment_count"]=13
        self.write_manifest()
        self.assertEqual(self.report().json()["status"],"queued")

    def test_historical_saved_soap_uid_cannot_fill_missing_browser_attachment(self):
        uid=f"{13:032X}"
        data=b"%PDF-1.7\nHistorical SOAP original"
        digest=hashlib.sha256(data).hexdigest()
        folder=self.root/"eis223_official_originals"/self.reg/uid
        folder.mkdir(parents=True)
        (folder/(digest+".pdf")).write_bytes(data)
        (folder/"receipt.json").write_text(json.dumps({
            "reg_number":self.reg,"uid":uid,"extension":".pdf",
            "sha256":digest,"bytes":len(data)}))
        soap=self.root/"eis223_official_archive_inbox"/self.reg
        soap.mkdir(parents=True)
        (soap/"historical.json").write_text(json.dumps({
            "reg_number":self.reg,"source":"official_eis_soap_archive",
            "notices":[{"reg_number":self.reg,"documents":[{
                "name":"historical.pdf",
                "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+uid}]}]}))
        self.manifest["declared_attachment_count"]=13
        self.write_manifest()
        refs=jobs.allowed_documents(self.reg)
        self.assertEqual(len(refs),13)
        self.assertTrue(all(jobs.saved_document(self.reg,ref) for ref in refs))
        self.assertFalse(jobs._stored_download_complete(self.reg))
        self.assertEqual(self.report().json()["status"],"queued")

    def test_duplicate_browser_uid_cannot_fill_declared_count(self):
        self.manifest["documents"]=self.documents[:-1]+[self.documents[0]]
        self.write_manifest()
        self.assertFalse(jobs._stored_download_complete(self.reg))

    def test_invalid_browser_name_cannot_fill_declared_count(self):
        self.manifest["documents"]=[*self.documents[:-1],
            {**self.documents[-1],"name":"not-an-original.html"}]
        self.write_manifest()
        self.assertFalse(jobs._stored_download_complete(self.reg))

    def test_missing_or_untrusted_manifest_does_not_finish(self):
        for value in [None,{**self.manifest,"source":"other"},
                      {**self.manifest,"reg_number":"32610000001"},
                      {**self.manifest,"declared_attachment_count":0}]:
            with self.subTest(manifest=value):
                if value is None:self.manifest_path.unlink()
                else:self.manifest_path.write_text(json.dumps(value))
                self.assertFalse(jobs._stored_download_complete(self.reg))

    def test_metadata_inn_and_deferral_keep_existing_behavior(self):
        self.add_job("metadata",parent_id="v341-card-facts")
        self.add_job("inn",kind="inn")
        with patch.object(jobs,"_stored_download_complete",side_effect=AssertionError("No file scan expected")):
            self.assertEqual(self.report("metadata").json()["status"],"queued")
            self.assertEqual(self.report("inn").json()["status"],"partial")
            self.assertEqual(self.report(status="deferred",defer=60).json()["status"],"queued")

    def test_terminal_replay_preserves_result_without_scanning(self):
        self.report()
        before=self.stored()
        with patch.object(jobs,"_stored_download_complete",side_effect=AssertionError("No rescan expected")):
            self.assertEqual(self.report(detail="stale report",accepted=99).json()["status"],"done")
        self.assertEqual(self.stored(),before)

    def test_receipt_scan_precedes_write_lock_and_rechecks_terminal_state(self):
        def concurrent_finish(reg):
            self.assertEqual(reg,self.reg)
            with self.ns["conn"]() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE v301_search_jobs SET status='done',detail='concurrent result' "
                           "WHERE id='fixture'")
            return True
        with patch.object(jobs,"_stored_download_complete",side_effect=concurrent_finish):
            row=jobs._finish(self.ns,"fixture","partial","stale report",1,0)
        self.assertEqual(row["status"],"done")
        self.assertEqual(row["detail"],"concurrent result")

    def test_changed_manifest_during_receipt_scan_is_not_complete(self):
        original=jobs.saved_document
        def changed_manifest(reg,uid):
            result=original(reg,uid)
            self.manifest["declared_attachment_count"]=13
            self.write_manifest()
            return result
        with patch.object(jobs,"saved_document",side_effect=changed_manifest):
            self.assertFalse(jobs._stored_download_complete(self.reg))

    def test_authentication_precedes_verification(self):
        with patch.object(jobs,"_stored_download_complete",side_effect=AssertionError("No unauthenticated scan")):
            response=self.client.post("/api/v301/bridge/report/fixture",json={"status":"partial"},
                                      headers={"origin":"https://zakupki.gov.ru"})
            self.assertEqual(response.status_code,401)


if __name__=="__main__":unittest.main()
