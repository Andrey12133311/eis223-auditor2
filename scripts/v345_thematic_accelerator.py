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
from audit_source_version import original_sources, source_version

MAX_CONCURRENT=4
MAX_PER_BATCH=18
MAX_PER_CYCLE=36
RECHECK_SECONDS=90
INDEX_SECONDS=90
# Establish a source snapshot once for historical completed reviews. Prior
# findings remain intact; only newly evidenced legal episodes are appended.
ANALYSIS_VERSION=5
DB_RETRY_DELAYS=(.15,.4)
MAX_RETRY_SECONDS=1800
ITEM_TIMEOUT_SECONDS=600
PENDING_RESULT_LIMIT=MAX_PER_CYCLE
PENDING_RESULT_BYTES=4*1024*1024
PENDING_RESULT_SECONDS=600
PENDING_RESULT_REUSES=3

def _open(ns):
    c=original.db(ns)
    c.execute("PRAGMA busy_timeout=2500")
    return c

async def _retry_db(operation,*args):
    """Retry a complete short operation after its failed connection is closed."""
    from v332_all_procurements_audit import thread_call
    for attempt in range(len(DB_RETRY_DELAYS)+1):
        try:
            # A cancelled asyncio.to_thread future does not stop its SQLite
            # thread. Settle any actual commit before ending this worker cycle.
            return await thread_call(operation,*args)
        except sqlite3.OperationalError as exc:
            if not original.sqlite_busy(exc) or attempt==len(DB_RETRY_DELAYS):
                raise
            print(f"THEMATIC_DB_RETRY operation={getattr(operation,'__name__',type(operation).__name__)} attempt={attempt+1}",flush=True)
            await asyncio.sleep(DB_RETRY_DELAYS[attempt])

def _index_batch(ns,batch):
    c=_open(ns)
    try:
        # Serialize before reading a scope that is about to be written. Commit
        # each campaign independently, rather than holding all campaign writes
        # while reading source versions or running legal analysis.
        c.execute("BEGIN IMMEDIATE")
        campaign=c.execute("SELECT * FROM thematic_batches WHERE id=? AND status='active'",(batch,)).fetchone()
        if campaign:
            index_campaign(c,campaign,original.customer_form,original.now())
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:c.close()

def _prepare(ns):
    clock=time.time()
    c=_open(ns)
    try:
        campaigns=c.execute("SELECT * FROM thematic_batches WHERE status='active' ORDER BY created_at,id").fetchall()
    finally:c.close()
    # Continue after the last served campaign if historical/imported
    # active campaigns exceed the normal three-campaign UI limit.
    previous=ns.get("_v381_last_thematic_batch")
    for index,camp in enumerate(campaigns):
        if camp["id"]==previous:
            campaigns=campaigns[index+1:]+campaigns[:index+1]
            break
    if clock-original.WORKER_STATE.get("index_at",0)>INDEX_SECONDS:
        for camp in campaigns:
            _index_batch(ns,camp["id"])
        original.WORKER_STATE["index_at"]=clock
    c=_open(ns)
    try:
        by_campaign=[]
        versions={}
        original_readiness={}
        source_errors={}
        for camp in campaigns:
            selected=[]
            scope,scope_args=selection_sql(c,camp)
            # MAX(id) alone cannot see a repaired file or a new metadata/text
            # revision in the same docs row. Finish the read before stat'ing
            # files; no write transaction is held while checking source versions.
            rows=c.execute("""
             SELECT t.reg,t.status,t.updated_at,t.last_doc_id,t.last_ready_count,t.last_analysis_version,
              t.last_source_version,t.retry_attempts,
              (SELECT COALESCE(MAX(id),0) FROM docs d WHERE d.reg_number=t.reg) doc_id,
              (SELECT COUNT(*) FROM docs d
                 WHERE d.reg_number=t.reg AND COALESCE(d.local_path,'')!=''
                   AND d.status IN ('checked','downloaded_no_text')) ready_count
             FROM thematic_items t JOIN purchases p ON p.reg_number=t.reg WHERE t.batch_id=?
               AND t.status IN ('queued','waiting_originals','waiting_engine','retry','completed')
               AND """+scope+"""
             ORDER BY CASE WHEN EXISTS(
                 SELECT 1 FROM docs d WHERE d.reg_number=t.reg
                  AND COALESCE(d.local_path,'')!='' AND d.status IN ('checked','downloaded_no_text')
               ) THEN 0 ELSE 1 END,
               CASE WHEN t.status='queued' THEN 0
                    WHEN t.status='waiting_originals' THEN 1
                    WHEN t.status='retry' THEN 2 ELSE 3 END,
               t.updated_at ASC,t.reg
            """,(camp["id"],*scope_args)).fetchall()
            count=0
            for row in rows:
                if count>=MAX_PER_BATCH:break
                doc_id=int(row["doc_id"] or 0)
                ready=int(row["ready_count"] or 0)
                reg=row["reg"]
                if reg not in versions:
                    originals=original_sources(reg)
                    original_readiness[reg]=any(source["available"] for source in originals)
                    source_errors[reg]=next((source["scan_error"] for source in originals if source.get("scan_error")),None)
                    versions[reg]=source_version(c,reg,originals=originals)
                version=versions[reg]
                has_originals=original_readiness[reg]
                source_error=source_errors[reg]
                new_evidence=(doc_id>int(row["last_doc_id"] or 0)
                              or ready>int(row["last_ready_count"] or 0)
                              or version!=row["last_source_version"])
                state=row["status"]
                # Completed receipt-only inputs must remain visible to source
                # change detection even when the incoming docs save was delayed.
                if state=="completed" and not ready and not has_originals and not source_error and doc_id<=int(row["last_doc_id"] or 0):
                    continue
                updated_rules=state=="completed" and (ready>0 or has_originals or source_error) and int(row["last_analysis_version"] or 0)<ANALYSIS_VERSION
                if state=="completed" and not new_evidence and not updated_rules:
                    continue
                if state in ("waiting_originals","waiting_engine","retry") and not new_evidence:
                    try:age=clock-datetime.fromisoformat(row["updated_at"]).timestamp()
                    except (ValueError,TypeError):age=RECHECK_SECONDS
                    delay=RECHECK_SECONDS
                    if state=="retry":
                        delay=min(MAX_RETRY_SECONDS,RECHECK_SECONDS*2**min(5,max(0,int(row["retry_attempts"] or 0)-1)))
                    if age<delay:continue
                selected.append({
                  "batch":camp["id"],"reg":row["reg"],
                  "categories":json.loads(camp["categories"]),
                  "ready":ready,"doc_id":doc_id,"analysis_version":ANALYSIS_VERSION,
                  "originals_ready":has_originals,
                  "source_error":source_error,
                  "source_version":version})
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
        if item["ready"]<1 and not item.get("originals_ready") and not item.get("source_error"):
            return (item,"waiting_originals",[],"Ожидаются доступные оригиналы ЕИС")
        try:
            with background_audit():
                # Include waiting for a physical file permit and both legal
                # engine stages. Their own file timeout begins after acquire;
                # without an outer deadline one stuck item held the cycle open.
                state,findings=await asyncio.wait_for(
                    original.independent_review(ns,item["reg"],item["categories"]),
                    timeout=ITEM_TIMEOUT_SECONDS)
            description={
              "completed":"Проверено по сохранённым оригиналам ЕИС",
              "waiting_originals":"Ожидаются подтверждённые оригиналы ЕИС",
              "retry":"Часть оригиналов временно не удалось прочитать; запланирован повтор",
              "excluded_company":"Форма заказчика не соответствует выборке"
            }.get(state,"Тематическая юридическая проверка")
            if state=="retry" and item.get("source_error"):description=item["source_error"]
            return item,state,findings,description
        except asyncio.TimeoutError:
            # Extraction threads retain their real physical permits until they
            # exit. This cancelled read-only review cannot persist a late result.
            print(f"THEMATIC_REVIEW_TIMEOUT batch={item['batch']} reg={item['reg']} "
                  f"timeout_seconds={ITEM_TIMEOUT_SECONDS}",flush=True)
            return item,"retry",[],"Превышено время проверки оригиналов; запланирован повтор"
        except Exception as exc:
            return item,"retry",[],type(exc).__name__


def _pending_results(ns):
    """Keep only a bounded, short-lived set of finished but uncommitted reviews."""
    pending=ns.setdefault("_thematic_pending_results",{})
    clock=time.monotonic()
    for key,entry in list(pending.items()):
        if entry["expires_at"]<=clock or entry["reuses"]>=PENDING_RESULT_REUSES:
            pending.pop(key,None)
    return pending


async def _review_or_reuse(ns,item,sem,pending):
    key=(item["batch"],item["reg"])
    entry=pending.get(key)
    if entry is not None and (entry["expires_at"]<=time.monotonic() or
                              entry["reuses"]>=PENDING_RESULT_REUSES):
        pending.pop(key,None)
        entry=None
    if entry is not None:
        saved=entry["result"][0]
        # A new source or rule revision needs a new legal analysis. Never mark
        # its current watermark using a result computed from older originals.
        same=bool(item.get("source_version")) and all(saved.get(field)==item.get(field)
            for field in ("source_version","doc_id","ready","analysis_version","categories"))
        if same:
            entry["reuses"]+=1
            print(f"THEMATIC_RESULT_REUSED batch={item['batch']} reg={item['reg']} "
                  f"attempt={entry['reuses']}",flush=True)
            return entry["result"]
        pending.pop(key,None)
    return await _one(ns,item,sem)


def _retain_pending_result(pending,result):
    item=result[0]
    key=(item["batch"],item["reg"])
    entry=pending.get(key)
    if entry is not None:
        # Retries cannot keep the same result alive indefinitely.
        if entry["reuses"]>=PENDING_RESULT_REUSES or entry["expires_at"]<=time.monotonic():
            pending.pop(key,None)
        return
    size=len(json.dumps(result,ensure_ascii=False).encode("utf-8"))
    if size>PENDING_RESULT_BYTES:return
    while pending and (len(pending)>=PENDING_RESULT_LIMIT or
                       sum(value["bytes"] for value in pending.values())+size>PENDING_RESULT_BYTES):
        oldest=min(pending,key=lambda key:pending[key]["expires_at"])
        pending.pop(oldest)
    pending[key]={"result":result,"bytes":size,"reuses":0,
                  "expires_at":time.monotonic()+PENDING_RESULT_SECONDS}

def _thematic_result_snapshot(c,batch,reg):
    return [dict(row) for row in c.execute("""SELECT title,law,evidence,category,
        code,document,source_url,law_url FROM thematic_findings
        WHERE batch_id=? AND reg=? ORDER BY rowid""",(batch,reg)).fetchall()]


def _plan_thematic_results(results,snapshots):
    from v333_dedup_ui import collapse,_same_finding
    from v352_dashboard_truth import collapse_legal_events
    previous_by_key={key:list(rows) for key,rows in snapshots.items()}
    planned=[]
    for item,state,findings,detail in results:
        previous=previous_by_key[(item["batch"],item["reg"])]
        inserted=[]
        previous_count=0
        if state in ("completed","retry"):
            # All pairwise documentary/regex comparisons happen before the
            # sole SQLite writer is reserved. A full archive can yield many
            # checks without blocking other audits or incoming EIS reports.
            previous_count=len(collapse_legal_events(previous))
            unique_keys={(f.get("category"),f.get("title"),f.get("evidence")) for f in previous
                         if all(f.get(k) is not None for k in ("category","title","evidence"))}
            for f in collapse(findings):
                if any(_same_finding(old,f,"title") for old in previous):continue
                key=(f["category"],f["title"],f.get("evidence"))
                # Match the table's historical UNIQUE constraint too. SQLite
                # allows repeated NULLs; all non-NULL keys are binary strings.
                if all(value is not None for value in key) and key in unique_keys:continue
                inserted.append(f)
                previous.append(f)
                if all(value is not None for value in key):unique_keys.add(key)
        gained=len(collapse_legal_events(previous))-previous_count if inserted else 0
        planned.append((item,state,inserted,detail,gained))
    return planned


def _persist(ns,results):
    c=_open(ns)
    saved_events=[]
    try:
        for attempt in range(4):
            snapshots={(item["batch"],item["reg"]):_thematic_result_snapshot(c,item["batch"],item["reg"])
                       for item,_,_,_ in results}
            planned=_plan_thematic_results(results,snapshots)
            c.execute("BEGIN IMMEDIATE")
            # Rebase the legal comparisons if a concurrent save changed the
            # same result. Unrelated intake writes do not invalidate this work.
            if any(_thematic_result_snapshot(c,*key)!=rows for key,rows in snapshots.items()):
                c.rollback()
                continue
            break
        else:
            raise sqlite3.OperationalError("database is locked: thematic results changed during save")
        for item,state,findings,detail,gained in planned:
            batch,reg=item["batch"],item["reg"]
            # Do not resurrect an already deleted or stopped thematic campaign.
            campaign=c.execute("SELECT * FROM thematic_batches WHERE id=? AND status='active'",(batch,)).fetchone()
            if not campaign:
                continue
            if not c.execute("SELECT 1 FROM thematic_items WHERE batch_id=? AND reg=?",(batch,reg)).fetchone():
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
            if state in ("completed","retry"):
                added=0
                for f in findings:
                    inserted=c.execute("""INSERT OR IGNORE INTO thematic_findings
                      (batch_id,reg,category,title,law,evidence,
                       code,document,source_url,law_url)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (batch,reg,f["category"],f["title"],f.get("law"),f.get("evidence"),
                       f.get("code") or "",f.get("document") or "",
                       f.get("source_url") or "",f.get("law_url") or ""))
                    added+=int(inserted.rowcount==1)
                if added!=len(findings):
                    raise sqlite3.OperationalError("database is locked: thematic insert changed during save")
                if gained>0:
                    saved_events.append((batch,reg,gained,item.get("analysis_version",ANALYSIS_VERSION)))
            c.execute("""UPDATE thematic_items SET status=?,detail=?,updated_at=?,
              last_doc_id=?,last_ready_count=?,
              last_analysis_version=CASE WHEN ?='completed' THEN ? ELSE last_analysis_version END,
              retry_attempts=CASE WHEN ?='retry' AND last_source_version=? THEN retry_attempts+1
                                  WHEN ?='retry' THEN 1 ELSE 0 END,
              last_source_version=?
              WHERE batch_id=? AND reg=?""",
              (state,detail,original.now(),item["doc_id"],item["ready"],state,
               item.get("analysis_version",ANALYSIS_VERSION),state,item.get("source_version",""),
               state,item.get("source_version",""),batch,reg))
        c.commit()
        for batch,reg,gained,version in saved_events:
            print(f"THEMATIC_EVIDENCE_SAVED reg={reg} new_unique_findings={gained} "
                  f"analysis_version={version} batch={batch}",flush=True)
    except BaseException:
        c.rollback()
        raise
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
    pending=_pending_results(ns)
    if original.storage_health()["blocked"]:
        original.WORKER_STATE["last_error"]="Недостаточно места на диске Railway для сохранения проверок"
        return
    items=await _retry_db(_prepare,ns)
    original.WORKER_STATE["last_batch_size"]=len(items)
    if not items:
        # Missing originals are dispatched even when no legal recheck is due.
        try:await asyncio.to_thread(_request_missing_originals,ns)
        except Exception as exc:
            print("V353 THEME_EIS_DISPATCH_ERROR "+type(exc).__name__,flush=True)
        return
    sem=asyncio.Semaphore(MAX_CONCURRENT)
    tasks=[asyncio.create_task(_review_or_reuse(ns,item,sem,pending)) for item in items]
    processed=completed=0
    first_result_ms=None
    try:
        for finished in asyncio.as_completed(tasks):
            result=await finished
            # Durable progress is visible immediately, even while another
            # procurement is still extracting a slow PDF or large archive.
            try:
                await _retry_db(_persist,ns,[result])
            except Exception as exc:
                # The transaction rolled back, including its source watermark.
                # Leave this row eligible for retry and let every other finished
                # analysis save its result instead of cancelling the whole batch.
                item=result[0]
                if isinstance(exc,sqlite3.OperationalError) and original.sqlite_busy(exc):
                    _retain_pending_result(pending,result)
                else:
                    pending.pop((item["batch"],item["reg"]),None)
                original.WORKER_STATE["last_error"]=type(exc).__name__+": "+str(exc)[:120]
                print(f"THEMATIC_ITEM_RETRY batch={item['batch']} reg={item['reg']} "
                      f"stage=persist type={type(exc).__name__} error={str(exc)[:120]!r}",flush=True)
                continue
            pending.pop((result[0]["batch"],result[0]["reg"]),None)
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
        c.execute("BEGIN IMMEDIATE")
        cols={x[1] for x in c.execute("PRAGMA table_info(thematic_items)")}
        if "last_doc_id" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_doc_id INTEGER NOT NULL DEFAULT 0")
        if "last_ready_count" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_ready_count INTEGER NOT NULL DEFAULT 0")
        if "last_analysis_version" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_analysis_version INTEGER NOT NULL DEFAULT 0")
        if "last_source_version" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN last_source_version TEXT NOT NULL DEFAULT ''")
        if "retry_attempts" not in cols:
            c.execute("ALTER TABLE thematic_items ADD COLUMN retry_attempts INTEGER NOT NULL DEFAULT 0")
        c.execute("CREATE INDEX IF NOT EXISTS idx_thematic_status ON thematic_items(batch_id,status,updated_at)")
        # Retain legacy document-watermark compatibility. The new analysis
        # revision still requires one real review before saving a source digest.
        c.execute("""UPDATE thematic_items SET last_doc_id=COALESCE((
           SELECT MAX(d.id) FROM docs d WHERE d.reg_number=thematic_items.reg),0),
           last_ready_count=(SELECT COUNT(*) FROM docs d
             WHERE d.reg_number=thematic_items.reg
               AND COALESCE(d.local_path,'')!='' AND d.status IN ('checked','downloaded_no_text'))
             WHERE status='completed' AND last_doc_id=0 AND last_ready_count=0""")
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:c.close()
    original.tick=faster_tick
    original.WORKER_STATE["interval_seconds"]=20
    # v342 drains ready batches immediately and waits for evidence notifications
    # while idle. The fallback poll still handles external DB writers.
    print("THEMATIC_FAST_WORKER_READY concurrency=4 per_cycle=36 index_seconds=90 "
          f"analysis_version={ANALYSIS_VERSION} inclusive_publication_period=True",flush=True)
