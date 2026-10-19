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
from v381_audit_scheduling import background_audit
from thematic_period import index_campaign, selection_sql, joined_selection_sql

MAX_CONCURRENT=4
MAX_PER_BATCH=18
MAX_PER_CYCLE=36
RECHECK_SECONDS=90
INDEX_SECONDS=90
# The previous thematic path did not run build_checks. Revisit saved originals
# once after enabling those evidence-based rules, including completed checks.
ANALYSIS_VERSION=2

def _open(ns):
    c=original.db(ns)
    c.execute("PRAGMA busy_timeout=2500")
    return c

def _prepare(ns):
    clock=time.time()
    c=_open(ns)
    try:
        campaigns=c.execute("SELECT * FROM thematic_batches WHERE status='active' ORDER BY created_at,id").fetchall()
        # Continue after the last served campaign if historical/imported
        # active campaigns exceed the normal three-campaign UI limit.
        previous=ns.get("_v381_last_thematic_batch")
        for index,camp in enumerate(campaigns):
            if camp["id"]==previous:
                campaigns=campaigns[index+1:]+campaigns[:index+1]
                break
        if clock-original.WORKER_STATE.get("index_at",0)>INDEX_SECONDS:
            for camp in campaigns:
                index_campaign(c,camp,original.customer_form,original.now())
            c.commit()
            original.WORKER_STATE["index_at"]=clock
        by_campaign=[]
        for camp in campaigns:
            selected=[]
            scope,scope_args=selection_sql(c,camp)
            # Fast indexed subqueries avoid scanning or re-reading original files
            # that have not changed since the last successful legal evaluation.
            rows=c.execute("""
             SELECT t.reg,t.status,t.updated_at,t.last_doc_id,t.last_ready_count,t.last_analysis_version,
              (SELECT COALESCE(MAX(id),0) FROM docs d WHERE d.reg_number=t.reg) doc_id,
              (SELECT COUNT(*) FROM docs d
                 WHERE d.reg_number=t.reg AND COALESCE(d.local_path,'')!=''
                   AND d.status='checked') ready_count
             FROM thematic_items t JOIN purchases p ON p.reg_number=t.reg WHERE t.batch_id=?
               AND t.status IN ('queued','waiting_originals','waiting_engine','retry','completed')
               AND (t.status!='completed'
                    OR t.last_analysis_version<? AND EXISTS(
                        SELECT 1 FROM docs d WHERE d.reg_number=t.reg
                         AND COALESCE(d.local_path,'')!='' AND d.status='checked')
                    OR t.last_doc_id<(SELECT COALESCE(MAX(id),0) FROM docs d WHERE d.reg_number=t.reg)
                    OR t.last_ready_count<(SELECT COUNT(*) FROM docs d
                       WHERE d.reg_number=t.reg AND COALESCE(d.local_path,'')!='' AND d.status='checked'))
               AND """+scope+"""
             ORDER BY CASE WHEN EXISTS(
                 SELECT 1 FROM docs d WHERE d.reg_number=t.reg
                  AND COALESCE(d.local_path,'')!='' AND d.status='checked'
               ) THEN 0 ELSE 1 END,
               CASE WHEN t.status='queued' THEN 0
                    WHEN t.status='waiting_originals' THEN 1
                    WHEN t.status='retry' THEN 2 ELSE 3 END,
               t.updated_at ASC LIMIT 3000
            """,(camp["id"],ANALYSIS_VERSION,*scope_args)).fetchall()
            count=0
            for row in rows:
                if count>=MAX_PER_BATCH:break
                doc_id=int(row["doc_id"] or 0)
                ready=int(row["ready_count"] or 0)
                new_evidence=doc_id>int(row["last_doc_id"] or 0) or ready>int(row["last_ready_count"] or 0)
                state=row["status"]
                updated_rules=state=="completed" and ready>0 and int(row["last_analysis_version"] or 0)<ANALYSIS_VERSION
                if state=="completed" and not new_evidence and not updated_rules:
                    continue
                if state in ("waiting_originals","waiting_engine","retry") and not new_evidence:
                    try:age=clock-datetime.fromisoformat(row["updated_at"]).timestamp()
                    except (ValueError,TypeError):age=RECHECK_SECONDS
                    if age<RECHECK_SECONDS:continue
                selected.append({
                  "batch":camp["id"],"reg":row["reg"],
                  "categories":json.loads(camp["categories"]),
                  "ready":ready,"doc_id":doc_id,"analysis_version":ANALYSIS_VERSION})
                count+=1
            by_campaign.append(selected)
        # A third active campaign must not wait forever behind 18 + 18 jobs
        # from the first two. Interleave bounded selections before dispatch.
        selected=[]
        for offset in range(MAX_PER_BATCH):
            for queue in by_campaign:
                if offset<len(queue):selected.append(queue[offset])
                if len(selected)>=MAX_PER_CYCLE:
                    ns["_v381_last_thematic_batch"]=selected[-1]["batch"]
                    return selected
        if selected:ns["_v381_last_thematic_batch"]=selected[-1]["batch"]
        return selected
    finally:c.close()

async def _one(ns,item,sem):
    async with sem:
        if item["ready"]<1:
            return (item,"waiting_originals",[],"Ожидаются доступные оригиналы ЕИС")
        try:
            with background_audit():
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
    saved_events=[]
    try:
        # Keep the scope/active checks and saved result in one write transaction.
        # EIS corrections and stop/delete actions may otherwise commit between
        # the SELECT guards and the first INSERT below.
        c.execute("BEGIN IMMEDIATE")
        for item,state,findings,detail in results:
            batch,reg=item["batch"],item["reg"]
            # Do not resurrect an already deleted or stopped thematic campaign.
            campaign=c.execute("SELECT * FROM thematic_batches WHERE id=? AND status='active'",(batch,)).fetchone()
            if not campaign:
                continue
            scope,scope_args=selection_sql(c,campaign)
            # A later EIS metadata correction must not persist a new result
            # outside the chosen period while a slow original was being read.
            if not c.execute("SELECT 1 FROM purchases p WHERE p.reg_number=? AND "+scope,
                             (reg,*scope_args)).fetchone():
                c.execute("UPDATE thematic_items SET status='outside_period',"
                          "detail='Дата размещения закупки не входит в выбранный период',updated_at=? "
                          "WHERE batch_id=? AND reg=?",(original.now(),batch,reg))
                continue
            if state=="completed":
                from v333_dedup_ui import collapse,_same_finding
                from v352_dashboard_truth import collapse_legal_events
                previous=[dict(r) for r in c.execute(
                    """SELECT title,law,evidence,category,code,document,source_url,law_url
                       FROM thematic_findings WHERE batch_id=? AND reg=?""",
                    (batch,reg))]
                previous_count=None
                for f in collapse(findings):
                    if any(_same_finding(old,f,"title") for old in previous):
                        continue
                    inserted=c.execute("""INSERT OR IGNORE INTO thematic_findings
                      (batch_id,reg,category,title,law,evidence,
                       code,document,source_url,law_url)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (batch,reg,f["category"],f["title"],f.get("law"),f.get("evidence"),
                       f.get("code") or "",f.get("document") or "",
                       f.get("source_url") or "",f.get("law_url") or ""))
                    if inserted.rowcount==1:
                        if previous_count is None:
                            previous_count=len(collapse_legal_events(previous))
                        previous.append(f)
                if previous_count is not None:
                    gained=len(collapse_legal_events(previous))-previous_count
                    if gained>0:
                        saved_events.append((batch,reg,gained,item.get("analysis_version",ANALYSIS_VERSION)))
            c.execute("""UPDATE thematic_items SET status=?,detail=?,updated_at=?,
              last_doc_id=?,last_ready_count=?,
              last_analysis_version=CASE WHEN ?='completed' THEN ? ELSE last_analysis_version END
              WHERE batch_id=? AND reg=?""",
              (state,detail,original.now(),item["doc_id"],item["ready"],state,
               item.get("analysis_version",ANALYSIS_VERSION),batch,reg))
        c.commit()
        for batch,reg,gained,version in saved_events:
            print(f"THEMATIC_EVIDENCE_SAVED reg={reg} new_unique_findings={gained} "
                  f"analysis_version={version} batch={batch}",flush=True)
    finally:c.close()

def _request_missing_originals(ns,results=()):
    """Enqueue missing thematic originals independently of the legal batch.

    v301 bridge polling is sufficient proof that the EIS job worker is active.
    The five-minute discovery collector and the v301 job bridge have distinct
    heartbeats: requiring BOTH used to strand thematic procurements.

    Bounded queueing also works when both are offline (small waiting backlog),
    so the next official browser-bridge poll has real tasks to process.
    """
    clock=time.time()
    if clock-original.WORKER_STATE.get("last_eis_dispatch",0)<30:
        return 0
    if original.storage_health()["blocked"]:
        original.WORKER_STATE["bridge_download_status"]="Нет места для заданий ЕИС; сохранённые оригиналы не удаляются"
        original.WORKER_STATE["last_eis_dispatch"]=clock
        return 0
    from v299_browser_intake import _scan_status
    from v301_search_jobs import _bridge,_enqueue,MAX_QUEUE
    conn=_open(ns)
    try:
        if not conn.execute("""SELECT 1 FROM sqlite_master WHERE
               type='table' AND name='v301_search_jobs'""").fetchone():
            original.WORKER_STATE["bridge_download_status"]="Очередь загрузчика ЕИС инициализируется"
            return 0
    finally:conn.close()
    # No network requests to zakupki.gov.ru are made by the server.
    try:live=_bridge(ns)
    except Exception:live={"active":False}
    try:monitor=_scan_status()
    except Exception:monitor={"browser_collector_active":False}
    connected=bool(live.get("active") or monitor.get("browser_collector_active"))
    conn=_open(ns)
    try:
        queued_thematic=conn.execute("""SELECT COUNT(*) FROM v301_search_jobs
          WHERE status IN ('queued','leased','deferred')
            AND parent_id LIKE 'thematic-%'""").fetchone()[0]
        queued_all=conn.execute("""SELECT COUNT(*) FROM v301_search_jobs
          WHERE status IN ('queued','leased','deferred')""").fetchone()[0]
        # Never starve explicit user checks. Queue only a small offline backlog.
        thematic_cap=20 if connected else 4
        per_cycle=6 if connected else 2
        budget=min(per_cycle,max(0,thematic_cap-queued_thematic),
                   max(0,MAX_QUEUE-queued_all-8))
        if budget<=0:
            original.WORKER_STATE["bridge_download_status"]=(
                "Задания ЕИС ожидают обработки" if queued_thematic else
                "Общая очередь загрузчика ЕИС заполнена")
            original.WORKER_STATE["last_eis_dispatch"]=clock
            return 0
        # Crucial: scan pending thematic ORIGINALS directly. Never rely on
        # whichever 36 procurements happened to be legally audited this cycle.
        cutoff=datetime.fromtimestamp(clock-1800,timezone.utc).isoformat()
        day_cutoff=datetime.fromtimestamp(clock-86400,timezone.utc).isoformat()
        scope=joined_selection_sql(conn)
        waiting=conn.execute("""SELECT t.reg,MIN(t.updated_at) first_waiting
          FROM thematic_items t JOIN thematic_batches b ON b.id=t.batch_id
          JOIN purchases p ON p.reg_number=t.reg
          WHERE b.status='active' AND t.status='waiting_originals'
            AND """+scope+"""
            AND NOT EXISTS (
              SELECT 1 FROM v301_search_jobs j
              WHERE j.kind='reg' AND j.target=t.reg
                AND (j.status IN ('queued','leased','deferred') OR j.created_at>=?))
            AND (SELECT COUNT(*) FROM v301_search_jobs j
                 WHERE j.kind='reg' AND j.target=t.reg
                   AND j.created_at>=?)<5
          GROUP BY t.reg ORDER BY first_waiting ASC LIMIT 250""",
          (cutoff,day_cutoff)).fetchall()
        ready=[]
        for item in waiting:
            if len(ready)>=budget:break
            reg=str(item["reg"])
            last=conn.execute("""SELECT status,created_at FROM v301_search_jobs
              WHERE kind='reg' AND target=?
              ORDER BY created_at DESC LIMIT 1""",(reg,)).fetchone()
            if last:
                try:
                    created=datetime.fromisoformat(last["created_at"]).timestamp()
                except (TypeError,ValueError):
                    created=clock
                # Re-attempt only after sufficient time for an EIS collector
                # to fetch and verify the files. Never hammer missing URLs.
                if clock-created<(1800 if last["status"] in ("partial","error","done") else 900):
                    continue
                daily=conn.execute("""SELECT COUNT(*) FROM v301_search_jobs
                  WHERE kind='reg' AND target=? AND created_at>=?""",
                  (reg,datetime.fromtimestamp(clock-86400,timezone.utc).isoformat())).fetchone()[0]
                if daily>=5:continue
            ready.append(reg)
    finally:conn.close()
    made=0
    for reg in ready:
        try:
            _,created=_enqueue(ns,"reg",reg,page=1,priority=7,
                               parent_id="thematic-auto",user_input=False)
            if created:made+=1
        except Exception as e:
            print("V353 THEME_EIS_QUEUE_DELAY "+type(e).__name__+
                  " "+str(e)[:120],flush=True)
            break
    original.WORKER_STATE["last_eis_dispatch"]=clock
    original.WORKER_STATE["bridge_download_status"]=(
        "Задания на оригиналы ЕИС поставлены: "+str(made) if made else
        "Задания ЕИС уже ожидают обработки или повторного запроса")
    if made:
        print("V353 THEME_EIS_QUEUED count="+str(made)+
              " bridge_active="+str(connected)+
              " pending_total="+str(queued_all)+
              " no_manual_records=True",flush=True)
    return made

async def faster_tick(ns):
    started=time.monotonic()
    if original.storage_health()["blocked"]:
        original.WORKER_STATE["last_error"]="Недостаточно места на диске Railway для сохранения проверок"
        return
    items=await asyncio.to_thread(_prepare,ns)
    original.WORKER_STATE["last_batch_size"]=len(items)
    if not items:
        # Missing originals are dispatched even when no legal recheck is due.
        try:await asyncio.to_thread(_request_missing_originals,ns)
        except Exception as exc:
            print("V353 THEME_EIS_DISPATCH_ERROR "+type(exc).__name__,flush=True)
        return
    sem=asyncio.Semaphore(MAX_CONCURRENT)
    tasks=[asyncio.create_task(_one(ns,item,sem)) for item in items]
    processed=completed=0
    first_result_ms=None
    try:
        for finished in asyncio.as_completed(tasks):
            result=await finished
            # Durable progress is visible immediately, even while another
            # procurement is still extracting a slow PDF or large archive.
            await asyncio.to_thread(_persist,ns,[result])
            processed+=1
            completed+=int(result[1]=="completed")
            if first_result_ms is None:first_result_ms=round((time.monotonic()-started)*1000,1)
            original.WORKER_STATE["processed_total"]=original.WORKER_STATE.get("processed_total",0)+1
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    try:
        await asyncio.to_thread(_request_missing_originals,ns)
    except Exception as exc:
        print("V352 THEME_EIS_DISPATCH_ERROR "+type(exc).__name__,flush=True)
    elapsed_ms=round((time.monotonic()-started)*1000,1)
    original.WORKER_STATE["last_batch_elapsed_ms"]=elapsed_ms
    print(f"V381 THEMATIC_BATCH_COMPLETED items_processed={processed} audits_completed={completed} "
          f"elapsed_ms={elapsed_ms} first_result_ms={first_result_ms}",flush=True)
    return processed

def install(ns):
    c=_open(ns)
    try:
        cols={x[1] for x in c.execute("PRAGMA table_info(thematic_items)")}
        if "last_doc_id" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_doc_id INTEGER NOT NULL DEFAULT 0")
        if "last_ready_count" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_ready_count INTEGER NOT NULL DEFAULT 0")
        if "last_analysis_version" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_analysis_version INTEGER NOT NULL DEFAULT 0")
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
    # v342 drains ready batches immediately and waits for evidence notifications
    # while idle. The fallback poll still handles external DB writers.
    print("THEMATIC_FAST_WORKER_READY concurrency=4 per_cycle=36 index_seconds=90 "
          f"analysis_version={ANALYSIS_VERSION} inclusive_publication_period=True",flush=True)
