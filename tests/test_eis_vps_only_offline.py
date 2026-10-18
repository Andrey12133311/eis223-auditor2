"""VPS-only mode never retrieves originals remotely; saved files stay usable."""
import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v295_eis_originals as originals
import v298_eis_only as mode

REG = "32616233661"
UID = "A" * 32
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + UID


class VPSOnlyOfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {"EIS223_SOURCE_MODE": "OFFICIAL_EIS_VPS_ONLY",
                                      "EIS223_DATA_DIR": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.remote_discovery = AsyncMock(side_effect=AssertionError("Remote discovery called"))
        self.remote_download = AsyncMock(side_effect=AssertionError("Remote download called"))
        self.remote_batch = AsyncMock(side_effect=AssertionError("Remote batch called"))
        self.ns = {"app": FastAPI(),
                   "discover_eis_documents": self.remote_discovery,
                   "download_doc": self.remote_download,
                   "download_all_documents": self.remote_batch,
                   "extract_text": lambda path: path.read_text()}
        originals.install(self.ns)

    def assert_no_remote_calls(self):
        self.remote_discovery.assert_not_awaited()
        self.remote_download.assert_not_awaited()
        self.remote_batch.assert_not_awaited()

    def test_discovery_reads_saved_references_without_fetching_card(self):
        reference = {UID: {"name": "notice.xml", "url": URL, "ext": ".xml"}}
        with patch.object(originals, "allowed_documents", return_value=reference):
            documents = asyncio.run(self.ns["discover_eis_documents"](None, REG, {}))
        self.assertEqual([d["url"] for d in documents], [URL])
        self.assert_no_remote_calls()

    def test_missing_original_and_batch_wait_without_remote_fallback(self):
        documents = [{"url": URL, "name": "missing.pdf"}]
        with patch.object(originals, "allowed_documents", return_value={}):
            asyncio.run(self.ns["download_all_documents"](None, REG, documents))
        self.assertEqual(documents[0]["status"], "waiting_for_originals")
        self.assertEqual(documents[0]["text"], "")
        self.assertIsNone(documents[0]["local"])
        self.assert_no_remote_calls()

    def test_authenticated_saved_original_is_still_extracted(self):
        path = self.home / "original.xml"
        path.write_text("Verified saved original")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        document = {"url": URL, "name": path.name}
        with patch.object(originals, "allowed_documents", return_value={UID: {}}), \
             patch.object(originals, "saved_document", return_value=(path, {"sha256": digest})):
            asyncio.run(self.ns["download_doc"](None, REG, document))
        self.assertEqual(document["status"], "checked")
        self.assertEqual(document["text"], "Verified saved original")
        self.assertEqual(document["meta"]["sha256"], digest)
        self.assert_no_remote_calls()

    def test_historical_local_files_and_cache_remain_available(self):
        cache_path = self.home / "historical-cache.xml"
        local_path = self.home / "historical-local.xml"
        cache_path.write_text("Historical file from database cache")
        local_path.write_text("Historical file from saved document")
        self.ns["_cache_docs"] = Mock(return_value={URL: str(cache_path)})
        documents = [{"url": URL, "name": cache_path.name},
                     {"url": URL + "&v=2", "local": str(local_path), "name": local_path.name}]
        with patch.object(originals, "allowed_documents", return_value={}):
            asyncio.run(self.ns["download_all_documents"](None, REG, documents))
        self.assertEqual([d["status"] for d in documents], ["checked", "checked"])
        self.assertEqual([d["text"] for d in documents],
                         [cache_path.read_text(), local_path.read_text()])
        self.assert_no_remote_calls()

    def test_vps_install_does_not_grant_browser_cors(self):
        self.assertFalse(any(m.cls.__name__ == "CORSMiddleware" for m in self.ns["app"].user_middleware))

    def test_vps_mode_status_stays_vps_only_when_worker_offline(self):
        mode.install(self.ns)
        self.assertEqual(os.environ["EIS223_SOURCE_MODE"], "OFFICIAL_EIS_VPS_ONLY")
        with patch("eis_server_worker.health", return_value={"active": False, "status": "not_connected"}), \
             TestClient(self.ns["app"]) as client:
            status = client.get("/api/v298/eis-only/status").json()
        self.assertEqual(status["mode"], "official_eis_vps_only")
        self.assertEqual(status["sources"], ["ЕИС getDocsIP (VPS)"])
        self.assertFalse(status["browser_collector_enabled"])
        self.assertFalse(status["direct_eis_server_requests"])

    def test_legacy_mode_retains_previous_fallback_contract(self):
        os.environ["EIS223_SOURCE_MODE"] = "OFFICIAL_EIS_BROWSER_ONLY"
        self.remote_discovery.side_effect = None
        self.remote_discovery.return_value = []
        self.remote_download.side_effect = None
        with patch.object(originals, "allowed_documents", return_value={}):
            asyncio.run(self.ns["discover_eis_documents"](None, REG, {}))
            asyncio.run(self.ns["download_doc"](None, REG, {"url": URL}))
        self.remote_discovery.assert_awaited_once()
        self.remote_download.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
