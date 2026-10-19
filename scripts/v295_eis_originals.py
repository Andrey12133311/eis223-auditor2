"""Verified original-document bridge for official EIS 223-FZ notice archives.

The SOAP archive holds XML metadata, not the five binary originals.
Files enter the existing legal review ONLY after authenticated upload, exact
EIS UID cross-reference, format checks, and persistence on /data.
"""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
import fcntl
from itertools import islice
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
EXT = frozenset((".docx",".doc",".xlsx",".xlsm",".xls",".pdf",".rtf",".odt",".ods",".zip",".rar",".7z",".tar",".gz",".tgz",".bz2",".xz",".tbz2",".txz",".cab",".xml"))
MAX_BYTES = 25*1024*1024
MAX_EXPANDED = 80*1024*1024
# Per-request limits do not truncate the accumulated evidence of later captures.
# Fail visibly when the local retention boundary is reached; never report a
# prefix of an accepted procurement as its complete document manifest.
MAX_SOURCE_RECEIPTS = 1000
MAX_KNOWN_DOCUMENTS = 10000
MAX_ORIGINAL_VERSIONS = 1000
MAX_VERSION_PROVENANCE = 1000
MAX_QUARANTINED_ORIGINALS = 20
MAX_SOURCE_MANIFEST_BYTES = 8*1024*1024


def _bounded_paths(folder, pattern, limit):
    paths=list(islice(folder.glob(pattern),limit+1))
    if len(paths)>limit:
        raise HTTPException(409,"Stored EIS evidence exceeds the bounded intake limit; review is incomplete")
    return sorted(paths)


def _merge_source_metadata(primary, secondary):
    """Keep all provenance when byte-identical source records are collapsed."""
    if not isinstance(primary.get("meta"),dict):primary["meta"]={}
    secondary_meta=secondary.get("meta")
    if not isinstance(secondary_meta,dict):secondary_meta={}
    alternatives=[]
    for source in (primary["meta"],secondary_meta):
        values=source.get("identical_sources")
        if isinstance(values,list):
            alternatives.extend(item for item in values if isinstance(item,dict))
    snapshot={key:value for key,value in secondary.items() if key not in ("text","meta")}
    snapshot["meta"]={key:value for key,value in secondary_meta.items() if key!="identical_sources"}
    alternatives.append(snapshot)
    unique={json.dumps(item,ensure_ascii=False,sort_keys=True,default=str):item for item in alternatives}
    primary["meta"]["identical_sources"]=list(unique.values())


@contextmanager
def _receipt_lock(folder):
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (folder/".receipt.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

def _rejection_code(error):
    """Expose only fixed format diagnostics in operational logs."""
    return {
        "Invalid original file size":"original_file_size",
        "ЕИС вернула HTML вместо оригинального файла":"original_is_html",
        "ЕИС вернула XML вместо запрошенного документа":"original_is_xml",
        "Не является архивом RAR":"original_not_rar",
        "Не является архивом 7z":"original_not_7z",
        "Не является GZIP":"original_not_gzip",
        "Не является BZIP2":"original_not_bzip2",
        "Не является XZ":"original_not_xz",
        "Не является TAR":"original_not_tar",
        "Не является CAB":"original_not_cab",
        "Not a binary Office original":"original_not_binary_office",
        "Not a PDF original":"original_not_pdf",
        "Not an RTF original":"original_not_rtf",
        "Not an XML original":"original_not_xml",
        "Not a ZIP-based Office/archive original":"original_not_zip",
        "Unsafe compressed original":"original_zip_expansion_limit",
        "Not a DOCX document":"original_not_docx",
        "Not an XLSX document":"original_not_xlsx",
        "Not an XLSM document":"original_not_xlsm",
        "Not an OpenDocument":"original_not_opendocument",
        "Damaged ZIP":"original_zip_crc",
        "Damaged ZIP original":"original_zip_damaged",
    }.get(str(error),"unclassified_validation_error")

def root():
    return Path(os.environ.get("EIS223_DATA_DIR") or os.environ.get("DATA_DIR") or "/data")

def vps_only():
    return os.environ.get("EIS223_SOURCE_MODE") == "OFFICIAL_EIS_VPS_ONLY"

def auth(request):
    expected=os.getenv("EIS223_UPLOAD_TOKEN","").strip()
    provided=request.headers.get("x-eis223-upload-token","")
    if not expected:raise HTTPException(503,"Upload key is not configured")
    if not provided or not hmac.compare_digest(expected,provided):
        raise HTTPException(401,"Unauthorized")

def uid_from_url(url):
    """Stable internal attachment reference from one of TWO official EIS URLs.

    The legacy numeric id maps to an internal 32-hex reference prefixed F1;
    this is NOT represented to users as an original 32-hex EIS filestore UID.
    Every manifest retains the real source URL and authentic uploaded bytes.
    """
    try:
        p=urlsplit(url)
        if (p.scheme!="https" or p.hostname!="zakupki.gov.ru"
                or p.port not in (None,443) or p.username or p.password or p.fragment):
            return None
        params=parse_qs(p.query,keep_blank_values=True)
        if p.path=="/223/filestore/public/1.0/download/fz223/file.html":
            values=params.get("uid",[])
            value=values[0].upper() if len(values)==1 else ""
            return value if UID.fullmatch(value) else None
        if p.path=="/223/purchase/public/download/download.html":
            values=params.get("id",[])
            if len(params)!=1 or len(values)!=1 or not re.fullmatch(r"[0-9]{1,15}",values[0]):
                return None
            number=int(values[0])
            return ("F1"+format(number,"030X")) if number>0 else None
        return None
    except (ValueError,AttributeError,TypeError,OverflowError):
        return None

def allowed_documents(reg):
    if not REG.fullmatch(reg):raise HTTPException(400,"Invalid registry number")
    folder=root()/"eis223_official_archive_inbox"/reg
    found={}
    # SOAP archives are preferred; browser-origin public EIS metadata is fallback.
    soap_receipts=_bounded_paths(folder,"*.json",MAX_SOURCE_RECEIPTS) if folder.is_dir() else []
    for path in soap_receipts:
        try:
            record=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record,dict) or record.get("reg_number")!=reg or record.get("source")!="official_eis_soap_archive":
                continue
            for notice in record.get("notices",[]):
                if not isinstance(notice,dict) or notice.get("reg_number")!=reg:continue
                for item in notice.get("documents",[]):
                    if not isinstance(item,dict):continue
                    url=item.get("url","")
                    uid=uid_from_url(url)
                    if not uid or uid in found:continue
                    name=Path(item.get("name") or "").name
                    if not name or len(name)>255 or Path(name).suffix.lower() not in EXT:continue
                    found[uid]={"uid":uid,"name":name,"url":url,"ext":Path(name).suffix.lower()}
                    if len(found)>MAX_KNOWN_DOCUMENTS:
                        raise HTTPException(409,"Stored EIS document history exceeds the intake limit; review is incomplete")
        except (OSError,ValueError,TypeError,KeyError):
            continue
    # Additional 223-FZ notices discovered on the PUBLIC EIS domain in a
    # browser session, without server-to-EIS traffic.
    manifest=root()/"eis223_public_browser_intake"/reg/"manifest.json"
    try:
        public=json.loads(manifest.read_text(encoding="utf-8"))
        if (isinstance(public,dict) and public.get("reg_number")==reg and
                public.get("source")=="public_eis_223_browser" and
                isinstance(public.get("documents"),list)):
            for item in public["documents"]:
                if not isinstance(item,dict):continue
                url=item.get("url","")
                uid=uid_from_url(url)
                if not uid or uid in found:continue
                name=Path(item.get("name") or "").name
                if not name or len(name)>255 or Path(name).suffix.lower() not in EXT:continue
                found[uid]={"uid":uid,"name":name,"url":url,"ext":Path(name).suffix.lower()}
                if len(found)>MAX_KNOWN_DOCUMENTS:
                    raise HTTPException(409,"Stored EIS document history exceeds the intake limit; review is incomplete")
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

def _manifest_source_evidence(value):
    """Stable legal/card facts; capture counters and timestamps are not evidence."""
    keys=("source","reg_number","documents","changes","clarifications","protocols","cancellations",
          "title","customer","customer_inn","published","submission_start","submission_end","price",
          "declared_attachment_count")
    # Schema additions such as changes=[] must not turn an unchanged legacy
    # capture into a deployment-wide recheck. Absence and empty optional facts
    # have identical meaning; an undeclared attachment count means known refs.
    evidence={key:value[key] for key in keys if key in value and value[key] not in (None,"",[])}
    documents=value.get("documents")
    if isinstance(documents,list):
        evidence["declared_attachment_count"]=max(int(value.get("declared_attachment_count") or 0),len(documents))
    for key in ("documents","changes","clarifications","protocols","cancellations"):
        if isinstance(evidence.get(key),list):
            evidence[key]=sorted(evidence[key],key=lambda item:json.dumps(item,sort_keys=True,ensure_ascii=False))
    return evidence


def _soap_source_evidence(reg):
    """Cheap scheduling identities from authenticated intake receipts.

    This is not authenticity verification: the offline reader rechecks ZIP
    and XML hashes before any legal use. Never hash/decompress original bytes
    in enqueue callbacks or request handling merely to decide whether to run.
    """
    folder=root()/"eis223_official_archive_inbox"/reg
    identities=set()
    for path in _bounded_paths(folder,"*.json",MAX_SOURCE_RECEIPTS):
        try:
            receipt=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(receipt,dict) or receipt.get("source")!="official_eis_soap_archive" or receipt.get("reg_number")!=reg:
                continue
            sha=receipt.get("archive_sha256","")
            if not isinstance(sha,str) or not re.fullmatch(r"[0-9a-f]{64}",sha):continue
            archive=folder/(sha+".zip")
            if not archive.is_file() or archive.stat().st_size>15*1024*1024:continue
            for record in receipt.get("notices",[]):
                if not isinstance(record,dict) or record.get("reg_number")!=reg:continue
                digest=record.get("xml_sha256","")
                if isinstance(digest,str) and re.fullmatch(r"[0-9a-f]{64}",digest):identities.add(digest)
        except (OSError,ValueError,TypeError):continue
    return sorted(identities)


def _review_source_version(reg):
    """Schedule by current evidence identity, never by attachment count alone.

    Receipt history is deliberately excluded: preserving an old version must
    not create extra legal findings or continuously retrigger the same audit.
    The audit still verifies the original byte hash before using evidence.
    """
    sources=[]
    for uid,item in sorted(allowed_documents(reg).items()):
        stored=saved_document(reg,uid)
        receipt=stored[1] if stored else {}
        identity={key:receipt.get(key) for key in ("sha256","extension","bytes")}
        sources.append([uid,item,identity])
    manifest={}
    path=root()/"eis223_public_browser_intake"/reg/"manifest.json"
    try:
        with path.open("rb") as stream:raw=stream.read(MAX_SOURCE_MANIFEST_BYTES+1)
        if len(raw)>MAX_SOURCE_MANIFEST_BYTES:
            raise HTTPException(409,"Stored EIS manifest exceeds the safe read limit; review is incomplete")
        value=json.loads(raw)
        if isinstance(value,dict) and value.get("reg_number")==reg and value.get("source")=="public_eis_223_browser":
            manifest=_manifest_source_evidence(value)
    except (OSError,ValueError,TypeError):pass
    encoded=json.dumps({"originals":sources,"manifest":manifest,"soap_xml":_soap_source_evidence(reg)},
                       ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _verified_unique_original_count(reg):
    """Confirm duplicate-only nonarchive coverage; never infer member coverage."""
    from v310_archive_scan import ARCHIVE_EXT
    saved=[];identities=set()
    for uid,item in allowed_documents(reg).items():
        if item["ext"] in ARCHIVE_EXT:return None
        stored=saved_document(reg,uid)
        if stored is None:return None
        path,receipt=stored
        saved.append(stored);identities.add((receipt["sha256"],receipt["extension"]))
    if len(identities)==len(saved):return None
    for path,receipt in saved:
        try:
            with path.open("rb") as stream:
                if hashlib.file_digest(stream,"sha256").hexdigest()!=receipt["sha256"]:return None
        except OSError:return None
    return len(identities)


def _store_original_version(reg,uid,entry,data):
    """Append provenance before advancing the current receipt, under one UID lock.

    History is evidence retention, not a request to count old bytes as extra
    findings. The active receipt and saved_document API keep their meaning.
    """
    sha=hashlib.sha256(data).hexdigest()
    folder=root()/"eis223_official_originals"/reg/uid
    receipt={"reg_number":reg,"uid":uid,"sha256":sha,"bytes":len(data),
             "extension":entry["ext"],"source_url":entry["url"],"name":entry["name"],
             "extracted_chars":0,"state":"stored_pending_legal_review"}
    with _receipt_lock(folder):
        versions=folder/"versions"
        existing=_bounded_paths(versions,"*.json",MAX_ORIGINAL_VERSIONS)
        current=saved_document(reg,uid)
        pending=[]
        prior_path=folder/"receipt.json"
        if prior_path.exists():
            try:
                prior=json.loads(prior_path.read_text(encoding="utf-8"))
                if (not isinstance(prior,dict) or prior.get("reg_number")!=reg or prior.get("uid")!=uid
                        or prior.get("extension") not in EXT
                        or not isinstance(prior.get("sha256"),str)
                        or not re.fullmatch(r"[0-9a-f]{64}",prior["sha256"])
                        or not isinstance(prior.get("bytes"),int) or not 0<prior["bytes"]<=MAX_BYTES):
                    raise ValueError("Invalid retained receipt")
            except (OSError,ValueError,TypeError) as exc:
                raise HTTPException(409,"Existing original receipt is damaged; upload was not replaced") from exc
            if current is None:prior={**prior,"retained_file_state":"unavailable_or_unverified"}
            pending.append(prior)
        pending.append(receipt)
        additions={}
        for item in pending:
            target=versions/(item["sha256"]+item["extension"]+".json")
            additions.setdefault(target,[]).append(item)
        if len(set(existing)|set(additions))>MAX_ORIGINAL_VERSIONS:
            raise HTTPException(409,"Original version history exceeds the intake limit; upload was not replaced")
        histories={}
        for target,observations in additions.items():
            if target.exists():
                try:
                    history=json.loads(target.read_text(encoding="utf-8"))
                    if (not isinstance(history,dict) or any(history.get(key)!=observations[0].get(key)
                            for key in ("reg_number","uid","sha256","extension"))):
                        raise ValueError("Mismatched version receipt")
                except (OSError,ValueError,TypeError) as exc:
                    raise HTTPException(409,"Original version provenance is damaged; upload was not replaced") from exc
            else:history=dict(observations[0])
            provenance=history.get("provenance",[])
            if not isinstance(provenance,list):
                raise HTTPException(409,"Original version provenance is damaged; upload was not replaced")
            seen={}
            for item in [*provenance,history,*observations]:
                if not isinstance(item,dict):
                    raise HTTPException(409,"Original version provenance is damaged; upload was not replaced")
                observation={key:value for key,value in item.items() if key!="provenance"}
                seen[json.dumps(observation,ensure_ascii=False,sort_keys=True)]=observation
            if len(seen)>MAX_VERSION_PROVENANCE:
                raise HTTPException(409,"Original provenance history exceeds the intake limit; upload was not replaced")
            if len(seen)>1:history["provenance"]=list(seen.values())
            histories[target]=history
        file=folder/(sha+entry["ext"])
        repaired=False
        if file.exists():
            # Never reuse an existing filename as proof that the expected
            # bytes survived. Preserve a corrupt target as explicitly
            # unverified diagnostic data before repairing it from this upload.
            if file.is_symlink():raise HTTPException(409,"Original target is not a regular stored file")
            with file.open("rb") as stream:prior_bytes=stream.read(MAX_BYTES+1)
            if len(prior_bytes)>MAX_BYTES:
                raise HTTPException(409,"Existing original exceeds the safe repair limit; upload was not replaced")
            actual=hashlib.sha256(prior_bytes).hexdigest()
            if actual!=sha:
                quarantine=folder/"quarantine"
                retained=_bounded_paths(quarantine,"*.json",MAX_QUARANTINED_ORIGINALS)
                diagnostic=quarantine/(sha+"-"+actual+entry["ext"]+".json")
                if diagnostic not in retained and len(retained)>=MAX_QUARANTINED_ORIGINALS:
                    raise HTTPException(409,"Original quarantine exceeds the safe repair limit; upload was not replaced")
                save_atomic(quarantine/(actual+entry["ext"]),prior_bytes)
                save_atomic(diagnostic,json.dumps({"reg_number":reg,"uid":uid,
                    "expected_sha256":sha,"observed_sha256":actual,"bytes":len(prior_bytes),
                    "state":"unverified_corrupt_original"},ensure_ascii=False).encode("utf-8"))
                save_atomic(file,data)
                repaired=True
        else:save_atomic(file,data)
        for target,history in histories.items():
            encoded=json.dumps(history,ensure_ascii=False).encode("utf-8")
            if not target.exists() or target.read_bytes()!=encoded:save_atomic(target,encoded)
        save_atomic(folder/"receipt.json",json.dumps(receipt,ensure_ascii=False).encode("utf-8"))
    changed=(repaired or current is None or any(current[1].get(key)!=receipt.get(key)
                                    for key in ("sha256","extension","bytes")))
    return receipt,changed


def assert_format(data,ext):
    if len(data)<8 or len(data)>MAX_BYTES:raise ValueError("Invalid original file size")
    prefix=data[:512].lstrip().lower()
    if prefix.startswith((b"<html",b"<!doctype html",b"<soap")):
        raise ValueError("ЕИС вернула HTML вместо оригинального файла")
    # XML is permitted only when explicitly supplied as an XML original.
    if prefix.startswith(b"<?xml") and ext!=".xml":
        raise ValueError("ЕИС вернула XML вместо запрошенного документа")
    ole=data[:8]==bytes.fromhex("d0cf11e0a1b11ae1")
    is_zip=data[:4]==bytes.fromhex("504b0304")
    rar=data.startswith(bytes.fromhex("526172211a07"))
    seven=data.startswith(bytes.fromhex("377abcaf271c"))
    gzip_data=data.startswith(bytes.fromhex("1f8b"))
    bz2_data=data.startswith(b"BZh")
    xz_data=data.startswith(bytes.fromhex("fd377a585a00"))
    tar_data=len(data)>=262 and data[257:262]==b"ustar"
    cab_data=data.startswith(b"MSCF")
    if ext==".rar" and not rar:raise ValueError("Не является архивом RAR")
    if ext==".7z" and not seven:raise ValueError("Не является архивом 7z")
    if ext in (".gz",".tgz") and not gzip_data:raise ValueError("Не является GZIP")
    if ext in (".bz2",".tbz2") and not bz2_data:raise ValueError("Не является BZIP2")
    if ext in (".xz",".txz") and not xz_data:raise ValueError("Не является XZ")
    if ext==".tar" and not tar_data:raise ValueError("Не является TAR")
    if ext==".cab" and not cab_data:raise ValueError("Не является CAB")
    if ext in (".doc",".xls") and not ole:raise ValueError("Not a binary Office original")
    if ext==".pdf" and not data.startswith(b"%PDF"):raise ValueError("Not a PDF original")
    if ext==".rtf" and not data.startswith(bytes.fromhex("7b5c727466")):raise ValueError("Not an RTF original")
    if ext==".xml" and not data.lstrip().startswith(b"<"):raise ValueError("Not an XML original")
    if ext in (".docx",".xlsx",".xlsm",".odt",".ods",".zip"):
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
                if ext in (".xlsx",".xlsm") and "xl/workbook.xml" not in entries:
                    raise ValueError("Not an XLSM document" if ext==".xlsm" else "Not an XLSX document")
                if ext==".xlsm":
                    # A macro-enabled workbook is read-only evidence. Validate
                    # its package type; never execute macros or recalculate it.
                    from eis223_soap_xml import safe_xml
                    try:
                        types=safe_xml(z.read("[Content_Types].xml"))
                        namespace="{http://schemas.openxmlformats.org/package/2006/content-types}"
                        correct=(types.tag==namespace+"Types" and any(
                            item.tag==namespace+"Override" and item.get("PartName")=="/xl/workbook.xml"
                            and item.get("ContentType")=="application/vnd.ms-excel.sheet.macroEnabled.main+xml"
                            for item in types))
                    except (KeyError,ValueError):correct=False
                    if not correct:raise ValueError("Not an XLSM document")
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
    # VPS uploads are server-to-server; browsers receive no cross-origin grant.
    if not vps_only():
        app.add_middleware(CORSMiddleware,
            allow_origins=["https://zakupki.gov.ru","https://int.zakupki.gov.ru"],
            allow_methods=["GET","POST","OPTIONS"],
            allow_headers=["content-type","x-eis223-upload-token","x-eis223-collector-version"],
            allow_credentials=False,
            max_age=600)
    discovery=ns.get("discover_eis_documents")
    downloader=ns.get("download_doc")
    extractor=ns.get("extract_text")
    doc_type=ns.get("doc_type",lambda name,desc="":"other")

    review_jobs={}
    review_states={}
    review_priorities={}

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
        received=sum(bool(saved_document(reg,uid)) for uid in allowed)
        expected=len(allowed)
        # The public ЕИС page can list attachments without filename suffixes.
        # Do not launch legal audit while such official links remain
        # unresolved, even if every already-registered file was received.
        manifest=root()/"eis223_public_browser_intake"/reg/"manifest.json"
        try:
            stored=json.loads(manifest.read_text(encoding="utf-8"))
            if stored.get("reg_number")==reg and stored.get("source")=="public_eis_223_browser":
                declared=int(stored.get("declared_attachment_count") or 0)
                if 0<declared<=100:expected=max(expected,declared)
        except (OSError,ValueError,TypeError,AttributeError):
            pass
        return expected,received

    def reconcile_document_rows(reg):
        """Safely remove repeated *rows*, never delete original files or legal findings."""
        official=allowed_documents(reg)
        connector=ns.get("conn")
        if not official or connector is None:return None
        query="SELECT * FROM docs WHERE reg_number=? ORDER BY id"
        db=connector()
        try:
            rows=[dict(r) for r in db.execute(query,(reg,)).fetchall()]
        finally:
            db.close()
        # Hashing dozens of large originals must not own SQLite's sole writer.
        # Revalidate the row snapshot and file identities before any deletion.
        groups={}
        identities={}
        for row in rows:
            local=row.get("local_path")
            if row.get("status")!="checked" or not local:continue
            try:
                file=Path(local)
                before=file.stat()
                if not file.is_file() or before.st_size>MAX_BYTES:continue
                with file.open("rb") as content:
                    digest=hashlib.file_digest(content,"sha256").hexdigest()
                after=file.stat()
                identity=lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
                if identity(before)!=identity(after):continue
                identities[local]=identity(after)
            except OSError:
                continue
            groups.setdefault(digest,[]).append(row)
        db=connector()
        try:
            db.execute("BEGIN IMMEDIATE")
            current=[dict(r) for r in db.execute(query,(reg,)).fetchall()]
            if current!=rows:
                db.rollback()
                return None
            for local,expected in identities.items():
                try:unchanged=identity(Path(local).stat())==expected
                except OSError:unchanged=False
                if not unchanged:
                    db.rollback()
                    return None
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
                    # A duplicate body can have a distinct title, date, role,
                    # or previous provenance. Retain the full source row,
                    # including its metadata, before collapsing its DB row.
                    alternatives.append({**row,"sha256":digest})
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

    async def review_worker(reg,foreground=False):
        from v381_audit_scheduling import notify_evidence,review_slot
        total,received=await asyncio.to_thread(review_counts,reg)
        lock=ns.get("lock")
        process=ns.get("process_purchase")
        offline=ns.get("_v298_offline_audit")
        if lock is None or (not callable(offline) and process is None):
            update_review(reg,"failed",originals_received=received,reason="Audit pipeline unavailable")
            return
        source_version=None
        try:
            # Preserve single-writer review while letting manual requests
            # pass queued automatic work. Running work is never interrupted.
            foreground=foreground or review_priorities.get(reg,False)
            async with review_slot(background=not foreground):
                async with lock:
                    source_version=await asyncio.to_thread(_review_source_version,reg)
                    total,received=await asyncio.to_thread(review_counts,reg)
                    update_review(reg,"running",originals_received=received,source_version=source_version)
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
                    unique=None
                    if received>=total and checked<received:
                        unique=await asyncio.to_thread(_verified_unique_original_count,reg)
                    complete=received>=total and (checked>=received or (unique is not None and checked>=unique))
                    update_review(reg,"completed" if complete else "partial",
                                  originals_received=received,checked_documents=checked,
                                  findings=result.get("findings") if isinstance(result,dict) else None,
                                  deduplicated=bool(verified is not None),source_version=source_version)
            notify_evidence(ns)
        except asyncio.CancelledError:
            update_review(reg,"queued",originals_received=received,reason="Interrupted; will resume")
            raise
        except Exception as exc:
            update_review(reg,"failed",originals_received=received,reason=type(exc).__name__,
                          source_version=source_version)

    def review_finished(reg,task):
        # An upload can arrive while the prior job owns the single-writer
        # lock. Recheck after the task is done so enqueue does not merely
        # return that old running task and lose the newly arrived evidence.
        if task.cancelled():return
        error=task.exception()
        if error is not None:
            update_review(reg,"failed",reason=type(error).__name__)
            return
        previous=review_state(reg)
        version=previous.get("source_version")
        if not version:return
        try:
            if _review_source_version(reg)!=version:enqueue_review(reg,force=True)
        except Exception as exc:
            update_review(reg,"failed",reason=type(exc).__name__)

    def enqueue_review(reg,force=False):
        expected,received=review_counts(reg)
        if not expected or received==0:
            return {"reg_number":reg,"status":"waiting_for_originals",
                    "originals_received":received,"originals_expected":expected}
        from v381_audit_scheduling import is_manual_review,promote_review
        foreground=is_manual_review(ns,reg)
        task=review_jobs.get(reg)
        if task and not task.done():
            if foreground:
                review_priorities[reg]=True
                promote_review(task)
            return {**review_state(reg),"originals_expected":expected}
        previous=review_state(reg)
        if (not force and previous.get("status")=="completed"
            and previous.get("originals_received")==received and received>=expected
            and (not previous.get("source_version") or
                 previous["source_version"]==_review_source_version(reg))):
            return {**previous,"originals_expected":expected}
        data=update_review(reg,"queued",originals_received=received,
                           originals_expected=expected)
        review_priorities[reg]=foreground
        review_jobs[reg]=asyncio.create_task(review_worker(reg,foreground))
        review_jobs[reg].add_done_callback(lambda task:review_finished(reg,task))
        return data

    # Shared offline recheck entry point for immediate manual searches.
    ns["_v295_enqueue_review"]=enqueue_review

    @app.on_event("startup")
    async def resume_official_uploads():
        inbox=root()/"eis223_official_archive_inbox"
        public_inbox=root()/"eis223_public_browser_intake"
        for source_dir in (inbox,public_inbox):
            if source_dir.is_dir():
                for folder in sorted(source_dir.iterdir())[:500]:
                    if folder.is_dir() and REG.fullmatch(folder.name):
                        try:
                            # A persistent snapshot campaign owns these
                            # saved originals. Do not queue hundreds of
                            # duplicate boot jobs ahead of that campaign.
                            owns=ns.get("_v332_owns_boot_review")
                            if callable(owns) and owns(folder.name):
                                continue
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

    if discovery is not None or vps_only():
        async def enriched_discovery(client,reg,card=None):
            from_source=[] if vps_only() else await discovery(client,reg,card)
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
    if original_batch_download is not None or vps_only():
        async def deduplicated_batch(client,reg,docs):
            if vps_only():
                cache_loader=ns.get("_cache_docs")
                cache=await asyncio.to_thread(cache_loader,reg) if callable(cache_loader) else {}
                for document in docs:
                    await ns["download_doc"](client,reg,document,cache)
            else:
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
                prefer_official=(d["meta"].get("source")=="official_eis_getDocsIP" and
                                 primary["meta"].get("source")!="official_eis_getDocsIP")
                if prefer_official:
                    _merge_source_metadata(d,primary)
                    unique[idx]=d
                else:
                    _merge_source_metadata(primary,d)
            docs[:]=unique
            print(f"V296 ORIGINAL_DEDUP reg={reg} before={original_count} after={len(unique)}",flush=True)
        ns["download_all_documents"]=deduplicated_batch

    if (downloader is not None and extractor is not None) or vps_only():
        async def downloaded_from_local(client,reg,d,cache=None):
            uid=uid_from_url(d.get("url",""))
            if uid and uid in allowed_documents(reg):
                item=saved_document(reg,uid)
                if item:
                    path,receipt=item
                    from v381_audit_scheduling import run_file_io
                    txt=await run_file_io(extractor,path) if callable(extractor) else ""
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
            if vps_only():
                # Previously saved originals remain available for local analysis,
                # including files predating the authenticated receipt pipeline.
                cache=cache or {}
                urls=[d.get("url"),d.get("final_url")]
                urls.extend((d.get("meta") or {}).get("alt_urls") or [])
                candidates=[d.get("local")]+[cache.get(url) for url in urls if url]
                for local in candidates:
                    if not local:continue
                    path=Path(local)
                    if not path.is_file():continue
                    from v381_audit_scheduling import run_file_io
                    txt=await run_file_io(extractor,path) if callable(extractor) else ""
                    text=str(txt or "")
                    chars=len(text.strip())
                    d.update(local=str(path),text=text,
                             final_url=d.get("final_url") or d.get("url"),
                             status="checked" if chars>=3 else "downloaded_no_text",
                             error=None if chars>=3 else "Файл получен, но текст не извлечён")
                    return
                d.update(local=None,text="",status="waiting_for_originals",
                         error="Оригинал ожидается от загрузчика ЕИС на VPS")
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
        except ValueError as exc:
            print(f"V295 ORIGINAL_REJECTED reg={reg} code={_rejection_code(exc)} bytes={size}",flush=True)
            raise HTTPException(422,str(exc)) from exc
        # Persist both the prior and incoming version provenance before the
        # active pointer advances. Concurrent captures cannot lose a receipt.
        receipt,changed=await asyncio.to_thread(_store_original_version,reg,uid,entry,data)
        chars=0
        print(f"V295 ORIGINAL_IMPORTED reg={reg} uid={uid} size={size} chars={chars}",flush=True)
        review=enqueue_review(reg,force=changed)
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
