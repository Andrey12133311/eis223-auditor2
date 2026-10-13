"""Verified original-document bridge for official EIS 223-FZ notice archives.

The SOAP archive holds XML metadata, not the five binary originals.
Files enter the existing legal review ONLY after authenticated upload, exact
EIS UID cross-reference, format checks, and persistence on /data.
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse
from starlette.middleware.cors import CORSMiddleware

REG = re.compile(r"[0-9]{11,20}$")
UID = re.compile(r"[0-9A-F]{32}$")
EXT = frozenset((".docx",".doc",".xlsx",".xls",".pdf",".rtf",".odt",".ods",".zip",".xml"))
MAX_BYTES = 25*1024*1024
MAX_EXPANDED = 80*1024*1024

def root():
    return Path(os.environ.get("EIS223_DATA_DIR") or os.environ.get("DATA_DIR") or "/data")

def auth(request):
    expected=os.getenv("EIS223_UPLOAD_TOKEN","").strip()
    provided=request.headers.get("x-eis223-upload-token","")
    if not expected:raise HTTPException(503,"Upload key is not configured")
    if not provided or not hmac.compare_digest(expected,provided):
        raise HTTPException(401,"Unauthorized")

def uid_from_url(url):
    try:
        p=urlsplit(url)
        if p.scheme!="https" or p.hostname!="zakupki.gov.ru" or p.username or p.password:
            return None
        if p.path!="/223/filestore/public/1.0/download/fz223/file.html":
            return None
        values=parse_qs(p.query).get("uid",[])
        value=values[0].upper() if len(values)==1 else ""
        return value if UID.fullmatch(value) else None
    except (ValueError,AttributeError,TypeError):
        return None

def allowed_documents(reg):
    if not REG.fullmatch(reg):raise HTTPException(400,"Invalid registry number")
    folder=root()/"eis223_official_archive_inbox"/reg
    found={}
    # SOAP archives are preferred; browser-origin public EIS metadata is fallback.
    if not folder.is_dir():folder.mkdir(parents=True,exist_ok=True)
    for path in sorted(folder.glob("*.json"))[:100]:
        try:
            record=json.loads(path.read_text(encoding="utf-8"))
            if record.get("reg_number")!=reg or record.get("source")!="official_eis_soap_archive":
                continue
            for notice in record.get("notices",[]):
                if notice.get("reg_number")!=reg:continue
                for item in notice.get("documents",[]):
                    url=item.get("url","")
                    uid=uid_from_url(url)
                    if not uid or uid in found:continue
                    name=Path(item.get("name") or "").name
                    if not name or len(name)>255 or Path(name).suffix.lower() not in EXT:continue
                    found[uid]={"uid":uid,"name":name,"url":url,"ext":Path(name).suffix.lower()}
        except (OSError,ValueError,TypeError,KeyError):
            continue
    # Additional 223-FZ notices discovered on the PUBLIC EIS domain in a
    # browser session, without server-to-EIS traffic.
    manifest=root()/"eis223_public_browser_intake"/reg/"manifest.json"
    try:
        public=json.loads(manifest.read_text(encoding="utf-8"))
        if (public.get("reg_number")==reg and
                public.get("source")=="public_eis_223_browser" and
                isinstance(public.get("documents"),list)):
            for item in public["documents"][:100]:
                url=item.get("url","")
                uid=uid_from_url(url)
                if not uid or uid in found:continue
                name=Path(item.get("name") or "").name
                if not name or len(name)>255 or Path(name).suffix.lower() not in EXT:continue
                found[uid]={"uid":uid,"name":name,"url":url,"ext":Path(name).suffix.lower()}
    except (OSError,ValueError,TypeError,KeyError):
        pass
    return found

def saved_document(reg,uid):
    folder=root()/"eis223_official_originals"/reg/uid
    receipt=folder/"receipt.json"
    try:
        value=json.loads(receipt.read_text(encoding="utf-8"))
        if value.get("reg_number")!=reg or value.get("uid")!=uid:
            return None
        ext=value["extension"]
        digest=value["sha256"]
        if ext not in EXT or not re.fullmatch(r"[0-9a-f]{64}",digest):return None
        path=folder/(digest+ext)
        if not path.is_file() or path.stat().st_size!=value["bytes"]:return None
        return (path,value)
    except (OSError,ValueError,KeyError,TypeError):
        return None

def assert_format(data,ext):
    if len(data)<8 or len(data)>MAX_BYTES:raise ValueError("Invalid original file size")
    prefix=data[:512].lstrip().lower()
    if prefix.startswith((b"<html",b"<!doctype html",b"<?xml",b"<soap")):
        raise ValueError("Source returned HTML/XML instead of requested original")
    ole=data[:8]==bytes.fromhex("d0cf11e0a1b11ae1")
    is_zip=data[:4]==bytes.fromhex("504b0304")
    if ext in (".doc",".xls") and not ole:raise ValueError("Not a binary Office original")
    if ext==".pdf" and not data.startswith(b"%PDF"):raise ValueError("Not a PDF original")
    if ext==".rtf" and not data.startswith(bytes.fromhex("7b5c727466")):raise ValueError("Not an RTF original")
    if ext==".xml" and not data.lstrip().startswith(b"<"):raise ValueError("Not an XML original")
    if ext in (".docx",".xlsx",".odt",".ods",".zip"):
        if not is_zip:raise ValueError("Not a ZIP-based Office/archive original")
        try:
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos=z.infolist()
                if len(infos)>2000 or sum(i.file_size for i in infos)>MAX_EXPANDED:
                    raise ValueError("Unsafe compressed original")
                entries=set(z.namelist())
                if ext==".docx" and "word/document.xml" not in entries:
                    raise ValueError("Not a DOCX document")
                if ext==".xlsx" and "xl/workbook.xml" not in entries:
                    raise ValueError("Not an XLSX document")
                if ext in (".odt",".ods") and "mimetype" not in entries:
                    raise ValueError("Not an OpenDocument")
                if z.testzip() is not None:raise ValueError("Damaged ZIP")
        except zipfile.BadZipFile as e:
            raise ValueError("Damaged ZIP original") from e

def save_atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    handle,tmp=tempfile.mkstemp(prefix=".incoming-",dir=path.parent)
    try:
        with os.fdopen(handle,"wb") as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def install(ns):
    app=ns["app"]
    # Browser-only bridge: permit requests exclusively from the two official EIS origins.
    # Token authentication still protects XML/original uploads and manifests.
    app.add_middleware(CORSMiddleware,
        allow_origins=["https://zakupki.gov.ru","https://int.zakupki.gov.ru"],
        allow_methods=["GET","POST","OPTIONS"],
        allow_headers=["content-type","x-eis223-upload-token"],
        allow_credentials=False,
        max_age=600)
    discovery=ns.get("discover_eis_documents")
    downloader=ns.get("download_doc")
    extractor=ns.get("extract_text")
    doc_type=ns.get("doc_type",lambda name,desc="":"other")

    review_jobs={}
    review_states={}

    def review_state_path(reg):
        return root()/"eis223_official_review_jobs"/(reg+".json")

    def review_state(reg):
        if reg in review_states:return dict(review_states[reg])
        try:
            data=json.loads(review_state_path(reg).read_text(encoding="utf-8"))
            if isinstance(data,dict) and data.get("reg_number")==reg:return data
        except (OSError,ValueError,TypeError):pass
        return {"reg_number":reg,"status":"not_queued"}

    def update_review(reg,status,**fields):
        from datetime import datetime,timezone
        data={"reg_number":reg,"status":status,
              "updated_at":datetime.now(timezone.utc).isoformat(),**fields}
        review_states[reg]=data
        save_atomic(review_state_path(reg),json.dumps(data,ensure_ascii=False).encode("utf-8"))
        print(f"V296 EIS_REVIEW reg={reg} status={status} originals={fields.get('originals_received',0)}",flush=True)
        return data

    def review_counts(reg):
        allowed=allowed_documents(reg)
        return len(allowed),sum(bool(saved_document(reg,uid)) for uid in allowed)

    def reconcile_document_rows(reg):
        """Safely remove repeated *rows*, never delete original files or legal findings."""
        official=allowed_documents(reg)
        connector=ns.get("conn")
        if not official or connector is None:return None
        db=connector()
        try:
            db.execute("BEGIN IMMEDIATE")
            rows=[dict(r) for r in db.execute(
                "SELECT id,name,url,local_path,status,text_chars,metadata_json FROM docs WHERE reg_number=?",
                (reg,)).fetchall()]
            groups={}
            for row in rows:
                local=row.get("local_path")
                if row.get("status")!="checked" or not local:continue
                try:
                    file=Path(local)
                    if not file.is_file() or file.stat().st_size>MAX_BYTES:continue
                    with file.open("rb") as content:
                        digest=hashlib.file_digest(content,"sha256").hexdigest()
                except OSError:
                    continue
                groups.setdefault(digest,[]).append(row)
            removed=0
            for digest,group in groups.items():
                if len(group)<2:continue
                chosen=max(group,key=lambda r:int(uid_from_url(r.get("url","")) in official))
                try:
                    meta=json.loads(chosen.get("metadata_json") or "{}")
                    if not isinstance(meta,dict):meta={}
                except (ValueError,TypeError):meta={}
                alternatives=list(meta.get("identical_sources") or [])
                for row in group:
                    if row["id"]==chosen["id"]:continue
                    alternatives.append({"name":row.get("name") or "",
                                         "url":row.get("url") or "",
                                         "sha256":digest})
                    db.execute("DELETE FROM docs WHERE id=? AND reg_number=?",
                               (row["id"],reg))
                    removed+=1
                meta["identical_sources"]=alternatives
                meta["sha256"]=digest
                db.execute("UPDATE docs SET metadata_json=? WHERE id=? AND reg_number=?",
                           (json.dumps(meta,ensure_ascii=False),chosen["id"],reg))
            counts=db.execute(
                "SELECT COUNT(*) total, SUM(CASE WHEN status='checked' THEN 1 ELSE 0 END) verified "
                "FROM docs WHERE reg_number=?",(reg,)).fetchone()
            total=int(counts[0] or 0)
            verified=int(counts[1] or 0)
            db.execute(
                "UPDATE purchases SET documents_found=?, documents_checked=? WHERE reg_number=?",
                (total,verified,reg))
            db.commit()
            print(f"V297 DB_DEDUP reg={reg} removed={removed} remaining={total} checked={verified}",flush=True)
            return {"remaining":total,"checked":verified,"removed":removed}
        except Exception as exc:
            db.rollback()
            print(f"V297 DB_DEDUP_FAILED reg={reg} type={type(exc).__name__}",flush=True)
            raise
        finally:
            db.close()

    async def reconcile_rows_after_lock(reg):
        lock=ns.get("lock")
        if lock is None:return
        try:
            async with lock:
                await asyncio.to_thread(reconcile_document_rows,reg)
        except Exception as exc:
            print("V297 RECONCILE_ERROR "+type(exc).__name__,flush=True)

    async def review_worker(reg):
        total,received=review_counts(reg)
        lock=ns.get("lock")
        process=ns.get("process_purchase")
        offline=ns.get("_v298_offline_audit")
        if lock is None or (not callable(offline) and process is None):
            update_review(reg,"failed",originals_received=received,reason="Audit pipeline unavailable")
            return
        try:
            # One running official EIS analysis at a time. Never query the
            # unreachable server or other feeds when offline mode is active.
            async with lock:
                update_review(reg,"running",originals_received=received)
                if callable(offline):
                    result=await asyncio.wait_for(offline(reg),timeout=240)
                else:
                    import httpx
                    async with httpx.AsyncClient(timeout=httpx.Timeout(20,connect=6),
                                                follow_redirects=True) as client:
                        result=await asyncio.wait_for(process(client,reg,None),timeout=240)
                verified=await asyncio.to_thread(reconcile_document_rows,reg)
                checked=(int(verified["checked"]) if verified is not None
                         else int(result.get("checked_documents") or 0) if isinstance(result,dict) else 0)
                update_review(reg,"completed" if checked>=received else "partial",
                              originals_received=received,checked_documents=checked,
                              findings=result.get("findings") if isinstance(result,dict) else None,
                              deduplicated=bool(verified is not None))
        except asyncio.CancelledError:
            update_review(reg,"queued",originals_received=received,reason="Interrupted; will resume")
            raise
        except Exception as exc:
            update_review(reg,"failed",originals_received=received,reason=type(exc).__name__)

    def enqueue_review(reg,force=False):
        expected,received=review_counts(reg)
        if not expected or received<expected:
            return {"reg_number":reg,"status":"waiting_for_originals",
                    "originals_received":received,"originals_expected":expected}
        task=review_jobs.get(reg)
        if task and not task.done():
            return {**review_state(reg),"originals_expected":expected}
        previous=review_state(reg)
        if (not force and previous.get("status")=="completed"
            and previous.get("originals_received")==received):
            return {**previous,"originals_expected":expected}
        data=update_review(reg,"queued",originals_received=received,
                           originals_expected=expected)
        review_jobs[reg]=asyncio.create_task(review_worker(reg))
        return data

    @app.on_event("startup")
    async def resume_official_uploads():
        inbox=root()/"eis223_official_archive_inbox"
        public_inbox=root()/"eis223_public_browser_intake"
        for source_dir in (inbox,public_inbox):
            if source_dir.is_dir():
                for folder in sorted(source_dir.iterdir())[:500]:
                    if folder.is_dir() and REG.fullmatch(folder.name):
                        try:
                            state=enqueue_review(folder.name)
                            if state.get("status")=="completed":
                                asyncio.create_task(reconcile_rows_after_lock(folder.name))
                        except Exception as exc:
                            print("V299 REQUEUE_ERROR "+type(exc).__name__,flush=True)
        async def periodic_unique_reconciliation():
            await asyncio.sleep(600)
            while True:
                if inbox.is_dir():
                    for folder in sorted(inbox.iterdir())[:500]:
                        if folder.is_dir() and REG.fullmatch(folder.name):
                            await reconcile_rows_after_lock(folder.name)
                await asyncio.sleep(600)
        asyncio.create_task(periodic_unique_reconciliation())

    if discovery is not None:
        async def enriched_discovery(client,reg,card=None):
            from_source=await discovery(client,reg,card)
            present={x.get("url") for x in from_source if isinstance(x,dict)}
            for uid,item in allowed_documents(reg).items():
                if item["url"] in present:continue
                present.add(item["url"])
                from_source.append({"name":item["name"],"desc":"Официальный XML ЕИС (RI223)",
                    "url":item["url"],"type":doc_type(item["name"]),
                    "published":"","meta":{"source":"official_eis_getDocsIP","uid":uid},
                    "text":"","local":None,"annotated":None,"status":"metadata",
                    "error":None,"final_url":None})
            return from_source
        ns["discover_eis_documents"]=enriched_discovery

    original_batch_download=ns.get("download_all_documents")
    if original_batch_download is not None:
        async def deduplicated_batch(client,reg,docs):
            await original_batch_download(client,reg,docs)
            if not allowed_documents(reg):return
            original_count=len(docs)
            unique=[]
            fingerprints={}
            for d in docs:
                local=d.get("local") if isinstance(d,dict) else None
                if d.get("status")!="checked" or not local or not Path(local).is_file():
                    unique.append(d)
                    continue
                try:
                    p=Path(local)
                    if p.stat().st_size>MAX_BYTES:
                        unique.append(d)
                        continue
                    with p.open("rb") as f:
                        sha=hashlib.file_digest(f,"sha256").hexdigest()
                except OSError:
                    unique.append(d)
                    continue
                if sha not in fingerprints:
                    fingerprints[sha]=len(unique)
                    unique.append(d)
                    continue
                idx=fingerprints[sha]
                primary=unique[idx]
                if not isinstance(primary.get("meta"),dict):primary["meta"]={}
                if not isinstance(d.get("meta"),dict):d["meta"]={}
                alternatives=list(primary["meta"].get("identical_sources") or [])
                alternatives.append({"name":d.get("name") or "",
                                     "url":d.get("url") or ""})
                prefer_official=(d["meta"].get("source")=="official_eis_getDocsIP" and
                                 primary["meta"].get("source")!="official_eis_getDocsIP")
                if prefer_official:
                    d["meta"]["identical_sources"]=alternatives
                    unique[idx]=d
                else:
                    primary["meta"]["identical_sources"]=alternatives
            docs[:]=unique
            print(f"V296 ORIGINAL_DEDUP reg={reg} before={original_count} after={len(unique)}",flush=True)
        ns["download_all_documents"]=deduplicated_batch

    if downloader is not None and extractor is not None:
        async def downloaded_from_local(client,reg,d,cache=None):
            uid=uid_from_url(d.get("url",""))
            if uid and uid in allowed_documents(reg):
                item=saved_document(reg,uid)
                if item:
                    path,receipt=item
                    txt=await asyncio.to_thread(extractor,path)
                    text=str(txt or "")
                    chars=len(text.strip())
                    d["local"]=str(path)
                    d["text"]=text
                    d["final_url"]=d["url"]
                    d["status"]="checked" if chars>=3 else "downloaded_no_text"
                    d["error"]=None if chars>=3 else "Файл получен, но текст не извлечён"
                    d.setdefault("meta",{})["source"]="official_eis_getDocsIP"
                    d["meta"]["sha256"]=receipt["sha256"]
                    print(f"V295 LOCAL_EIS_ORIGINAL reg={reg} uid={uid} chars={chars} status={d['status']}",flush=True)
                    return
            return await downloader(client,reg,d,cache)
        ns["download_doc"]=downloaded_from_local

    @app.post("/api/v295/eis223/original/{reg}/{uid}")
    async def upload_original(reg:str,uid:str,request:Request):
        auth(request)
        if not REG.fullmatch(reg) or not UID.fullmatch(uid):
            raise HTTPException(400,"Invalid original identifier")
        allowed=allowed_documents(reg)
        if uid not in allowed:raise HTTPException(404,"Upload official XML first")
        if request.headers.get("content-length","").isdigit() and int(request.headers["content-length"])>MAX_BYTES:
            raise HTTPException(413,"Original exceeds 25 MiB")
        raw=[];size=0
        async for part in request.stream():
            size+=len(part)
            if size>MAX_BYTES:raise HTTPException(413,"Original exceeds 25 MiB")
            raw.append(part)
        data=b"".join(raw)
        entry=allowed[uid]
        try:assert_format(data,entry["ext"])
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        sha=hashlib.sha256(data).hexdigest()
        folder=root()/"eis223_official_originals"/reg/uid
        file=folder/(sha+entry["ext"])
        if not file.exists():await asyncio.to_thread(save_atomic,file,data)
        # Text extraction is diagnostic only. Existing procurement recheck remains authoritative.
        chars=0
        if extractor is not None:
            try:
                txt=await asyncio.wait_for(asyncio.to_thread(extractor,file),timeout=45)
                chars=len(str(txt or "").strip())
            except Exception as e:
                print("V295 EXTRACTION_ERROR "+type(e).__name__,flush=True)
        receipt={"reg_number":reg,"uid":uid,"sha256":sha,"bytes":size,
            "extension":entry["ext"],"source_url":entry["url"],
            "extracted_chars":chars,"state":"ready_for_legal_review" if chars>=3 else "downloaded_no_text"}
        await asyncio.to_thread(save_atomic,folder/"receipt.json",
                                json.dumps(receipt,ensure_ascii=False).encode())
        print(f"V295 ORIGINAL_IMPORTED reg={reg} uid={uid} size={size} chars={chars}",flush=True)
        review=enqueue_review(reg)
        return {"accepted":True,"reg_number":reg,"uid":uid,"bytes":size,
            "extracted_chars":chars,"status":receipt["state"],
            "review_status":review["status"],"audited_documents_increment":0}

    @app.get("/api/v295/eis223/manifest/{reg}")
    async def manifest(reg:str,request:Request):
        auth(request)
        items=[]
        for uid,d in allowed_documents(reg).items():
            receipt=saved_document(reg,uid)
            items.append({**d,"received":receipt is not None,
                          "extracted_chars":receipt[1].get("extracted_chars",0) if receipt else 0})
        return {"reg_number":reg,"metadata_received":bool(items),"documents":items,
                "counted_as_audited":False}

    @app.post("/api/v295/eis223/recheck/{reg}")
    async def recheck_official(reg:str,request:Request):
        auth(request)
        if not REG.fullmatch(reg):raise HTTPException(400,"Invalid registry number")
        info=enqueue_review(reg,force=True)
        if info["status"]=="waiting_for_originals":
            raise HTTPException(422,"Original documents are not all received")
        return JSONResponse({"reg_number":reg,**info,"queued":info["status"] in ("queued","running")},
                            status_code=202 if info["status"] in ("queued","running") else 200)

    @app.get("/api/v295/eis223/recheck-status/{reg}")
    async def recheck_status(reg:str,request:Request):
        auth(request)
        if not REG.fullmatch(reg):raise HTTPException(400,"Invalid registry number")
        total,received=review_counts(reg)
        return {**review_state(reg),"originals_received":received,"originals_expected":total}

    @app.get("/api/v295/eis223/documents",response_class=HTMLResponse)
    async def page():
        return HTMLResponse('''<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Загрузка оригиналов ЕИС</title>
<style>body{font:16px system-ui;max-width:820px;margin:2rem auto;padding:1rem;line-height:1.5}
input,button{font:inherit;padding:.65rem;margin:.5rem 0}input[type=password]{width:95%}
li{padding:.5rem 0;border-bottom:1px solid #ddd}small{display:block;color:#555}
button{cursor:pointer}pre{white-space:pre-wrap;background:#eee;padding:1rem}</style>
<h2>ЕИС 223-ФЗ — загрузка оригиналов</h2>
<p>Сначала передайте скачанный ZIP-архив XML, затем настоящие файлы DOC, DOCX, XLSX,
которые перечислены в нём. Наличие ссылки не считается проверкой.</p>
<label>Ключ EIS223_UPLOAD_TOKEN из Railway<input id="key" type="password" autocomplete="off"></label>
<label>ZIP ЕИС: <input id="zip" type="file" accept=".zip"></label>
<button id="zipbtn">1. Загрузить XML-архив</button>
<label>Номер ЕИС: <input id="reg" value="32616440427" pattern="[0-9]{11,20}"></label>
<button id="list">2. Показать документы</button>
<div id="docs"></div><button id="recheck">3. Провести юридическую проверку загруженных оригиналов</button><pre id="result"></pre>
<script>
const el=n=>document.getElementById(n);
const status=t=>el('result').textContent=t;
const token=()=>el('key').value.trim();
async function api(url,opts={}) {
if(!token())throw Error('Введите ключ EIS223_UPLOAD_TOKEN');
const r=await fetch(url,{...opts,headers:{...(opts.headers||{}),
'x-eis223-upload-token':token()}});
let j=await r.json();if(!r.ok)throw Error('HTTP '+r.status+': '+(j.detail||'Ошибка'));return j;
}
el('zipbtn').onclick=async()=>{
const file=el('zip').files[0];if(!file)return status('Выберите ZIP');
try {let r=await api('/api/v294/eis223/archive',{method:'POST',
headers:{'Content-Type':'application/zip'},body:file});
el('reg').value=r.reg_number;status('XML принят. Ссылок на документы: '+r.document_links);
el('list').click();}catch(e){status(e.message);}
};
el('recheck').onclick=async()=>{
try{
 const reg=el('reg').value.trim();
 status('Выполняется юридическая проверка, это может занять до трёх минут...');
 const r=await api('/api/v295/eis223/recheck/'+reg,{method:'POST'});
 status(r.queued?'Оригиналы в приоритетной очереди. Проверка начнётся после текущей.':
 'Статус проверки: '+(r.status||'—')+', проверенных документов: '+(r.checked_documents??'—'));
}catch(e){status(e.message);}
};
el('list').onclick=async()=>{
try{
const reg=el('reg').value.trim();
const info=await api('/api/v295/eis223/manifest/'+reg);
el('docs').replaceChildren();
for(const d of info.documents) {
const li=document.createElement('li');
const a=document.createElement('a');
a.href=d.url;a.target='_blank';a.rel='noopener noreferrer';
a.textContent='Скачать с ЕИС: '+d.name;
const input=document.createElement('input');input.type='file';
input.accept=d.ext;
const b=document.createElement('button');b.textContent='Передать оригинал';
const small=document.createElement('small');
small.textContent=d.received?'Файл уже получен; извлечено символов: '+d.extracted_chars:
'Оригинал ещё не загружен';
b.onclick=async()=>{
if(!input.files[0])return status('Выберите скачанный файл: '+d.name);
if(input.files[0].name.split('.').at(-1).toLowerCase()!==d.ext.slice(1))
return status('Выбран файл с неправильным расширением');
b.disabled=true;try{
let r=await api('/api/v295/eis223/original/'+reg+'/'+d.uid,{method:'POST',
headers:{'Content-Type':'application/octet-stream'},body:input.files[0]});
small.textContent='Получен оригинал, извлечено символов: '+r.extracted_chars+
'. Проверка: '+(r.review_status||'ожидание')+'.';
status('Сохранено '+d.name+', байт: '+r.bytes);
}catch(e){status(e.message);}finally{b.disabled=false;}}
li.append(a,document.createElement('br'),input,b,small);
el('docs').append(li);
}
if(!info.documents.length)status('XML для этой закупки ещё не загружен');
else status('Найдено '+info.documents.length+' ссылок ЕИС. Скачайте файлы и передайте их сюда.');
}catch(e){status(e.message);}
};
</script></html>''')
    print("V295 BRIDGE_READY discovery="+str(discovery is not None)+" download="+str(downloader is not None),flush=True)
