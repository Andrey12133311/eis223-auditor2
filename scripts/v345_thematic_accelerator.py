"""Fast processing of thematic audit campaigns without additional EIS requests.

All source download frequency remains controlled by the official EIS browser
collector. This module processes *already saved* EIS files on the Railway server,
with short database transactions and bounded concurrent legal analyses.
"""
from __future__ import annotations
import asyncio
import json
import sqlite3
import time
from datetime import datetime,timezone
import v342_thematic_queue as original

MAX_CONCURRENT=3
MAX_PER_BATCH=18
MAX_PER_CYCLE=36
RECHECK_SECONDS=90
INDEX_SECONDS=90

def _open(ns):
    c=original.db(ns)
    c.execute("PRAGMA busy_timeout=2500")
    return c

def _prepare(ns):
    clock=time.time()
    c=_open(ns)
    try:
        campaigns=c.execute("SELECT * FROM thematic_batches WHERE status='active'").fetchall()
        if clock-original.WORKER_STATE.get("index_at",0)>INDEX_SECONDS:
            rows=c.execute("SELECT reg_number,customer FROM purchases WHERE reg_number GLOB '3??????????' LIMIT 50000").fetchall()
            for camp in campaigns:
                permitted=set(json.loads(camp["legal_forms"]))
                queue=[(camp["id"],r["reg_number"],original.now()) for r in rows
                       if original.customer_form(r["customer"]) in permitted]
                c.executemany("INSERT OR IGNORE INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?)",queue)
            c.commit()
            original.WORKER_STATE["index_at"]=clock
        selected=[]
        for camp in campaigns:
            if len(selected)>=MAX_PER_CYCLE: break
            # Fast indexed subqueries avoid scanning or re-reading original files
            # that have not changed since the last successful legal evaluation.
            rows=c.execute("""
             SELECT t.reg,t.status,t.updated_at,t.last_doc_id,t.last_ready_count,
              (SELECT COALESCE(MAX(id),0) FROM docs d WHERE d.reg_number=t.reg) doc_id,
              (SELECT COUNT(*) FROM docs d
                 WHERE d.reg_number=t.reg AND COALESCE(d.local_path,'')!=''
                   AND d.status='checked') ready_count
             FROM thematic_items t WHERE t.batch_id=?
               AND t.status IN ('queued','waiting_originals','waiting_engine','retry','completed')
             ORDER BY CASE WHEN EXISTS(
                 SELECT 1 FROM docs d WHERE d.reg_number=t.reg
                  AND COALESCE(d.local_path,'')!='' AND d.status='checked'
               ) THEN 0 ELSE 1 END,
               CASE WHEN t.status='queued' THEN 0
                    WHEN t.status='waiting_originals' THEN 1
                    WHEN t.status='retry' THEN 2 ELSE 3 END,
               t.updated_at ASC LIMIT 3000
            """,(camp["id"],)).fetchall()
            count=0
            for row in rows:
                if count>=MAX_PER_BATCH or len(selected)>=MAX_PER_CYCLE:break
                doc_id=int(row["doc_id"] or 0)
                ready=int(row["ready_count"] or 0)
                new_evidence=doc_id>int(row["last_doc_id"] or 0) or ready>int(row["last_ready_count"] or 0)
                state=row["status"]
                if state=="completed" and not new_evidence:
                    continue
                if state in ("waiting_originals","waiting_engine","retry") and not new_evidence:
                    try:age=clock-datetime.fromisoformat(row["updated_at"]).timestamp()
                    except (ValueError,TypeError):age=RECHECK_SECONDS
                    if age<RECHECK_SECONDS:continue
                selected.append({
                  "batch":camp["id"],"reg":row["reg"],
                  "categories":json.loads(camp["categories"]),
                  "ready":ready,"doc_id":doc_id})
                count+=1
        return selected
    finally:c.close()

async def _one(ns,item,sem):
    async with sem:
        if item["ready"]<1:
            return (item,"waiting_originals",[],"Ожидаются доступные оригиналы ЕИС")
        try:
            state,findings=await original.independent_review(ns,item["reg"],item["categories"])
            description={
              "completed":"Проверено по сохранённым оригиналам ЕИС",
              "waiting_originals":"Ожидаются подтверждённые оригиналы ЕИС",
              "excluded_company":"Форма заказчика не соответствует выборке"
            }.get(state,"Тематическая юридическая проверка")
            return item,state,findings,description
        except Exception as exc:
            return item,"retry",[],type(exc).__name__

def _persist(ns,results):
    c=_open(ns)
    try:
        for item,state,findings,detail in results:
            batch,reg=item["batch"],item["reg"]
            # Do not resurrect an already deleted or stopped thematic campaign.
            if not c.execute("SELECT 1 FROM thematic_batches WHERE id=? AND status='active'",(batch,)).fetchone():
                continue
            if state=="completed":
                for f in findings:
                    c.execute("""INSERT OR IGNORE INTO thematic_findings
                      (batch_id,reg,category,title,law,evidence)
                      VALUES(?,?,?,?,?,?)""",
                      (batch,reg,f["category"],f["title"],f.get("law"),f.get("evidence")))
            c.execute("""UPDATE thematic_items SET status=?,detail=?,updated_at=?,
              last_doc_id=?,last_ready_count=? WHERE batch_id=? AND reg=?""",
              (state,detail,original.now(),item["doc_id"],item["ready"],batch,reg))
        c.commit()
    finally:c.close()

async def faster_tick(ns):
    items=await asyncio.to_thread(_prepare,ns)
    original.WORKER_STATE["last_batch_size"]=len(items)
    if not items:return
    sem=asyncio.Semaphore(MAX_CONCURRENT)
    results=await asyncio.gather(*(_one(ns,item,sem) for item in items))
    await asyncio.to_thread(_persist,ns,results)
    original.WORKER_STATE["processed_total"]=original.WORKER_STATE.get("processed_total",0)+len(results)

def install(ns):
    c=_open(ns)
    try:
        cols={x[1] for x in c.execute("PRAGMA table_info(thematic_items)")}
        if "last_doc_id" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_doc_id INTEGER NOT NULL DEFAULT 0")
        if "last_ready_count" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_ready_count INTEGER NOT NULL DEFAULT 0")
        c.execute("CREATE INDEX IF NOT EXISTS idx_thematic_status ON thematic_items(batch_id,status,updated_at)")
        # Old completed checks already occurred; initialise their source version
        # once to avoid a pointless full recheck of every historic completed item.
        c.execute("""UPDATE thematic_items SET last_doc_id=COALESCE((
           SELECT MAX(d.id) FROM docs d WHERE d.reg_number=thematic_items.reg),0),
           last_ready_count=(SELECT COUNT(*) FROM docs d
             WHERE d.reg_number=thematic_items.reg
               AND COALESCE(d.local_path,'')!='' AND d.status='checked')
             WHERE status='completed' AND last_doc_id=0 AND last_ready_count=0""")
        c.commit()
    finally:c.close()
    original.tick=faster_tick
    original.WORKER_STATE["interval_seconds"]=20
    # The v342 worker resolves asyncio.sleep from its original module globals.
    # Its existing loop still waits two minutes; replace the worker's sleep in
    # v342 directly in a separate, bounded code change.
    print("THEMATIC_FAST_WORKER_READY concurrency=3 per_cycle=36 index_seconds=90",flush=True)
