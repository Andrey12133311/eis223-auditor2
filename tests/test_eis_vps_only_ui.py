"""A retired browser collector cannot make VPS-only ingestion appear online."""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import eis_server_worker as worker
import v299_browser_intake as intake
import v301_search_jobs as queue
import v302_eis_modern_ui as ui


class VpsOnlyStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        env = patch.dict(os.environ, {"EIS223_SOURCE_MODE": "OFFICIAL_EIS_VPS_ONLY",
                                     "EIS223_DATA_DIR": str(self.folder)})
        env.start()
        self.addCleanup(env.stop)
        self.ns = {"conn": lambda: sqlite3.connect(self.folder/"queue.sqlite")}
        with self.ns["conn"]() as db:
            db.executescript("""
                CREATE TABLE v301_bridge_health(singleton INTEGER,seen_at INTEGER,last_action TEXT);
                CREATE TABLE v301_search_jobs(status TEXT);
                INSERT INTO v301_search_jobs VALUES('queued');
            """)
            db.execute("INSERT INTO v301_bridge_health VALUES(1,?,'claimed')", (int(time.time()),))
        worker.init(self.ns)
        self.identity = str(uuid.uuid4())

    def test_fresh_browser_heartbeat_cannot_mark_disconnected_vps_online(self):
        status = queue._bridge(self.ns)
        self.assertFalse(status["active"])
        self.assertFalse(status["browser_active"])
        self.assertFalse(status["browser_collector_enabled"])
        self.assertIsNone(status["last_seen"])
        self.assertEqual(status["intake_mode"], "vps_only")
        self.assertEqual(status["queued"], 1)

    def test_status_tracks_vps_connection_and_stop(self):
        worker.heartbeat(self.ns, {"worker_id": self.identity, "status": "idle"})
        status = queue._bridge(self.ns)
        self.assertTrue(status["active"])
        self.assertTrue(status["server_active"])
        self.assertFalse(status["browser_active"])
        self.assertEqual(status["last_seen"], status["server_last_seen"])
        worker.heartbeat(self.ns, {"worker_id": self.identity, "status": "stopped"})
        self.assertFalse(queue._bridge(self.ns)["active"])

    def test_browser_monitor_is_disabled_even_with_fresh_saved_scan(self):
        folder = self.folder/"eis223_public_browser_intake"
        folder.mkdir()
        (folder/"_monitor_status.json").write_text(json.dumps({
            "last_scan": datetime.now(timezone.utc).isoformat(),
            "collector_version": 377, "status": "ok"}))
        status = intake._scan_status()
        self.assertFalse(status["browser_collector_active"])
        self.assertFalse(status["browser_collector_enabled"])
        self.assertFalse(status["upgrade_required"])
        self.assertEqual(status["last_cycle_status"], "disabled")
        with patch.dict(os.environ, {"EIS223_SOURCE_MODE": "OFFICIAL_EIS_BROWSER_ONLY"}):
            self.assertTrue(intake._scan_status()["browser_collector_active"])
            self.assertTrue(queue._bridge(self.ns)["browser_active"])

    def test_new_inn_job_is_rejected_without_changing_queue(self):
        app = FastAPI()
        ns = {"app": app, "conn": lambda: sqlite3.connect(self.folder/"search.sqlite")}
        queue.install(ns)
        with ns["conn"]() as db:
            previous = db.execute("SELECT id,kind,target,status FROM v301_search_jobs ORDER BY id").fetchall()
        with TestClient(app) as client:
            result = client.post("/api/v301/search", json={"kind": "inn", "value": "1234567890"})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json()["detail"], "В режиме VPS укажите номер закупки ЕИС.")
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT id,kind,target,status FROM v301_search_jobs ORDER BY id").fetchall(), previous)

    def test_registry_search_still_enters_shared_vps_queue(self):
        app = FastAPI()
        ns = {"app": app, "conn": lambda: sqlite3.connect(self.folder/"search.sqlite")}
        queue.install(ns)
        with patch.object(queue, "_local_results", return_value=[]), TestClient(app) as client:
            result = client.post("/api/v301/search", json={"kind": "reg", "value": "32616233661"})
        self.assertEqual(result.status_code, 200, result.text)
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT status FROM v301_search_jobs WHERE target='32616233661'").fetchone()[0], "queued")


NODE_UI = r'''
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),nodes={},requests=[];
function element(){
  return {style:{},dataset:{},handlers:{},classList:{toggle(){}},setAttribute(){},
    addEventListener(name,fn){this.handlers[name]=fn},
    set innerHTML(html){this.html=html;for(const m of html.matchAll(/id="([^"]+)"/g))
      nodes[m[1]]??=element()},get innerHTML(){return this.html||''}};
}
const host={insertBefore(node){nodes[node.id]=node}};
const context={window:{dispatchEvent(){}},location:{pathname:'/manual-purchases',search:''},
  document:{readyState:'complete',body:host,querySelector:()=>host,
    createElement:element,getElementById:id=>nodes[id]||null},
  setInterval(){},setTimeout(){},URLSearchParams,Event,
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>
    url.endsWith('/status')?input.status:{id:'queued-job',bridge:input.status,existing:[]}}}};
vm.runInNewContext(input.script,context);
(async()=>{
  // Settle the asynchronous initial connection status and search response.
  for(let i=0;i<10;i++)await Promise.resolve();
  nodes['v302-reg'].value='32616233661';
  nodes['v302-reg-form'].handlers.submit({preventDefault(){}});
  for(let i=0;i<10;i++)await Promise.resolve();
  process.stdout.write(JSON.stringify({html:nodes['v302-search-panel'].innerHTML,
    status:nodes['v302-connect'].textContent,result:nodes['v302-search-result'].innerHTML,
    copyControl:!!nodes['v302-copy-bridge'],innControl:!!nodes['v302-inn-form'],requests}));
})().catch(error=>{console.error(error);process.exitCode=1});
'''


@unittest.skipUnless(shutil.which("node"), "Node is required for DOM behavior checks")
class VpsOnlyUiTests(unittest.TestCase):
    def rendered_ui(self, mode, status):
        app = FastAPI()

        @app.get("/manual-purchases")
        async def page():
            return HTMLResponse("<html><head></head><body><main></main></body></html>")

        ui.install({"app": app})
        with patch.dict(os.environ, {"EIS223_SOURCE_MODE": mode}), TestClient(app) as client:
            response = client.get("/manual-purchases")
        self.assertEqual(response.status_code, 200)
        script = re.search(r'<script id="v302-modern-js">(.*?)</script>', response.text, re.S)[1]
        result = subprocess.run([shutil.which("node"), "-e", NODE_UI],
            input=json.dumps({"script": script, "status": status}), text=True,
            capture_output=True, check=True, timeout=10)
        return json.loads(result.stdout)

    def test_offline_vps_has_no_browser_setup_and_ignores_old_active_flag(self):
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY",
                                 {"active": True, "server_active": False, "queued": 2})
        self.assertFalse(value["copyControl"])
        self.assertFalse(value["innControl"])
        self.assertNotIn("collector.js", value["html"])
        self.assertIn("VPS не на связи", value["status"])
        self.assertIn("Задание сохранено", value["result"])
        self.assertNotIn("браузер", value["result"])
        self.assertTrue(all(url.startswith("/api/v301/") for url in value["requests"]))

    def test_online_vps_uses_automatic_cycle_message(self):
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY",
                                 {"active": True, "server_active": True, "queued": 2})
        self.assertIn("VPS подключён", value["status"])
        self.assertIn("автоматически", value["result"])
        self.assertIn("ближайших циклов", value["result"])
        self.assertFalse(value["copyControl"])
        self.assertFalse(value["innControl"])

    def test_other_mode_preserves_existing_browser_setup(self):
        value = self.rendered_ui("OFFICIAL_EIS_BROWSER_ONLY",
                                 {"active": False, "server_active": False, "queued": 2})
        self.assertTrue(value["copyControl"])
        self.assertTrue(value["innControl"])
        self.assertIn("collector.js", value["html"])
        self.assertIn("запустите браузерный мост", value["result"])


if __name__ == "__main__":
    unittest.main()
