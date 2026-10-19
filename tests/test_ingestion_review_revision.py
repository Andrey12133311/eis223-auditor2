"""New original bytes with unchanged counts must not reuse an old completion."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v295_eis_originals as originals

REG="32616233661"
UID="00000000000000000000000000000001"
class ReviewRevision(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        env=patch.dict(os.environ,{"EIS223_DATA_DIR":str(self.root),"EIS223_UPLOAD_TOKEN":"synthetic-token"})
        env.start();self.addCleanup(env.stop)
        self.doc={"uid":UID,"name":"fixture.pdf","ext":".pdf",
                  "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+UID}
        path=self.root/"eis223_public_browser_intake"/REG/"manifest.json";path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"source":"public_eis_223_browser","reg_number":REG,"documents":[self.doc]}))
        self.first=b"%PDF-1.7\nfirst version";self.second=b"%PDF-1.7\nsecond version"
        originals._store_original_version(REG,UID,self.doc,self.first)
        self.calls=[];self.block=None
        async def inspect(reg):
            self.calls.append(originals.saved_document(reg,UID)[1]["sha256"])
            if self.block is not None:
                current=self.block;self.block=None
                await current.wait()
            return {"checked_documents":1,"findings":0}
        self.ns={"app":FastAPI(),"lock":asyncio.Lock(),"_v298_offline_audit":inspect}
        originals.install(self.ns)
        self.enqueue=self.ns["_v295_enqueue_review"]
        self.state_path=self.root/"eis223_official_review_jobs"/(REG+".json")

    async def wait_completed(self,calls=1):
        for _ in range(100):
            if (len(self.calls)>=calls and self.state_path.exists()
                    and json.loads(self.state_path.read_text()).get("status")=="completed"):
                return
            await asyncio.sleep(.005)
        self.fail("Expected completion was not reached")

    async def test_replaced_current_original_reopens_completed_review(self):
        self.enqueue(REG);await self.wait_completed()
        originals._store_original_version(REG,UID,self.doc,self.second)
        state=self.enqueue(REG)
        self.assertEqual(state["status"],"queued")
        await self.wait_completed(2)
        self.assertEqual(self.calls,[hashlib.sha256(self.first).hexdigest(),hashlib.sha256(self.second).hexdigest()])

    async def test_unchanged_completed_original_does_not_repeat(self):
        self.enqueue(REG);await self.wait_completed()
        originals._store_original_version(REG,UID,self.doc,self.first)
        self.assertEqual(self.enqueue(REG)["status"],"completed")
        await asyncio.sleep(.01)
        self.assertEqual(len(self.calls),1)

    async def test_new_bytes_during_running_review_are_not_lost(self):
        release=asyncio.Event();self.block=release
        self.enqueue(REG)
        while not self.calls:await asyncio.sleep(.005)
        originals._store_original_version(REG,UID,self.doc,self.second)
        self.assertEqual(self.enqueue(REG)["status"],"running")
        release.set()
        await self.wait_completed(2)
        self.assertEqual(self.calls[-1],hashlib.sha256(self.second).hexdigest())

    async def test_current_fingerprint_survives_process_restart_without_repeating(self):
        self.enqueue(REG);await self.wait_completed()
        # Finish the old process's done callback before simulating a restart.
        await asyncio.sleep(0)
        restarted={**self.ns,"app":FastAPI(),"lock":asyncio.Lock()}
        originals.install(restarted)
        enqueue=restarted["_v295_enqueue_review"]
        self.assertEqual(enqueue(REG)["status"],"completed")
        originals._store_original_version(REG,UID,self.doc,self.second)
        self.assertEqual(enqueue(REG)["status"],"queued")
        await self.wait_completed(2)
        await asyncio.sleep(.025)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(enqueue(REG)["status"],"completed")

    def legacy_completed(self):
        self.state_path.parent.mkdir(parents=True,exist_ok=True)
        self.state_path.write_text(json.dumps({"reg_number":REG,"status":"completed","originals_received":1}))

    async def test_legacy_completion_without_fingerprint_is_not_reset_on_deploy(self):
        self.legacy_completed()
        self.assertEqual(self.enqueue(REG)["status"],"completed")
        await asyncio.sleep(.025)
        self.assertEqual(self.calls,[])
        self.assertNotIn("source_version",json.loads(self.state_path.read_text()))

    async def upload(self,blob):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns["app"]),base_url="http://test") as client:
            result=await client.post(f"/api/v295/eis223/original/{REG}/{UID}",content=blob,
                                     headers={"x-eis223-upload-token":"synthetic-token"})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    async def test_actual_changed_upload_reopens_legacy_completion(self):
        self.legacy_completed()
        self.assertEqual((await self.upload(self.second))["review_status"],"queued")
        await self.wait_completed()
        self.assertEqual(self.calls,[hashlib.sha256(self.second).hexdigest()])
        await asyncio.sleep(.025)
        self.assertEqual(len(self.calls),1)

    async def test_same_byte_upload_does_not_reset_legacy_completion(self):
        self.legacy_completed()
        self.assertEqual((await self.upload(self.first))["review_status"],"completed")
        await asyncio.sleep(.025)
        self.assertEqual(self.calls,[])

    async def test_receipt_history_and_review_timestamps_do_not_change_source_identity(self):
        before=originals._review_source_version(REG)
        folder=self.root/"eis223_official_originals"/REG/UID
        (folder/"versions"/"unrelated.json").write_text(json.dumps({"sha256":"old"}))
        receipt=json.loads((folder/"receipt.json").read_text());receipt["received_at"]="later"
        (folder/"receipt.json").write_text(json.dumps(receipt))
        self.legacy_completed()
        self.assertEqual(originals._review_source_version(REG),before)

    async def test_new_amendment_with_same_original_reopens_review(self):
        import v299_browser_intake as intake
        import test_amendment_history_intake as fixtures
        base=fixtures.payload(documents=[self.doc])
        intake._save_manifest(intake._parse_manifest(base))
        self.enqueue(REG);await self.wait_completed()
        previous=originals._review_source_version(REG)
        changed=fixtures.payload([fixtures.event(source_url=self.doc["url"])],documents=[self.doc])
        intake._save_manifest(intake._parse_manifest(changed))
        self.assertNotEqual(originals._review_source_version(REG),previous)
        self.assertEqual(self.enqueue(REG)["status"],"queued")
        await self.wait_completed(2)

    async def test_new_verified_soap_revision_with_same_original_changes_identity(self):
        import v294_eis_bridge as bridge
        import test_eis223_soap_archive as fixtures
        bridge.install(self.ns)
        async def archive(version):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns["app"]),base_url="http://test") as client:
                response=await client.post("/api/v294/eis223/archive",
                    content=fixtures.archive(fixtures.fixture(version=version,url=self.doc["url"])),
                    headers={"x-eis223-upload-token":"synthetic-token"})
            self.assertEqual(response.status_code,200,response.text)
        await archive(1)
        if self.calls:await self.wait_completed()
        previous=originals._review_source_version(REG)
        await archive(2)
        self.assertNotEqual(originals._review_source_version(REG),previous)

    def second_source(self,data=None,ext=".pdf"):
        uid="00000000000000000000000000000002"
        doc={**self.doc,"uid":uid,"name":"second"+ext,"ext":ext,
             "url":self.doc["url"][:-32]+uid}
        originals._store_original_version(REG,uid,doc,self.first if data is None else data)
        path=self.root/"eis223_public_browser_intake"/REG/"manifest.json"
        value=json.loads(path.read_text());value["documents"].append(doc);path.write_text(json.dumps(value))
        return doc

    async def test_two_identical_verified_originals_complete_once_without_repeating(self):
        self.second_source()
        self.enqueue(REG);await self.wait_completed()
        for _ in range(3):self.assertEqual(self.enqueue(REG)["status"],"completed")
        await asyncio.sleep(.025)
        self.assertEqual(len(self.calls),1)

    async def test_distinct_unreadable_original_cannot_be_hidden_by_duplicate_coverage(self):
        self.second_source(self.second)
        self.enqueue(REG)
        await asyncio.sleep(.05)
        self.assertEqual(json.loads(self.state_path.read_text())["status"],"partial")
        self.assertIsNone(originals._verified_unique_original_count(REG))

    async def test_archive_sources_never_infer_complete_member_coverage(self):
        self.second_source(self.first,ext=".zip")
        self.enqueue(REG)
        await asyncio.sleep(.05)
        self.assertEqual(json.loads(self.state_path.read_text())["status"],"partial")
        self.assertIsNone(originals._verified_unique_original_count(REG))

    async def test_corrupt_duplicate_bytes_do_not_satisfy_coverage(self):
        second=self.second_source()
        originals.saved_document(REG,second["uid"])[0].write_bytes(b"!"*len(self.first))
        self.enqueue(REG)
        await asyncio.sleep(.05)
        self.assertEqual(json.loads(self.state_path.read_text())["status"],"partial")

    async def test_original_repair_reopens_completed_review_even_with_same_expected_hash(self):
        self.enqueue(REG);await self.wait_completed()
        path=originals.saved_document(REG,UID)[0];path.write_bytes(b"!"*len(self.first))
        self.assertEqual((await self.upload(self.first))["review_status"],"queued")
        await self.wait_completed(2)
        self.assertEqual(path.read_bytes(),self.first)

    async def test_soap_source_identity_does_not_read_or_decompress_zip_bytes(self):
        import test_eis223_soap_archive as fixtures
        import v294_eis_bridge as bridge
        raw=fixtures.archive(fixtures.fixture(url=self.doc["url"]));sha=hashlib.sha256(raw).hexdigest()
        folder=self.root/"eis223_official_archive_inbox"/REG;folder.mkdir(parents=True)
        (folder/(sha+".zip")).write_bytes(raw)
        (folder/(sha+".json")).write_text(json.dumps({"source":"official_eis_soap_archive","reg_number":REG,
                                                     "archive_sha256":sha,"notices":bridge.parse_notices(raw)}))
        actual=Path.open
        def guarded(path,*args,**kwargs):
            if path.suffix==".zip":raise AssertionError("Scheduler read compressed original bytes")
            return actual(path,*args,**kwargs)
        with patch.object(Path,"open",guarded):
            self.assertEqual(len(originals._soap_source_evidence(REG)),1)
            self.assertEqual(originals._review_source_version(REG),originals._review_source_version(REG))

    async def test_manifest_endpoint_new_event_rechecks_once_and_replay_does_not(self):
        import v299_browser_intake as intake
        import test_amendment_history_intake as fixtures
        base=fixtures.payload(documents=[self.doc]);intake._save_manifest(intake._parse_manifest(base))
        self.enqueue(REG);await self.wait_completed()
        intake.install(self.ns)
        changed=fixtures.payload([fixtures.event(source_url=self.doc["url"])],documents=[self.doc])
        async def post(value):
            with patch.object(intake,"_apply_customer",return_value=False):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns["app"]),base_url="http://test") as client:
                    response=await client.post("/api/v299/eis223/public-manifest",json=value,
                        headers={"x-eis223-upload-token":"synthetic-token","origin":"https://zakupki.gov.ru"})
            return response
        self.assertEqual((await post(changed)).status_code,200)
        await self.wait_completed(2)
        self.assertEqual((await post(changed)).status_code,200)
        await asyncio.sleep(.025);self.assertEqual(len(self.calls),2)
        invalid=fixtures.payload([fixtures.event(reg_number="32616233662",source_url=self.doc["url"])],documents=[self.doc])
        self.assertEqual((await post(invalid)).status_code,422)
        self.assertEqual(len(self.calls),2)

    async def test_soap_endpoint_revision_rechecks_once_and_duplicate_transport_does_not(self):
        import io,zipfile
        import v294_eis_bridge as bridge
        import test_eis223_soap_archive as fixtures
        bridge.install(self.ns)
        async def post(version,comment=b""):
            raw=fixtures.archive(fixtures.fixture(version=version,url=self.doc["url"]))
            if comment:
                stream=io.BytesIO(raw)
                with zipfile.ZipFile(stream,"a") as archive:archive.comment=comment
                raw=stream.getvalue()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns["app"]),base_url="http://test") as client:
                response=await client.post("/api/v294/eis223/archive",content=raw,
                    headers={"x-eis223-upload-token":"synthetic-token"})
            self.assertEqual(response.status_code,200,response.text)
        await post(1);await self.wait_completed()
        await post(1,b"same XML in another transport reply")
        await asyncio.sleep(.025);self.assertEqual(len(self.calls),1)
        await post(2);await self.wait_completed(2)
        await post(2);await asyncio.sleep(.025);self.assertEqual(len(self.calls),2)

if __name__=="__main__":unittest.main()
