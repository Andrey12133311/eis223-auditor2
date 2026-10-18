"""Safe official EIS 223-FZ ZIP/XML inbox; never count metadata as audited documents."""
from __future__ import annotations
import asyncio, hashlib, hmac, io, json, os, re, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from eis223_soap_xml import parse_document

MAX_UPLOAD = 15*1024*1024
MAX_UNCOMPRESSED = 50*1024*1024
MAX_XML = 15*1024*1024
ALLOWED_ORIGINS = frozenset(("https://int.zakupki.gov.ru","https://zakupki.gov.ru"))
REG = re.compile(r"[0-9]{11,20}$")

def _name(tag):
    return tag.rsplit("}",1)[-1]

def _child(node, key):
    for e in node:
        if _name(e.tag)==key:
            return (e.text or "").strip()
    return ""

def parse_notices(data):
    if len(data)>MAX_UPLOAD: raise ValueError("Archive exceeds size limit")
    try: archive=zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc: raise ValueError("Invalid ZIP") from exc
    try:
        items=[m for m in archive.infolist() if not m.is_dir()]
        if not 1<=len(items)<=100: raise ValueError("Invalid ZIP entry count")
        if sum(m.file_size for m in items)>MAX_UNCOMPRESSED:
            raise ValueError("Archive decompression size limit")
        notices=[];names=set()
        for m in items:
            fn=m.filename
            if m.flag_bits&1 or m.file_size>MAX_XML:
                raise ValueError("Encrypted or oversized member")
            if ("/" in fn or "\\" in fn or ".." in fn or fn in names
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,240}[.]xml",fn)):
                raise ValueError("Unsafe or duplicate XML filename")
            names.add(fn)
            try: xml=archive.read(m)
            except (zipfile.BadZipFile,RuntimeError,NotImplementedError) as exc:
                raise ValueError("Unreadable ZIP member") from exc
            notice,_data=parse_document(xml,fn)
            notices.append(notice)
        if len({n["reg_number"] for n in notices})!=1:
            raise ValueError("Mixed registry numbers in archive")
        # Match the existing binary-original intake contract exactly. Retain
        # unsupported metadata in the immutable archive, but expose blockers
        # so a worker cannot call a truncated manifest a completed download.
        from v295_eis_originals import uid_from_url, EXT
        for notice in notices:
            unsupported=[]
            for document in notice["documents"]:
                name=document["name"]
                if (not uid_from_url(document["url"]) or Path(name).name!=name
                        or Path(name).suffix.lower() not in EXT):
                    unsupported.append({"name":name,"url":document["url"],
                                        "reason":"unsupported_original_upload_reference"})
            notice["unsupported_attachments"]=unsupported
            notice["unsupported_attachment_count"]=len(unsupported)
            notice["unresolved_attachment_count"]=len(notice["unresolved_attachments"])
            notice["total_attachment_count"]=(len(notice["documents"])
                                               +len(notice["unresolved_attachments"]))
        return notices
    finally: archive.close()

def _authorize(request):
    expected=os.environ.get("EIS223_UPLOAD_TOKEN","").strip()
    given=request.headers.get("x-eis223-upload-token","")
    if not expected: raise HTTPException(503,"Upload token not configured")
    if not given or not hmac.compare_digest(expected,given):
        raise HTTPException(401,"Unauthorized")

def _atomic(path, data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix=".incoming-",dir=str(path.parent))
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def _cors(origin):
    return {"Access-Control-Allow-Origin":origin,"Vary":"Origin"} if origin in ALLOWED_ORIGINS else {}

def install(ns):
    app=ns["app"]

    @app.options("/api/v294/eis223/archive")
    async def preflight(request:Request):
        if request.headers.get("origin") not in ALLOWED_ORIGINS:
            return Response(status_code=403)
        return Response(status_code=204,headers={
            **_cors(request.headers.get("origin")),
            "Access-Control-Allow-Methods":"POST, OPTIONS",
            "Access-Control-Allow-Headers":"content-type, x-eis223-upload-token",
            "Access-Control-Max-Age":"600"
        })

    @app.post("/api/v294/eis223/archive")
    async def receive(request:Request):
        origin=request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            raise HTTPException(403,"Forbidden origin")
        _authorize(request)
        if request.headers.get("content-length","").isdigit() and int(request.headers["content-length"])>MAX_UPLOAD:
            raise HTTPException(413,"Too large")
        blocks=[];total=0
        async for piece in request.stream():
            total+=len(piece)
            if total>MAX_UPLOAD:raise HTTPException(413,"Too large")
            blocks.append(piece)
        raw=b"".join(blocks)
        try: notices=parse_notices(raw)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        sha=hashlib.sha256(raw).hexdigest()
        reg=notices[0]["reg_number"]
        data_root=Path(os.environ.get("EIS223_DATA_DIR") or os.environ.get("DATA_DIR") or "/data")
        folder=data_root/"eis223_official_archive_inbox"/reg
        zip_path=folder/(sha+".zip")
        json_path=folder/(sha+".json")
        was_new=not zip_path.exists()
        if was_new: _atomic(zip_path,raw)
        if not json_path.exists():
            record={"source":"official_eis_soap_archive","reg_number":reg,
               "archive_sha256":sha,"received_at":datetime.now(timezone.utc).isoformat(),
               "notices":notices,"original_files_verified":0,
               "total_attachment_count":sum(n["total_attachment_count"] for n in notices),
               "unresolved_attachment_count":sum(n["unresolved_attachment_count"] for n in notices),
               "unsupported_attachment_count":sum(n["unsupported_attachment_count"] for n in notices),
               "audited_documents_increment":0,"status":"metadata_only"}
            _atomic(json_path,json.dumps(record,ensure_ascii=False).encode("utf-8"))
        metadata_import="pending"
        import_metadata=ns.get("_v379_import_soap_metadata")
        if callable(import_metadata):
            try:
                metadata=await asyncio.to_thread(import_metadata,reg)
                metadata_import="stored" if metadata.get("stored") else "pending"
            except Exception as exc:
                # The durable archive is acknowledged; a retry can repair the
                # card without losing bytes or claiming a completed audit.
                print(f"V379 SOAP_METADATA_PENDING reg={reg} type={type(exc).__name__}",flush=True)
        print(f"V294 EIS_INBOX reg={reg} metadata={len(notices)} links={sum(n['documents_referenced'] for n in notices)} duplicate={not was_new} verified_originals=0",flush=True)
        return JSONResponse({"accepted":True,"reg_number":reg,"metadata_xmls":len(notices),
            "archive_sha256":sha,"metadata_import":metadata_import,
            "document_links":sum(n["documents_referenced"] for n in notices),
            "notice_xmls":sum(n["xml_family"]=="notice" for n in notices),
            "protocol_xmls":sum(n["xml_family"]=="protocol" for n in notices),
            "unresolved_attachment_count":sum(len(n["unresolved_attachments"]) for n in notices),
            "unsupported_attachment_count":sum(n["unsupported_attachment_count"] for n in notices),
            "already_received":not was_new,"status":"metadata_only",
            "original_files_verified":0,"audited_documents_increment":0},headers=_cors(origin))

    @app.get("/api/v294/eis223/upload",response_class=HTMLResponse)
    async def upload_page():
        return HTMLResponse("""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Приём архива ЕИС 223-ФЗ</title>
<style>body{font:16px system-ui;margin:3rem auto;padding:0 1rem;max-width:720px}
input,button{display:block;margin:1rem 0;padding:.7rem}input[type=password]{width:90%}
pre{white-space:pre-wrap;background:#eee;padding:1rem}</style>
<h1>Архив ЕИС: приём XML-извещения</h1>
<p>Принимается официальный ZIP с извещениями и протоколами ЕИС. Все XML, редакции и ссылки на документы сохраняются;
оригиналы DOC/PDF/XLSX не считаются проверенными.</p>
<label>Ключ EIS223_UPLOAD_TOKEN из Railway<input id="key" type="password" autocomplete="off"></label>
<label>ZIP от ЕИС<input id="archive" type="file" accept=".zip,application/zip"></label>
<button id="send">Передать архив</button><pre id="state"></pre>
<script>document.querySelector('#send').onclick=async()=>{
const f=document.querySelector('#archive').files[0],k=document.querySelector('#key').value;
const out=document.querySelector('#state');
if(!f||!k){out.textContent='Выберите ZIP и введите ключ';return}
if(f.size>15*1024*1024){out.textContent='Слишком большой файл';return}
out.textContent='Загрузка...';
try{let r=await fetch('/api/v294/eis223/archive',{method:'POST',
headers:{'Content-Type':'application/zip','x-eis223-upload-token':k},body:f});
let j=await r.json();out.textContent=r.ok?
'Принято: закупка '+j.reg_number+', XML '+j.metadata_xmls+', ссылок '+j.document_links+
'. Оригиналы ещё не проверены.':'HTTP '+r.status+': '+(j.detail||'Ошибка');}
catch(e){out.textContent='Ошибка: '+e.name;}};</script></html>""")
