"""V353: thematic EIS files get real bridge tasks even when scan heartbeat is stale."""
import asyncio
import json
import sqlite3
import tempfile
import time
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI

import v342_thematic_queue as thematic
import v345_thematic_accelerator as worker
import v301_search_jobs as jobs

REG1="32616411111"
REG2="32616422222"
REG3="32616433333"
B1="0"*24
B2="1"*24

def run():
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"bridge.sqlite"
        def connect():
            c=sqlite3.connect(path,timeout=5,check_same_thread=False)
            c.row_factory=sqlite3.Row
            return c
        c=connect()
        c.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT);
          CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT);
          INSERT INTO purchases VALUES('32616411111','ООО Пример 1');
          INSERT INTO purchases VALUES('32616422222','ООО Пример 2');
          INSERT INTO purchases VALUES('32616433333','ООО Пример 3');
          CREATE TABLE v301_search_jobs(
              id TEXT PRIMARY KEY,kind TEXT NOT NULL,target TEXT NOT NULL,
              page INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'queued',
              priority INTEGER NOT NULL DEFAULT 10,created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,not_before INTEGER NOT NULL DEFAULT 0,
              lease_until INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
              detail TEXT NOT NULL DEFAULT '',found INTEGER NOT NULL DEFAULT 0,
              accepted INTEGER NOT NULL DEFAULT 0,parent_id TEXT NOT NULL DEFAULT ''
          );
          CREATE TABLE v301_bridge_health(singleton INTEGER PRIMARY KEY,
            seen_at INTEGER NOT NULL,last_action TEXT NOT NULL DEFAULT '');
        """)
        c.execute("INSERT INTO v301_bridge_health VALUES(1,?,'idle')",(int(time.time()),))
        c.commit();c.close()
        ns={"conn":connect,"app":FastAPI()}
        thematic.init(ns)
        worker.install(ns)
        c=connect()
        c.execute("""INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms)
          VALUES(?,?,'2026-10-15','active','["ООО"]')""",(B1,'["dates"]'))
        c.execute("""INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms)
          VALUES(?,?,'2026-10-15','active','["ООО"]')""",(B2,'["dates"]'))
        for batch in (B1,B2):
            c.execute("""INSERT INTO thematic_items(batch_id,reg,status,updated_at)
              VALUES(?,?,'waiting_originals','2026-10-15T09:00:00+00:00')""",
              (batch,REG1))
        c.execute("""INSERT INTO thematic_items(batch_id,reg,status,updated_at)
          VALUES(?,?,'waiting_originals','2026-10-15T10:00:00+00:00')""",
          (B1,REG2))
        c.commit();c.close()
        # v299 discovery heartbeat is stale, but v301 bridge is running.
        with patch("v299_browser_intake._scan_status",return_value={
                    "browser_collector_active":False}):
            new=worker._request_missing_originals(ns)
        assert new==2,new
        c=connect()
        records=[dict(x) for x in c.execute(
            "SELECT kind,target,status,priority,parent_id FROM v301_search_jobs")]
        assert len(records)==2,records
        assert {x["target"] for x in records}=={REG1,REG2},records
        assert all(x["parent_id"]=="thematic-auto" and x["priority"]==7
                   for x in records),records
        c.close()
        # Another thematic batch with same purchase may not triple the task.
        thematic.WORKER_STATE["last_eis_dispatch"]=0
        assert worker._request_missing_originals(ns)==0
        # Even with no active bridge, prequeue a bounded request so it is
        # available to the *next* official browser poll.
        c=connect()
        c.execute("""INSERT INTO thematic_items(batch_id,reg,status,updated_at)
          VALUES(?,?,'waiting_originals','2026-10-15T09:30:00+00:00')""",
          (B1,REG3))
        c.commit();c.close()
        thematic.WORKER_STATE["last_eis_dispatch"]=0
        with patch("v299_browser_intake._scan_status",return_value={
                "browser_collector_active":False}), \
             patch("v301_search_jobs._bridge",return_value={"active":False}):
            assert worker._request_missing_originals(ns)==1
        c=connect()
        assert c.execute("SELECT COUNT(*) FROM v301_search_jobs").fetchone()[0]==3
        assert c.execute(
            "SELECT COUNT(*) FROM v301_search_jobs WHERE target=?",(REG1,)
        ).fetchone()[0]==1
        assert c.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE target=?",
                         (REG3,)).fetchone()[0]==1
        assert not c.execute("""SELECT 1 FROM sqlite_master WHERE
                  type='table' AND name='v319_manual_inputs'""").fetchone()
        c.close()
        # Every one of the three waiting purchases must display its real job
        # status, not the old vague "Ожидается постановка задания..." text.
        info=thematic.overview(ns,B1)
        matching={x["reg"]:x for x in info["items"]}
        for reg in (REG1,REG2,REG3):
            assert "очереди загрузчика" in matching[reg]["detail"],matching
        print("V353_THEME_EIS_SELFTEST_OK stale_scan_live_bridge=True "
              "background_queue=True no_triplication=True offline_prequeue=True "
              "manual_isolated=True accurate_wait_status=True")

if __name__=="__main__":
    run()
