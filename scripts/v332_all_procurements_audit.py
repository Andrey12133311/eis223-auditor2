"""Durable non-destructive legal re-audit of every registered 223-FZ procurement.

* Freeze a snapshot of ALL purchase registration numbers, not EIS folders.
* Analyze each locally present and extractable document, including partial
  original sets and archive members already captured by the official pipeline.
* No remote downloads; missing originals never turn into "no violations".
* No DELETEs of original files, existing document rows, findings or checks.
* Append only new evidence-backed legal findings and review points.
* Keep persistent, observable progress after Railway restarts.
"""
from __future__ import annotations

import asyncio
import audit_funnel
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path

from audit_source_version import (source_version, semantic_source_version, SemanticSnapshotError,
                                  original_sources)
from legal_audit_revision import engine_signature, EngineSignatureError
from v295_eis_originals import (allowed_documents, saved_document, root, uid_from_url,
                               _merge_source_metadata, _review_source_version, HTTPException)
from v298_eis_only import offline_audit
from v314_eis_metadata import document_text
from v381_audit_scheduling import (audit_signals, background_audit,
                                   ordered_map, run_file_io)

CAMPAIGN="2026-10-15-main-1253-identity-and-legal-v342"
RULE_REVISION="2026-10-19-v387-legal-recall-persistence"
DASHBOARD_MAX=1253
REG=re.compile(r"^[0-9]{11,20}$")
HEX=re.compile(r"^[0-9a-f]{64}$")
FILE_EXT={".pdf",".doc",".docx",".odt",".rtf",".xlsx",".xlsm",".xls",
          ".ods",".xml",".txt",".csv"}
MAX_BYTES=25*1024*1024
MAX_DOC_ROWS=900
# Shared physical file limit lives in v381_audit_scheduling: six operations,
# with one slot reserved for foreground/manual checks.
FILE_BATCH_SIZE=12
GENERAL_WORKERS=4
RECEIPT_CLAIM_PAGE=64
SCAN_PAGE_SIZE=128
SCAN_BUDGET_SECONDS=5.0
MAX_RECHECK_ADMISSIONS=8
READY_HIGH_WATER=16
RETRY_BASE_SECONDS=1.0
RETRY_MAX_SECONDS=30.0
DB_STAGE_RETRY_SECONDS=30.0
BUSY_RETRY_BASE_SECONDS=60.0
HEARTBEAT_SECONDS=60.0
QUEUE_POLL_SECONDS=30.0
DOCUMENT_RETRY_BASE_SECONDS=90.0
DOCUMENT_RETRY_MAX_SECONDS=1800.0
PRIORITY=["32616233661","32616324151","32616287360"]

def now():
    return datetime.now(timezone.utc).isoformat()

def connect(ns):
    c=ns["conn"]()
    c.execute("PRAGMA busy_timeout=12000")
    return c


def transient_sqlite(exc):
    """Retry lock contention, including extended BUSY/LOCKED error codes."""
    if not isinstance(exc,sqlite3.OperationalError):return False
    code=getattr(exc,"sqlite_errorcode",None)
    if code is not None:
        return (code & 255) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED)
    # Older Python/SQLite and persisted legacy errors have only this text.
    return locked_error(str(exc))


def locked_error(message):
    value=str(message or "").lower()
    return any(text in value for text in
               ("database is locked","database table is locked","database schema is locked"))


def ensure_source_column(c):
    columns={row[1] for row in c.execute("PRAGMA table_info(v332_audit_queue)")}
    for name,definition in (("last_source_version","TEXT NOT NULL DEFAULT ''"),
                            ("retry_count","INTEGER NOT NULL DEFAULT 0"),
                            ("next_retry_at","REAL NOT NULL DEFAULT 0"),
                            ("last_result_token","TEXT NOT NULL DEFAULT ''"),
                            ("last_counted_token","TEXT NOT NULL DEFAULT ''"),
                            ("last_semantic_version","TEXT NOT NULL DEFAULT ''"),
                            ("last_engine_signature","TEXT NOT NULL DEFAULT ''"),
                            ("reservation_token","TEXT NOT NULL DEFAULT ''"),
                            ("receipt_ready","INTEGER NOT NULL DEFAULT 0")):
        if name not in columns:
            c.execute("ALTER TABLE v332_audit_queue ADD COLUMN "+name+" "+definition)


def evidence_version(ns,reg):
    c=connect(ns)
    try:return semantic_source_version(c,reg)
    finally:c.close()


def ensure_scan_cursors(c):
    c.execute("""CREATE TABLE IF NOT EXISTS v382_audit_scan_cursors(
        scope TEXT PRIMARY KEY,cursor TEXT NOT NULL DEFAULT '')""")


def scan_cursor(c,scope):
    row=c.execute("SELECT cursor FROM v382_audit_scan_cursors WHERE scope=?",(scope,)).fetchone()
    return row[0] if row else ""


def save_scan_cursor(c,scope,value):
    c.execute("INSERT INTO v382_audit_scan_cursors(scope,cursor) VALUES(?,?) "
              "ON CONFLICT(scope) DO UPDATE SET cursor=excluded.cursor",(scope,value))


def ready_work(c):
    # Missing originals and future retries do not consume runnable capacity.
    from v342_thematic_queue import business_company
    c.create_function("eis_business_company",1,business_company,deterministic=True)
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone():return 0
    return c.execute("""SELECT COUNT(*) FROM v332_audit_queue q
        JOIN purchases p ON p.reg_number=q.reg
        WHERE q.campaign=? AND eis_business_company(p.customer)=1 AND (q.state='running' OR
          (q.state='pending' AND q.next_retry_at<=? AND (q.receipt_ready=1 OR EXISTS(
            SELECT 1 FROM docs d WHERE d.reg_number=q.reg
              AND COALESCE(d.local_path,'')<>''
              AND d.status IN ('checked','downloaded_no_text')))))""",
        (CAMPAIGN,time.time())).fetchone()[0]


def receipt_readiness(reg):
    """Bounded catalogue metadata, never proof that original bytes are valid."""
    sources=original_sources(reg)
    return (any(source.get("available") for source in sources),
            next((source["scan_error"] for source in sources if source.get("scan_error")),None))


def review_watermark(ns,reg):
    """Capture only before inspection; callers use the physical file-I/O gate."""
    c=connect(ns)
    try:
        row=c.execute("SELECT reservation_token FROM v332_audit_queue "
                      "WHERE campaign=? AND reg=? AND state='running'",(CAMPAIGN,reg)).fetchone()
        if row is None:raise LookupError("Audit reservation disappeared")
        token=row[0]
        if getattr(reg,"reservation_token",token)!=token:
            raise LookupError("Audit reservation superseded")
    finally:c.close()
    _,source_error=receipt_readiness(reg)
    if source_error:
        # A bounded catalogue error still permits a proven readable subset,
        # but never acknowledges the full source/engine as successfully read.
        return {"reservation_token":token,"source_error":source_error}
    version=evidence_version(ns,reg)
    return {"last_semantic_version":version,"last_source_version":version,
            "last_engine_signature":engine_signature(),"reservation_token":token}


async def file_stage(ns,phase,function,*args,worker=None):
    # Cancellation must settle a scanner that may commit admissions before its
    # caller can release reservations. The physical gate also retains its slot.
    async def operation():
        with background_audit():
            task=asyncio.create_task(run_file_io(function,*args))
            try:return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:await settle_task(task)
                except Exception:pass
                raise
    return await retry_operation(ns,phase,operation,worker,retry_seconds=DB_STAGE_RETRY_SECONDS)


def runtime_snapshot(ns):
    state=ns.get("_v332_runtime") or {}
    workers={key:dict(value) for key,value in state.get("workers",{}).items()}
    return {**{key:value for key,value in state.items() if key!="workers"},
            "live_workers":sum(bool(value.get("alive")) for value in workers.values()),
            "workers":workers}

def dashboard_regs(ns,include_new=False):
    """Freeze the actual automatic main-page membership, not the whole DB.

    Matches /api/v257/dashboard without user-entered filters: its EIS
    verified set, excluding ONLY explicit manually entered purchase numbers.
    Never pulls in purchases outside the list, nor manufactures records to
    reach the requested count when fewer are currently visible.
    """
    getter=ns.get("_verified_set235")
    if not callable(getter):
        raise RuntimeError("Main-page verified membership unavailable; refusing global audit")
    verified={str(v) for v in getter()}
    c=connect(ns)
    try:
        columns={row[1] for row in c.execute('PRAGMA table_info(purchases)')}
        customer_expr='p.customer' if 'customer' in columns else "'' AS customer"
        rows=c.execute("""SELECT p.reg_number,"""+customer_expr+""" FROM purchases p
            WHERE p.reg_number NOT IN
                  (SELECT reg FROM v319_manual_inputs)
            ORDER BY p.last_seen DESC,p.reg_number DESC""").fetchall()
    finally:c.close()
    from v342_thematic_queue import business_company
    selected=[str(row[0]) for row in rows if str(row[0]) in verified and business_company(row[1])]
    return selected if include_new else selected[:DASHBOARD_MAX]


def initialize(ns):
    c=connect(ns)
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS v332_audit_run(
                campaign TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                baseline_purchases INTEGER NOT NULL,
                baseline_checked_documents INTEGER NOT NULL,
                baseline_violations INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS v332_audit_queue(
                campaign TEXT NOT NULL,
                reg TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                documents_found INTEGER NOT NULL DEFAULT 0,
                documents_read INTEGER NOT NULL DEFAULT 0,
                documents_unreadable INTEGER NOT NULL DEFAULT 0,
                verified_original_documents INTEGER NOT NULL DEFAULT 0,
                original_expected INTEGER NOT NULL DEFAULT 0,
                original_received INTEGER NOT NULL DEFAULT 0,
                new_findings INTEGER NOT NULL DEFAULT 0,
                new_checks INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT,
                PRIMARY KEY(campaign,reg)
            );
            CREATE INDEX IF NOT EXISTS idx_v332_queue
                ON v332_audit_queue(campaign,state);
        """)
        columns={row[1] for row in c.execute("PRAGMA table_info(v332_audit_queue)")}
        if "audit_revision" not in columns:
            c.execute("ALTER TABLE v332_audit_queue ADD COLUMN audit_revision TEXT NOT NULL DEFAULT ''")
        if "last_doc_id" not in columns:
            c.execute("ALTER TABLE v332_audit_queue ADD COLUMN last_doc_id INTEGER NOT NULL DEFAULT 0")
        ensure_source_column(c)
        ensure_scan_cursors(c)
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone():
            c.execute("CREATE INDEX IF NOT EXISTS idx_v332_docs_version ON docs(reg_number,id)")
        p=c.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
        d=c.execute("SELECT COALESCE(SUM(documents_checked),0) FROM purchases").fetchone()[0]
        v=c.execute("SELECT COALESCE(SUM(violations),0) FROM purchases").fetchone()[0]
        if not c.execute("SELECT 1 FROM v332_audit_run WHERE campaign=?",(CAMPAIGN,)).fetchone():
            c.execute("""INSERT INTO v332_audit_run
                (campaign,created_at,baseline_purchases,baseline_checked_documents,
                 baseline_violations) VALUES(?,?,?,?,?)""",
                (CAMPAIGN,now(),p,d,v))
            # Freeze only the purchases actually shown in the AUTOMATIC main
            # list. Do not run the remaining hundreds in the general database.
            main_regs=dashboard_regs(ns)
            c.executemany("""INSERT OR IGNORE INTO v332_audit_queue(campaign,reg)
                VALUES(?,?)""",((CAMPAIGN,reg) for reg in main_regs))
            print("V342 MAIN_1253_SCOPE locked="+str(len(main_regs))+
                  " max="+str(DASHBOARD_MAX)+" total_database="+str(p),
                  flush=True)
        # The first 1,253 remain preserved in this campaign. Expand the same
        # EIS-verified automatic queue when NEW eligible purchases appear;
        # manual checks and unverified notices are never admitted.
        c.commit()
        add_new_verified(ns,c)
        # Resume interrupted work, but never rerun a finished procurement just
        # because Railway redeployed a new code revision.
        c.execute("""UPDATE v332_audit_queue
            SET state='pending', reservation_token='', error='Возобновлено после перезапуска'
            WHERE campaign=? AND state='running'""",(CAMPAIGN,))
        c.commit()
        # Unknown historical semantic watermarks are caught up by the bounded
        # scanner after startup. Never seed them as successfully analysed.
    finally:c.close()
    return progress(ns)

def add_new_verified(ns,c=None,admission_limit=MAX_RECHECK_ADMISSIONS):
    """One durable membership page, preserving the original frozen cohort."""
    getter=ns.get("_verified_set235")
    if not callable(getter):raise RuntimeError("Main-page verified membership unavailable")
    verified={str(v) for v in getter()}
    from v342_thematic_queue import business_company
    owns=c is None
    if owns:c=connect(ns)
    try:
        ensure_source_column(c)
        ensure_scan_cursors(c)
        c.commit()
        scope="general-membership:"+CAMPAIGN
        cursor=scan_cursor(c,scope)
        has_docs=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone()
        ready_sql=("EXISTS(SELECT 1 FROM docs d WHERE d.reg_number=p.reg_number AND COALESCE(d.local_path,'')<>'' "
                   "AND d.status IN ('checked','downloaded_no_text'))" if has_docs else "0")
        rows=c.execute("SELECT p.reg_number,p.customer,"+ready_sql+""" ready
            FROM purchases p WHERE p.reg_number>?
            AND NOT EXISTS(SELECT 1 FROM v319_manual_inputs m WHERE m.reg=p.reg_number)
            AND NOT EXISTS(SELECT 1 FROM v332_audit_queue q WHERE q.campaign=? AND q.reg=p.reg_number)
            ORDER BY p.reg_number LIMIT ?""",(cursor,CAMPAIGN,SCAN_PAGE_SIZE)).fetchall()
        c.execute("BEGIN IMMEDIATE")
        available=max(0,READY_HIGH_WATER-ready_work(c))
        added=0
        last=cursor
        for row in rows:
            if added>=min(MAX_RECHECK_ADMISSIONS,max(0,admission_limit)):break
            last=row["reg_number"]
            if last not in verified or not business_company(row["customer"]):continue
            if row["ready"] and not available:continue
            result=c.execute("INSERT OR IGNORE INTO v332_audit_queue(campaign,reg) VALUES(?,?)",(CAMPAIGN,last))
            added+=result.rowcount
            if row["ready"]:available-=result.rowcount
        if admission_limit and scan_cursor(c,scope)==cursor:
            at_end=not rows or (len(rows)<SCAN_PAGE_SIZE and last==rows[-1]["reg_number"])
            save_scan_cursor(c,scope,"" if at_end else last)
        c.commit()
        return added
    except BaseException:
        if c.in_transaction:c.rollback()
        raise
    finally:
        if owns:c.close()


def progress(ns):
    c=connect(ns)
    try:
        run=c.execute("SELECT * FROM v332_audit_run WHERE campaign=?",(CAMPAIGN,)).fetchone()
        if run is None:return {"campaign":CAMPAIGN,"status":"not_started"}
        states={x[0]:int(x[1]) for x in c.execute(
            "SELECT state,COUNT(*) FROM v332_audit_queue WHERE campaign=? GROUP BY state",
            (CAMPAIGN,))}
        sums=c.execute("""SELECT COUNT(*),
            COALESCE(SUM(CASE WHEN state='completed' THEN documents_found ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state='completed' THEN documents_read ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state='completed' THEN verified_original_documents ELSE 0 END),0),
            COALESCE(SUM(new_findings),0),
            COALESCE(SUM(new_checks),0)
            FROM v332_audit_queue WHERE campaign=?""",(CAMPAIGN,)).fetchone()
        has_docs=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone()
        cohort=c.execute("""SELECT COUNT(*),COALESCE(SUM(last_semantic_version='' OR last_engine_signature=''),0)
            FROM v332_audit_queue q WHERE campaign=? AND state IN ('completed','failed','no_readable_documents')
            AND EXISTS(SELECT 1 FROM docs d WHERE d.reg_number=q.reg AND COALESCE(d.local_path,'')<>''
                       AND d.status IN ('checked','downloaded_no_text'))""",(CAMPAIGN,)).fetchone() if has_docs else (0,0)
        deferred=c.execute("""SELECT COUNT(*),MIN(next_retry_at) FROM v332_audit_queue
            WHERE campaign=? AND state='pending' AND next_retry_at>?""",(CAMPAIGN,time.time())).fetchone()
        return {
            "campaign":CAMPAIGN,
            "status":"finished" if not states.get("pending",0) and not states.get("running",0) else "running",
            "total_registered":int(sums[0]),
            "semantic_recheck_cohort":{"terminal_with_local_documents":int(cohort[0]),
                                       "unknown_semantic_or_engine":int(cohort[1])},
            "baseline_purchases":int(run["baseline_purchases"]),
            "baseline_checked_document_records":int(run["baseline_checked_documents"]),
            "baseline_recorded_violations":int(run["baseline_violations"]),
            "states":states,
            "documents_found_in_completed":int(sums[1]),
            "documents_read_in_completed":int(sums[2]),
            "verified_EIS_documents_in_completed":int(sums[3]),
            "new_evidence_based_findings":int(sums[4]),
            "new_legal_review_items":int(sums[5]),
            "scheduled_retries":int(deferred[0]),
            "next_retry_at":deferred[1],
            "runtime":runtime_snapshot(ns),
            "note":"The automatic EIS-verified queue expands with new eligible purchases. Available originals and changed evidence are analysed; missing documents are not declared violation-free."
        }
    finally:c.close()

class AuditReservation(str):
    """Backward-compatible reg string carrying the exact claim fencing token."""
    def __new__(cls,reg,token):
        value=super().__new__(cls,reg)
        value.reservation_token=token
        return value


# A single durable handoff lets an existing worker revisit a terminal row even
# when a legacy first-pass backlog is already above the admission high-water.
# These are scheduler hints in the existing cursor table, never result watermarks.
_RECHECK_IDENTITY=("reg","state","updated_at","last_semantic_version",
                   "last_engine_signature","reservation_token","next_retry_at",
                   "retry_count","last_result_token")
_RECHECK_MATCH=" AND ".join(key+" IS ?" for key in _RECHECK_IDENTITY)


def recheck_handoff(c):
    scope="general-recheck-handoff:"+CAMPAIGN
    raw=scan_cursor(c,scope)
    if not raw:return ""
    saved=json.loads(raw)
    if saved.get("claim_token") and not c.execute(
            "SELECT 1 FROM v332_audit_queue WHERE campaign=? AND reg=? "
            "AND state='running' AND reservation_token=?",
            (CAMPAIGN,saved["reg"],saved["claim_token"])).fetchone():
        # Completion, retry or startup recovery releases only this exact hint.
        c.execute("UPDATE v382_audit_scan_cursors SET cursor='' WHERE scope=? AND cursor=?",
                  (scope,raw))
        c.commit()
        return scan_cursor(c,scope)
    return raw


def ordinary_ready(c):
    return c.execute("""SELECT 1 FROM v332_audit_queue q JOIN purchases p ON p.reg_number=q.reg
        WHERE q.campaign=? AND q.state='pending' AND q.next_retry_at<=?
        AND eis_business_company(p.customer)=1 AND (q.receipt_ready=1 OR EXISTS(
            SELECT 1 FROM docs d WHERE d.reg_number=q.reg AND COALESCE(d.local_path,'')<>''
            AND d.status IN ('checked','downloaded_no_text'))) LIMIT 1""",
        (CAMPAIGN,time.time())).fetchone() is not None


def claim_recheck_handoff(c):
    """Freshness-check one terminal hint, then fence one existing worker claim."""
    scope="general-recheck-handoff:"+CAMPAIGN
    turn="general-recheck-turn:"+CAMPAIGN
    raw=scan_cursor(c,scope)
    if not raw:return None
    saved=json.loads(raw)
    if saved.get("claim_token"):return None
    if scan_cursor(c,turn)=="pending" and ordinary_ready(c):return None
    values=[saved[key] for key in _RECHECK_IDENTITY]
    row=c.execute("SELECT * FROM v332_audit_queue WHERE campaign=? AND "+_RECHECK_MATCH,
                  [CAMPAIGN,*values]).fetchone()
    eligible=False
    receipt_ready=False
    if row is not None:
        try:
            # Recheck after staging: sources can change or revert before a
            # worker becomes free. No hashing occurs in a writer transaction.
            receipt_ready,source_error=receipt_readiness(saved["reg"])
            ready=receipt_ready or source_error or c.execute(
                "SELECT 1 FROM docs WHERE reg_number=? AND COALESCE(local_path,'')<>'' "
                "AND status IN ('checked','downloaded_no_text') LIMIT 1",(saved["reg"],)).fetchone()
            current=None if source_error else semantic_source_version(c,saved["reg"])
            retry=saved["state"]=="failed" and locked_error(row["error"])
            eligible=bool(ready and (retry or saved["last_semantic_version"]!=current or
                                    saved["last_engine_signature"]!=engine_signature()))
            receipt_ready=bool(receipt_ready or source_error)
        except (SemanticSnapshotError,EngineSignatureError,OSError) as exc:
            print(f"GENERAL_HANDOFF_RETRY reg={saved['reg']} type={type(exc).__name__}",flush=True)
    c.execute("BEGIN IMMEDIATE")
    if scan_cursor(c,scope)!=raw or (scan_cursor(c,turn)=="pending" and ordinary_ready(c)):
        c.commit()
        return None
    save_scan_cursor(c,scope,"")
    claimed=None
    if eligible:
        token=uuid.uuid4().hex
        changed=c.execute("""UPDATE v332_audit_queue SET state='running',updated_at=?,
            reservation_token=?,receipt_ready=?,next_retry_at=0,
            last_doc_id=COALESCE((SELECT MAX(d.id) FROM docs d WHERE d.reg_number=?),0)
            WHERE campaign=? AND """+_RECHECK_MATCH+""" AND next_retry_at<=?
            AND EXISTS(SELECT 1 FROM purchases p WHERE p.reg_number=v332_audit_queue.reg
                AND eis_business_company(p.customer)=1)""",
            [now(),token,int(receipt_ready),saved["reg"],CAMPAIGN,*values,time.time()]).rowcount
        if changed:
            save_scan_cursor(c,scope,json.dumps({"reg":saved["reg"],"claim_token":token}))
            save_scan_cursor(c,turn,"pending")
            claimed=AuditReservation(saved["reg"],token)
    c.commit()
    if claimed is not None:
        print(f"GENERAL_RECHECK_CLAIM reg={claimed} direct_handoff=True",flush=True)
    return claimed


def claim_next(ns):
    """Atomically reserve an audit row; prefer already downloaded originals."""
    from v342_thematic_queue import business_company
    c=connect(ns)
    try:
        # Use exactly the same company scope as dashboard queue admission.
        # SQLite LIKE does not case-fold Cyrillic or normalize quoted names.
        c.create_function("eis_business_company",1,business_company,deterministic=True)
        c.execute("BEGIN IMMEDIATE")
        ensure_source_column(c)
        ensure_scan_cursors(c)
        c.commit()
        handoff=claim_recheck_handoff(c)
        if handoff is not None:return handoff
        ordinary_handoff=scan_cursor(c,"general-recheck-handoff:"+CAMPAIGN)
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("""SELECT q.reg FROM v332_audit_queue q
            JOIN purchases p ON p.reg_number=q.reg
            WHERE q.campaign=? AND q.state='pending'
            AND q.next_retry_at<=?
            AND EXISTS(SELECT 1 FROM docs d WHERE d.reg_number=q.reg
                AND COALESCE(d.local_path,'')<>'' AND d.status IN ('checked','downloaded_no_text'))
            AND eis_business_company(p.customer)=1
            ORDER BY CASE q.reg WHEN '32616233661' THEN 0
             WHEN '32616324151' THEN 1
             WHEN '32616287360' THEN 2 ELSE 3 END,
            q.reg LIMIT 1""",(CAMPAIGN,time.time())).fetchone()
        if not row:
            c.commit()
            # Scan receipt-only pending work outside every writer transaction.
            # A durable cursor survives restarts and a wall budget bounds cold IO.
            ensure_scan_cursors(c)
            c.commit()
            scope="general-receipts:"+CAMPAIGN
            after=scan_cursor(c,scope)
            query="""SELECT q.reg,q.updated_at,q.reservation_token,q.next_retry_at,q.receipt_ready
                FROM v332_audit_queue q JOIN purchases p ON p.reg_number=q.reg
                WHERE q.campaign=? AND q.state='pending' AND q.next_retry_at<=?
                  AND q.reg>? AND eis_business_company(p.customer)=1
                ORDER BY q.reg LIMIT ?"""
            candidates=c.execute(query,(CAMPAIGN,time.time(),after,RECEIPT_CLAIM_PAGE)).fetchall()
            last=after
            selected=None
            unavailable=[]
            deadline=time.monotonic()+SCAN_BUDGET_SECONDS
            for candidate in candidates:
                if last!=after and time.monotonic()>=deadline:break
                last=str(candidate["reg"])
                try:ready,blocked=receipt_readiness(last)
                except (OSError,SemanticSnapshotError) as exc:
                    print(f"GENERAL_RECEIPT_RETRY reg={last} type={type(exc).__name__}",flush=True)
                    continue
                if ready or blocked:
                    selected=candidate
                    break
                if candidate["receipt_ready"]:unavailable.append(candidate)
            c.execute("BEGIN IMMEDIATE")
            for candidate in unavailable:
                c.execute("UPDATE v332_audit_queue SET receipt_ready=0 WHERE campaign=? AND reg=? "
                    "AND state='pending' AND updated_at IS ? AND reservation_token=? AND next_retry_at=?",
                    (CAMPAIGN,candidate["reg"],candidate["updated_at"],candidate["reservation_token"],candidate["next_retry_at"]))
            if scan_cursor(c,scope)==after:
                at_end=not candidates or (len(candidates)<RECEIPT_CLAIM_PAGE and last==candidates[-1]["reg"])
                save_scan_cursor(c,scope,"" if at_end else last)
            claimed=None
            if selected is not None:
                reg=str(selected["reg"])
                token=uuid.uuid4().hex
                changed=c.execute("""UPDATE v332_audit_queue SET state='running',updated_at=?,
                    reservation_token=?,receipt_ready=1,next_retry_at=0,
                    last_doc_id=COALESCE((SELECT MAX(d.id) FROM docs d WHERE d.reg_number=?),0)
                    WHERE campaign=? AND reg=? AND state='pending' AND updated_at IS ?
                      AND reservation_token=? AND next_retry_at=? AND next_retry_at<=?
                      AND EXISTS(SELECT 1 FROM purchases p WHERE p.reg_number=?
                                 AND eis_business_company(p.customer)=1)""",
                    (now(),token,reg,CAMPAIGN,reg,selected["updated_at"],selected["reservation_token"],
                     selected["next_retry_at"],time.time(),reg)).rowcount
                if changed:
                    claimed=AuditReservation(reg,token)
                    save_scan_cursor(c,"general-recheck-turn:"+CAMPAIGN,"")
            if claimed is None and scan_cursor(c,"general-recheck-handoff:"+CAMPAIGN)==ordinary_handoff:
                # Cached receipt readiness can outlive a temporary read error.
                # Give ordinary work one bounded opportunity, not an infinite
                # veto over the same handoff. Do not clear a newer hint's turn.
                save_scan_cursor(c,"general-recheck-turn:"+CAMPAIGN,"")
            c.commit()
            return claimed
        reg=str(row[0])
        token=uuid.uuid4().hex
        c.execute("""UPDATE v332_audit_queue SET state='running',updated_at=?,reservation_token=?,next_retry_at=0,
                     last_doc_id=COALESCE((SELECT MAX(d.id) FROM docs d WHERE d.reg_number=?),0)
                     WHERE campaign=? AND reg=? AND state='pending'""",
                  (now(),token,reg,CAMPAIGN,reg))
        assert c.execute("SELECT changes()").fetchone()[0]==1
        save_scan_cursor(c,"general-recheck-turn:"+CAMPAIGN,"")
        c.commit()
        return AuditReservation(reg,token)
    except BaseException:
        if c.in_transaction:c.rollback()
        raise
    finally:c.close()

def refresh_changed(ns,admission_limit=MAX_RECHECK_ADMISSIONS):
    """One persistent keyset page; admit stale terminal rows with exact CAS.

    No pending/running reservation, retry deadline, legal result or old source
    watermark is modified. Hashing occurs outside every SQL transaction; the
    async service wraps this complete bounded operation in run_file_io.
    """
    c=connect(ns)
    try:
        ensure_source_column(c)
        ensure_scan_cursors(c)
        c.commit()
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone():return 0
        limit=min(MAX_RECHECK_ADMISSIONS,max(0,admission_limit))
        if not limit:return 0
        available=max(0,READY_HIGH_WATER-ready_work(c))
        handoff=recheck_handoff(c)
        if not available and handoff:return 0
        # Above high-water, scan only for one hint. Do not admit more pending
        # work, displace pending/running rows, or reserve an extra worker.
        limit=min(limit,available) if available else 1
        scope="general:"+CAMPAIGN
        cursor=scan_cursor(c,scope)
        rows=c.execute("""SELECT q.*,
            EXISTS(SELECT 1 FROM docs d WHERE d.reg_number=q.reg
                AND d.status IN ('checked','downloaded_no_text')
                AND COALESCE(d.local_path,'')<>'') ready
            FROM v332_audit_queue q JOIN purchases p ON p.reg_number=q.reg
            WHERE q.campaign=? AND q.reg>? AND eis_business_company(p.customer)=1
            AND q.state IN ('completed','failed','no_readable_documents')
            ORDER BY q.reg LIMIT ?""",(CAMPAIGN,cursor,SCAN_PAGE_SIZE)).fetchall()
        changed=[]
        inspected=errors=0
        started=time.monotonic()
        deadline=time.monotonic()+SCAN_BUDGET_SECONDS
        engine=engine_signature()
        last=cursor
        for row in rows:
            if last!=cursor and time.monotonic()>=deadline:break
            last=row["reg"]
            inspected+=1
            if not available and row["next_retry_at"]>time.time():continue
            try:
                receipt_ready,source_error=receipt_readiness(row["reg"])
                if not row["ready"] and not receipt_ready and not source_error:continue
                current=None if source_error else semantic_source_version(c,row["reg"])
            except (SemanticSnapshotError,OSError) as exc:
                errors+=1
                print(f"GENERAL_SNAPSHOT_RETRY reg={row['reg']} type={type(exc).__name__} "
                      f"reason={exc.diagnostic_code if isinstance(exc,SemanticSnapshotError) else 'source_snapshot_io_error'}",flush=True)
                # An unavailable snapshot is unknown, never a stable watermark.
                continue
            retry=row["state"]=="failed" and locked_error(row["error"])
            if not (retry or row["last_semantic_version"]!=current or
                    row["last_engine_signature"]!=engine):continue
            changed.append((row,bool(receipt_ready or source_error)))
            if len(changed)>=limit:break
        # Commit the cursor with admissions; a racing scanner can only CAS the
        # exact old row, and capacity is rechecked while holding the writer lock.
        c.execute("BEGIN IMMEDIATE")
        available=min(limit,max(0,READY_HIGH_WATER-ready_work(c)))
        count=0
        handoff_staged=bool(changed and not available and not scan_cursor(c,"general-recheck-handoff:"+CAMPAIGN))
        if handoff_staged:
            row=changed[0][0]
            save_scan_cursor(c,"general-recheck-handoff:"+CAMPAIGN,
                             json.dumps({key:row[key] for key in _RECHECK_IDENTITY}))
        for row,receipt_ready in changed:
            if count>=available:break
            result=c.execute("""UPDATE v332_audit_queue SET state='pending',
                error='Изменились правовые исходные данные или методика проверки',
                updated_at=?,reservation_token='',receipt_ready=?
                WHERE campaign=? AND reg=? AND state=? AND updated_at IS ?
                  AND last_semantic_version=? AND last_engine_signature=?
                  AND reservation_token=? AND next_retry_at=? AND retry_count=?
                  AND last_result_token=?""",
                (now(),int(receipt_ready),CAMPAIGN,row["reg"],row["state"],row["updated_at"],
                 row["last_semantic_version"],row["last_engine_signature"],
                 row["reservation_token"],row["next_retry_at"],row["retry_count"],row["last_result_token"]))
            count+=result.rowcount
        # Do not overwrite a cursor another process advanced while we hashed.
        if scan_cursor(c,scope)==cursor:
            at_end=not rows or (len(rows)<SCAN_PAGE_SIZE and last==rows[-1]["reg"])
            save_scan_cursor(c,scope,"" if at_end else last)
        c.commit()
        print(f"GENERAL_RECHECK_SWEEP inspected={inspected} admitted={count} snapshot_errors={errors} "
              f"handoff_staged={int(handoff_staged)} "
              f"elapsed_seconds={time.monotonic()-started:.3f} semantics=bounded_v1",flush=True)
        return count
    except BaseException:
        if c.in_transaction:c.rollback()
        raise
    finally:c.close()

def next_reg(ns):
    """Read-only queue peek for existing diagnostics."""
    c=connect(ns)
    try:
        row=c.execute("SELECT reg FROM v332_audit_queue WHERE campaign=? AND state='pending' ORDER BY reg LIMIT 1",(CAMPAIGN,)).fetchone()
        return str(row[0]) if row else None
    finally:c.close()


def result_token(reg,details):
    if details.get("result_token"):return str(details["result_token"])
    if "new_findings" not in details and "new_checks" not in details:return None
    # Direct maintenance calls have no worker token. Repeating the same
    # revision/source/result must still be an idempotent update.
    payload=json.dumps([reg,RULE_REVISION,details],ensure_ascii=False,sort_keys=True,default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mark(ns,reg,state,details=None):
    if state not in ("pending","running","completed","no_readable_documents","failed"):
        raise ValueError("Unknown queue status")
    details=dict(details or {})
    if getattr(reg,"reservation_token",None):details.setdefault("reservation_token",reg.reservation_token)
    keys={"documents_found","documents_read","documents_unreadable",
          "verified_original_documents","original_expected","original_received",
          "new_findings","new_checks","error","last_source_version","result_token",
          "last_semantic_version","last_engine_signature","reservation_token"}
    if set(details)-keys:raise ValueError("Unknown progress field")
    c=connect(ns)
    try:
        ensure_source_column(c)
        if state in ("completed","no_readable_documents","failed") and "last_source_version" not in details:
            # Workers supply their pre-analysis snapshot. Preserve the public
            # synchronous helper for maintenance/selftests that mark directly.
            details={**details,"last_source_version":source_version(c,reg)}
        assignments="state=?,updated_at=?"
        params=[state,now()]
        token=result_token(reg,details)
        for k,v in details.items():
            if k in ("result_token","reservation_token"):continue
            if k in ("new_findings","new_checks"):
                assignments+=","+k+"=COALESCE("+k+",0)+CASE WHEN last_counted_token=? THEN 0 ELSE ? END"
                params.append(token)
            else:assignments+=","+k+"=?"
            params.append(v)
        if state in ("completed","no_readable_documents","failed"):
            assignments+=",audit_revision=?,receipt_ready=0"
            params.append(RULE_REVISION)
        if state=="completed":
            assignments+=",retry_count=0,next_retry_at=0"
        if token:
            assignments+=",last_result_token=?,last_counted_token=?"
            params.extend([token,token])
        params.extend([CAMPAIGN,reg])
        condition=" AND last_result_token<>?" if token else ""
        if token:params.append(token)
        if details.get("reservation_token"):
            condition+=" AND state='running' AND reservation_token=?"
            params.append(details["reservation_token"])
        c.execute("UPDATE v332_audit_queue SET "+assignments+
                  " WHERE campaign=? AND reg=?"+condition,params)
        c.commit()
    finally:c.close()


def defer_retry(ns,reg,details,reason,base_seconds=None):
    """Retain partial evidence and schedule a bounded retry, without completion."""
    details=dict(details or {})
    if getattr(reg,"reservation_token",None):details.setdefault("reservation_token",reg.reservation_token)
    allowed={"documents_found","documents_read","documents_unreadable",
             "verified_original_documents","original_expected","original_received",
             "new_findings","new_checks","last_source_version",
             "last_semantic_version","last_engine_signature"}
    c=connect(ns)
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_source_column(c)
        row=c.execute("SELECT retry_count,next_retry_at,last_result_token,state,reservation_token FROM v332_audit_queue WHERE campaign=? AND reg=?",
                      (CAMPAIGN,reg)).fetchone()
        if row is None:raise LookupError("Audit reservation disappeared")
        token=result_token(reg,details)
        if (row[3]!="running" or (token and row[2]==token) or
            (details.get("reservation_token") and row[4]!=details["reservation_token"])):
            # A late retry must not overwrite a completed result or a retry
            # already saved by the operation whose reply was interrupted.
            c.commit()
            return max(0,float(row[1])-time.time())
        attempts=int(row[0])+1
        base=DOCUMENT_RETRY_BASE_SECONDS if base_seconds is None else base_seconds
        delay=min(DOCUMENT_RETRY_MAX_SECONDS,base*(2**min(attempts-1,10)))
        assignments="state='pending',updated_at=?,retry_count=?,next_retry_at=?,error=?"
        values=[now(),attempts,time.time()+delay,reason]
        for key,value in details.items():
            if key in allowed:
                if key in ("new_findings","new_checks"):
                    assignments+=","+key+"=COALESCE("+key+",0)+CASE WHEN last_counted_token=? THEN 0 ELSE ? END"
                    values.append(token)
                else:assignments+=","+key+"=?"
                values.append(value)
        if token:
            assignments+=",last_result_token=?,last_counted_token=?"
            values.extend([token,token])
        values.extend([CAMPAIGN,reg])
        c.execute("UPDATE v332_audit_queue SET "+assignments+
                  " WHERE campaign=? AND reg=? AND state='running'",values)
        c.commit()
        return delay
    except BaseException:
        if c.in_transaction:c.rollback()
        raise
    finally:c.close()

def existing(ns,reg):
    c=connect(ns)
    try:
        p=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        if p is None:raise LookupError("Procurement disappeared from DB")
        rows=[dict(r) for r in c.execute("""SELECT id,name,url,doc_type,published_at,
            local_path,status,metadata_json,final_url FROM docs
            WHERE reg_number=? AND COALESCE(local_path,'')<>'' AND status IN ('checked','downloaded_no_text') ORDER BY id LIMIT ?""",(reg,MAX_DOC_ROWS))]
        return dict(p),rows
    finally:c.close()

def as_procurement(row):
    reg=str(row["reg_number"])
    out={
        "reg":reg,
        "title":row.get("title") or ("Закупка № "+reg),
        "customer":row.get("customer") or "",
        "customer_inn":row.get("customer_inn") or "",
        "method":row.get("method") or "",
        "price":row.get("price") or "",
        "price_num":row.get("price_num"),
        "published":row.get("published_at") or "",
        "submission_start":row.get("submission_start") or "",
        "submission_end":row.get("submission_end") or "",
        "deadline":row.get("deadline") or "",
        "url":row.get("url") or "",
    }
    # A verified official card can establish NMCD even when its amount is
    # not repeated in the uploaded text. Do NOT use subject cost/maximum
    # value as NMCD or a stale unverified DB numeric field.
    try:
        card=json.loads(row.get("card_fields_json") or "{}")
        ev=(card.get("price_evidence") or {}) if isinstance(card,dict) else {}
        if isinstance(card,dict):
            out["card_fields_json"]=card
            out["fact_evidence"]=card.get("fact_evidence") or {}
        from v311_verified_customer import _official_source
        from v335_price_recovery import money
        verified=money(ev.get("price"))
        number=money(out["price_num"])
        if (ev.get("source")=="official_eis_card" and
            ev.get("price_kind")=="nmcd" and verified and
            number and verified[1]==number[1] and
            _official_source(ev.get("source_url") or ev.get("evidence_page"),reg)):
            out["verified_price_source"]={
                "kind":"nmcd","source":ev.get("source_url") or ev.get("evidence_page"),
                "price":verified[0]
            }
    except (ValueError,TypeError,AttributeError,KeyError):
        pass
    return out

def source_verified(reg,path,digest,meta):
    source=meta.get("source")
    if source not in ("official_eis_original","official_eis_archive_member"):
        return False
    uid=uid_from_url(meta.get("source_url") or "")
    if not uid:return False
    receipt=saved_document(reg,uid)
    if receipt is None:return False
    original,info=receipt
    if source=="official_eis_original":
        return original.resolve()==path and info["sha256"]==digest
    # A member of an original archive is authenticated by the saved parent
    # archive receipt and the child's own SHA256.
    return info["sha256"]==meta.get("archive_sha256") and digest==meta.get("sha256")

async def stored_docs(ns,reg,rows):
    """Authenticate every file and extract with bounded, rolling workers.

    Results keep source order. Manual checks retain reserved physical capacity;
    timed-out threads keep their permits until they actually stop.
    """
    extractor=ns.get("extract_text")
    if not callable(extractor):raise RuntimeError("Document extractor unavailable")
    data_root=root().resolve()

    def read_one(row):
        raw=row.get("local_path")
        if not raw:return None
        stage="path"
        try:
            f=Path(raw).resolve(strict=True)
            if (not f.is_file() or not f.is_relative_to(data_root) or
                f.suffix.lower() not in FILE_EXT or
                not 0<f.stat().st_size<=MAX_BYTES):
                return None
            def load_hash():
                with f.open("rb") as h:
                    return hashlib.file_digest(h,"sha256").hexdigest()
            stage="hash"
            sha=load_hash()
            try:meta=json.loads(row.get("metadata_json") or "{}")
            except (ValueError,TypeError):meta={}
            if not isinstance(meta,dict):meta={}
            if meta.get("sha256") and meta["sha256"]!=sha:return None
            # Older saved originals stored their EIS URL in docs.url.
            # A receipt still has to authenticate this exact file/hash.
            if not meta.get("source_url"):
                meta={**meta,"source_url":row.get("url") or ""}
            stage="receipt"
            trusted=source_verified(reg,f,sha,meta)
            meta={**meta,"sha256":sha}
            if not trusted:
                meta["source"]="existing_local_unverified"
            stage="text"
            body=document_text(f,extractor)
            body=str(body or "")[:650000]
            readable=len(body.strip())>=3
            return {
                "name":row.get("name") or f.name,
                "url":row.get("url") or "",
                "type":row.get("doc_type") or "other",
                "published":row.get("published_at") or "",
                "meta":meta,"text":body,"local":str(f),"annotated":None,
                "status":"checked" if readable else "downloaded_no_text",
                "error":None if readable else "Текст не удалось извлечь",
                "final_url":row.get("final_url") or row.get("url") or ""
            }
        except (FileNotFoundError,NotADirectoryError):
            # Old stale file paths cannot be audited: skip instead of
            # producing thousands of repeated exception logs.
            return None
        except Exception as exc:
            reason=" ".join(str(exc).split())[:240]
            print(f"V351 DOC_SKIP reg={reg} type={type(exc).__name__} "
                  f"file_id={row.get('id')} stage={stage} reason={reason!r}",flush=True)
            return None

    async def one(row):
        try:
            return await run_file_io(read_one,row,timeout=55)
        except asyncio.TimeoutError:
            print(f"V381 DOC_TIMEOUT reg={reg} file_id={row.get('id')}",flush=True)
            return None

    results=await ordered_map(one,rows,limit=FILE_BATCH_SIZE)
    output=[doc for doc in results if doc is not None]
    unreadable=sum(doc is None or doc["status"]!="checked" for doc in results)
    return output,unreadable

def merge_docs(official,other):
    # Only one logical document for duplicate binary SHA; official receipts
    # take priority over unverified local copies.
    out=[]
    hashes={}
    for d in [*official,*other]:
        m=d.get("meta") or {}
        digest=m.get("sha256")
        if not digest or not HEX.fullmatch(str(digest)):continue
        if digest in hashes:
            index=hashes[digest]
            # stored_docs also contains receipt-authenticated originals. An
            # older unverified row with identical bytes cannot hide that proof.
            # Replace in place so the order of distinct hashes stays stable.
            if is_official(d) and not is_official(out[index]):
                _merge_source_metadata(d,out[index])
                out[index]=d
            elif d is not out[index]:
                _merge_source_metadata(out[index],d)
            continue
        hashes[digest]=len(out)
        out.append(d)
    return out

def is_official(doc):
    m=doc.get("meta") or {}
    return (doc.get("status")=="checked" and
            m.get("source") in ("official_eis_original","official_eis_archive_member")
            and HEX.fullmatch(str(m.get("sha256") or "")) is not None
            and bool(uid_from_url(m.get("source_url") or "")))

def require_reservation(c,reg):
    """Fence automatic writers; explicit maintenance keeps its existing API."""
    token=getattr(reg,"reservation_token",None)
    if token:
        from v342_thematic_queue import business_company
        row=c.execute("SELECT p.customer FROM v332_audit_queue q JOIN purchases p ON p.reg_number=q.reg "
            "WHERE q.campaign=? AND q.reg=? AND q.state='running' AND q.reservation_token=?",(CAMPAIGN,reg,token)).fetchone()
        if not row or not business_company(row[0]):raise LookupError("Audit reservation superseded")


def check_reservation(ns,reg):
    if not getattr(reg,"reservation_token",None):return
    c=connect(ns)
    try:require_reservation(c,reg)
    finally:c.close()


@audit_funnel.trace_sync("general",registration="reg")
def persist(ns,reg,findings,checks,progress_token=None):
    """Append only under the caller's exact general or intake reservation."""
    guard=ns.get("_v295_persist_guard")
    # Intake's token/file lock is acquired before SQLite's writer transaction.
    # Its short guard contains no source hashing, downloads or analysis.
    with guard(reg) if callable(guard) else nullcontext():
        return _persist_reserved(ns,reg,findings,checks,progress_token)


def _audit_result_snapshot(c,reg):
    """Materialize and close all readers before documentary comparisons."""
    findings=[dict(row) for row in c.execute("""SELECT id,code,title,law,evidence,document,source_url
        FROM findings WHERE reg_number=? AND kind='violation' ORDER BY id""",(reg,)).fetchall()]
    checks=[dict(row) for row in c.execute("""SELECT id,field_path,item,status,law,evidence,document,source_url
        FROM checks WHERE reg_number=? ORDER BY id""",(reg,)).fetchall()]
    return findings,checks


def _plan_audit_results(reg,findings,checks,snapshot,diagnostic=None):
    """Do legal episode matching with no SQLite read/write transaction held."""
    from v353_episode_guard import distinct_new
    saved_findings,saved_checks=snapshot
    previous=[{key:row[key] for key in ("code","title","law","evidence","document","source_url")}
              for row in saved_findings]
    previous.extend({"code":row["field_path"],"title":row["item"],
                     **{key:row[key] for key in ("law","evidence","document","source_url")}}
                    for row in saved_checks if row["status"]=="violation")
    # Preserve the checklist's existing key, including rows inserted earlier
    # in this same result. NULL field/item values do not equal an empty string.
    checklist_keys={(row["field_path"],row["item"],row["document"] or "") for row in saved_checks}
    new_findings,new_checks=[],[]
    confirmed_checks=0
    for f in findings:
        if not isinstance(f,dict) or f.get("kind")!="violation":continue
        code=str(f.get("code") or "")[:100]
        title=str(f.get("title") or "")[:500]
        if not code or not title:
            audit_funnel.increment(diagnostic,"reasons","invalid_record")
            continue
        if not distinct_new(f,previous):
            audit_funnel.increment(diagnostic,"reasons","duplicate")
            continue
        new_findings.append((reg,code,"violation",title,
            f.get("law"),f.get("law_url"),str(f.get("evidence") or "")[:3000],
            str(f.get("note") or "")[:3000],f.get("document"),f.get("source_url"),
            f.get("location"),f.get("match_text")))
        previous.append({**f,"code":code,"title":title})
    for ck in checks:
        if not isinstance(ck,dict):continue
        status=ck.get("status")
        if status not in ("ok","check","violation","risk"):continue
        scope=str(ck.get("scope") or "")[:200]
        item=str(ck.get("item") or "")[:450]
        path=str(ck.get("field_path") or "")[:170]
        document=str(ck.get("document") or "")[:255]
        if status=="violation":
            found=not distinct_new({**ck,"title":item},previous)
        else:
            found=(path,item,document) in checklist_keys
        if found:
            audit_funnel.increment(diagnostic,"reasons","duplicate")
            continue
        new_checks.append((reg,scope,item,status,str(ck.get("evidence") or "")[:2200],
            ck.get("source"),ck.get("source_url"),ck.get("document"),path,
            str(ck.get("note") or "")[:3000],ck.get("law"),
            ck.get("law_url"),ck.get("location")))
        checklist_keys.add((path,item,ck.get("document") or ""))
        if status in ("check","risk"):audit_funnel.increment(diagnostic,"counts","persisted_review")
        elif status=="ok":audit_funnel.increment(diagnostic,"counts","persisted_ok")
        if status=="violation":
            confirmed_checks+=1
            previous.append({**ck,"title":item,"code":path})
    return new_findings,new_checks,confirmed_checks


@audit_funnel.safe
def _merge_committed_audit_diagnostics(diagnostic):
    if diagnostic is None:return
    for group in ("counts","reasons"):
        for key,value in diagnostic[group].items():
            audit_funnel.current_increment(group,key,value)


def _persist_reserved(ns,reg,findings,checks,progress_token=None):
    """Append unseen results atomically without holding the writer for regex work."""
    c=connect(ns)
    try:
        audit_funnel.observe("persist_input",findings,checks)
        for attempt in range(4):
            snapshot=_audit_result_snapshot(c,reg)
            # Discard diagnostic counts along with any stale outside-writer plan.
            diagnostic=audit_funnel.new("general",reg)
            insert_findings,insert_checks,confirmed_checks=_plan_audit_results(reg,findings,checks,snapshot,diagnostic)
            c.execute("BEGIN IMMEDIATE")
            require_reservation(c,reg)
            # Another analyzer may have committed this same procurement while
            # matching episodes. Rebase outside the writer before inserting;
            # otherwise two correct independent reviews could create duplicates.
            if _audit_result_snapshot(c,reg)!=snapshot:
                c.rollback()
                continue
            break
        else:
            raise sqlite3.OperationalError("database is locked: audit results changed during save")
        new_f,new_c=len(insert_findings),len(insert_checks)
        if insert_findings:
            c.executemany("""INSERT INTO findings
                (reg_number,code,kind,title,law,law_url,evidence,note,
                 document,source_url,location,match_text)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",insert_findings)
        if insert_checks:
            c.executemany("""INSERT INTO checks
                (reg_number,scope,item,status,evidence,source,source_url,
                 document,field_path,note,law,law_url,location)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",insert_checks)
        if new_f or new_c:
            counts=c.execute("""SELECT
              (SELECT COUNT(*) FROM findings WHERE reg_number=? AND kind='violation')
              +(SELECT COUNT(*) FROM checks WHERE reg_number=? AND status='violation'),
              (SELECT COUNT(*) FROM checks WHERE reg_number=? AND status='ok'),
              (SELECT COUNT(*) FROM checks WHERE reg_number=? AND status IN ('check','violation','risk'))
            """,(reg,reg,reg,reg)).fetchone()
            c.execute("""UPDATE purchases SET
                violations=?,checks_ok=?,checks_attention=? WHERE reg_number=?""",
                (*[int(v) for v in counts],reg))
        if progress_token and c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v332_audit_queue'").fetchone():
            # Legal facts and their cumulative campaign credit commit together.
            # A crash before mark/defer cannot erase their recorded growth.
            ensure_source_column(c)
            c.execute("""UPDATE v332_audit_queue SET
                new_findings=COALESCE(new_findings,0)+?,
                new_checks=COALESCE(new_checks,0)+?,last_counted_token=?
                WHERE campaign=? AND reg=? AND last_counted_token<>?""",
                (new_f+confirmed_checks,new_c-confirmed_checks,progress_token,
                 CAMPAIGN,reg,progress_token))
        c.commit()
        audit_funnel.current_increment("counts","persisted_confirmed",new_f+confirmed_checks)
        _merge_committed_audit_diagnostics(diagnostic)
        audit_funnel.committed()
        return new_f,new_c
    except:
        if c.in_transaction:c.rollback()
        raise
    finally:c.close()

async def collect_saved_evidence(ns,reg,rows):
    """Collect the same receipt-authenticated local inputs for both audits.

    Incoming originals can precede their docs rows. One persisted archive
    member also cannot establish that every other member has been read, so
    received archives are expanded through the existing bounded collector.
    No database evidence rows are replaced and no network download is made.
    """
    source_error=None
    try:
        allowed=await run_file_io(allowed_documents,reg)
        await run_file_io(_review_source_version,reg)
    except HTTPException as exc:
        if exc.status_code!=409:raise
        allowed={}
        source_error=str(exc.detail)
    expected=len(allowed) if not source_error else None
    received_uids=await run_file_io(
        lambda:{uid for uid in allowed if saved_document(reg,uid) is not None})
    received=len(received_uids) if not source_error else None
    other,unreadable=await stored_docs(ns,reg,rows)
    represented={uid_from_url((d.get("meta") or {}).get("source_url") or "")
                 for d in other if (d.get("meta") or {}).get("source")==
                 "official_eis_original"}
    official=[]
    result={}
    source_incomplete=bool(source_error)
    from v294_eis_bridge import soap_metadata_incomplete
    try:
        metadata_incomplete=await run_file_io(soap_metadata_incomplete,reg,timeout=30)
    except asyncio.TimeoutError:
        metadata_incomplete=True
    if metadata_incomplete:
        source_incomplete=True
        source_error=source_error or "Часть официальных XML ЕИС ещё не поддерживается или не подтверждена"
    if received_uids-represented:
        try:
            # Pull from REAL, locally saved and receipt-validated originals;
            # it processes every received archive member too.
            result=await asyncio.wait_for(
                offline_audit(ns,reg,collect_only=True),timeout=280)
            official=result.get("docs") or []
        except Exception as exc:
            # The readable stored subset can still yield proven findings,
            # but a failed collection has not examined every received original.
            source_incomplete=True
            result={}
            print(f"V332 OFFICIAL_SOURCE_ERROR reg={reg} "
                  f"type={type(exc).__name__}",flush=True)
    # The collector skips bytes that fail their receipt hash. One other
    # readable original must not turn that missing input into a full audit.
    # Check before deduplication so identical persisted copies retain all
    # authenticated UIDs. Fresh collector aliases were hash-checked there.
    seen_uids={uid_from_url((d.get("meta") or {}).get("source_url") or "")
               for d in [*official,*other] if (d.get("meta") or {}).get("source") in
               ("official_eis_original","official_eis_archive","official_eis_archive_member")}
    for d in official:
        for alias in (d.get("meta") or {}).get("identical_sources") or []:
            if isinstance(alias,dict):seen_uids.add(uid_from_url(alias.get("url") or ""))
    source_incomplete=source_incomplete or bool(received_uids-seen_uids)
    docs=merge_docs(official,other)
    # Collection may return an unreadable original instead of raising. Inspect
    # the deduplicated result so a readable copy can satisfy the same evidence;
    # an expanded archive container is not itself an unreadable legal document.
    source_incomplete=source_incomplete or any(
        d.get("status") in ("downloaded_no_text","archive_unreadable") or
        bool((d.get("meta") or {}).get("archive_warnings")) for d in docs)
    return {"docs":docs,"unreadable":unreadable,
            "original_expected":expected,"original_received":received,
            "source_incomplete":source_incomplete,
            "source_error":source_error,
            "p":result.get("p") or {},"card":result.get("card") or {},
            "detail":result.get("detail") or {}}


@audit_funnel.trace("general",registration="reg")
async def inspect_one(ns,reg):
    from audit_write_guard import background_write_guard
    intake_guard=ns.get("_v295_persist_guard")
    outer=(lambda:intake_guard(reg)) if callable(intake_guard) else None
    sql_guard=(lambda c:require_reservation(c,reg)) if getattr(reg,"reservation_token",None) else None
    with background_write_guard(sql_guard,outer):
        return await _inspect_one(ns,reg)


async def _inspect_one(ns,reg):
    old,rows=await db_stage(ns,"existing",existing,ns,reg)
    purchase=as_procurement(old)
    card={"status":"partial","pages":{}}
    detail={"registrationNumber":reg,"purchaseName":purchase["title"],
            "purchaseMethodName":purchase["method"],
            "publicationDate":purchase["published"],
            "submissionStart":purchase["submission_start"],
            "submissionEnd":purchase["submission_end"]}
    source=await collect_saved_evidence(ns,reg,rows)
    docs,unreadable=source["docs"],source["unreadable"]
    expected,received=source["original_expected"],source["original_received"]
    source_incomplete=source["source_incomplete"]
    for key,value in source["p"].items():
        if value and (not purchase.get(key) or key in ("method","submission_end","submission_start")):
            purchase[key]=value
    card=source["card"] or card
    detail=source["detail"] or detail
    # Repair persisted identity before legal comparison. This also covers
    # purchases whose originals were imported before the metadata parser.
    from v314_eis_metadata import from_documents,apply_metadata
    from v311_verified_customer import _name
    from v342_identity import subject,valid_inn
    metadata=await asyncio.to_thread(from_documents,reg,docs,_name(purchase.get("customer")))
    if metadata:
        await db_stage(ns,"reservation",check_reservation,ns,reg)
        await db_stage(ns,"metadata",apply_metadata,ns,reg,metadata)
        if not subject(purchase.get("title")) and metadata.get("title"):
            purchase["title"]=metadata["title"]
        if not _name(purchase.get("customer")) and metadata.get("customer"):
            purchase["customer"]=metadata["customer"]
        if not valid_inn(purchase.get("customer_inn")) and metadata.get("customer_inn"):
            purchase["customer_inn"]=metadata["customer_inn"]
    readable=[d for d in docs if d.get("status")=="checked" and str(d.get("text") or "").strip()]
    counted=len(readable)
    verified_count=sum(is_official(d) for d in readable)
    audit_funnel.observe("documents_general",docs,unreadable,verified_count,counted)
    audit_funnel.observe("incomplete",source_incomplete)
    if not readable:
        audit_funnel.observe("gate","no_readable_documents")
        return ("no_readable_documents",{
            "documents_found":len(docs)+unreadable,"documents_read":0,
            "documents_unreadable":unreadable,"verified_original_documents":0,
            **({"original_expected":expected,"original_received":received} if expected is not None else {}),
            "source_incomplete":source_incomplete,
            "error":source["source_error"] or "Нет пригодного текста в доступных документах; нарушения не оценивались"})
    analyzer=ns.get("analyze")
    checker=ns.get("build_checks")
    if not callable(analyzer) or not callable(checker):
        raise RuntimeError("No legal analyzer installed")
    # Installed analyzer wrappers can persist official card facts too. A
    # cancellation must settle those writes before another worker takes reg.
    await db_stage(ns,"reservation",check_reservation,ns,reg)
    findings=await thread_call(analyzer,purchase,docs)
    audit_funnel.observe("analyzer_results",findings)
    await db_stage(ns,"reservation",check_reservation,ns,reg)
    checks=await thread_call(checker,purchase,detail,card,docs)
    audit_funnel.observe("checklist_results",checks)
    verified_sources={d.get("name") for d in readable if is_official(d)}
    verified_urls={str((d.get("meta") or {}).get("source_url"))
                   for d in readable if is_official(d)}
    accepted=[]
    for f in findings:
        if not isinstance(f,dict) or f.get("kind")!="violation":
            audit_funnel.observe("gate","non_violation")
            continue
        name=f.get("document")
        url=f.get("source_url")
        confirmed=bool((name and name in verified_sources) or (url and url in verified_urls))
        if not confirmed and str(f.get("code") or "").startswith("223-4-11-"):
            # A cross-checked official EIS amendments feed can establish an
            # infraction even when its tab is not a downloadable binary.
            from v330_amendments_223 import manifest_changes,official_url
            confirmed=bool(url and official_url(url) and
                           any(e.get("source")==url and e.get("decision_date")
                               and e.get("publication_date")
                               for e in manifest_changes(reg)))
        if confirmed:accepted.append(f)
        else:
            audit_funnel.observe("gate","source_unverified")
            checks.append({
            "scope":"Предварительный юридический анализ",
            "item":f.get("title") or "Проверить правовое основание",
            "status":"check",
            "evidence":str(f.get("evidence") or "")[:1800],
            "source":name or "Материалы закупки","source_url":url or "",
            "document":name,"field_path":f.get("code") or "provisional",
            "law":f.get("law") or "223-ФЗ","law_url":f.get("law_url") or "",
            "location":f.get("location") or "",
            "note":"Документ не подтверждён квитанцией ЕИС; в число установленных нарушений не включено"
        })
    audit_funnel.observe("outcomes_general",accepted,checks)
    token=uuid.uuid4().hex
    # Keep the computed evidence and token while a short write lock clears.
    # Retrying the whole inspection would needlessly rerun legal analyzers.
    new_f,new_c=await db_stage(ns,"persist",persist,ns,reg,accepted,checks,token)
    return ("completed",{
        "documents_found":len(docs)+unreadable,
        "documents_read":counted,"documents_unreadable":unreadable,
        "verified_original_documents":verified_count,
        **({"original_expected":expected,"original_received":received} if expected is not None else {}),
        "new_findings":new_f,"new_checks":new_c,
        "source_incomplete":source_incomplete,
        "result_token":token,
        "error":source["source_error"]
    })

def retry_delay(attempt):
    return min(RETRY_MAX_SECONDS,RETRY_BASE_SECONDS*(2**min(attempt-1,10)))


async def settle_task(task):
    """Finish a shielded executor operation before releasing its reservation."""
    while True:
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():raise
            # Repeated shutdown cancellation must not abandon a SQLite thread
            # that may still commit an atomic reservation or result.
            continue


async def thread_call(function,*args):
    task=asyncio.create_task(asyncio.to_thread(function,*args))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:await settle_task(task)
        except Exception:pass
        raise


async def retry_operation(ns,phase,operation,worker=None,*,retry_seconds=None):
    attempt=0
    deadline=None if retry_seconds is None else time.monotonic()+retry_seconds
    while True:
        if worker is not None:worker["phase"]=phase
        try:
            result=await operation()
            if attempt:
                print(f"V332 SQLITE_RECOVERED phase={phase} attempts={attempt}",flush=True)
            return result
        except Exception as exc:
            if not transient_sqlite(exc):raise
            attempt+=1
            delay=retry_delay(attempt)
            message=f"{type(exc).__name__}: {str(exc)[:180]}"
            runtime=ns.get("_v332_runtime")
            if runtime is not None:
                runtime["last_error"]=message
                runtime["last_error_at"]=now()
            if worker is not None:
                worker.update(phase="retrying_"+phase,last_error=message,
                              last_error_at=now(),last_error_reg=worker.get("reg"),
                              last_error_phase=phase,retries=worker.get("retries",0)+1)
            if deadline is not None:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    print(f"V332 SQLITE_RETRY_EXHAUSTED phase={phase} "
                          f"attempts={attempt} error={message}",flush=True)
                    raise
                delay=min(delay,remaining)
            print(f"V332 SQLITE_RETRY phase={phase} "
                  f"worker={(worker or {}).get('id','service')} "
                  f"reg={(worker or {}).get('reg')} attempt={attempt} "
                  f"delay_seconds={delay} error={message}",flush=True)
            await asyncio.sleep(delay)


async def db_stage(ns,phase,function,*args,worker=None):
    """Retry just this DB stage, settling writes before cancellation/defer."""
    return await retry_operation(ns,phase,lambda:thread_call(function,*args),worker,
                                 retry_seconds=DB_STAGE_RETRY_SECONDS)


def return_running(ns,reg):
    """Return an unfinished reservation; never overwrite a committed result."""
    c=connect(ns)
    try:
        token=getattr(reg,"reservation_token",None)
        c.execute("""UPDATE v332_audit_queue SET state='pending',updated_at=?,
            error='Возобновление прерванной проверки',reservation_token=''
            WHERE campaign=? AND reg=? AND state='running'
              AND (? IS NULL OR reservation_token=?)""",(now(),CAMPAIGN,reg,token,token))
        c.commit()
    finally:c.close()


async def release_reservation(ns,reg):
    cleanup=asyncio.create_task(retry_operation(ns,"release",lambda:thread_call(
        return_running,ns,reg)))
    try:await settle_task(cleanup)
    except Exception as exc:
        # Startup also resets durable running rows if storage itself is broken.
        print(f"V332 RELEASE_FAILED reg={reg} type={type(exc).__name__} "
              f"reason={str(exc)[:180]}",flush=True)


async def claim_safely(ns):
    """Cancellation waits for the real claim thread and returns any late claim."""
    async def gated_claim():
        with background_audit():return await run_file_io(claim_next,ns)
    task=asyncio.create_task(gated_claim())
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:reg=await settle_task(task)
        except Exception:reg=None
        if reg is not None:await release_reservation(ns,reg)
        raise


def install(ns):
    app=ns["app"]
    boot_scope=None
    def owns_boot_review(reg):
        nonlocal boot_scope
        if boot_scope is None:
            c=connect(ns)
            try:
                try:
                    boot_scope={str(row[0]) for row in c.execute(
                        "SELECT reg FROM v332_audit_queue WHERE campaign=?",(CAMPAIGN,))}
                except Exception:
                    boot_scope=set()
            finally:
                c.close()
            if not boot_scope:
                boot_scope=set(dashboard_regs(ns))
        return str(reg) in boot_scope
    ns["_v332_owns_boot_review"]=owns_boot_review
    @app.get("/api/v332/legal-audit/progress")
    async def audit_progress():
        return await asyncio.to_thread(progress,ns)

    @app.on_event("startup")
    async def start_campaign():
        previous=ns.get("_v332_campaign_worker")
        if previous is not None and not previous.done():return
        runtime={"status":"starting","started_at":now(),"last_error":None,
                 "last_error_at":None,"last_completed_at":None,
                 "workers":{str(i):{"id":i,"alive":False,"phase":"starting",
                                    "reg":None,"retries":0}
                            for i in range(1,GENERAL_WORKERS+1)}}
        ns["_v332_runtime"]=runtime
        ns["_v332_tasks"]={}
        async def work():
            await asyncio.sleep(14)
            attempts=0
            while True:
                try:
                    base=await thread_call(initialize,ns)
                    print("V332 CAMPAIGN_STARTED "+json.dumps(base,ensure_ascii=False),flush=True)
                    runtime["status"]="running"
                    break
                except Exception as exc:
                    attempts+=1
                    runtime.update(status="retrying_initialization",
                                   last_error=f"{type(exc).__name__}: {str(exc)[:200]}",
                                   last_error_at=now())
                    print("V332 INIT_FAILED type="+type(exc).__name__+
                          " reason="+str(exc)[:200]+" retry_seconds="+str(retry_delay(attempts)),flush=True)
                    await asyncio.sleep(retry_delay(attempts))
            async def worker(worker_id):
                index=0
                signals=audit_signals(ns)
                slot=runtime["workers"][str(worker_id)]
                reg=None
                try:
                    while True:
                        version=signals.version("general")
                        reg=await retry_operation(ns,"claim",lambda:claim_safely(ns),slot)
                        slot["reg"]=reg
                        if reg is None:
                            slot["phase"]="waiting_for_documents"
                            await signals.wait("general",version,QUEUE_POLL_SECONDS)
                            continue
                        snapshot=None
                        result=None
                        try:
                            started=time.monotonic()
                            async def review():
                                nonlocal snapshot
                                # Source/stat reads precede analysis and run
                                # outside the short claim/result transactions.
                                snapshot=await file_stage(ns,"source",review_watermark,ns,reg,worker=slot)
                                slot["phase"]="inspect"
                                with background_audit():
                                    return await asyncio.wait_for(inspect_one(ns,reg),timeout=600)
                            slot["phase"]="inspect"
                            state,detail=await review()
                            # This diagnostic describes collection coverage;
                            # durable state/backoff carries it without a new DB
                            # column or an invented unreadable-document count.
                            source_incomplete=bool(detail.get("source_incomplete") or snapshot.get("source_error"))
                            result={key:value for key,value in detail.items() if key!="source_incomplete"}
                            result.update({key:value for key,value in snapshot.items() if key!="source_error"})
                            if snapshot.get("source_error"):result["error"]=snapshot["source_error"]
                            result["result_token"]=detail.get("result_token") or uuid.uuid4().hex
                            incomplete=(state=="no_readable_documents" or
                                        int(detail.get("documents_unreadable") or 0)>0 or source_incomplete)
                            if incomplete:
                                reason=((result.get("error") or "Не завершено чтение полученных оригиналов ЕИС; назначен повтор") if source_incomplete else
                                    "Не все доступные оригиналы удалось прочитать; назначен повтор" if
                                        state=="completed" else
                                        "Текст доступных оригиналов пока не прочитан; назначен повтор")
                                delay=await db_stage(ns,"save_partial",defer_retry,ns,reg,result,reason,worker=slot)
                                print(f"V332 DOCUMENT_RETRY reg={reg} reason=partial "
                                      f"delay_seconds={delay}",flush=True)
                            else:
                                await db_stage(ns,"save",mark,ns,reg,state,result,worker=slot)
                            elapsed_ms=round((time.monotonic()-started)*1000,1)
                            index+=1
                            slot["last_success_at"]=now()
                            slot.update(last_error=None,last_error_at=None,last_error_reg=None,last_error_phase=None)
                            if state=="completed":
                                if not incomplete:runtime["last_completed_at"]=slot["last_success_at"]
                                print(f"V332 AUDITED reg={reg} worker={worker_id} docs={detail['documents_read']}/"
                                      f"{detail['documents_found']} verified={detail['verified_original_documents']} "
                                      f"originals={detail.get('original_received','?')}/{detail.get('original_expected','?')} "
                                      f"new_findings={detail['new_findings']} new_checks={detail['new_checks']} "
                                      f"elapsed_ms={elapsed_ms} partial={incomplete}",flush=True)
                            elif index<=4 or index%20==0:
                                print(f"V332 NO_DOCUMENT_TEXT reg={reg}",flush=True)
                        except asyncio.CancelledError:raise
                        except asyncio.TimeoutError:
                            detail={"result_token":uuid.uuid4().hex}
                            if snapshot is not None:detail["reservation_token"]=snapshot["reservation_token"]
                            reason="Проверка превысила 600 секунд; назначен повтор без удаления результатов"
                            delay=await retry_operation(ns,"save_timeout",lambda:thread_call(
                                defer_retry,ns,reg,detail,reason),slot)
                            print(f"V332 DOCUMENT_RETRY reg={reg} reason=timeout "
                                  f"delay_seconds={delay}",flush=True)
                        except Exception as exc:
                            index+=1
                            error=str(exc)[:300]
                            slot.update(last_error=f"{type(exc).__name__}: {error}",last_error_at=now(),
                                        last_error_reg=str(reg),last_error_phase=slot.get("phase"))
                            if transient_sqlite(exc) or isinstance(exc,(SemanticSnapshotError,EngineSignatureError,OSError)):
                                # Preserve the completed result's token when
                                # persist committed but its queue mark stayed busy.
                                detail=dict(result) if result is not None else {"result_token":uuid.uuid4().hex}
                                if snapshot is not None:
                                    detail.update(snapshot if result is not None else {"reservation_token":snapshot["reservation_token"]})
                                database_busy=transient_sqlite(exc)
                                code=exc.diagnostic_code if isinstance(exc,SemanticSnapshotError) else (
                                    "engine_signature_unavailable" if isinstance(exc,EngineSignatureError) else
                                    "source_io_error" if isinstance(exc,OSError) else "database_busy")
                                reason=(f"{code}: снимок правовых исходных данных не завершён; проверка будет повторена без удаления результатов"
                                        if isinstance(exc,SemanticSnapshotError) else
                                        "Временная блокировка БД; проверка будет повторена без удаления результатов"
                                        if database_busy else f"{code}: проверка будет повторена без удаления результатов")
                                delay=await retry_operation(ns,"save_busy",lambda:thread_call(
                                    defer_retry,ns,reg,detail,reason,BUSY_RETRY_BASE_SECONDS),slot)
                                print(f"V332 AUDIT_DEFERRED reg={reg} worker={worker_id} "
                                      f"database_busy={database_busy} reason={code} retry_after_seconds={delay}",flush=True)
                            else:
                                # Preserve prior successful watermarks on a deterministic
                                # engine failure, but do not hot-loop its unchanged input.
                                detail={"result_token":uuid.uuid4().hex}
                                if snapshot is not None and snapshot.get("reservation_token"):
                                    detail["reservation_token"]=snapshot["reservation_token"]
                                delay=await retry_operation(ns,"save_failure",lambda:thread_call(
                                    defer_retry,ns,reg,detail,error),slot)
                                print(f"V332 AUDIT_FAILED reg={reg} worker={worker_id} "
                                      f"retry_after_seconds={delay} error={type(exc).__name__}: {str(exc)[:180]}",flush=True)
                        reg=None
                        slot["reg"]=None
                        await asyncio.sleep(.05)
                finally:
                    if reg is not None:await release_reservation(ns,reg)
                    slot.update(reg=None,phase="stopped")
            async def refresh_queue():
                signals=audit_signals(ns)
                while True:
                    version=signals.version("evidence")
                    started=time.monotonic()
                    try:
                        refreshed=await file_stage(ns,"queue_refresh",refresh_changed,ns)
                        new=await retry_operation(ns,"queue_add",lambda:thread_call(
                            add_new_verified,ns,None,MAX_RECHECK_ADMISSIONS-refreshed))
                        if refreshed or new:
                            signals.notify("general")
                            print("V381 QUEUE_WAKEUP new="+str(new)+
                                  " new_evidence="+str(refreshed)+
                                  " elapsed_ms="+str(round((time.monotonic()-started)*1000,1)),flush=True)
                    except Exception as exc:
                        # A transient busy DB must not cancel all four audits.
                        print("V381 QUEUE_REFRESH_FAILED type="+type(exc).__name__,flush=True)
                    # Independent of slow audits: newly ready evidence can
                    # enter a freed worker immediately, with polling fallback.
                    await signals.wait("evidence",version,QUEUE_POLL_SECONDS)

            async def heartbeat():
                while True:
                    await asyncio.sleep(HEARTBEAT_SECONDS)
                    try:
                        state=await thread_call(progress,ns)
                    except Exception as exc:
                        runtime.update(last_error=f"{type(exc).__name__}: {str(exc)[:180]}",
                                       last_error_at=now())
                        state={"campaign":CAMPAIGN,"progress_error":runtime["last_error"],
                               "runtime":runtime_snapshot(ns)}
                    print("V332 HEARTBEAT "+json.dumps(state,ensure_ascii=False),flush=True)

            async def supervise(name,factory,slot=None):
                attempts=0
                while True:
                    task=asyncio.create_task(factory(),name="general-audit-"+name)
                    ns["_v332_tasks"][name]=task
                    if slot is not None:slot["alive"]=True
                    try:
                        await task
                        raise RuntimeError("Audit service ended unexpectedly")
                    except asyncio.CancelledError:
                        if asyncio.current_task().cancelling():
                            task.cancel()
                            await asyncio.gather(task,return_exceptions=True)
                            raise
                        # An individually cancelled service is restarted;
                        # only cancellation of its supervisor means shutdown.
                        attempts+=1
                        print(f"V332 SERVICE_RESTART service={name} error=cancelled "
                              f"retry_seconds={retry_delay(attempts)}",flush=True)
                    except Exception as exc:
                        attempts+=1
                        runtime.update(last_error=f"{type(exc).__name__}: {str(exc)[:180]}",
                                       last_error_at=now())
                        print(f"V332 SERVICE_RESTART service={name} "
                              f"error={runtime['last_error']} retry_seconds={retry_delay(attempts)}",flush=True)
                    finally:
                        if slot is not None:slot["alive"]=False
                    await asyncio.sleep(retry_delay(attempts))

            tasks=[asyncio.create_task(supervise("worker-"+str(i),lambda i=i:worker(i),
                         runtime["workers"][str(i)])) for i in range(1,GENERAL_WORKERS+1)]
            tasks.append(asyncio.create_task(supervise("queue",refresh_queue)))
            tasks.append(asyncio.create_task(supervise("heartbeat",heartbeat)))
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                runtime["status"]="stopped"
        ns["_v332_campaign_worker"]=asyncio.create_task(work(),name="general-audit-campaign")
        def campaign_finished(task):
            runtime["status"]="stopped"
            if not task.cancelled() and task.exception() is not None:
                error=task.exception()
                runtime.update(status="failed",last_error=f"{type(error).__name__}: {str(error)[:180]}",
                               last_error_at=now())
                print("V332 CAMPAIGN_STOPPED error="+runtime["last_error"],flush=True)
        ns["_v332_campaign_worker"].add_done_callback(campaign_finished)
    print("V332 FULL_LEGAL_QUEUE_READY main_dashboard_only=True max_purchases=1253 "
          "partial_originals=True local_files=True no_deletions=True "
          "evidence_only=True",flush=True)
