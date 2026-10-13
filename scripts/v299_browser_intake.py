"""Conservative five-minute PUBLIC EIS browser-origin 223-FZ intake.

No server-side polling of zakupki.gov.ru, no risky third-party sources.
Validated EIS public registry numbers and file UID URLs enter the existing
verified-originals pipeline. No metadata alone is counted as audited.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from fastapi import HTTPException,Request
from fastapi.responses import JSONResponse,PlainTextResponse

from v295_eis_originals import (
    REG,EXT,uid_from_url,root,auth,save_atomic,
    allowed_documents,saved_document,
)

MAX_MANIFEST_BYTES=65536
MAX_DOCS=100
MAX_MANIFESTS=20000

def _safe_string(value,max_length=255):
    return str(value or "").replace("\x00","").strip()[:max_length]

def _parse_manifest(payload):
    if not isinstance(payload,dict):raise ValueError("Expected manifest object")
    reg=_safe_string(payload.get("reg_number"),20)
    if not REG.fullmatch(reg) or not (reg.startswith("3") and len(reg)==11):
        raise ValueError("Not a valid 223-FZ procurement registry number")
    source_page=_safe_string(payload.get("source_page"),1000)
    landing_page=_safe_string(payload.get("landing_page"),1000)
    try:
        source=urlsplit(source_page)
        q=parse_qs(source.query)
        source_ok=(source.scheme=="https" and source.hostname=="zakupki.gov.ru"
            and source.port is None and not source.username and not source.password
            and source.path in (
                "/epz/order/notice/notice223/documents.html",
                "/223/purchase/public/purchase/info/documents.html"))
        supplied=q.get("regNumber",[])+q.get("purchaseNoticeNumber",[])
        if supplied and (len(supplied)!=1 or supplied[0]!=reg):source_ok=False
        has_reg=bool(supplied and supplied[0]==reg)
        has_notice_id=bool(len(q.get("noticeInfoId",[]))==1
            and re.fullmatch(r"[0-9]{1,15}",q["noticeInfoId"][0]))
        landing=urlsplit(landing_page)
        lq=parse_qs(landing.query)
        landing_ok=(landing.scheme=="https" and landing.hostname=="zakupki.gov.ru"
             and landing.port is None and not landing.username and not landing.password
             and landing.path=="/epz/order/notice/notice223/common-info.html"
             and lq.get("regNumber")==[reg])
        if not source_ok or not (has_reg or (has_notice_id and landing_ok)):
            raise ValueError("Invalid official EIS notice223 provenance")
    except ValueError as exc:
        raise ValueError("Invalid official EIS document-page provenance") from exc
    docs=payload.get("documents")
    if not isinstance(docs,list) or not 1<=len(docs)<=MAX_DOCS:
        raise ValueError("Expected 1–100 EIS original-file references")
    out=[];seen=set()
    for item in docs:
        if not isinstance(item,dict):continue
        url=_safe_string(item.get("url"),1000)
        uid=uid_from_url(url)
        if not uid or uid in seen:continue
        name=_safe_string(item.get("name"),255)
        if name in (".","..") or "/" in name or "\\" in name:continue
        ext=Path(name).suffix.lower()
        if ext not in EXT:continue
        seen.add(uid)
        out.append({"uid":uid,"url":url,"name":name,"ext":ext})
    if not out:raise ValueError("No recognized official EIS attachment URLs")
    return {
        "source":"public_eis_223_browser",
        "reg_number":reg,
        "source_page":source_page,
        "landing_page":landing_page,
        "title":_safe_string(payload.get("title"),800),
        "customer":_safe_string(payload.get("customer"),400),
        "published":_safe_string(payload.get("published"),40),
        "submission_start":_safe_string(payload.get("submission_start"),40),
        "submission_end":_safe_string(payload.get("submission_end"),40),
        "price":_safe_string(payload.get("price"),60),
        "documents":out,
        "verified_originals":0,
    }

def _manifest_path(reg):
    return root()/"eis223_public_browser_intake"/reg/"manifest.json"

def _save_manifest(value):
    path=_manifest_path(value["reg_number"])
    if path.is_file():
        try:
            previous=json.loads(path.read_text(encoding="utf-8"))
            if previous.get("source")==value["source"] and previous.get("reg_number")==value["reg_number"]:
                old_docs=previous.get("documents",[])
                by_uid={x["uid"]:x for x in old_docs if isinstance(x,dict) and "uid" in x}
                for x in value["documents"]:by_uid[x["uid"]]=x
                value["documents"]=list(by_uid.values())[:MAX_DOCS]
                for key in ("title","customer","published","submission_start","submission_end","price"):
                    if not value.get(key):value[key]=previous.get(key,"")
        except (OSError,ValueError,KeyError,TypeError):
            pass
    value["received_at"]=datetime.now(timezone.utc).isoformat()
    save_atomic(path,json.dumps(value,ensure_ascii=False).encode("utf-8"))
    return len(value["documents"])

def _known(ns,regs):
    db=ns["conn"]()
    try:
        existing={}
        if regs:
            pmarks=",".join("?" for _ in regs)
            rows=db.execute(
                "SELECT reg_number,documents_checked FROM purchases WHERE reg_number IN ("+pmarks+")",
                regs).fetchall()
            existing={r[0]:int(r[1] or 0) for r in rows}
        result={}
        for reg in regs:
            refs=allowed_documents(reg)
            saved=sum(bool(saved_document(reg,uid)) for uid in refs)
            expected=len(refs)
            result[reg]={
                "manifest_documents":expected,
                "originals_received":saved,
                "checked_documents":existing.get(reg,0),
                "complete":bool(expected and saved==expected and existing.get(reg,0)>=expected),
            }
        return result
    finally:db.close()

def _scan_status():
    path=root()/"eis223_public_browser_intake"/"_monitor_status.json"
    try:
        info=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(info,dict):info={}
    except (ValueError,OSError,TypeError):
        info={}
    at=info.get("last_scan")
    active=False
    if isinstance(at,str):
        try:
            date=datetime.fromisoformat(at)
            active=(datetime.now(timezone.utc)-date)<timedelta(minutes=12)
        except (ValueError,TypeError):pass
    return {
        "browser_collector_active":active,
        "last_scan":at,
        "last_cycle_status":info.get("status","not_started"),
        "last_new_purchases":info.get("new_purchases",0),
        "last_originals_uploaded":info.get("originals_uploaded",0),
        "poll_interval_seconds":300,
        "search_requests_per_cycle":1,
        "max_procurements_per_cycle":2,
        "eis_source":"official public 223-FZ search, browser only",
        "server_eis_requests":0,
    }

def install(ns):
    app=ns["app"]

    @app.post("/api/v299/eis223/public-manifest")
    async def public_manifest(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Use official public EIS browser session")
        if request.headers.get("content-length","").isdigit() and int(request.headers["content-length"])>MAX_MANIFEST_BYTES:
            raise HTTPException(413,"Manifest exceeds limit")
        raw=await request.body()
        if len(raw)>MAX_MANIFEST_BYTES:raise HTTPException(413,"Manifest exceeds limit")
        try:
            value=_parse_manifest(json.loads(raw))
        except (ValueError,TypeError,UnicodeDecodeError) as exc:
            raise HTTPException(422,str(exc)) from exc
        qty=await asyncio.to_thread(_save_manifest,value)
        print(f"V299 PUBLIC_MANIFEST reg={value['reg_number']} docs={qty} status=metadata_only",flush=True)
        return {"accepted":True,"reg_number":value["reg_number"],
                "documents_discovered":qty,"source":"ЕИС",
                "originals_verified_increment":0}

    @app.get("/api/v299/eis223/known")
    async def known(request:Request,regs:str=""):
        auth(request)
        seen=[]
        for candidate in regs.split(",")[:40]:
            candidate=candidate.strip()
            if REG.fullmatch(candidate) and candidate not in seen:seen.append(candidate)
        result=await asyncio.to_thread(_known,ns,seen)
        return {"items":result}

    @app.post("/api/v299/eis223/heartbeat")
    async def heartbeat(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Expected EIS browser origin")
        raw=await request.body()
        if len(raw)>1500:raise HTTPException(413,"Heartbeat too large")
        try:
            body=json.loads(raw)
            if not isinstance(body,dict):raise ValueError("Expected object")
        except (ValueError,TypeError) as exc:raise HTTPException(422,"Invalid heartbeat") from exc
        vals={}
        for key in ("new_purchases","originals_uploaded"):
            value=body.get(key,0)
            vals[key]=max(0,min(100,int(value))) if str(value).isdigit() else 0
        status=str(body.get("status") or "")[:32]
        if status not in ("ok","rate_limited","fetch_error","collector_started","empty","stopped"):
            status="fetch_error"
        record={"last_scan":datetime.now(timezone.utc).isoformat(),
                "status":status,**vals}
        path=root()/"eis223_public_browser_intake"/"_monitor_status.json"
        await asyncio.to_thread(save_atomic,path,json.dumps(record).encode())
        return {"received":True,**_scan_status()}

    @app.get("/api/v299/eis223/monitor-status")
    async def monitor_status():
        return _scan_status()

    @app.get("/api/v299/eis223/collector.js")
    async def collector_script():
        path=Path(__file__).with_name("v299_browser_collector.js")
        return PlainTextResponse(path.read_text(encoding="utf-8"),media_type="text/javascript",
                                 headers={"Cache-Control":"no-store"})

    print("V299 BROWSER_DISCOVERY_READY interval=300 search_per_cycle=1 purchases_per_cycle=2 only_eis=True",flush=True)
