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
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from v295_eis_originals import allowed_documents, saved_document, root, uid_from_url
from v298_eis_only import offline_audit
from v314_eis_metadata import document_text

CAMPAIGN="2026-10-14-full-evidence-and-price-v338"
REG=re.compile(r"^[0-9]{11,20}$")
HEX=re.compile(r"^[0-9a-f]{64}$")
FILE_EXT={".pdf",".doc",".docx",".odt",".rtf",".xlsx",".xls",
          ".ods",".xml",".txt",".csv"}
MAX_BYTES=25*1024*1024
MAX_DOC_ROWS=900
PRIORITY=["32616233661","32616324151","32616287360"]

def now():
    return datetime.now(timezone.utc).isoformat()

def connect(ns):
    c=ns["conn"]()
    c.execute("PRAGMA busy_timeout=25000")
    return c

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
        p=c.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
        d=c.execute("SELECT COALESCE(SUM(documents_checked),0) FROM purchases").fetchone()[0]
        v=c.execute("SELECT COALESCE(SUM(violations),0) FROM purchases").fetchone()[0]
        if not c.execute("SELECT 1 FROM v332_audit_run WHERE campaign=?",(CAMPAIGN,)).fetchone():
            c.execute("""INSERT INTO v332_audit_run
                (campaign,created_at,baseline_purchases,baseline_checked_documents,
                 baseline_violations) VALUES(?,?,?,?,?)""",
                (CAMPAIGN,now(),p,d,v))
            c.execute("""INSERT OR IGNORE INTO v332_audit_queue(campaign,reg)
                SELECT ?,reg_number FROM purchases""",(CAMPAIGN,))
        # Resume interrupted work, but never rerun a finished procurement just
        # because Railway redeployed a new code revision.
        c.execute("""UPDATE v332_audit_queue
            SET state='pending', error='Возобновлено после перезапуска'
            WHERE campaign=? AND state='running'""",(CAMPAIGN,))
        c.commit()
    finally:c.close()
    return progress(ns)

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
            "note":"Review of available local content; unavailable items do not count as reviewed."
        }
    finally:c.close()

def next_reg(ns):
    c=connect(ns)
    try:
        row=c.execute("""SELECT reg FROM v332_audit_queue
            WHERE campaign=? AND state='pending'
            ORDER BY CASE reg
              WHEN '32616233661' THEN 0 WHEN '32616324151' THEN 1
              WHEN '32616287360' THEN 2 ELSE 3 END,reg LIMIT 1""",
              (CAMPAIGN,)).fetchone()
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
            WHERE reg_number=? ORDER BY id LIMIT ?""",(reg,MAX_DOC_ROWS))]
        return dict(p),rows
    finally:c.close()

def as_procurement(row):
    reg=str(row["reg_number"])
    out={
        "reg":reg,
        "title":row.get("title") or ("Закупка № "+reg),
        "customer":row.get("customer") or "",
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
    extractor=ns.get("extract_text")
    if not callable(extractor):raise RuntimeError("Document extractor unavailable")
    data_root=root().resolve()
    output=[]
    unreadable=0
    for row in rows:
        raw=row.get("local_path")
        if not raw:
            unreadable+=1
            continue
        try:
            f=Path(raw).resolve(strict=True)
            if (not f.is_file() or not f.is_relative_to(data_root) or
                f.suffix.lower() not in FILE_EXT or
                not 0<f.stat().st_size<=MAX_BYTES):
                unreadable+=1
                continue
            def load_hash():
                with f.open("rb") as h:return hashlib.file_digest(h,"sha256").hexdigest()
            sha=await asyncio.to_thread(load_hash)
            try:meta=json.loads(row.get("metadata_json") or "{}")
            except (ValueError,TypeError):meta={}
            if not isinstance(meta,dict):meta={}
            if meta.get("sha256") and meta["sha256"]!=sha:
                unreadable+=1
                continue
            trusted=await asyncio.to_thread(source_verified,reg,f,sha,meta)
            meta={**meta,"sha256":sha}
            if not trusted:
                # Never let a historical label masquerade as authenticated
                # original provenance when the receipt check fails.
                meta["source"]="existing_local_unverified"
            text=await asyncio.wait_for(
                asyncio.to_thread(document_text,f,extractor),timeout=55)
            text=str(text or "")[:650000]
            readable=len(text.strip())>=3
            output.append({
                "name":row.get("name") or f.name,
                "url":row.get("url") or "",
                "type":row.get("doc_type") or "other",
                "published":row.get("published_at") or "",
                "meta":meta,"text":text,"local":str(f),"annotated":None,
                "status":"checked" if readable else "downloaded_no_text",
                "error":None if readable else "Текст не удалось извлечь",
                "final_url":row.get("final_url") or row.get("url") or ""
            })
            if not readable:unreadable+=1
        except Exception as exc:
            unreadable+=1
            print(f"V332 DOC_ERROR reg={reg} type={type(exc).__name__} "
                  f"file_id={row.get('id')}",flush=True)
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
        new_f=new_c=0
        for f in findings:
            if not isinstance(f,dict) or f.get("kind")!="violation":continue
            code=str(f.get("code") or "")[:100]
            title=str(f.get("title") or "")[:500]
            if not code or not title:continue
            found=c.execute("""SELECT 1 FROM findings WHERE reg_number=?
                AND kind='violation'
                AND (code=? OR LOWER(TRIM(title))=LOWER(TRIM(?)))
                LIMIT 1""",(reg,code,title)).fetchone()
            if found:continue
            c.execute("""INSERT INTO findings
                (reg_number,code,kind,title,law,law_url,evidence,note,
                 document,source_url,location,match_text)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(
                    reg,code,"violation",title,
                    f.get("law"),f.get("law_url"),str(f.get("evidence") or "")[:3000],
                    str(f.get("note") or "")[:3000],f.get("document"),f.get("source_url"),
                    f.get("location"),f.get("match_text")))
            new_f+=1
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
                found=c.execute("""SELECT 1 FROM checks
                    WHERE reg_number=? AND status='violation'
                      AND LOWER(TRIM(item))=LOWER(TRIM(?))
                    LIMIT 1""",(reg,item)).fetchone()
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
    received=await asyncio.to_thread(
        lambda:sum(saved_document(reg,uid) is not None for uid in allowed))
    official=[]
    if received:
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
    other,unreadable=await stored_docs(ns,reg,rows)
    docs=merge_docs(official,other)
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
    new_f,new_c=await asyncio.to_thread(persist,ns,reg,accepted,checks)
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
                print(f"V332 INIT_FAILED type={type(exc).__name__} "
                      f"reason={str(exc)[:200]}",flush=True)
                return
            index=0
            while True:
                reg=await asyncio.to_thread(next_reg,ns)
                if reg is None:break
                try:
                    await asyncio.to_thread(mark,ns,reg,"running")
                    lock=ns.get("lock")
                    if lock is None:raise RuntimeError("Application lock unavailable")
                    async with lock:
                        state,detail=await asyncio.wait_for(inspect_one(ns,reg),timeout=600)
                    await asyncio.to_thread(mark,ns,reg,state,detail)
                    index+=1
                    if state=="completed":
                        print(f"V332 AUDITED reg={reg} docs={detail['documents_read']}/"
                              f"{detail['documents_found']} verified={detail['verified_original_documents']} "
                              f"originals={detail['original_received']}/{detail['original_expected']} "
                              f"new_findings={detail['new_findings']} new_checks={detail['new_checks']}",
                              flush=True)
                    elif index<=4 or index%20==0:
                        print(f"V332 NO_DOCUMENT_TEXT reg={reg}",flush=True)
                except asyncio.CancelledError:
                    await asyncio.to_thread(mark,ns,reg,"pending",
                                            {"error":"Перезапуск Railway"})
                    raise
                except Exception as exc:
                    index+=1
                    await asyncio.to_thread(mark,ns,reg,"failed",
                                            {"error":str(exc)[:300]})
                    print(f"V332 AUDIT_FAILED reg={reg} "
                          f"error={type(exc).__name__}: {str(exc)[:180]}",flush=True)
                if index%20==0:
                    state=await asyncio.to_thread(progress,ns)
                    print("V332 CAMPAIGN_PROGRESS "+json.dumps(state,ensure_ascii=False),flush=True)
                await asyncio.sleep(.12)
            status=await asyncio.to_thread(progress,ns)
            print("V332 CAMPAIGN_FINISHED "+json.dumps(status,ensure_ascii=False),flush=True)
        asyncio.create_task(work())
    print("V332 FULL_LEGAL_QUEUE_READY all_database_purchases=True "
          "partial_originals=True local_files=True no_deletions=True "
          "evidence_only=True",flush=True)
