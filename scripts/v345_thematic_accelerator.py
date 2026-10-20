"""Fast processing of thematic audit campaigns without additional EIS requests.

All source download frequency remains controlled by the official EIS browser
collector. This module processes *already saved* EIS files on the Railway server,
with short database transactions and bounded concurrent legal analyses.
"""
from __future__ import annotations
import asyncio
import audit_funnel
import json
import sqlite3
import time
import uuid
import full_recheck_runtime
from datetime import datetime,timezone
import v342_thematic_queue as original
from v381_audit_scheduling import background_audit, run_file_io, database_write_slot
from thematic_period import index_campaign, selection_sql, joined_selection_sql, contains, period_fields
from audit_source_version import source_version, semantic_source_version, SemanticSnapshotError
from legal_audit_revision import engine_signature
from v332_all_procurements_audit import (ensure_scan_cursors, scan_cursor,
    save_scan_cursor, thread_call, settle_task, receipt_readiness)

SCAN_PAGE_SIZE=128
SCAN_BUDGET_SECONDS=5.0
MAX_CAMPAIGNS_PER_CYCLE=8
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
    for attempt in range(len(DB_RETRY_DELAYS)+1):
        try:
            # A cancelled asyncio.to_thread future does not stop its SQLite
            # thread. Settle any actual commit before ending this worker cycle.
            async with database_write_slot():
                return await thread_call(operation,*args)
        except sqlite3.OperationalError as exc:
            if not original.sqlite_busy(exc) or attempt==len(DB_RETRY_DELAYS):
                raise
            print(f"THEMATIC_DB_RETRY operation={getattr(operation,'__name__',type(operation).__name__)} attempt={attempt+1}",flush=True)
            await asyncio.sleep(DB_RETRY_DELAYS[attempt])

async def _retry_file(operation,*args):
    async def gated():
        with background_audit():
            task=asyncio.create_task(run_file_io(operation,*args))
            try:return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:await settle_task(task)
                except Exception:pass
                raise
    for attempt in range(len(DB_RETRY_DELAYS)+1):
        try:return await gated()
        except sqlite3.OperationalError as exc:
            if not original.sqlite_busy(exc) or attempt==len(DB_RETRY_DELAYS):raise
            await asyncio.sleep(DB_RETRY_DELAYS[attempt])


def _index_batch(ns,batch):
    """Page raw purchases before filtering, without retaining SQLite's writer.

    LIMIT after a publication predicate only bounded matching output: sparse
    periods scanned the whole cohort under BEGIN IMMEDIATE. Materialize one
    raw keyset page, release its reader, and do all classification outside SQL.
    The writer validates the campaign, cursor and exact page source rows before
    admitting it; a stale page stays due and is retried on the next sweep.
    """
    scope="thematic-index:"+batch
    c=_open(ns)
    try:
        campaign=c.execute("SELECT * FROM thematic_batches WHERE id=? AND status='active'",(batch,)).fetchone()
        if not campaign:return False
        campaign=dict(campaign)
        cursor=scan_cursor(c,scope)
        columns={row[1] for row in c.execute("PRAGMA table_info(purchases)")}
        publication="published_at" if "published_at" in columns else "NULL AS published_at"
        select="SELECT reg_number,customer,"+publication+" FROM purchases "
        rows=[dict(row) for row in c.execute(select+
            "WHERE reg_number>? "
            "ORDER BY reg_number LIMIT ?",(cursor,SCAN_PAGE_SIZE)).fetchall()]
    finally:c.close()
    allowed=set(json.loads(campaign["legal_forms"]))
    bounds=period_fields(campaign)
    queue=[row["reg_number"] for row in rows
           if len(row["reg_number"])==11 and row["reg_number"].startswith("3")
           and contains(row["published_at"],**bounds)
           and original.customer_form(row["customer"]) in allowed]
    c=_open(ns)
    try:
        c.execute("BEGIN IMMEDIATE")
        current=c.execute("SELECT * FROM thematic_batches WHERE id=? AND status='active'",(batch,)).fetchone()
        if (not current or _campaign_identity(current)!=_campaign_identity(campaign)
                or scan_cursor(c,scope)!=cursor):
            c.rollback()
            return False
        # One bounded DB-only reread, not one query per purchase. Publication
        # and customer changes invalidate the precomputed page, so stale scope
        # never creates membership or skips a newly eligible source row.
        current_rows=[dict(row) for row in c.execute(select+
            "WHERE reg_number IN ("+",".join("?" for _ in rows)+") ORDER BY reg_number",
            [row["reg_number"] for row in rows]).fetchall()] if rows else []
        if current_rows!=rows:
            c.rollback()
            return False
        for reg in queue:
            c.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(batch_id,reg) DO UPDATE SET status='queued',detail='',"
                "updated_at=excluded.updated_at,reservation_token='' WHERE thematic_items.status='outside_period'",
                (batch,reg,original.now()))
        # Advance past excluded rows too; wrap discovers inserts behind the
        # cursor and revisits corrected metadata without deleting saved work.
        save_scan_cursor(c,scope,rows[-1]["reg_number"] if len(rows)==SCAN_PAGE_SIZE else "")
        save_scan_cursor(c,"thematic-index-at:"+batch,str(time.time()))
        c.commit()
        return True
    except BaseException:
        c.rollback()
        raise
    finally:c.close()


def _campaign_guard(c,campaign,reg,item=None):
    if not campaign or campaign["status"]!="active":return False
    if item is not None and item.get("campaign_guard")!=_campaign_identity(campaign):return False
    scope,args=selection_sql(c,campaign)
    row=c.execute("SELECT p.customer FROM purchases p WHERE p.reg_number=? AND "+scope,(reg,*args)).fetchone()
    return bool(row and original.customer_form(row[0]) in set(json.loads(campaign["legal_forms"])))


def _campaign_identity(campaign):
    # Include ownership fields when installed; do not widen an owner's campaign
    # if its options or ownership were changed while an original was being read.
    return tuple((key,campaign[key]) for key in sorted(campaign.keys())
                 if key in ("id","categories","legal_forms","date_from","date_to",
                            "owner_id","owner_user_id","user_id","owner_key","created_by"))


def _prepare(ns):
    """Bounded fair inspection followed by exact, durable queued reservations."""
    clock=time.time()
    started=time.monotonic()
    deadline=started+SCAN_BUDGET_SECONDS
    inspected=errors=0
    c=_open(ns)
    try:
        previous=scan_cursor(c,"thematic-campaigns")
        campaigns=c.execute("SELECT * FROM thematic_batches WHERE status='active' AND id>? "
            "ORDER BY id LIMIT ?",(previous,MAX_CAMPAIGNS_PER_CYCLE)).fetchall()
        if not campaigns and previous:
            campaigns=c.execute("SELECT * FROM thematic_batches WHERE status='active' "
                "ORDER BY id LIMIT ?",(MAX_CAMPAIGNS_PER_CYCLE,)).fetchall()
    finally:c.close()
    c=_open(ns)
    try:
        due=[]
        for camp in campaigns:
            stamp=scan_cursor(c,"thematic-index-at:"+camp["id"])
            try:last_index=float(stamp or 0)
            except ValueError:last_index=0
            if not original.WORKER_STATE.get("index_at") or clock-last_index>INDEX_SECONDS:
                due.append(camp["id"])
    finally:c.close()
    for batch in due:_index_batch(ns,batch)
    original.WORKER_STATE["index_at"]=clock
    c=_open(ns)
    try:
        by_campaign=[]
        versions={}
        receipts={}
        cursors=[]
        engine=engine_signature()
        last_campaign=previous
        for camp in campaigns:
            if by_campaign and time.monotonic()>=deadline:break
            last_campaign=camp["id"]
            selected=[]
            scope,args=selection_sql(c,camp)
            key="thematic:"+camp["id"]
            cursor=scan_cursor(c,key)
            rows=c.execute("""SELECT t.*,
                (SELECT COALESCE(MAX(id),0) FROM docs d WHERE d.reg_number=t.reg) doc_id,
                (SELECT COUNT(*) FROM docs d WHERE d.reg_number=t.reg
                    AND COALESCE(d.local_path,'')<>''
                    AND d.status IN ('checked','downloaded_no_text')) ready_count
                FROM thematic_items t JOIN purchases p ON p.reg_number=t.reg
                WHERE t.batch_id=? AND t.reg>? AND t.reservation_token=''
                  AND t.status IN ('queued','waiting_originals','waiting_engine','retry','completed')
                  AND t.next_retry_at<=? AND """+scope+
                " ORDER BY t.reg LIMIT ?",(camp["id"],cursor,clock,*args,SCAN_PAGE_SIZE)).fetchall()
            last=cursor
            for row in rows:
                if last!=cursor and time.monotonic()>=deadline:break
                last=row["reg"]
                inspected+=1
                if not _campaign_guard(c,camp,row["reg"]):continue
                if not c.execute("SELECT 1 FROM thematic_items t WHERE t.batch_id=? AND t.reg=?"+
                    full_recheck_runtime.available_sql(c,"t.reg"),(camp["id"],row["reg"])).fetchone():continue
                ready=int(row["ready_count"] or 0)
                state=row["status"]
                try:
                    if row["reg"] not in receipts:receipts[row["reg"]]=receipt_readiness(row["reg"])
                    has_originals,source_error=receipts[row["reg"]]
                except (SemanticSnapshotError,OSError) as exc:
                    errors+=1
                    print(f"THEMATIC_RECEIPT_RETRY batch={camp['id']} reg={row['reg']} type={type(exc).__name__}",flush=True)
                    continue
                if state=="completed" and not ready and not has_originals and not source_error:continue
                # Preserve old waiting/retry deadlines on first migration too.
                if state in ("waiting_originals","waiting_engine","retry"):
                    try:age=clock-datetime.fromisoformat(row["updated_at"]).timestamp()
                    except (ValueError,TypeError):age=RECHECK_SECONDS
                    delay=min(MAX_RETRY_SECONDS,RECHECK_SECONDS*2**min(5,max(0,int(row["retry_attempts"] or 0)-1)))
                    if age<delay:continue
                version=None
                if (ready or has_originals) and not source_error:
                    try:
                        if row["reg"] not in versions:
                            versions[row["reg"]]=semantic_source_version(c,row["reg"])
                        version=versions[row["reg"]]
                    except (SemanticSnapshotError,OSError) as exc:
                        errors+=1
                        print(f"THEMATIC_SNAPSHOT_RETRY batch={camp['id']} reg={row['reg']} type={type(exc).__name__} "
                              f"reason={exc.diagnostic_code if isinstance(exc,SemanticSnapshotError) else 'source_snapshot_io_error'}",flush=True)
                        continue
                if state=="completed" and version==row["last_semantic_version"] and engine==row["last_engine_signature"]:
                    continue
                selected.append({"batch":camp["id"],"reg":row["reg"],
                    "categories":json.loads(camp["categories"]),"ready":ready,
                    "doc_id":int(row["doc_id"] or 0),"analysis_version":ANALYSIS_VERSION,
                    "originals_ready":has_originals,"source_error":source_error,
                    "campaign_guard":_campaign_identity(camp),"before":dict(row)})
                if len(selected)>=MAX_PER_BATCH:break
            at_end=not rows or (len(rows)<SCAN_PAGE_SIZE and last==rows[-1]["reg"])
            cursors.append((key,cursor,"" if at_end else last))
            by_campaign.append(selected)
        candidates=[]
        for offset in range(MAX_PER_BATCH):
            for queue in by_campaign:
                if offset<len(queue):candidates.append(queue[offset])
                if len(candidates)>=MAX_PER_CYCLE:break
            if len(candidates)>=MAX_PER_CYCLE:break
        c.execute("BEGIN IMMEDIATE")
        selected=[]
        reserved=c.execute("SELECT COUNT(*) FROM thematic_items t JOIN thematic_batches b ON b.id=t.batch_id "
            "WHERE b.status='active' AND t.reservation_token<>'' AND t.status IN ('queued','running')").fetchone()[0]
        per_batch={row[0]:row[1] for row in c.execute("SELECT batch_id,COUNT(*) FROM thematic_items "
            "WHERE reservation_token<>'' AND status IN ('queued','running') GROUP BY batch_id")}
        for item in candidates:
            if reserved>=MAX_PER_CYCLE:break
            if per_batch.get(item["batch"],0)>=MAX_PER_BATCH:continue
            campaign=c.execute("SELECT * FROM thematic_batches WHERE id=?",(item["batch"],)).fetchone()
            if not _campaign_guard(c,campaign,item["reg"],item):continue
            if not c.execute("SELECT 1 FROM thematic_items t WHERE t.batch_id=? AND t.reg=?"+
                full_recheck_runtime.available_sql(c,"t.reg"),(item["batch"],item["reg"])).fetchone():continue
            row=item.pop("before")
            token=uuid.uuid4().hex
            result=c.execute("""UPDATE thematic_items SET status='queued',reservation_token=?,
                detail='Назначена повторная юридическая проверка',updated_at=?
                WHERE batch_id=? AND reg=? AND status=? AND updated_at IS ?
                  AND reservation_token='' AND next_retry_at=? AND retry_attempts=?
                  AND last_semantic_version=? AND last_engine_signature=?""",
                (token,original.now(),item["batch"],item["reg"],row["status"],row["updated_at"],
                 row["next_retry_at"],row["retry_attempts"],row["last_semantic_version"],row["last_engine_signature"]))
            if result.rowcount:
                item["reservation_token"]=token
                selected.append(item)
                reserved+=1
                per_batch[item["batch"]]=per_batch.get(item["batch"],0)+1
        for key,old,new in cursors:
            if scan_cursor(c,key)==old:save_scan_cursor(c,key,new)
        # Persist campaign rotation even if all inspected items were current.
        if campaigns and scan_cursor(c,"thematic-campaigns")==previous:
            save_scan_cursor(c,"thematic-campaigns",last_campaign)
        c.commit()
        print(f"THEMATIC_RECHECK_SWEEP campaigns={len(by_campaign)} inspected={inspected} "
              f"reserved={len(selected)} snapshot_errors={errors} elapsed_seconds={time.monotonic()-started:.3f}",flush=True)
        return selected
    except BaseException:
        if c.in_transaction:c.rollback()
        raise
    finally:c.close()


def _start_item(ns,item):
    c=_open(ns)
    try:
        c.execute("BEGIN IMMEDIATE")
        campaign=c.execute("SELECT * FROM thematic_batches WHERE id=?",(item["batch"],)).fetchone()
        if not _campaign_guard(c,campaign,item["reg"],item):
            c.commit()
            return False
        result=c.execute("UPDATE thematic_items SET status='running',updated_at=? "
            "WHERE batch_id=? AND reg=? AND status='queued' AND reservation_token=?",
            (original.now(),item["batch"],item["reg"],item["reservation_token"]))
        c.commit()
        return bool(result.rowcount)
    except BaseException:
        c.rollback()
        raise
    finally:c.close()


class OriginalCatalogueError(RuntimeError):pass


def _watermark(ns,reg):
    c=_open(ns)
    try:
        _,source_error=receipt_readiness(reg)
        if source_error:raise OriginalCatalogueError(source_error)
        return semantic_source_version(c,reg),engine_signature()
    finally:c.close()


def _release_items(ns,items,failed=False):
    """Return only this cycle's still-owned work after cancellation/save error."""
    c=_open(ns)
    try:
        for item in items:
            c.execute("""UPDATE thematic_items SET status='queued',reservation_token='',
                next_retry_at=MAX(next_retry_at,?),updated_at=?
                WHERE batch_id=? AND reg=? AND status IN ('queued','running')
                AND reservation_token=? AND EXISTS(SELECT 1 FROM thematic_batches b
                  WHERE b.id=thematic_items.batch_id AND b.status='active')""",
                (time.time()+RECHECK_SECONDS if failed else 0,original.now(),
                 item["batch"],item["reg"],item["reservation_token"]))
        c.commit()
    finally:c.close()

def _require_item_reservation(c,item):
    row=c.execute("SELECT 1 FROM thematic_items WHERE batch_id=? AND reg=? "
        "AND status='running' AND reservation_token=?",(item["batch"],item["reg"],item["reservation_token"])).fetchone()
    campaign=c.execute("SELECT * FROM thematic_batches WHERE id=?",(item["batch"],)).fetchone()
    if not row or not _campaign_guard(c,campaign,item["reg"],item):
        audit_funnel.observe("gate","analysis_reservation_changed")
        raise LookupError("Thematic audit reservation superseded")


@audit_funnel.trace("thematic","analysis",registration="item")
async def _one(ns,item,sem,pending=None):
    audit_funnel.bind(item)
    async with sem:
        try:
            if not await _retry_db(_start_item,ns,item):return item,"superseded",[],""
            if item["ready"]<1 and not item.get("originals_ready") and not item.get("source_error"):
                return (item,"waiting_originals",[],"Ожидаются доступные оригиналы ЕИС")
            try:
                version,engine=await _retry_file(_watermark,ns,item["reg"])
                item.update(source_version=version,semantic_version=version,engine_signature=engine)
                item["source_error"]=None
            except OriginalCatalogueError as exc:
                item["source_error"]=str(exc)
                for key in ("source_version","semantic_version","engine_signature"):item.pop(key,None)
            # Reuse is still a newly owned attempt: queued -> running and a
            # fresh pre-analysis source/engine snapshot are mandatory. A cached
            # result never carries the released reservation into this attempt.
            reused=_reuse_pending_result(item,pending) if pending is not None else None
            if reused is not None:
                # No analyzer ran on this attempt; retain that distinction.
                audit_funnel.observe("outcomes_thematic",reused[2])
                return reused
            from audit_write_guard import background_write_guard
            with background_audit(), background_write_guard(lambda c:_require_item_reservation(c,item)):
                # Include waiting for a physical file permit and both legal
                # engine stages. Their own file timeout begins after acquire;
                # without an outer deadline one stuck item held the cycle open.
                state,findings=await asyncio.wait_for(
                    original.independent_review(ns,item["reg"],item["categories"]),
                    timeout=ITEM_TIMEOUT_SECONDS)
            audit_funnel.observe("outcomes_thematic",findings)
            audit_funnel.bind(item)
            if item.get("source_error"):state="retry"
            description={
              "completed":"Проверено по сохранённым оригиналам ЕИС",
              "waiting_originals":"Ожидаются подтверждённые оригиналы ЕИС",
              "retry":"Часть оригиналов временно не удалось прочитать; запланирован повтор",
              "excluded_company":"Форма заказчика не соответствует выборке"
            }.get(state,"Тематическая юридическая проверка")
            if state=="retry" and item.get("source_error"):description=item["source_error"]
            return item,state,findings,description
        except asyncio.TimeoutError:
            audit_funnel.observe("gate","analysis_timeout")
            for key in ("source_version","semantic_version","engine_signature"):item.pop(key,None)
            # Extraction threads retain their real physical permits until they
            # exit. This cancelled read-only review cannot persist a late result.
            print(f"THEMATIC_REVIEW_TIMEOUT batch={item['batch']} reg={item['reg']} "
                  f"timeout_seconds={ITEM_TIMEOUT_SECONDS}",flush=True)
            return item,"retry",[],"Превышено время проверки оригиналов; запланирован повтор"
        except Exception as exc:
            audit_funnel.observe("gate","analysis_failed")
            audit_funnel.observe("analysis_exception",exc)
            for key in ("source_version","semantic_version","engine_signature"):item.pop(key,None)
            return item,"retry",[],exc.diagnostic_code if isinstance(exc,SemanticSnapshotError) else type(exc).__name__


def _pending_results(ns):
    """Keep only a bounded, short-lived set of finished but uncommitted reviews."""
    pending=ns.setdefault("_thematic_pending_results",{})
    clock=time.monotonic()
    for key,entry in list(pending.items()):
        if entry["expires_at"]<=clock or entry["reuses"]>=PENDING_RESULT_REUSES:
            pending.pop(key,None)
    return pending


def _reuse_pending_result(item,pending):
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
        same=(not item.get("source_error") and not saved.get("source_error")
            and all(item.get(field) for field in ("source_version","semantic_version","engine_signature"))
            and all(saved.get(field)==item.get(field) for field in (
                "source_version","semantic_version","engine_signature","doc_id","ready",
                "originals_ready","analysis_version","categories","campaign_guard")))
        if same:
            entry["reuses"]+=1
            print(f"THEMATIC_RESULT_REUSED batch={item['batch']} reg={item['reg']} "
                  f"attempt={entry['reuses']}",flush=True)
            return (item,*entry["result"][1:])
        pending.pop(key,None)
    return None


async def _review_or_reuse(ns,item,sem,pending):
    return await _one(ns,item,sem,pending)


def _retain_pending_result(pending,result):
    # Diagnostic metadata must not change PR72 byte-limit admission or eviction.
    # Reused attempts get a fresh trace and explicitly unobserved analysis.
    item=dict(result[0])
    item.pop("_audit_funnel",None)
    result=(item,*result[1:])
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


def _plan_thematic_results(results,snapshots,diagnostic_events=None):
    from v333_dedup_ui import _same_finding
    from v348_cross_document_legal import local_event_refines
    previous_by_key={key:list(rows) for key,rows in snapshots.items()}
    planned=[]
    for item,state,findings,detail in results:
        diagnostic=audit_funnel.pending(diagnostic_events,item,findings,state)
        previous=previous_by_key[(item["batch"],item["reg"])]
        before=list(previous)
        inserted=[]
        if state in ("completed","retry"):
            # All pairwise documentary/regex comparisons happen before the
            # sole SQLite writer is reserved. A full archive can yield many
            # checks without blocking other audits or incoming EIS reports.
            unique_keys={(f.get("category"),f.get("title"),f.get("evidence")) for f in previous
                         if all(f.get(k) is not None for k in ("category","title","evidence"))}
            for f in findings:
                if any(not local_event_refines(f,old) and _same_finding(old,f,"title")
                       for old in previous):
                    audit_funnel.increment(diagnostic,"reasons","duplicate")
                    continue
                key=(f["category"],f["title"],f.get("evidence"))
                # Match the table's historical UNIQUE constraint too. SQLite
                # allows repeated NULLs; all non-NULL keys are binary strings.
                if all(value is not None for value in key) and key in unique_keys:
                    audit_funnel.increment(diagnostic,"reasons","duplicate")
                    continue
                inserted.append(f)
                previous.append(f)
                if all(value is not None for value in key):unique_keys.add(key)
        planned.append((item,state,inserted,detail,before,list(previous),diagnostic))
    return planned


def _persist(ns,results):
    from v333_dedup_ui import collapse
    from v352_dashboard_truth import collapse_legal_events
    # Normalize incoming findings once, without opening SQLite. Comparisons
    # with existing evidence are planned from read snapshots below.
    results=[(item,state,collapse(findings) if state in ("completed","retry") else findings,detail)
             for item,state,findings,detail in results]
    c=_open(ns)
    saved_events=[]
    diagnostic_events=[]
    applied=0
    try:
        for attempt in range(4):
            snapshots={(item["batch"],item["reg"]):_thematic_result_snapshot(c,item["batch"],item["reg"])
                       for item,_,_,_ in results}
            # Each snapshot rebase owns a fresh summary; stale plans are silent.
            diagnostic_events=[]
            planned=_plan_thematic_results(results,snapshots,diagnostic_events)
            c.execute("BEGIN IMMEDIATE")
            # Rebase the legal comparisons if a concurrent save changed the
            # same result. Unrelated intake writes do not invalidate this work.
            if any(_thematic_result_snapshot(c,*key)!=rows for key,rows in snapshots.items()):
                c.rollback()
                continue
            break
        else:
            raise sqlite3.OperationalError("database is locked: thematic results changed during save")
        for item,state,findings,detail,before,after,diagnostic in planned:
            batch,reg=item["batch"],item["reg"]
            if state=="superseded":
                audit_funnel.increment(diagnostic,"reasons","reservation_changed")
                continue
            # Do not resurrect an already deleted or stopped thematic campaign.
            campaign=c.execute("SELECT * FROM thematic_batches WHERE id=? AND status='active'",(batch,)).fetchone()
            if not campaign:
                audit_funnel.increment(diagnostic,"reasons","campaign_inactive")
                continue
            row=c.execute("SELECT status,reservation_token,retry_attempts FROM thematic_items "
                "WHERE batch_id=? AND reg=?",(batch,reg)).fetchone()
            if not row:
                audit_funnel.increment(diagnostic,"reasons","reservation_changed")
                continue
            if item.get("reservation_token"):
                if (row["status"]!="running" or row["reservation_token"]!=item["reservation_token"]
                    or item.get("campaign_guard")!=_campaign_identity(campaign)):
                    audit_funnel.increment(diagnostic,"reasons","reservation_changed")
                    continue
            elif row["reservation_token"]:
                # Legacy direct callers cannot mutate a durably owned attempt.
                audit_funnel.increment(diagnostic,"reasons","reservation_changed")
                continue
            scope,scope_args=selection_sql(c,campaign)
            # A later EIS metadata correction must not persist a new result
            # outside the chosen period while a slow original was being read.
            if not c.execute("SELECT 1 FROM purchases p WHERE p.reg_number=? AND "+scope,
                             (reg,*scope_args)).fetchone():
                audit_funnel.increment(diagnostic,"reasons","outside_period")
                c.execute("UPDATE thematic_items SET status='outside_period',reservation_token='',"
                          "detail='Дата размещения закупки не входит в выбранный период',updated_at=? "
                          "WHERE batch_id=? AND reg=?",(original.now(),batch,reg))
                continue
            customer=c.execute("SELECT customer FROM purchases WHERE reg_number=?",(reg,)).fetchone()
            if not customer or original.customer_form(customer[0]) not in set(json.loads(campaign["legal_forms"])):
                audit_funnel.increment(diagnostic,"reasons","company_scope_changed")
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
                audit_funnel.increment(diagnostic,"counts","persisted_confirmed",added)
                if added:
                    saved_events.append((batch,reg,before,after,item.get("analysis_version",ANALYSIS_VERSION)))
            attempts=(int(row["retry_attempts"])+1) if state=="retry" else 0
            delay=min(MAX_RETRY_SECONDS,RECHECK_SECONDS*2**min(5,max(0,attempts-1)))
            retry_at=time.time()+delay if state in ("retry","waiting_originals","waiting_engine") else 0
            # Source and engine are the actual pre-analysis snapshot; never take
            # a fresh post-analysis snapshot that could swallow an arriving file.
            c.execute("""UPDATE thematic_items SET status=?,detail=?,updated_at=?,
              last_doc_id=?,last_ready_count=?,
              last_analysis_version=CASE WHEN ?='completed' THEN ? ELSE last_analysis_version END,
              retry_attempts=?,next_retry_at=?,reservation_token='',
              last_source_version=COALESCE(?,last_source_version),
              last_semantic_version=COALESCE(?,last_semantic_version),
              last_engine_signature=COALESCE(?,last_engine_signature)
              WHERE batch_id=? AND reg=?""",
              (state,detail,original.now(),item["doc_id"],item["ready"],state,
               item.get("analysis_version",ANALYSIS_VERSION),attempts,retry_at,
               item.get("source_version"),item.get("semantic_version"),item.get("engine_signature"),batch,reg))
            applied+=1
            audit_funnel.applied(diagnostic)
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:c.close()
    audit_funnel.emit_committed(diagnostic_events,planned)
    # Exact before/after snapshots keep diagnostic counting outside the writer.
    # Neither grouping nor a broken log sink can invalidate committed evidence.
    for batch,reg,before,after,version in saved_events:
        try:
            gained=len(collapse_legal_events(after))-len(collapse_legal_events(before))
            if gained>0:
                print(f"THEMATIC_EVIDENCE_SAVED reg={reg} new_unique_findings={gained} "
                      f"analysis_version={version} batch={batch}",flush=True)
        except Exception as exc:
            try:
                print(f"THEMATIC_DIAGNOSTIC_FAILED batch={batch} reg={reg} "
                      f"committed=True type={type(exc).__name__}",flush=True)
            except Exception:
                pass
    return applied

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

async def _release_eventually(ns,items,failed=False):
    """Busy cleanup cannot silently strand durable reservations until restart."""
    async def release():
        attempt=0
        while True:
            try:return await _retry_db(_release_items,ns,items,failed)
            except sqlite3.OperationalError as exc:
                if not original.sqlite_busy(exc):raise
                attempt+=1
                original.WORKER_STATE["last_error"]="Temporary database lock releasing audit reservations"
                print(f"THEMATIC_RELEASE_RETRY attempts={attempt} items={len(items)}",flush=True)
                await asyncio.sleep(min(30,2**min(attempt-1,5)))
    task=asyncio.create_task(release())
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        await settle_task(task)
        raise


async def _prepare_safely(ns):
    task=asyncio.create_task(_retry_file(_prepare,ns))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:items=await settle_task(task)
        except Exception:items=[]
        if items:
            cleanup=asyncio.create_task(_release_eventually(ns,items))
            await settle_task(cleanup)
        raise

async def faster_tick(ns):
    # Only one dispatch cycle per namespace/event loop; overlapping evidence
    # wakes must not multiply the four legal-analysis slots.
    loop=asyncio.get_running_loop()
    stored=ns.get("_v345_cycle_lock")
    if stored is None or stored[0] is not loop:
        stored=(loop,asyncio.Lock())
        ns["_v345_cycle_lock"]=stored
    async with stored[1]:
        return await _faster_tick(ns)


async def _faster_tick(ns):
    started=time.monotonic()
    pending=_pending_results(ns)
    if original.storage_health()["blocked"]:
        original.WORKER_STATE["last_error"]="Недостаточно места на диске Railway для сохранения проверок"
        return
    items=await _prepare_safely(ns)
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
                applied=await _retry_db(_persist,ns,[result])
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
                await _release_eventually(ns,[item],True)
                continue
            pending.pop((result[0]["batch"],result[0]["reg"]),None)
            if result[1]=="superseded" or applied==0:continue
            processed+=1
            completed+=int(result[1]=="completed")
            if first_result_ms is None:first_result_ms=round((time.monotonic()-started)*1000,1)
            original.WORKER_STATE["processed_total"]=original.WORKER_STATE.get("processed_total",0)+1
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await _release_eventually(ns,items)
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
        for name,definition in (("last_semantic_version","TEXT NOT NULL DEFAULT ''"),
                                ("last_engine_signature","TEXT NOT NULL DEFAULT ''"),
                                ("reservation_token","TEXT NOT NULL DEFAULT ''"),
                                ("next_retry_at","REAL NOT NULL DEFAULT 0")):
            if name not in cols:
                c.execute("ALTER TABLE thematic_items ADD COLUMN "+name+" "+definition)
        ensure_scan_cursors(c)
        # A startup alone never stamps unknown historical reviews as current.
        # Recover interrupted reservations; saved results and retry deadlines stay.
        c.execute("""UPDATE thematic_items SET status='queued',reservation_token=''
            WHERE (status='running' OR (status='queued' AND reservation_token<>''))
            AND EXISTS(SELECT 1 FROM thematic_batches b WHERE b.id=thematic_items.batch_id
                       AND b.status='active')""")
        c.commit()
        cohort=c.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0]
        items=c.execute("SELECT COUNT(*) FROM thematic_items t JOIN thematic_batches b ON b.id=t.batch_id "
            "WHERE b.status='active'").fetchone()[0]
        print(f"THEMATIC_RECHECK_COHORT active_campaigns={cohort} active_items={items} semantics=bounded_v1",flush=True)
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

