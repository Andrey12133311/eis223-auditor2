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

    def test_new_inn_job_is_saved_in_shared_queue_without_creating_manual_purchase(self):
        app = FastAPI()
        ns = {"app": app, "conn": lambda: sqlite3.connect(self.folder/"search.sqlite")}
        queue.install(ns)
        with patch.object(queue, "_local_results", return_value=[]), TestClient(app) as client:
            result = client.post("/api/v301/search", json={"kind": "inn", "value": "1234567890"})
        self.assertEqual(result.status_code, 200, result.text)
        with ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT kind,status FROM v301_search_jobs WHERE target='1234567890'").fetchall(), [("inn", "queued")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)

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
const input=JSON.parse(fs.readFileSync(0,'utf8')),nodes={},requests=[],submissions=[],timers=[],navigation=[];
function element(){
  return {style:{},dataset:{},handlers:{},classList:{toggle(){}},setAttribute(){},
    addEventListener(name,fn){this.handlers[name]=fn},
    requestSubmit(){this.handlers.submit({preventDefault(){}})},
    set innerHTML(html){this.html=html;for(const m of html.matchAll(/id="([^"]+)"/g))
      nodes[m[1]]??=element()},get innerHTML(){return this.html||''}};
}
const host={insertBefore(node){nodes[node.id]=node}};
const context={window:{dispatchEvent(){}},location:{pathname:'/manual-purchases',search:input.search||'',hash:''},
  history:{replaceState(...args){navigation.push(args.at(-1))}},
  document:{readyState:'complete',body:host,querySelector:()=>host,
    createElement:element,getElementById:id=>nodes[id]||null},
  setInterval(){},setTimeout(fn){timers.push(fn)},URLSearchParams,Event,
  fetch:async (url,options)=>{requests.push(url);if(options?.body)submissions.push(JSON.parse(options.body));return {ok:true,json:async()=>
    url.endsWith('/status')?input.status:url.includes('/job/')?input.jobResponse:
      {id:'queued-job',bridge:input.status,existing:[]}}}};
vm.runInNewContext(input.script,context);
(async()=>{
  // Settle the asynchronous initial connection status and search response.
  for(let i=0;i<10;i++)await Promise.resolve();
  if(!input.search){
    const kind=input.kind||'reg';
    nodes['v302-'+kind].value=kind==='inn'?'1234567890':'32616233661';
    nodes['v302-'+kind+'-form'].handlers.submit({preventDefault(){}});
  }
  for(let i=0;i<10;i++)await Promise.resolve();
  if(input.jobResponse){
    for(let n=0;n<(input.pollSteps||1)&&timers.length;n++){
      timers.shift()();for(let i=0;i<10;i++)await Promise.resolve();
    }
  }
  process.stdout.write(JSON.stringify({html:nodes['v302-search-panel'].innerHTML,
    status:nodes['v302-connect'].textContent,result:nodes['v302-search-result'].innerHTML,
    copyControl:!!nodes['v302-copy-bridge'],innControl:!!nodes['v302-inn-form'],requests,submissions,navigation,
    taskStatus:nodes['v302-task-status']?.textContent,pendingPolls:timers.length}));
})().catch(error=>{console.error(error);process.exitCode=1});
'''


@unittest.skipUnless(shutil.which("node"), "Node is required for DOM behavior checks")
class VpsOnlyUiTests(unittest.TestCase):
    def rendered_ui(self, mode, status, kind="reg", search="", job_response=None, poll_steps=1):
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
            input=json.dumps({"script": script, "status": status, "kind": kind,
                              "search": search, "jobResponse": job_response,
                              "pollSteps": poll_steps}), text=True,
            capture_output=True, check=True, timeout=10)
        return json.loads(result.stdout)

    def test_offline_vps_has_no_browser_setup_and_ignores_old_active_flag(self):
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY",
                                 {"active": True, "server_active": False, "queued": 2})
        self.assertFalse(value["copyControl"])
        self.assertTrue(value["innControl"])
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
        self.assertTrue(value["innControl"])

    def test_inn_search_is_submitted_while_vps_upgrade_is_pending(self):
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", {
            "active": True, "server_active": True, "queued": 2,
            "inn_search_available": False}, kind="inn")
        self.assertTrue(value["innControl"])
        self.assertFalse(value["copyControl"])
        self.assertEqual(value["submissions"], [{"kind": "inn", "value": "1234567890"}])
        self.assertIn("Поиск сохранён. Для выполнения требуется обновление загрузчика VPS.", value["result"])
        self.assertNotIn("выполнятся автоматически", value["result"])

    def test_capable_vps_starts_inn_search_without_browser_setup(self):
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", {
            "active": True, "server_active": True, "queued": 2,
            "inn_search_available": True}, kind="inn")
        self.assertTrue(value["innControl"])
        self.assertFalse(value["copyControl"])
        self.assertEqual(value["submissions"], [{"kind": "inn", "value": "1234567890"}])
        self.assertIn("Поиск закупок и загрузка документов выполнятся автоматически через VPS.", value["result"])
        self.assertNotIn("требуется обновление", value["result"])

    def test_favorite_inn_still_submits_one_search_in_vps_mode(self):
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", {
            "active": True, "server_active": True, "queued": 2,
            "inn_search_available": False}, search="?favorite_inn=1234567890")
        self.assertEqual(value["submissions"], [{"kind": "inn", "value": "1234567890"}])
        self.assertEqual(value["navigation"], ["/manual-purchases"])
        self.assertIn("Поиск сохранён", value["result"])

    def test_inn_candidates_keep_polling_until_customer_verification_finishes(self):
        status = {"active": True, "server_active": True, "queued": 2, "inn_search_available": True}
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", status, kind="inn", job_response={
            "job": {"status": "done"}, "verification_pending": 2, "bridge": status,
            "existing": [], "total_existing": 0})
        self.assertIn("Кандидаты найдены; проверяем ИНН заказчика по данным ЕИС", value["taskStatus"])
        self.assertEqual(value["pendingPolls"], 1)
        self.assertNotIn("Поиск закупок по ИНН завершён", value["taskStatus"])

    def test_finished_inn_discovery_does_not_claim_finished_document_audit(self):
        status = {"active": True, "server_active": True, "queued": 2, "inn_search_available": True}
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", status, kind="inn", job_response={
            "job": {"status": "done"}, "verification_pending": 0, "bridge": status,
            "existing": [], "total_existing": 0})
        self.assertIn("Загрузка документов и их проверка продолжаются в отдельной очереди", value["taskStatus"])
        self.assertEqual(value["pendingPolls"], 0)

    def test_inn_first_page_keeps_polling_for_next_pages(self):
        status = {"active": True, "server_active": True, "queued": 2, "inn_search_available": True}
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", status, kind="inn", job_response={
            "job": {"status": "done"}, "verification_pending": 0,
            "search_pages_pending": 1, "bridge": status,
            "existing": [], "total_existing": 0})
        self.assertIn("Поиск следующих страниц на VPS…", value["taskStatus"])
        self.assertEqual(value["pendingPolls"], 1)
        self.assertNotIn("Поиск закупок по ИНН завершён", value["taskStatus"])

    def test_finished_limited_inn_search_discloses_page_limit(self):
        status = {"active": True, "server_active": True, "queued": 2, "inn_search_available": True}
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", status, kind="inn", job_response={
            "job": {"status": "done"}, "verification_pending": 0,
            "search_pages_pending": 0, "pagination_limited": True, "bridge": status,
            "existing": [], "total_existing": 0})
        self.assertIn("Достигнут лимит: 10 страниц поиска.", value["taskStatus"])
        self.assertEqual(value["pendingPolls"], 0)
        self.assertNotIn("все закупки", value["taskStatus"].lower())

    def test_inn_progress_can_continue_beyond_registry_poll_window(self):
        status = {"active": True, "server_active": True, "queued": 2, "inn_search_available": True}
        value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", status, kind="inn", job_response={
            "job": {"status": "done"}, "verification_pending": 0,
            "search_pages_pending": 1, "bridge": status,
            "existing": [], "total_existing": 0}, poll_steps=90)
        self.assertEqual(sum("/job/" in url for url in value["requests"]), 90)
        self.assertEqual(value["pendingPolls"], 1)
        registry = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", status, job_response={
            "job": {"status": "queued"}, "bridge": status,
            "existing": [], "total_existing": 0}, poll_steps=90)
        self.assertEqual(sum("/job/" in url for url in registry["requests"]), 85)
        self.assertEqual(registry["pendingPolls"], 0)

    def test_inn_finished_with_missing_data_is_partial_with_counts(self):
        status = {"active": True, "server_active": True, "queued": 2, "inn_search_available": True}
        for pages_failed, unresolved in ((2, 0), (0, 3), (1, 4)):
            with self.subTest(pages_failed=pages_failed, unresolved=unresolved):
                value = self.rendered_ui("OFFICIAL_EIS_VPS_ONLY", status, kind="inn", job_response={
                    "job": {"status": "done"}, "verification_pending": 0,
                    "search_pages_pending": 0, "search_pages_failed": pages_failed,
                    "unresolved_candidates": unresolved, "bridge": status,
                    "existing": [], "total_existing": 0})
                self.assertIn("Поиск выполнен частично: часть данных ЕИС получить не удалось", value["taskStatus"])
                self.assertIn("страниц с ошибками: "+str(pages_failed), value["taskStatus"])
                self.assertIn("закупок без подтверждённых данных: "+str(unresolved), value["taskStatus"])
                self.assertNotIn("Поиск закупок по ИНН завершён", value["taskStatus"])
                self.assertEqual(value["pendingPolls"], 0)

    def test_other_mode_preserves_existing_browser_setup(self):
        value = self.rendered_ui("OFFICIAL_EIS_BROWSER_ONLY",
                                 {"active": False, "server_active": False, "queued": 2})
        self.assertTrue(value["copyControl"])
        self.assertTrue(value["innControl"])
        self.assertIn("collector.js", value["html"])
        self.assertIn("запустите браузерный мост", value["result"])


if __name__ == "__main__":
    unittest.main()
