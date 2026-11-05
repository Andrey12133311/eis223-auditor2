"""Saved-original compatibility hooks keep HTTP responsive under slow disk reads."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v295_eis_originals as originals

REG="32616233661"
UID="A"*32
URL="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+UID
REFERENCE={UID:{"name":"notice.xml","url":URL,"ext":".xml"}}


class IntakeCompatibilityResponsiveness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        env=patch.dict(os.environ,{"EIS223_SOURCE_MODE":"OFFICIAL_EIS_VPS_ONLY",
                                  "EIS223_DATA_DIR":str(self.folder)})
        env.start();self.addCleanup(env.stop)
        self.app=FastAPI()
        @self.app.get("/health-probe")
        async def health():return {"ok":True}
        self.discovery=AsyncMock(return_value=[])
        self.downloader=AsyncMock()
        self.batch=AsyncMock()
        self.ns={"app":self.app,"discover_eis_documents":self.discovery,
                 "download_doc":self.downloader,"download_all_documents":self.batch,
                 "extract_text":lambda path:path.read_text()}
        originals.install(self.ns)

    async def asyncTearDown(self):
        for handler in self.app.router.on_shutdown:await handler()

    async def while_io_is_held(self,hook,target,attribute,result):
        """Hold a real hook's file stage and concurrently serve a health request."""
        entered,release=threading.Event(),threading.Event()
        loop_thread=threading.get_ident()
        def slow(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),loop_thread,
                                "Compatibility file work ran on the HTTP event loop")
            entered.set()
            if not release.wait(3):raise RuntimeError("Test file stage did not release")
            return result(*args,**kwargs) if callable(result) else result
        with patch.object(target,attribute,side_effect=slow):
            task=asyncio.create_task(hook())
            try:
                if not await asyncio.to_thread(entered.wait,1):
                    # Surface an immediate hook failure rather than hiding it
                    # behind the synchronization assertion.
                    if task.done():await task
                    self.fail("Compatibility hook did not reach its file stage")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                            base_url="http://test") as client:
                    response=await asyncio.wait_for(client.get("/health-probe"),.5)
                self.assertEqual(response.json(),{"ok":True})
                self.assertFalse(task.done())
            finally:
                release.set()
                settled=await asyncio.gather(task,return_exceptions=True)
            if isinstance(settled[0],BaseException):raise settled[0]
            return settled[0]

    async def test_discovery_manifest_read_does_not_stall_health(self):
        async def hook():return await self.ns["discover_eis_documents"](None,REG,{})
        docs=await self.while_io_is_held(hook,originals,"allowed_documents",REFERENCE)
        self.assertEqual([doc["url"] for doc in docs],[URL])
        self.assertEqual(docs[0]["meta"]["uid"],UID)
        self.discovery.assert_not_awaited()

    async def test_download_manifest_read_does_not_stall_health(self):
        doc={"url":URL,"name":"missing.xml"}
        async def hook():await self.ns["download_doc"](None,REG,doc)
        await self.while_io_is_held(hook,originals,"allowed_documents",{})
        self.assertEqual(doc["status"],"waiting_for_originals")
        self.downloader.assert_not_awaited()

    async def test_download_receipt_read_preserves_verified_original_and_health(self):
        path=self.folder/"original.xml";path.write_text("Verified original evidence")
        doc={"url":URL,"name":path.name}
        async def hook():await self.ns["download_doc"](None,REG,doc)
        with patch.object(originals,"allowed_documents",return_value=REFERENCE):
            await self.while_io_is_held(hook,originals,"saved_document",(path,{"sha256":"verified-sha"}))
        self.assertEqual(doc["status"],"checked")
        self.assertEqual(doc["text"],"Verified original evidence")
        self.assertEqual(doc["meta"],{"source":"official_eis_getDocsIP","sha256":"verified-sha"})
        self.downloader.assert_not_awaited()

    async def test_batch_manifest_read_does_not_stall_health(self):
        # Legacy batch completes first; only its saved-original reconciliation
        # is delayed. Existing fallback and in-place list semantics remain.
        os.environ["EIS223_SOURCE_MODE"]="OFFICIAL_EIS_BROWSER_ONLY"
        docs=[]
        async def hook():await self.ns["download_all_documents"](None,REG,docs)
        await self.while_io_is_held(hook,originals,"allowed_documents",REFERENCE)
        self.batch.assert_awaited_once()
        self.assertEqual(docs,[])

    async def test_dedup_file_stat_preserves_official_occurrence_and_health(self):
        os.environ["EIS223_SOURCE_MODE"]="OFFICIAL_EIS_BROWSER_ONLY"
        path=self.folder/"same.xml";path.write_text("Same original evidence")
        docs=[{"url":"https://zakupki.gov.ru/old","local":str(path),"status":"checked","meta":{}},
              {"url":URL,"local":str(path),"status":"checked",
               "meta":{"source":"official_eis_getDocsIP","uid":UID}}]
        actual=Path.stat
        async def hook():await self.ns["download_all_documents"](None,REG,docs)
        with patch.object(originals,"allowed_documents",return_value=REFERENCE):
            await self.while_io_is_held(hook,Path,"stat",lambda *args,**kwargs:actual(path,*args,**kwargs))
        self.assertEqual(len(docs),1)
        self.assertEqual(docs[0]["url"],URL)
        self.assertEqual(docs[0]["meta"]["source"],"official_eis_getDocsIP")


if __name__=="__main__":unittest.main()
