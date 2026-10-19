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

from audit_db_retry import run_db,is_busy
import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from v295_eis_originals import allowed_documents, saved_document, root, uid_from_url
from v298_eis_only import offline_audit
from v314_eis_metadata import document_text
from v381_audit_scheduling import (audit_signals, background_audit,
                                   ordered_map, run_file_io)

CAMPAIGN="2026-10-15-main-1253-identity-and-legal-v342"
RULE_REVISION="2026-10-18-v380-v372-protocol-identity-evidence"
DASHBOARD_MAX=1253
REG=re.compile(r"^[0-9]{11,20}$")
HEX=re.compile(r"^[0-9a-f]{64}$")
FILE_EXT={".pdf",".doc",".docx",".odt",".rtf",".xlsx",".xls",
          ".ods",".xml",".txt",".csv"}
MAX_BYTES=25*1024*1024
MAX_DOC_ROWS=900
# Shared physical file limit lives in v381_audit_scheduling: six operations,
# with one slot reserved for foreground/manual checks.
FILE_BATCH_SIZE=12
GENERAL_WORKERS=4
PRIORITY=["32616233661","32616324151","32616287360"]

def now():
    return datetime.now(timezone.utc).isoformat()

def connect(ns):
    c=ns["conn"]()
    c.execute("PRAGMA busy_timeout=12000")
    return c

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
            SET state='pending', error='Возобновлено после перезапуска'
            WHERE campaign=? AND state='running'""",(CAMPAIGN,))
        c.commit()
        # Re-run finished, documented originals on a new legal revision.
        # Historical findings/checks remain append-only: no previous legal
        # records or original documents are removed or downgraded.
        refreshed=refresh_changed(ns)
        if refreshed:
            print("V350 RULE_REVISION_RECHECK queued="+str(refreshed),flush=True)
    finally:c.close()
    return progress(ns)

def add_new_verified(ns,c=None):
    """Append new, EIS-verified automatic entries; preserve every past status."""
    regs=dashboard_regs(ns,include_new=True)
    owns=c is None
    if owns:c=connect(ns)
    try:
        before=c.total_changes
        c.executemany("INSERT OR IGNORE INTO v332_audit_queue(campaign,reg) VALUES(?,?)",
                      ((CAMPAIGN,reg) for reg in regs))
        added=c.total_changes-before
        c.commit()
        if added:
            print("V351 GENERAL_QUEUE_EXPANDED new="+str(added)+
                  " eligible="+str(len(regs)),flush=True)
        return added
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
            COALESCE(SUM(CASE WHEN state='completed' THEN new_findings ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state='completed' THEN new_checks ELSE 0 END),0)
            FROM v332_audit_queue WHERE campaign=?""",(CAMPAIGN,)).fetchone()
        return {
            "campaign":CAMPAIGN,
            "status":"finished" if not states.get("pending",0) and not states.get("running",0) else "running",
            "total_registered":int(sums[0]),
            "baseline_purchases":int(run["baseline_purchases"]),
            "baseline_checked_document_records":int(run["baseline_checked_documents"]),
            "baseline_recorded_violations":int(run["baseline_violations"]),
            "states":states,
            "documents_found_in_completed":int(sums[1]),
            "documents_read_in_completed":int(sums[2]),
            "verified_EIS_documents_in_completed":int(sums[3]),
            "new_evidence_based_findings":int(sums[4]),
            "new_legal_review_items":int(sums[5]),
            "note":"Fixed subset: up to 1253 purchases from the main automatic list; source-backed facts and available originals are analysed. Missing documents are not declared violation-free."
        }
    finally:c.close()

def claim_next(ns):
    """Atomically reserve an audit row; prefer already downloaded originals."""
    c=connect(ns)
    try:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("""SELECT q.reg FROM v332_audit_queue q
            JOIN purchases p ON p.reg_number=q.reg
            WHERE q.campaign=? AND q.state='pending'
            AND EXISTS(SELECT 1 FROM docs d WHERE d.reg_number=q.reg
                AND COALESCE(d.local_path,'')<>'' AND d.status='checked')
            AND (p.customer LIKE 'ООО %' OR p.customer LIKE 'АО %'
                 OR p.customer LIKE 'ПАО %' OR p.customer LIKE 'ОАО %'
                 OR p.customer LIKE 'ЗАО %'
                 OR p.customer LIKE 'Общество с ограниченной ответственностью%'
                 OR p.customer LIKE 'Акционерное общество%'
                 OR p.customer LIKE 'Публичное акционерное общество%')
            ORDER BY CASE q.reg WHEN '32616233661' THEN 0
             WHEN '32616324151' THEN 1
             WHEN '32616287360' THEN 2 ELSE 3 END,
            q.reg LIMIT 1""",(CAMPAIGN,)).fetchone()
        if not row:
            c.commit()
            return None
        reg=str(row[0])
        c.execute("""UPDATE v332_audit_queue SET state='running',updated_at=?,
                     last_doc_id=COALESCE((SELECT MAX(d.id) FROM docs d WHERE d.reg_number=?),0)
                     WHERE campaign=? AND reg=? AND state='pending'""",
                  (now(),reg,CAMPAIGN,reg))
        assert c.execute("SELECT changes()").fetchone()[0]==1
        c.commit()
        return reg
    except BaseException:
        if c.in_transaction:c.rollback()
        raise
    finally:c.close()

def refresh_changed(ns):
    """Queue only newly supported rules or actually added local documents."""
    c=connect(ns)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone():
            return 0
        c.execute("BEGIN IMMEDIATE")
        c.execute("""UPDATE v332_audit_queue
            SET state='pending',error='Новая редакция методики или новые оригиналы ЕИС'
            WHERE campaign=? AND state IN ('completed','failed','no_readable_documents')
              AND EXISTS (
                SELECT 1 FROM docs d WHERE d.reg_number=v332_audit_queue.reg
                 AND d.status='checked' AND COALESCE(d.local_path,'')<>'')
              AND (audit_revision<>? OR last_doc_id<
                  COALESCE((SELECT MAX(d2.id) FROM docs d2
                            WHERE d2.reg_number=v332_audit_queue.reg),0))""",
                  (CAMPAIGN,RULE_REVISION))
        changed=int(c.execute("SELECT changes()").fetchone()[0])
        c.commit()
        return changed
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

def mark(ns,reg,state,details=None):
    if state not in ("pending","running","completed","no_readable_documents","failed"):
        raise ValueError("Unknown queue status")
    details=details or {}
    keys={"documents_found","documents_read","documents_unreadable",
          "verified_original_documents","original_expected","original_received",
          "new_findings","new_checks","error"}
    if set(details)-keys:raise ValueError("Unknown progress field")
    c=connect(ns)
    try:
        assignments="state=?,updated_at=?"
        params=[state,now()]
        for k,v in details.items():
            assignments+=","+k+"=?"
            params.append(v)
        if state in ("completed","no_readable_documents","failed"):
            assignments+=",audit_revision=?"
            params.append(RULE_REVISION)
        params.extend([CAMPAIGN,reg])
        c.execute("UPDATE v332_audit_queue SET "+assignments+
                  " WHERE campaign=? AND reg=?",params)
        c.commit()
    finally:c.close()

def existing(ns,reg):
    c=connect(ns)
    try:
        p=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        if p is None:raise LookupError("Procurement disappeared from DB")
        rows=[dict(r) for r in c.execute("""SELECT id,name,url,doc_type,published_at,
            local_path,status,metadata_json,final_url FROM docs
            WHERE reg_number=? AND COALESCE(local_path,'')<>'' AND status='checked' ORDER BY id LIMIT ?""",(reg,MAX_DOC_ROWS))]
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
    hashes=set()
    for d in [*official,*other]:
        m=d.get("meta") or {}
        digest=m.get("sha256")
        if not digest or not HEX.fullmatch(str(digest)):continue
        if digest in hashes:continue
        hashes.add(digest)
        out.append(d)
    return out

def is_official(doc):
    m=doc.get("meta") or {}
    return (doc.get("status")=="checked" and
            m.get("source") in ("official_eis_original","official_eis_archive_member")
            and HEX.fullmatch(str(m.get("sha256") or "")) is not None
            and bool(uid_from_url(m.get("source_url") or "")))

def persist(ns,reg,findings,checks):
    """Append only unseen findings/checks; never delete previous legal results."""
    c=connect(ns)
    try:
        c.execute("BEGIN IMMEDIATE")
        from v353_episode_guard import distinct_new
        # Read both legal tables once. The UI and persistence use documentary
        # episode identity, never just a shared rule code / title.
        previous=[dict(row) for row in c.execute("""
            SELECT code,title,law,evidence,document,source_url
              FROM findings WHERE reg_number=? AND kind='violation'
            UNION ALL
            SELECT field_path AS code,item AS title,law,evidence,document,source_url
              FROM checks WHERE reg_number=? AND status='violation'
            """,(reg,reg)).fetchall()]
        new_f=new_c=0
        for f in findings:
            if not isinstance(f,dict) or f.get("kind")!="violation":continue
            code=str(f.get("code") or "")[:100]
            title=str(f.get("title") or "")[:500]
            if not code or not title:continue
            if not distinct_new(f,previous):continue
            c.execute("""INSERT INTO findings
                (reg_number,code,kind,title,law,law_url,evidence,note,
                 document,source_url,location,match_text)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(
                    reg,code,"violation",title,
                    f.get("law"),f.get("law_url"),str(f.get("evidence") or "")[:3000],
                    str(f.get("note") or "")[:3000],f.get("document"),f.get("source_url"),
                    f.get("location"),f.get("match_text")))
            new_f+=1
            previous.append({**f,"code":code,"title":title})
        for ck in checks:
            if not isinstance(ck,dict):continue
            status=ck.get("status")
            if status not in ("ok","check","violation","risk"):continue
            scope=str(ck.get("scope") or "")[:200]
            item=str(ck.get("item") or "")[:450]
            path=str(ck.get("field_path") or "")[:170]
            document=str(ck.get("document") or "")[:255]
            # Same type of legal check in the same original should not
            # repeatedly inflate the checklist on each redeploy.
            if status=="violation":
                # A confirmed checklist violation can be a separately dated
                # act despite sharing its title with an earlier checklist row.
                # It must not reproduce a finding already stored for this act.
                found=not distinct_new({**ck,"title":item},previous)
            else:
                found=c.execute("""SELECT 1 FROM checks
                    WHERE reg_number=? AND field_path=? AND item=?
                      AND COALESCE(document,'')=?
                    LIMIT 1""",(reg,path,item,document)).fetchone()
            if found:continue
            c.execute("""INSERT INTO checks
                (reg_number,scope,item,status,evidence,source,source_url,
                 document,field_path,note,law,law_url,location)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                    reg,scope,item,status,str(ck.get("evidence") or "")[:2200],
                    ck.get("source"),ck.get("source_url"),ck.get("document"),path,
                    str(ck.get("note") or "")[:3000],ck.get("law"),
                    ck.get("law_url"),ck.get("location")))
            new_c+=1
            if status=="violation":
                previous.append({**ck,"title":item,"code":path})
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
        c.commit()
        return new_f,new_c
    except:
        if c.in_transaction:c.rollback()
        raise
    finally:c.close()

async def inspect_one(ns,reg):
    old,rows=await asyncio.to_thread(existing,ns,reg)
    purchase=as_procurement(old)
    card={"status":"partial","pages":{}}
    detail={"registrationNumber":reg,"purchaseName":purchase["title"],
            "purchaseMethodName":purchase["method"],
            "publicationDate":purchase["published"],
            "submissionStart":purchase["submission_start"],
            "submissionEnd":purchase["submission_end"]}
    allowed=await asyncio.to_thread(allowed_documents,reg)
    expected=len(allowed)
    received_uids=await asyncio.to_thread(
        lambda:{uid for uid in allowed if saved_document(reg,uid) is not None})
    received=len(received_uids)
    other,unreadable=await stored_docs(ns,reg,rows)
    represented={uid_from_url((d.get("meta") or {}).get("source_url") or "")
                 for d in other if (d.get("meta") or {}).get("source") in
                 ("official_eis_original","official_eis_archive_member")}
    official=[]
    if received_uids-represented:
        try:
            # Pull from REAL, locally saved and receipt-validated originals;
            # it processes every received archive member too.
            result=await asyncio.wait_for(
                offline_audit(ns,reg,collect_only=True),timeout=280)
            official=result.get("docs") or []
            official_p=result.get("p") or {}
            for key,value in official_p.items():
                if value and (not purchase.get(key) or key in ("method","submission_end","submission_start")):
                    purchase[key]=value
            card=result.get("card") or card
            detail=result.get("detail") or detail
        except Exception as exc:
            print(f"V332 OFFICIAL_SOURCE_ERROR reg={reg} "
                  f"type={type(exc).__name__}",flush=True)
    docs=merge_docs(official,other)
    # Repair persisted identity before legal comparison. This also covers
    # purchases whose originals were imported before the metadata parser.
    from v314_eis_metadata import from_documents,apply_metadata
    from v311_verified_customer import _name
    from v342_identity import subject,valid_inn
    metadata=await asyncio.to_thread(from_documents,reg,docs,_name(purchase.get("customer")))
    if metadata:
        await asyncio.to_thread(apply_metadata,ns,reg,metadata)
        if not subject(purchase.get("title")) and metadata.get("title"):
            purchase["title"]=metadata["title"]
        if not _name(purchase.get("customer")) and metadata.get("customer"):
            purchase["customer"]=metadata["customer"]
        if not valid_inn(purchase.get("customer_inn")) and metadata.get("customer_inn"):
            purchase["customer_inn"]=metadata["customer_inn"]
    readable=[d for d in docs if d.get("status")=="checked" and str(d.get("text") or "").strip()]
    counted=len(readable)
    verified_count=sum(is_official(d) for d in readable)
    if not readable:
        return ("no_readable_documents",{
            "documents_found":len(docs)+unreadable,"documents_read":0,
            "documents_unreadable":unreadable,"verified_original_documents":0,
            "original_expected":expected,"original_received":received,
            "error":"Нет пригодного текста в доступных документах; нарушения не оценивались"})
    analyzer=ns.get("analyze")
    checker=ns.get("build_checks")
    if not callable(analyzer) or not callable(checker):
        raise RuntimeError("No legal analyzer installed")
    findings=await asyncio.to_thread(analyzer,purchase,docs)
    checks=await asyncio.to_thread(checker,purchase,detail,card,docs)
    verified_sources={d.get("name") for d in readable if is_official(d)}
    verified_urls={str((d.get("meta") or {}).get("source_url"))
                   for d in readable if is_official(d)}
    accepted=[]
    for f in findings:
        if not isinstance(f,dict) or f.get("kind")!="violation":continue
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
        else:checks.append({
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
    new_f,new_c=await run_db(persist,ns,reg,accepted,checks,stage="general_persist")
    return ("completed",{
        "documents_found":len(docs)+unreadable,
        "documents_read":counted,"documents_unreadable":unreadable,
        "verified_original_documents":verified_count,
        "original_expected":expected,"original_received":received,
        "new_findings":new_f,"new_checks":new_c,
        "error":None
    })

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
        async def work():
            await asyncio.sleep(14)
            try:
                base=await asyncio.to_thread(initialize,ns)
                print("V332 CAMPAIGN_STARTED "+json.dumps(base,ensure_ascii=False),flush=True)
            except Exception as exc:
                print("V332 INIT_FAILED type="+type(exc).__name__+
                      " reason="+str(exc)[:200],flush=True)
                return
            async def mark_result(reg,state,details):
                # Retain the already-computed result while the database is busy.
                # Each bounded retry closes its connection before yielding;
                # other workers keep running and cancellation still propagates.
                while True:
                    try:
                        return await run_db(mark,ns,reg,state,details,stage="general_mark")
                    except Exception as exc:
                        if not is_busy(exc):raise
                        await asyncio.sleep(2)

            async def worker(worker_id):
                index=0
                signals=audit_signals(ns)
                while True:
                    version=signals.version("general")
                    try:
                        reg=await run_db(claim_next,ns,stage="general_claim")
                    except Exception as exc:
                        if not is_busy(exc):raise
                        # A busy claim has changed no queue row. Keep this
                        # worker and its siblings alive rather than cancelling
                        # the whole campaign through gather().
                        await asyncio.sleep(2)
                        continue
                    if reg is None:
                        await signals.wait("general",version,30)
                        continue
                    try:
                        started=time.monotonic()
                        # EIS originals are already stored; no HTTP/EIS request.
                        # Keep the four existing audit workers; foreground
                        # file extraction always has reserved capacity.
                        with background_audit():
                            state,detail=await asyncio.wait_for(inspect_one(ns,reg),timeout=600)
                        await mark_result(reg,state,detail)
                        elapsed_ms=round((time.monotonic()-started)*1000,1)
                        index+=1
                        if state=="completed":
                            print(f"V332 AUDITED reg={reg} worker={worker_id} docs={detail['documents_read']}/"
                                  f"{detail['documents_found']} verified={detail['verified_original_documents']} "
                                  f"originals={detail['original_received']}/{detail['original_expected']} "
                                  f"new_findings={detail['new_findings']} new_checks={detail['new_checks']} "
                                  f"elapsed_ms={elapsed_ms}",
                                  flush=True)
                        elif index<=4 or index%20==0:
                            print(f"V332 NO_DOCUMENT_TEXT reg={reg}",flush=True)
                    except asyncio.CancelledError:
                        await asyncio.to_thread(mark,ns,reg,"pending",
                                                {"error":"Перезапуск Railway"})
                        raise
                    except Exception as exc:
                        index+=1
                        await mark_result(reg,"failed",{"error":str(exc)[:300]})
                        print(f"V332 AUDIT_FAILED reg={reg} worker={worker_id} "
                              f"error={type(exc).__name__}: {str(exc)[:180]}",flush=True)
                    if index%20==0:
                        try:
                            state=await run_db(progress,ns,stage="general_progress")
                            print("V332 CAMPAIGN_PROGRESS "+json.dumps(state,ensure_ascii=False),flush=True)
                        except Exception as exc:
                            if not is_busy(exc):raise
                            print("V332 PROGRESS_DELAY database_busy=True",flush=True)
                    await asyncio.sleep(.05)
            async def refresh_queue():
                signals=audit_signals(ns)
                while True:
                    version=signals.version("evidence")
                    started=time.monotonic()
                    try:
                        new=await asyncio.to_thread(add_new_verified,ns)
                        refreshed=await asyncio.to_thread(refresh_changed,ns)
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
                    await signals.wait("evidence",version,30)
            tasks=[asyncio.create_task(worker(i+1)) for i in range(GENERAL_WORKERS)]
            tasks.append(asyncio.create_task(refresh_queue()))
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        ns["_v332_campaign_worker"]=asyncio.create_task(work(),name="general-audit-campaign")
    print("V332 FULL_LEGAL_QUEUE_READY main_dashboard_only=True max_purchases=1253 "
          "partial_originals=True local_files=True no_deletions=True "
          "evidence_only=True",flush=True)
