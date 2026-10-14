"""Resumable audit of every purchase in SQLite, using all available local docs.

Never calls remote endpoints, manufactures source receipts, or deletes existing
docs, findings, checks or purchase rows. Unverified local documents are read
but any putative violations they contain become review signals only.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime,timezone
from pathlib import Path

from v295_eis_originals import root,allowed_documents,saved_document,uid_from_url
from v298_eis_only import offline_audit
from v314_eis_metadata import document_text

CAMPAIGN="all-existing-223-documents-20261014-v331"
HEX=re.compile(r"^[0-9a-f]{64}$")
REG=re.compile(r"^[0-9]{11,20}$")

def stamp():return datetime.now(timezone.utc).isoformat()

def conn(ns):
    db=ns["conn"]()
    db.execute("PRAGMA busy_timeout=30000")
    return db

def initialize(ns):
    db=conn(ns)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS v331_campaign
          (id TEXT PRIMARY KEY,created_at TEXT NOT NULL,
           baseline INTEGER NOT NULL,baseline_verified_docs INTEGER NOT NULL,
           baseline_violations INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS v331_queue
          (campaign TEXT NOT NULL,reg TEXT NOT NULL,
           state TEXT NOT NULL DEFAULT 'pending',
           local_docs INTEGER NOT NULL DEFAULT 0,
           text_docs INTEGER NOT NULL DEFAULT 0,
           trusted_docs INTEGER NOT NULL DEFAULT 0,
           missing_docs INTEGER NOT NULL DEFAULT 0,
           original_received INTEGER NOT NULL DEFAULT 0,
           original_expected INTEGER NOT NULL DEFAULT 0,
           new_violations INTEGER NOT NULL DEFAULT 0,
           review_signals INTEGER NOT NULL DEFAULT 0,
           error TEXT,updated_at TEXT,
           PRIMARY KEY(campaign,reg))""")
        db.execute("""CREATE INDEX IF NOT EXISTS v331_by_state
          ON v331_queue(campaign,state)""")
        n=db.execute("SELECT COUNT(*) FROM v331_campaign WHERE id=?",(CAMPAIGN,)).fetchone()[0]
        if not n:
            summary=db.execute("""SELECT COUNT(*),COALESCE(SUM(documents_checked),0),
               COALESCE(SUM(violations),0) FROM purchases""").fetchone()
            db.execute("INSERT INTO v331_campaign VALUES(?,?,?,?,?)",
                       (CAMPAIGN,stamp(),*map(int,summary)))
            regs=db.execute("SELECT reg_number FROM purchases ORDER BY reg_number").fetchall()
            db.executemany("INSERT OR IGNORE INTO v331_queue(campaign,reg) VALUES(?,?)",
                           ((CAMPAIGN,r[0]) for r in regs if REG.fullmatch(str(r[0]))))
        db.execute("UPDATE v331_queue SET state='pending',error='Interrupted by restart' "
                   "WHERE campaign=? AND state='running'",(CAMPAIGN,))
        db.commit()
    finally:db.close()
    return progress(ns)

def progress(ns):
    db=conn(ns)
    try:
        camp=db.execute("SELECT * FROM v331_campaign WHERE id=?",(CAMPAIGN,)).fetchone()
        if not camp:return {"campaign":CAMPAIGN,"state":"not_started"}
        states={k:int(n) for k,n in db.execute(
            "SELECT state,COUNT(*) FROM v331_queue WHERE campaign=? GROUP BY state",(CAMPAIGN,))}
        counts=db.execute("""SELECT
            COALESCE(SUM(CASE WHEN state='done' THEN local_docs ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state='done' THEN text_docs ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state='done' THEN trusted_docs ELSE 0 END),0),
            COALESCE(SUM(missing_docs),0),
            COALESCE(SUM(new_violations),0),
            COALESCE(SUM(review_signals),0),
            COALESCE(SUM(original_received),0),
            COALESCE(SUM(original_expected),0)
            FROM v331_queue WHERE campaign=?""",(CAMPAIGN,)).fetchone()
        return {"campaign":CAMPAIGN,"total":int(camp["baseline"]),
           "queued":states.get("pending",0),"running":states.get("running",0),
           "completed":states.get("done",0),"without_readable_docs":states.get("unreviewable",0),
           "failed":states.get("failed",0),
           "documents_from_processed_jobs":int(counts[0]),
           "documents_with_text":int(counts[1]),
           "verified_original_documents":int(counts[2]),
           "unavailable_document_rows":int(counts[3]),
           "new_evidence_based_findings":int(counts[4]),
           "new_review_signals":int(counts[5]),
           "received_originals":int(counts[6]),
           "declared_originals":int(counts[7]),
           "baseline_checked_document_rows":int(camp["baseline_verified_docs"]),
           "baseline_violations":int(camp["baseline_violations"]),
           "state":"finished" if not states.get("pending",0) and not states.get("running",0) else "running"}
    finally:db.close()

def next_reg(ns):
    db=conn(ns)
    try:
        row=db.execute("""SELECT reg FROM v331_queue
           WHERE campaign=? AND state='pending'
           ORDER BY CASE reg
           WHEN '32616233661' THEN 0
           WHEN '32616324151' THEN 1 ELSE 2 END,reg LIMIT 1""",(CAMPAIGN,)).fetchone()
        return str(row[0]) if row else None
    finally:db.close()

def set_status(ns,reg,state,**values):
    names={"local_docs","text_docs","trusted_docs","missing_docs",
           "original_received","original_expected","new_violations",
           "review_signals","error"}
    if set(values)-names:raise ValueError("Invalid state fields")
    columns=["state=?","updated_at=?"]+[k+"=?" for k in values]
    params=[state,stamp(),*values.values(),CAMPAIGN,reg]
    db=conn(ns)
    try:
        db.execute("UPDATE v331_queue SET "+",".join(columns)+
                   " WHERE campaign=? AND reg=?",params)
        db.commit()
    finally:db.close()

def purchase_and_docs(ns,reg):
    db=conn(ns)
    try:
        p=db.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        if not p:raise ValueError("Unknown purchase")
        rows=db.execute("SELECT * FROM docs WHERE reg_number=? ORDER BY id",(reg,)).fetchall()
        return dict(p),[dict(row) for row in rows]
    finally:db.close()

def official_meta(reg,meta,path,digest):
    if not isinstance(meta,dict):return {}
    url=meta.get("source_url") or ""
    uid=uid_from_url(url)
    if not uid:return {}
    receipt=saved_document(reg,uid)
    if receipt is None:return {}
    official,info=receipt
    if official.resolve()==path and info["sha256"]==digest:
        return {**meta,"source":"official_eis_original","source_url":url,"sha256":digest}
    if (meta.get("source")=="official_eis_archive_member"
        and info["sha256"]==meta.get("archive_sha256")
        and meta.get("sha256")==digest):
        return meta
    return {}

async def read_existing(ns,reg,rows):
    extractor=ns.get("extract_text")
    if not callable(extractor):raise RuntimeError("Text extractor absent")
    data=root().resolve()
    result=[]
    missing=0
    seen=set()
    max_bytes=25*1024*1024
    supported={".docx",".doc",".pdf",".rtf",".xlsx",".xls",
               ".odt",".ods",".txt",".xml",".csv"}
    from v310_archive_scan import ARCHIVE_EXT,extract_members
    for row in rows:
        raw=row.get("local_path")
        if not raw:missing+=1;continue
        try:
            file=Path(raw).resolve(strict=True)
            if (not file.is_relative_to(data) or not file.is_file()
                 or not 0<file.stat().st_size<=max_bytes):
                missing+=1;continue
            with file.open("rb") as stream:
                digest=hashlib.file_digest(stream,"sha256").hexdigest()
            if digest in seen:continue
            seen.add(digest)
            try:meta=json.loads(row.get("metadata_json") or "{}")
            except (ValueError,TypeError):meta={}
            if not isinstance(meta,dict):meta={}
            named_hash=meta.get("sha256")
            if named_hash and (not HEX.fullmatch(str(named_hash)) or named_hash!=digest):
                missing+=1;continue
            trusted=official_meta(reg,meta,file,digest)
            provenance=trusted if trusted else {
                "source":"existing_local_unverified","sha256":digest}
            name=str(row.get("name") or file.name)[:300]
            if file.suffix.lower() in ARCHIVE_EXT:
                uid=uid_from_url((meta or {}).get("source_url") or "") or digest[:32].upper()
                members,warnings=await asyncio.wait_for(
                    asyncio.to_thread(extract_members,file,reg,uid,root(),extractor),
                    timeout=165)
                for m in members:
                    mmeta={**provenance,"sha256":m["sha256"]}
                    if trusted:
                        mmeta["source"]="official_eis_archive_member"
                        mmeta["archive_sha256"]=digest
                    result.append({"name":name+" / "+m["name"],"url":row.get("url") or "",
                        "type":"other","published":"","meta":mmeta,
                        "text":str(m.get("text") or "")[:650000],"local":str(m["path"]),
                        "status":"checked" if str(m.get("text") or "").strip() else "downloaded_no_text",
                        "final_url":row.get("final_url") or row.get("url") or ""})
                missing+=len(warnings)
                if not members:missing+=1
                continue
            if file.suffix.lower() not in supported:
                missing+=1;continue
            text=await asyncio.wait_for(asyncio.to_thread(document_text,file,extractor),timeout=45)
            text=str(text or "")[:650000]
            result.append({"name":name,"url":row.get("url") or "",
               "type":row.get("doc_type") or "other",
               "published":row.get("published_at") or "",
               "meta":provenance,"text":text,"local":str(file),
               "status":"checked" if len(text.strip())>=3 else "downloaded_no_text",
               "final_url":row.get("final_url") or row.get("url") or ""})
            if len(text.strip())<3:missing+=1
        except (OSError,ValueError,RuntimeError,TypeError,asyncio.TimeoutError) as exc:
            missing+=1
            print(f"V331 DOC_SKIPPED reg={reg} cause={type(exc).__name__}",flush=True)
    return result,missing

def merge(official,stored):
    result=[]
    seen=set()
    for d in [*official,*stored]:
        key=(d.get("meta") or {}).get("sha256")
        if not key or not HEX.fullmatch(str(key)):continue
        if key in seen:continue
        seen.add(key)
        result.append(d)
    return result

def is_official(d):
    meta=d.get("meta") or {}
    return (d.get("status")=="checked" and
      meta.get("source") in ("official_eis_original","official_eis_archive_member")
      and HEX.fullmatch(str(meta.get("sha256") or "")) is not None
      and uid_from_url(meta.get("source_url") or "") is not None)

def p_data(p):
    from v311_verified_customer import read_verified_price
    reg=p["reg_number"]
    return {"reg":reg,"title":p.get("title") or "Закупка "+reg,
        "customer":p.get("customer") or "","method":p.get("method") or "",
        "price":p.get("price") or "","price_num":p.get("price_num"),
        "submission_start":p.get("submission_start") or "",
        "submission_end":p.get("submission_end") or "",
        "deadline":p.get("deadline") or "",
        "published":p.get("published_at") or "",
        "url":p.get("url") or "https://zakupki.gov.ru/"}

def save_analysis(ns,reg,findings,checks):
    """Append only. No old finding, check, original, or document row is removed."""
    db=conn(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        added=0
        reviewed=0
        for f in findings:
            if f.get("kind")!="violation":continue
            evidence=str(f.get("evidence") or "")[:1800]
            exists=db.execute("""
                SELECT 1 FROM findings WHERE reg_number=? AND code=?
                AND COALESCE(document,'')=COALESCE(?, '')
                AND COALESCE(evidence,'')=COALESCE(?, '') LIMIT 1
            """,(reg,f.get("code"),f.get("document"),evidence)).fetchone()
            if exists:continue
            db.execute("""INSERT INTO findings
                 (reg_number,code,kind,title,law,law_url,evidence,note,
                  document,source_url,location,match_text)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (reg,f.get("code"),"violation",f.get("title"),f.get("law"),
               f.get("law_url"),evidence,f.get("note"),f.get("document"),
               f.get("source_url"),f.get("location"),f.get("match_text")))
            added+=1
        for check in checks:
            if check.get("status") not in ("check","violation"):continue
            evidence=str(check.get("evidence") or "")[:2200]
            exists=db.execute("""
                SELECT 1 FROM checks
                WHERE reg_number=? AND item=?
                AND COALESCE(document,'')=COALESCE(?, '')
                AND COALESCE(evidence,'')=COALESCE(?, '') LIMIT 1
            """,(reg,check.get("item"),check.get("document"),evidence)).fetchone()
            if exists:continue
            db.execute("""INSERT INTO checks
               (reg_number,scope,item,status,evidence,source,source_url,
                document,field_path,note,law,law_url,location)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (reg,check.get("scope") or "Юридическая проверка",
                check.get("item") or "Требует проверки",check.get("status"),
                evidence,check.get("source"),check.get("source_url"),
                check.get("document"),check.get("field_path"),check.get("note"),
                check.get("law"),check.get("law_url"),check.get("location")))
            reviewed+=1
        confirmed=db.execute("SELECT COUNT(*) FROM findings WHERE reg_number=? AND kind='violation'",
                             (reg,)).fetchone()[0]
        confirmed+=db.execute("SELECT COUNT(*) FROM checks WHERE reg_number=? AND status='violation'",
                              (reg,)).fetchone()[0]
        db.execute("UPDATE purchases SET violations=? WHERE reg_number=?",(confirmed,reg))
        db.commit()
        return added,reviewed
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

async def audit_one(ns,reg):
    p,rows=await asyncio.to_thread(purchase_and_docs,ns,reg)
    allowed=await asyncio.to_thread(allowed_documents,reg)
    expected=len(allowed)
    received=await asyncio.to_thread(lambda:sum(
        saved_document(reg,uid) is not None for uid in allowed))
    official=[]
    context=p_data(p)
    detail={"registrationNumber":reg}
    card={"status":"partial","pages":{},"error":"Проверены доступные локальные файлы"}
    if received:
        try:
            parsed=await asyncio.wait_for(offline_audit(ns,reg,collect_only=True),timeout=310)
            official=parsed["docs"]
            context=parsed["p"]
            detail=parsed["detail"]
            card=parsed["card"]
        except Exception as exc:
            print(f"V331 OFFICIAL_COLLECT_PARTIAL reg={reg} cause={type(exc).__name__}",flush=True)
    stored,missing=await read_existing(ns,reg,rows)
    documents=merge(official,stored)
    checked=[d for d in documents if d.get("status")=="checked" and str(d.get("text") or "").strip()]
    if not checked:
        return {"state":"unreviewable","local_docs":len(documents),
            "text_docs":0,"trusted_docs":0,"missing_docs":missing,
            "original_received":received,"original_expected":expected,
            "error":"Нет сохранённых документов с извлечённым текстом"}
    analyze=ns.get("analyze")
    review=ns.get("build_checks")
    if not callable(analyze) or not callable(review):
        raise RuntimeError("Legal analyzer is unavailable")
    found=await asyncio.to_thread(analyze,context,documents)
    checks=await asyncio.to_thread(review,context,detail,card,documents)
    official_docs={(d.get("name"),(d.get("meta") or {}).get("source_url"))
                   for d in checked if is_official(d)}
    confirmed=[]
    for f in found:
        if not isinstance(f,dict) or f.get("kind")!="violation":continue
        if (f.get("document"),f.get("source_url")) in official_docs:
            confirmed.append(f)
        else:
            checks.append({"scope":"Документы закупки","item":f.get("title") or "Возможное нарушение",
                "status":"check","document":f.get("document"),
                "source":f.get("document"),"source_url":f.get("source_url"),
                "evidence":f.get("evidence"),
                "note":"Не подтверждён источник оригинала ЕИС. Не включено в счётчик.",
                "law":f.get("law"),"law_url":f.get("law_url")})
    if not official_docs:
        for c in checks:
            if c.get("status")=="violation":c["status"]="check"
    added,signals=await asyncio.to_thread(save_analysis,ns,reg,confirmed,checks)
    return {"state":"done","local_docs":len(documents),
        "text_docs":len(checked),"trusted_docs":len(official_docs),
        "missing_docs":missing,"original_received":received,
        "original_expected":expected,"new_violations":added,
        "review_signals":signals,"error":None}

def install(ns):
    app=ns["app"]
    @app.get("/api/v331/legal-recheck/progress",include_in_schema=False)
    async def read_progress():
        return await asyncio.to_thread(progress,ns)
    @app.on_event("startup")
    async def init_worker():
        async def worker():
            await asyncio.sleep(16)
            try:
                snapshot=await asyncio.to_thread(initialize,ns)
                print("V331 REAUDIT_STARTED "+json.dumps(snapshot,ensure_ascii=False),flush=True)
            except Exception as exc:
                print(f"V331 INIT_FAILED {type(exc).__name__}: {str(exc)[:180]}",flush=True)
                return
            count=0
            while True:
                reg=await asyncio.to_thread(next_reg,ns)
                if not reg:break
                try:
                    await asyncio.to_thread(set_status,ns,reg,"running")
                    async with ns["lock"]:
                        result=await asyncio.wait_for(audit_one(ns,reg),timeout=650)
                    await asyncio.to_thread(set_status,ns,reg,**result)
                    count+=1
                    if result["state"]=="done":
                        print(f"V331 AUDITED reg={reg} docs={result['local_docs']} "
                              f"checked={result['text_docs']} originals={result['trusted_docs']} "
                              f"new_violations={result['new_violations']}",flush=True)
                    elif count<=4 or count%20==0:
                        print(f"V331 UNREVIEWABLE reg={reg} local_docs={result['local_docs']}",flush=True)
                except asyncio.CancelledError:
                    await asyncio.to_thread(set_status,ns,reg,"pending",error="Interrupted restart")
                    raise
                except Exception as exc:
                    await asyncio.to_thread(set_status,ns,reg,"failed",
                                            error=f"{type(exc).__name__}: {str(exc)[:150]}")
                    print(f"V331 FAILED reg={reg} cause={type(exc).__name__}: {str(exc)[:120]}",flush=True)
                if count and count%20==0:
                    summary=await asyncio.to_thread(progress,ns)
                    print("V331 PROGRESS "+json.dumps(summary,ensure_ascii=False),flush=True)
                await asyncio.sleep(0.10)
            result=await asyncio.to_thread(progress,ns)
            print("V331 REAUDIT_FINISHED "+json.dumps(result,ensure_ascii=False),flush=True)
        asyncio.create_task(worker())
    print("V331 FULL_AUDIT_READY all_purchase_rows=True every_available_local_doc=True no_data_deletion=True",flush=True)
