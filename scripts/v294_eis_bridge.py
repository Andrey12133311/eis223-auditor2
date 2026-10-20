"""Safe official EIS 223-FZ ZIP/XML inbox; never count metadata as audited documents."""
from __future__ import annotations
import asyncio, hashlib, hmac, io, json, os, re, tempfile, zipfile
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import ClientDisconnect
from eis223_soap_xml import parse_document, quarantine_document, UnsupportedXmlType

MAX_UPLOAD = 15*1024*1024
MAX_UNCOMPRESSED = 50*1024*1024
MAX_XML = 15*1024*1024
ALLOWED_ORIGINS = frozenset(("https://int.zakupki.gov.ru","https://zakupki.gov.ru"))
REG = re.compile(r"[0-9]{11,20}$")

def _rejection_code(error):
    """Log a fixed code, never an XML name, URL, token or response excerpt."""
    message=str(error)
    if message.startswith("Unsupported official EIS XML type: "):
        return "unsupported_xml_type"
    return {
        "Archive exceeds size limit":"archive_size_limit",
        "Invalid ZIP":"invalid_zip",
        "Invalid ZIP entry count":"zip_entry_count",
        "Archive decompression size limit":"zip_expansion_limit",
        "Encrypted or oversized member":"zip_member_encrypted_or_oversized",
        "Unsafe or duplicate XML filename":"xml_filename_unsafe_or_duplicate",
        "Unreadable ZIP member":"zip_member_unreadable",
        "Mixed registry numbers in archive":"mixed_registry_numbers",
        "XML entities are disabled":"xml_entities_disabled",
        "Malformed XML":"malformed_xml",
        "Not an EIS 223-FZ purchase XML namespace":"xml_namespace",
        "Registry number/type and ZIP filename differ":"xml_filename_type_or_registry",
        "Missing or ambiguous official XML data":"xml_data_missing_or_ambiguous",
        "Foreign official XML data namespace":"xml_data_namespace",
        "Official XML data is outside the transport envelope":"xml_data_envelope",
        "Registry number and ZIP filename differ":"xml_registry_mismatch",
        "Unexpected document source host":"attachment_source_host",
        "Attachment metadata exceeds limit":"attachment_metadata_limit",
    }.get(message,"unclassified_validation_error")

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
        notices=[];names=set();unsupported=[]
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
            try:
                notice,_data=parse_document(xml,fn)
                notices.append(notice)
            except UnsupportedXmlType as exc:
                # A supported sibling can still be checked. Keep unknown
                # bytes only after independently binding their envelope to
                # that same procurement; no unknown facts/URLs are imported.
                unsupported.append((len(notices),xml,fn,exc))
                notices.append(None)
        supported=[notice for notice in notices if notice is not None]
        if not supported:
            raise unsupported[0][3]
        if len({n["reg_number"] for n in supported})!=1:
            raise ValueError("Mixed registry numbers in archive")
        reg=supported[0]["reg_number"]
        for index,xml,filename,_error in unsupported:
            notices[index]=quarantine_document(xml,filename,reg)
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

def soap_metadata_incomplete(reg):
    """Keep both audits partial while saved SOAP metadata is unsupported.

    Revalidate the saved bytes rather than trusting a receipt's completeness
    counters. This read-only check runs outside every audit write transaction;
    it never exposes unknown facts or adds an expected-original placeholder.
    """
    if not isinstance(reg,str) or not REG.fullmatch(reg):return True
    data_root=Path(os.environ.get("EIS223_DATA_DIR") or os.environ.get("DATA_DIR") or "/data")
    folder=data_root/"eis223_official_archive_inbox"/reg
    try:
        paths=list(islice(folder.glob("*.json"),251))
        if len(paths)>250:return True
        for path in paths:
            if path.stat().st_size>MAX_UPLOAD:return True
            record=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record,dict):return True
            if record.get("source")!="official_eis_soap_archive" or record.get("reg_number")!=reg:
                continue
            sha=record.get("archive_sha256","")
            if not isinstance(sha,str) or not re.fullmatch(r"[0-9a-f]{64}",sha):return True
            archive=folder/(sha+".zip")
            if not archive.is_file() or archive.stat().st_size>MAX_UPLOAD:return True
            raw=archive.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha:return True
            parsed=parse_notices(raw)
            if any(item["reg_number"]!=reg or item["xml_family"]=="unsupported" for item in parsed):
                return True
        return False
    except (OSError,ValueError,TypeError,KeyError):
        return True

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

def _archive_operation(function, args):
    # Returning worker exceptions preserves their identity across asyncio's
    # cancellation boundary; a cancelled HTTP awaiter still propagates normally.
    try:
        return None, function(*args)
    except BaseException as exc:
        return exc, None


async def _archive_io(function, *args):
    from v381_audit_scheduling import run_file_io
    error, result = await run_file_io(_archive_operation, function, args)
    if error is not None:
        raise error
    return result


def _store_archive(zip_path, json_path, raw, sha, reg, notices, unsupported_xmls):
    """Disk hashing/fsync must not hold the request event loop."""
    was_new=not zip_path.exists()
    if not was_new:
        if zip_path.stat().st_size>MAX_UPLOAD or hashlib.sha256(zip_path.read_bytes()).hexdigest()!=sha:
            raise HTTPException(409,"Stored SOAP archive fails hash verification; existing evidence was not replaced")
    if was_new: _atomic(zip_path,raw)
    if not json_path.exists():
        record={"source":"official_eis_soap_archive","reg_number":reg,
           "archive_sha256":sha,"received_at":datetime.now(timezone.utc).isoformat(),
           "notices":notices,"original_files_verified":0,
           "total_attachment_count":sum(n["total_attachment_count"] for n in notices),
           "unresolved_attachment_count":sum(n["unresolved_attachment_count"] for n in notices),
           "unsupported_attachment_count":sum(n["unsupported_attachment_count"] for n in notices),
           "unsupported_xml_count":unsupported_xmls,
           "audited_documents_increment":0,
           "status":"metadata_partial" if unsupported_xmls else "metadata_only"}
        _atomic(json_path,json.dumps(record,ensure_ascii=False).encode("utf-8"))
    return was_new


def _registry_diagnostics(raw):
    """Bounded, non-payload diagnostics for a rejected official archive.

    Only fixed type labels, digit-only registry identities and shape flags are
    emitted. No filename, XML excerpt, URL, auth header or arbitrary tag text.
    This never accepts/persists a rejected archive or changes the parser.
    """
    from eis223_soap_xml import safe_xml, direct, field, local_name, NOTICE_TYPES, PROTOCOL_TYPES
    result = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = [item for item in archive.infolist() if not item.is_dir()]
            if not 1 <= len(items) <= 100 or sum(item.file_size for item in items) > MAX_UNCOMPRESSED:
                return result
            for item in items:
                if item.flag_bits & 1 or item.file_size > MAX_XML:
                    continue
                match = re.fullmatch(r"([A-Za-z0-9]+)_([0-9]{11,20})(?:-[0-9]{2})?_[A-Za-z0-9_-]{1,160}[.]xml", item.filename)
                if not match:
                    continue
                xml = archive.read(item)
                try:
                    parse_document(xml, item.filename)
                    continue
                except ValueError as exc:
                    if not isinstance(exc, UnsupportedXmlType) and _rejection_code(exc) != "xml_registry_mismatch":
                        continue
                root = safe_xml(xml)
                kind = local_name(root)
                nodes = [node for node in root.iter() if local_name(node) == kind + "Data"]
                data = nodes[0] if len(nodes) == 1 else None
                own = field(data, "registrationNumber")
                parent = field(direct(data, "purchaseInfo"), "purchaseNoticeNumber")
                clean = lambda value: value if re.fullmatch(r"[0-9]{11,20}(?:-[0-9]{2})?", value) else "missing_or_invalid"
                result.append({"xml_type": kind if kind in NOTICE_TYPES | PROTOCOL_TYPES else "unsupported",
                               "filename_reg": match[2], "own_reg": clean(own), "purchase_reg": clean(parent),
                               "data_count": min(2, len(nodes)),
                               "own_count": min(2, sum(local_name(node) == "registrationNumber" for node in data)) if data is not None else 0,
                               "purchase_info_count": min(2, sum(local_name(node) == "purchaseInfo" for node in data)) if data is not None else 0})
                if len(result) == 4:
                    break
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        # Optional diagnostics must not replace the original validation fault.
        return []
    return result


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
        try:
            async for piece in request.stream():
                total+=len(piece)
                if total>MAX_UPLOAD:raise HTTPException(413,"Too large")
                blocks.append(piece)
        except ClientDisconnect:
            # No parsing, persistence or queue work starts before the entire
            # authenticated upload arrives; a partial body cannot be accepted.
            raise HTTPException(400,"Incomplete archive request body") from None
        raw=b"".join(blocks)
        try: notices=await _archive_io(parse_notices,raw)
        except ValueError as exc:
            code=_rejection_code(exc)
            print(f"V294 ARCHIVE_REJECTED code={code} bytes={total}",flush=True)
            headers={"X-EIS223-Rejection-Code":code}
            if code=="xml_registry_mismatch":
                # Invalid identity will not repair itself in the next five
                # minutes. Defer this job only, keeping other intake running.
                headers["Retry-After"]="1800"
                diagnostic=await _archive_io(_registry_diagnostics,raw)
                if diagnostic:
                    print("V294 XML_IDENTITY_DIAGNOSTIC "+json.dumps({
                        "archive_sha256":hashlib.sha256(raw).hexdigest(),
                        "members":diagnostic},sort_keys=True),flush=True)
            raise HTTPException(422,str(exc),headers=headers) from exc
        sha=hashlib.sha256(raw).hexdigest()
        reg=notices[0]["reg_number"]
        unsupported_xmls=sum(n["xml_family"]=="unsupported" for n in notices)
        data_root=Path(os.environ.get("EIS223_DATA_DIR") or os.environ.get("DATA_DIR") or "/data")
        folder=data_root/"eis223_official_archive_inbox"/reg
        zip_path=folder/(sha+".zip")
        json_path=folder/(sha+".json")
        from v295_eis_originals import _review_source_version
        enqueue=ns.get("_v295_enqueue_review")
        before=await asyncio.to_thread(_review_source_version,reg) if callable(enqueue) else None
        was_new=await _archive_io(_store_archive,zip_path,json_path,raw,sha,reg,notices,unsupported_xmls)
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
        if callable(enqueue):
            after=await asyncio.to_thread(_review_source_version,reg)
            if before!=after:enqueue(reg,force=True)
        print(f"V294 EIS_INBOX reg={reg} metadata={len(notices)} links={sum(n['documents_referenced'] for n in notices)} duplicate={not was_new} verified_originals=0",flush=True)
        if unsupported_xmls:
            print(f"V294 EIS_INBOX_PARTIAL reg={reg} unsupported_xmls={unsupported_xmls}",flush=True)
        return JSONResponse({"accepted":True,"reg_number":reg,"metadata_xmls":len(notices),
            "archive_sha256":sha,"metadata_import":metadata_import,
            "document_links":sum(n["documents_referenced"] for n in notices),
            "notice_xmls":sum(n["xml_family"]=="notice" for n in notices),
            "protocol_xmls":sum(n["xml_family"]=="protocol" for n in notices),
            "unresolved_attachment_count":sum(len(n["unresolved_attachments"]) for n in notices),
            "unsupported_attachment_count":sum(n["unsupported_attachment_count"] for n in notices),
            "unsupported_xml_count":unsupported_xmls,
            "already_received":not was_new,
            "status":"metadata_partial" if unsupported_xmls else "metadata_only",
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
