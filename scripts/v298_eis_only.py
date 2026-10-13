"""EIS-only mode: official browser-uploaded RI223 XML and verified originals.

No Gosplan / DaMIA / TenderGuru / Rostender / Roseltorg / ETP polling.
No direct calls to inaccessible EIS from Railway. Historical records retained.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from fastapi.responses import JSONResponse

from v295_eis_originals import (
    allowed_documents, saved_document, root as data_root,
)

REG=re.compile(r"[0-9]{11,20}$")


def tag(el):
    return el.tag.rsplit("}",1)[-1]


def direct(parent,name):
    if parent is None:return None
    return next((x for x in parent if tag(x)==name),None)


def field(parent,name):
    el=direct(parent,name)
    return (el.text or "").strip() if el is not None else ""


def official_notice(reg):
    """Read XML only from a validated, persisted official EIS SOAP archive."""
    folder=data_root()/"eis223_official_archive_inbox"/reg
    if not folder.is_dir():return None
    for receipt_path in sorted(folder.glob("*.json"),reverse=True)[:100]:
        try:
            receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("reg_number")!=reg or receipt.get("source")!="official_eis_soap_archive":
                continue
            sha=receipt["archive_sha256"]
            if not re.fullmatch("[0-9a-f]{64}",sha):continue
            archive_path=folder/(sha+".zip")
            if not archive_path.is_file() or archive_path.stat().st_size>15*1024*1024:
                continue
            with zipfile.ZipFile(archive_path) as z:
                for notice in receipt.get("notices",[])[:50]:
                    if notice.get("reg_number")!=reg:continue
                    name=notice.get("xml_filename","")
                    if name not in z.namelist():continue
                    info=z.getinfo(name)
                    if info.file_size>15*1024*1024:continue
                    xml=z.read(name)
                    if hashlib.sha256(xml).hexdigest()!=notice.get("xml_sha256"):continue
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():continue
                    root=ET.fromstring(xml)
                    if tag(root)!="purchaseNotice":continue
                    data=next((x for x in root.iter() if tag(x)=="purchaseNoticeData"),None)
                    if data is not None and field(data,"registrationNumber")==reg:
                        return data
        except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile,ET.ParseError):
            continue
    return None


def stored_purchase(ns,reg):
    db=ns["conn"]()
    try:
        row=db.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        return dict(row) if row else {}
    finally:db.close()


async def offline_audit(ns,reg):
    """Use only local official XML and saved original bytes, no remote I/O."""
    if not REG.fullmatch(reg):raise ValueError("Invalid official registry number")
    allowed=allowed_documents(reg)
    if not allowed:raise ValueError("Verified EIS XML has no documents")
    notice=await asyncio.to_thread(official_notice,reg)
    if notice is None:raise ValueError("Verified EIS XML is not available locally")

    previous=await asyncio.to_thread(stored_purchase,ns,reg)
    lot=direct(direct(notice,"lots"),"lot")
    lot_data=direct(lot,"lotData")
    cust=direct(direct(notice,"customer"),"mainInfo")
    reg_name=field(notice,"name") or previous.get("title") or ""
    customer=field(cust,"fullName") or field(cust,"shortName") or previous.get("customer") or ""
    start=field(notice,"applSubmisionStartDate")[:10]
    end=field(notice,"submissionCloseDateTime")[:10]
    published=field(notice,"publicationDateTime")
    price=field(lot_data,"initialSum") or str(previous.get("price") or "")
    num=ns.get("price_num",lambda x:None)(price)
    p={"reg":reg,
       "title":reg_name,
       "customer":customer,
       "method":field(notice,"purchaseCodeName") or previous.get("method") or "",
       "price":price,
       "price_num":num if num is not None else previous.get("price_num"),
       "published":published or previous.get("published_at") or "",
       "submission_start":start or previous.get("submission_start") or "",
       "submission_end":end or previous.get("submission_end") or "",
       "deadline":end or previous.get("deadline") or "",
       "url":ns.get("old_eis_url",lambda r:"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+r)(reg)}

    extractor=ns.get("extract_text")
    if extractor is None:raise RuntimeError("Existing original-document text extractor unavailable")
    docs=[]
    for uid,item in allowed.items():
        stored=saved_document(reg,uid)
        if stored is None:continue
        path,receipt=stored
        with path.open("rb") as f:
            sha=hashlib.file_digest(f,"sha256").hexdigest()
        if sha!=receipt["sha256"]:continue
        text=await asyncio.wait_for(asyncio.to_thread(extractor,path),timeout=45)
        text=str(text or "")
        valid=len(text.strip())>=3
        docs.append({
            "name":item["name"],
            "url":item["url"],
            "type":ns.get("doc_type",lambda n,d="":"other")(item["name"]),
            "published":"",
            "meta":{"source":"official_eis_getDocsIP","uid":uid,"sha256":sha,
                    "source_url":item["url"]},
            "text":text,
            "local":str(path),
            "annotated":None,
            "status":"checked" if valid else "downloaded_no_text",
            "error":None if valid else "Оригинал сохранён; текст не извлечён",
            "final_url":item["url"],
        })

    # Original URLs without downloaded content are deliberately NOT retried.
    # Show incomplete intake as waiting, never as an audited original.
    if not docs:raise ValueError("No readable original files have been uploaded from EIS")

    card={
       "status":"partial",
       "error":"Публичная карточка не перечитана с сервера; использованы официальный XML ЕИС и оригиналы",
       "pairs":[("registrationNumber",reg),("purchaseMethod",p["method"])],
       "visible_text":"",
       "pages":{},
       "summary":{"title":p["title"],"customer":p["customer"],
                  "method":p["method"],"published":p["published"],
                  "submission_start":p["submission_start"],
                  "submission_end":p["submission_end"],"price":p["price"],
                  "price_num":p["price_num"]},
       "discovery":"getDocsIP, RI223, official-browser-bridge",
    }
    detail={
       "registrationNumber":reg,
       "purchaseName":p["title"],
       "purchaseMethodName":p["method"],
       "customer":{"name":p["customer"]},
       "publicationDate":p["published"],
       "initialSum":p["price"],
       "submissionStart":p["submission_start"],
       "submissionEnd":p["submission_end"],
       "source":"official_eis_getDocsIP",
    }
    analyze=ns.get("analyze")
    build_checks=ns.get("build_checks")
    save=ns.get("save")
    if not all(callable(x) for x in (analyze,build_checks,save)):
        raise RuntimeError("Existing 223-FZ legal analysis functions are unavailable")
    findings=await asyncio.to_thread(analyze,p,docs)
    checks=await asyncio.to_thread(build_checks,p,detail,card,docs)
    annotate=ns.get("annotate")
    if callable(annotate):
        await asyncio.to_thread(annotate,reg,docs,findings,checks)
    payload={
       **card,
       "status":"partial",
       "origin":"official_eis_getDocsIP",
       "source_diagnostic":"EIS browser-origin archive and verified original bytes",
       "source_document_count":len(allowed),
       "attached_originals":len(docs),
       "api_fields":list(detail.items()),
    }
    await asyncio.to_thread(save,p,docs,findings,checks,payload)
    checked=sum(d.get("status")=="checked" for d in docs)
    print(f"V298 OFFLINE_AUDIT reg={reg} documents={len(docs)} checked={checked} findings={len(findings)} checks={len(checks)} remote_requests=0",flush=True)
    return {"reg":reg,"documents":len(docs),"checked_documents":checked,
            "findings":len(findings),"checks":len(checks),
            "source":"official_eis_getDocsIP","remote_requests":0}


def install(ns):
    """Switch off all historic background collectors and source-triggering routes."""
    os.environ["EIS223_SOURCE_MODE"]="OFFICIAL_EIS_BROWSER_ONLY"
    app=ns["app"]

    # Core Python module initializes its DB on import. Non-EIS start hooks launch
    # legacy scanners, retries, scrapers and timers; none may run in EIS-only mode.
    before=list(app.router.on_startup)
    kept=[]
    disabled=[]
    for fn in before:
        name=getattr(fn,"__name__",type(fn).__name__)
        if name in ("resume_official_uploads","reconcile"):
            kept.append(fn)
        else:
            disabled.append(name)
    app.router.on_startup[:]=kept

    async def no_legacy_cycle(*args,**kwargs):
        return {"started":False,"source":"ЕИС","reason":"Legacy discovery disabled"}
    ns["cycle"]=no_legacy_cycle
    ns["loop"]=no_legacy_cycle
    ns["_v290_schedule_roseltorg"]=lambda *a,**kw:None
    ns["_v290_roseltorg_attach"]=lambda *a,**kw:None
    ns["_v298_offline_audit"]=lambda reg: offline_audit(ns,reg)
    if isinstance(ns.get("state"),dict):
        ns["state"].update(running=False,current_reg=None,last_error=None)

    @app.middleware("http")
    async def eis_only_gateway(request,call_next):
        path=request.url.path.lower()
        method=request.method.upper()
        # Public historical dashboards, existing saved documents, PDF/ODT
        # output and official EIS bridge remain available.
        forbidden=(
          "/api/v278/", "/api/v279/", "/api/v280/", "/api/v281/",
          "/api/v282/", "/api/v288/", "/api/v289/", "/api/v290/",
          "/api/tenderguru", "/api/damia", "/api/rostender",
          "/api/roseltorg", "/api/gosplan",
        )
        if any(path.startswith(p) for p in forbidden):
            return JSONResponse({"source":"ЕИС","status":"disabled",
                                 "detail":"Источник отключён. Работает только официальный ЕИС через браузер."},
                                status_code=410)
        if (method in ("POST","PUT","PATCH") and
              not path.startswith(("/api/v294/eis223/","/api/v295/eis223/")) and
              (path=="/api/scan-now"
               or re.search(r"/(?:scan|retry|recheck|rescan|re-download|download-originals)(?:/|$)",path)
               or (path.startswith("/api/purchases/") and path.endswith("/recheck")))):
            return JSONResponse({"started":False,"source":"ЕИС","status":"disabled",
                                 "detail":"Старая очередь остановлена. Загружайте официальные XML и оригиналы ЕИС."})
        return await call_next(request)

    @app.get("/api/v298/eis-only/status")
    async def mode_status():
        inbox=data_root()/"eis223_official_archive_inbox"
        regs=0
        if inbox.is_dir():
            regs=sum(bool(p.is_dir() and REG.fullmatch(p.name)) for p in inbox.iterdir())
        return {"mode":"official_eis_browser_only",
                "sources":["ЕИС getDocsIP (браузерное подключение)"],
                "legacy_background_workers":0,
                "legacy_failed_requests_scheduled":0,
                "official_imported_procurements":regs,
                "existing_data_preserved":True,
                "direct_eis_server_requests":False}

    print("V298 EIS_ONLY_ACTIVE "+json.dumps({
        "remaining_hooks":[getattr(x,"__name__","?") for x in kept],
        "disabled_start_hooks":disabled,
        "legacy_sources_enabled":0,
        "offline_document_audit":True,
        "historical_data_preserved":True,
    },ensure_ascii=False),flush=True)
