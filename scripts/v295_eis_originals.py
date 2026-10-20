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
import io
import hmac
import heapq
import json
import os
import re
import secrets
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect

REG = re.compile(r"[0-9]{11,20}$")
UID = re.compile(r"[0-9A-F]{32}$")
EXT = frozenset((".docx",".doc",".xlsx",".xlsm",".xls",".pdf",".rtf",".odt",".ods",".zip",".rar",".7z",".tar",".gz",".tgz",".bz2",".xz",".tbz2",".txz",".cab",".xml"))
# Matches the bounded VPS v1.4.1 original limit; XML/archive limits are unchanged.
MAX_BYTES = 64*1024*1024
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
# A revisit never expands a campaign or discovers/downloads new EIS evidence.
REVIEW_SCAN_PAGE = 32
REVIEW_SCAN_INTERVAL = 60
MAX_REVIEW_TASKS = 4
MAX_REVIEW_ADMISSIONS = 2
MAX_AUTOMATIC_ATTEMPTS = 4
REVIEW_RETRY_BASE = 60
REVIEW_RETRY_MAX = 6*60*60
REVIEW_LEASE_SECONDS = 600


class ReviewLeaseLost(RuntimeError):
    """An older process must not acknowledge or persist over a new reservation."""


def _review_input_versions(ns,reg):
    """Compute pre-analysis identity with no SQLite transaction held.

    Call through run_file_io: the semantic snapshot hashes authoritative bytes.
    A minimal in-memory DB supports the original upload-only deployments too.
    """
    import sqlite3
    from audit_source_version import semantic_source_version
    from legal_audit_revision import engine_signature
    connector=ns.get("conn")
    db=connector() if callable(connector) else sqlite3.connect(":memory:")
    try:
        semantic=semantic_source_version(db,reg)
    finally:
        db.close()
    return {"source_version":_review_source_version(reg),
            "semantic_version":semantic,"engine_signature":engine_signature()}


def _review_identity(values):
    return tuple(values.get(key) for key in
                 ("source_version","semantic_version","engine_signature"))


def _review_cohort_page(ns,source,after,limit):
    """Bounded rows/files, persistent keysets; never read unrelated evidence.

    Purchases use the indexed DB key. Directory fallback also finds orphaned
    saved receipts and queued uploads; nsmallest bounds memory while enumerating
    *names only*. No receipt, source bytes or arbitrary EIS URL is read here.
    """
    if source=="purchases":
        connector=ns.get("conn")
        if not callable(connector):return []
        db=connector()
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchases'").fetchone():
                return []
            return [str(row[0]) for row in db.execute(
                "SELECT reg_number FROM purchases WHERE reg_number>? ORDER BY reg_number LIMIT ?",
                (after,limit)).fetchall()]
        finally:db.close()
    folder=root()/source
    try:
        with os.scandir(folder) as entries:
            def names():
                for entry in entries:
                    name=entry.name[:-5] if source=="eis223_official_review_jobs" and entry.name.endswith(".json") else entry.name
                    if name>after and REG.fullmatch(name) and not entry.is_symlink():
                        yield name
            return heapq.nsmallest(limit,names())
    except FileNotFoundError:return []


def _has_review_intake(reg):
    return any((root()/name/reg).is_dir() for name in (
        "eis223_official_archive_inbox","eis223_public_browser_intake","eis223_official_originals")) or (
        root()/"eis223_official_review_jobs"/(reg+".json")).is_file()


def _restore_intake_rows(ns,reg):
    """Recover a crash after verified bytes arrived but before DB pointers saved.

    New pointers mean downloadable text is pending, NEVER that it was checked.
    Hash a bounded batch before opening the short insertion transaction. Old
    originals, rows, findings, counters and campaign membership are untouched.
    """
    connector=ns.get("conn")
    if not callable(connector):return 0
    db=connector()
    try:
        purchase_cols={row[1] for row in db.execute("PRAGMA table_info(purchases)")}
        doc_cols={row[1] for row in db.execute("PRAGMA table_info(docs)")}
        if "reg_number" not in purchase_cols or not {"reg_number","local_path","status","url"}<=doc_cols:return 0
        purchase=bool(db.execute("SELECT 1 FROM purchases WHERE reg_number=?",(reg,)).fetchone())
        rows=db.execute("SELECT url,local_path FROM docs WHERE reg_number=? LIMIT ?",
                        (reg,MAX_KNOWN_DOCUMENTS+1)).fetchall()
        if len(rows)>MAX_KNOWN_DOCUMENTS:raise RuntimeError("Intake pointer recovery limit exceeded")
        pointers={(str(row[0]),str(row[1])) for row in rows}
    finally:db.close()
    from v298_eis_only import _read_original
    pending=[];proof=False;attempted=0;hashed_bytes=0;started=time.monotonic()
    cursor_path=root()/"eis223_official_review_jobs"/".repair-cursors"/(reg+".json")
    try:
        with cursor_path.open("rb") as stream:after=json.loads(stream.read(128)).get("after","")
    except (OSError,ValueError,TypeError,AttributeError):after=""
    documents=sorted(allowed_documents(reg).items())
    page=[item for item in documents if item[0]>after][:8]
    if not page:page=documents[:8]
    last=after
    def finish(changed):
        marker=last if documents and last!=documents[-1][0] else ""
        save_atomic(cursor_path,json.dumps({"after":marker}).encode())
        return changed
    for uid,item in page:
        if time.monotonic()-started>5:break
        attempted+=1;last=uid
        stored=saved_document(reg,uid)
        if not stored:continue
        path,receipt=stored
        if (item["url"],str(path)) in pointers and (purchase or proof):continue
        before=path.stat()
        if before.st_size>MAX_BYTES:continue
        if hashed_bytes+before.st_size>64*1024*1024:
            last=page[attempted-2][0] if attempted>1 else after
            break
        hashed_bytes+=before.st_size
        verified=_read_original(reg,uid)
        after=path.stat()
        identity=lambda stat:(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        if (not verified or verified[0]!=path or verified[2]!=receipt.get("sha256")
                or any(verified[1].get(key)!=receipt.get(key) for key in ("sha256","extension","bytes"))
                or identity(before)!=identity(after)):continue
        proof=True
        if (item["url"],str(path)) in pointers:continue
        values={"reg_number":reg,"name":item["name"],"url":item["url"],
            "local_path":str(path),"status":"downloaded_no_text","text_chars":0,
            "doc_type":ns.get("doc_type",lambda name:"other")(item["name"]),
            "metadata_json":json.dumps({"source":"official_eis_original","source_url":item["url"],
                "uid":uid,"sha256":receipt["sha256"]},ensure_ascii=False),"final_url":item["url"]}
        pending.append(({key:value for key,value in values.items() if key in doc_cols},identity(after)))
    if not pending and (purchase or not proof):return finish(0)
    # Only initial metadata is restored for a missing row. Legal applicability
    # still comes from the authenticated original during normal analysis.
    values={"reg_number":reg,"title":"Закупка № "+reg,
            "url":"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+reg,
            "card_source_status":"metadata_only"}
    values={key:value for key,value in values.items() if key in purchase_cols}
    db=connector()
    try:
        db.execute("BEGIN IMMEDIATE")
        changed=0
        if not purchase:
            columns=list(values)
            changed+=db.execute("INSERT OR IGNORE INTO purchases ("+",".join(columns)+") VALUES ("+
                ",".join("?" for _ in columns)+")",tuple(values[key] for key in columns)).rowcount
        for document,expected in pending:
            # Only quick stat identity checks here; no bytes under writer lock.
            try:
                if identity(Path(document["local_path"]).stat())!=expected:continue
            except OSError:continue
            if db.execute("SELECT 1 FROM docs WHERE reg_number=? AND url=? AND local_path=? LIMIT 1",
                          (reg,document["url"],document["local_path"])).fetchone():continue
            columns=list(document)
            db.execute("INSERT INTO docs ("+",".join(columns)+") VALUES ("+
                ",".join("?" for _ in columns)+")",tuple(document[key] for key in columns))
            changed+=1
        db.commit();return finish(changed)
    except BaseException:
        db.rollback();raise
    finally:db.close()


def _try_execution_lock(reg):
    folder=root()/"eis223_official_review_jobs"/".executions"
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=(folder/(reg+".lock")).open("a+b")
    try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();return None
    return lock


def _release_execution_lock(lock):
    if lock is not None:
        fcntl.flock(lock.fileno(),fcntl.LOCK_UN);lock.close()


def _review_owned_elsewhere(ns,reg):
    """Only currently eligible persisted campaign members own automatic work."""
    connector=ns.get("conn")
    if not callable(connector):
        owns=ns.get("_v332_owns_boot_review")
        return bool(callable(owns) and owns(reg))
    db=connector()
    try:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "purchases" not in tables:return False
        columns={row[1] for row in db.execute("PRAGMA table_info(purchases)")}
        if "customer" not in columns:return False
        purchase=db.execute("SELECT customer FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        from v342_thematic_queue import business_company,customer_form,LEGAL_FORMS
        if not purchase or not business_company(purchase[0]):return False
        if "v332_audit_queue" in tables:
            from v332_all_procurements_audit import CAMPAIGN
            if db.execute("SELECT 1 FROM v332_audit_queue WHERE campaign=? AND reg=? LIMIT 1",(CAMPAIGN,reg)).fetchone():
                return True
        if {"thematic_items","thematic_batches"}<=tables:
            from thematic_period import joined_selection_sql
            form=customer_form(purchase[0])
            def selected_forms(raw):
                try:return form in set(json.loads(raw))
                except (TypeError,ValueError):return False
            db.create_function("eis_intake_selected_forms",1,selected_forms,deterministic=True)
            batch_columns={row[1] for row in db.execute("PRAGMA table_info(thematic_batches)")}
            # Match the existing migration defaults without changing a legacy DB.
            defaults=[(key,value) for key,value in (("legal_forms",json.dumps(list(LEGAL_FORMS))),
                ("date_from",""),("date_to","")) if key not in batch_columns]
            batches="thematic_batches" if not defaults else "(SELECT *,"+", ".join(
                "? AS "+key for key,_ in defaults)+" FROM thematic_batches)"
            period=joined_selection_sql(db)
            return bool(db.execute("SELECT 1 FROM thematic_items t JOIN "+batches+" b ON b.id=t.batch_id "
                "JOIN purchases p ON p.reg_number=t.reg WHERE t.reg=? AND b.status='active' "
                "AND eis_intake_selected_forms(b.legal_forms)=1 AND "+period+" LIMIT 1",
                (*[value for _,value in defaults],reg)).fetchone())
        return False
    finally:db.close()


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
    finish_jobs={}
    scan_lock=asyncio.Lock()
    scan_task=None
    pending_admissions=0
    admission_locks={}
    stopping=False
    review_priorities={}

    def review_state_path(reg):
        return root()/"eis223_official_review_jobs"/(reg+".json")

    def review_state(reg):
        if not REG.fullmatch(str(reg)):return {"reg_number":reg,"status":"not_queued"}
        try:
            with review_state_path(reg).open("rb") as stream:raw=stream.read(64*1024+1)
            if len(raw)>64*1024:raise ValueError("Review state exceeds limit")
            data=json.loads(raw)
            if isinstance(data,dict) and data.get("reg_number")==reg:return data
        except (OSError,ValueError,TypeError):pass
        return {"reg_number":reg,"status":"not_queued"}

    def public_review_state(reg):
        data=review_state(reg)
        fields=("reg_number","status","updated_at","reason","checked_documents","findings","new_findings",
                "originals_received","originals_expected","next_retry_at","retry_exhausted",
                "attempt_checked_documents","successful_coverage")
        return {key:data[key] for key in fields if key in data}

    def state_lock(reg):
        return _receipt_lock(root()/"eis223_official_review_jobs"/".leases"/reg)

    def write_review(reg,previous,status,fields):
        from datetime import datetime,timezone
        data={**previous,"reg_number":reg,"status":status,
              "updated_at":datetime.now(timezone.utc).isoformat(),**fields}
        save_atomic(review_state_path(reg),json.dumps(data,ensure_ascii=False).encode("utf-8"))
        print(f"V296 EIS_REVIEW reg={reg} status={status} originals={data.get('originals_received',0)}",flush=True)
        return data

    def update_review(reg,status,expected_token=None,**fields):
        # Atomic per-reg fencing protects a rolling restart from late writes.
        # Successful result fields survive failed/partial/snapshot attempts.
        with state_lock(reg):
            previous=review_state(reg)
            if expected_token is not None and (previous.get("lease_token") or "")!=expected_token:
                raise ReviewLeaseLost(reg)
            if expected_token is None and float(previous.get("lease_until") or 0)>time.time():
                return previous
            return write_review(reg,previous,status,fields)

    @contextmanager
    def persistence_guard(reg,token):
        # Held only across append-only SQLite persistence, never byte hashing.
        with state_lock(reg):
            if review_state(reg).get("lease_token")!=token:raise ReviewLeaseLost(reg)
            yield

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

    def record_retry(reg,status,reason,versions=None,expected_token=None,**fields):
        previous=review_state(reg)
        identity=versions or previous.get("attempt_versions") or {}
        same=_review_identity(identity)==_review_identity(previous.get("attempt_versions") or {})
        attempts=int(previous.get("attempts") or 0)+1 if same else 1
        return update_review(reg,status,expected_token=expected_token,reason=reason,attempt_versions=identity,
            lease_until=0,attempts=attempts,next_retry_at=time.time()+min(REVIEW_RETRY_MAX,
                REVIEW_RETRY_BASE*2**min(attempts-1,12)),
            retry_exhausted=attempts>=MAX_AUTOMATIC_ATTEMPTS,**fields)

    async def review_worker(reg,token,foreground=False,automatic=False):
        from v381_audit_scheduling import notify_evidence,review_slot,run_file_io
        versions=None
        received=0
        execution=None
        try:
            acquire=asyncio.create_task(run_file_io(_try_execution_lock,reg))
            try:execution=await asyncio.shield(acquire)
            except asyncio.CancelledError:
                # A lock-acquiring executor must settle before its descriptor
                # can be released; cancellation cannot orphan a process lease.
                while not acquire.done():
                    try:await asyncio.shield(acquire)
                    except asyncio.CancelledError:continue
                execution=acquire.result()
                raise
            if execution is None:return
            total,received=await run_file_io(review_counts,reg)
            lock=ns.get("lock")
            process=ns.get("process_purchase")
            offline=ns.get("_v298_offline_audit")
            # Periodic rechecks never invoke the network-capable legacy path.
            if lock is None or (not automatic and not callable(offline) and process is None):
                raise RuntimeError("Local audit pipeline unavailable")
            foreground=foreground or review_priorities.get(reg,False)
            async with review_slot(background=not foreground):
                async with lock:
                    if automatic:
                        # Changed uploads can arrive before a docs pointer was
                        # saved. Restore verified unchecked pointers before
                        # handing an existing campaign its next review.
                        if await run_file_io(_restore_intake_rows,ns,reg):notify_evidence(ns)
                    if automatic and await run_file_io(_review_owned_elsewhere,ns,reg):
                        await run_file_io(lambda:update_review(reg,"deferred",expected_token=token,
                            lease_until=0,next_retry_at=time.time()+REVIEW_SCAN_INTERVAL,
                            reason="Owned by an existing audit campaign"))
                        return
                    versions=await run_file_io(_review_input_versions,ns,reg)
                    total,received=await run_file_io(review_counts,reg)
                    previous=await run_file_io(review_state,reg)
                    attempts=int(previous.get("attempts") or 0)
                    if _review_identity(versions)!=_review_identity(previous.get("attempt_versions") or {}):attempts=0
                    await run_file_io(lambda:update_review(reg,"running",expected_token=token,
                        lease_until=time.time()+REVIEW_LEASE_SECONDS,originals_received=received,attempts=attempts,
                        source_version=versions["source_version"],attempt_versions=versions))
                    if automatic:
                        from v332_all_procurements_audit import inspect_one
                        local_ns={**ns,"_v295_persist_guard":lambda number:persistence_guard(number,token)}
                        inspect=ns.get("_v295_automatic_review") or inspect_one
                        state,result=await asyncio.wait_for(inspect(local_ns,reg),timeout=360)
                        checked=int(result.get("documents_read") or 0)
                        complete=(state=="completed" and bool(checked)
                            and int(result.get("verified_original_documents") or 0)>0
                            and not result.get("source_incomplete")
                            and not int(result.get("documents_unreadable") or 0)
                            and received>=total and total>0
                            and int(result.get("original_received") or 0)>=int(result.get("original_expected") or 0))
                        verified=None
                    elif callable(offline):
                        if str(getattr(offline,"__module__","")).endswith("v298_eis_only") and callable(ns.get("save")):
                            # Preserve initial-upload behavior while fencing a
                            # physical legacy save/metadata thread on shutdown.
                            from v298_eis_only import offline_audit
                            from audit_write_guard import background_write_guard
                            def fenced_save(*args,**kwargs):
                                with persistence_guard(reg,token):return ns["save"](*args,**kwargs)
                            local_ns={**ns,"save":fenced_save}
                            with background_write_guard(None,lambda:persistence_guard(reg,token)):
                                result=await asyncio.wait_for(offline_audit(local_ns,reg),timeout=240)
                        else:
                            result=await asyncio.wait_for(offline(reg),timeout=240)
                    else:
                        import httpx
                        async with httpx.AsyncClient(timeout=httpx.Timeout(20,connect=6),
                                                    follow_redirects=True) as client:
                            result=await asyncio.wait_for(process(client,reg,None),timeout=240)
                    if not automatic:
                        verified=await run_file_io(reconcile_document_rows,reg)
                        checked=(int(verified["checked"]) if verified is not None
                                 else int(result.get("checked_documents") or 0) if isinstance(result,dict) else 0)
                        unique=None
                        if received>=total and checked<received:
                            unique=await run_file_io(_verified_unique_original_count,reg)
                        complete=bool(total and received>=total and
                                      (checked>=received or (unique is not None and checked>=unique)))
                    if complete:
                        # Only the successfully persisted pre-analysis snapshot
                        # is acknowledged; arrivals during this run stay dirty.
                        await run_file_io(lambda:update_review(reg,"completed",expected_token=token,
                            lease_until=0,originals_received=received,checked_documents=checked,
                            findings=(result.get("findings") if not automatic else previous.get("findings")),
                            new_findings=result.get("new_findings") if automatic else None,
                            successful_coverage={"checked_documents":checked,"originals_received":received,
                                "originals_expected":total,"completed_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())},
                            deduplicated=bool(verified is not None),source_version=versions["source_version"],
                            last_source_version=versions["source_version"],
                            last_semantic_version=versions["semantic_version"],
                            last_engine_signature=versions["engine_signature"],
                            attempts=0,next_retry_at=0,retry_exhausted=False,reason=None))
                    else:
                        await run_file_io(lambda:record_retry(reg,"partial","Incomplete original coverage",versions,
                            expected_token=token,originals_received=received,attempt_checked_documents=checked))
            notify_evidence(ns)
        except ReviewLeaseLost:
            return
        except asyncio.CancelledError:
            try:
                await run_file_io(lambda:update_review(reg,"queued",expected_token=token,lease_until=0,
                    originals_received=received,reason="Interrupted; will resume"))
            except ReviewLeaseLost:pass
            raise
        except Exception as exc:
            reason=type(exc).__name__
            try:
                await run_file_io(lambda:record_retry(reg,"failed",reason,versions,
                    expected_token=token,originals_received=received))
            except ReviewLeaseLost:pass
        finally:
            _release_execution_lock(execution)

    async def _check_arrivals(reg,token):
        from v381_audit_scheduling import run_file_io
        changed=False
        previous={};versions=None
        try:
            previous=await run_file_io(review_state,reg)
            versions=previous.get("attempt_versions")
            if previous.get("lease_token")!=token:return
            # Receipt changes, card corrections and engine revisions all use
            # the same lossless finish-time comparison. No synchronous hashing.
            if versions and (previous.get("status") in ("completed","partial","failed")
                             or previous.get("pending_arrival")):
                current=await run_file_io(_review_input_versions,ns,reg)
                changed=bool(previous.get("pending_arrival")) or _review_identity(current)!=_review_identity(versions)
        except Exception as exc:
            # Preserve a complete result when snapshotting becomes unavailable.
            reason=type(exc).__name__
            try:
                await run_file_io(lambda:record_retry(reg,previous.get("status","failed"),reason,versions,
                                                     expected_token=token))
            except ReviewLeaseLost:pass
        finally:
            if finish_jobs.get(reg) is asyncio.current_task():finish_jobs.pop(reg,None)
        if changed:
            latest=await run_file_io(review_state,reg)
            acknowledged={"source_version":latest.get("last_source_version"),
                          "semantic_version":latest.get("last_semantic_version"),
                          "engine_signature":latest.get("last_engine_signature")}
            if latest.get("status")!="completed" or _review_identity(acknowledged)!=_review_identity(current):
                await enqueue_automatic(reg,force=True,automatic=bool(previous.get("automatic")))

    async def check_arrivals(reg,token):
        from v381_audit_scheduling import background_audit
        with background_audit():await _check_arrivals(reg,token)

    def review_finished(reg,token,task):
        if review_jobs.get(reg) is not task:return
        review_jobs.pop(reg,None)
        review_priorities.pop(reg,None)
        if task.cancelled():return
        # Retrieve exceptions even if storage failure prevented retry metadata.
        error=task.exception()
        if error is not None:print("V295 REVIEW_ERROR "+type(error).__name__,flush=True)
        if stopping:return
        finish_jobs[reg]=asyncio.create_task(check_arrivals(reg,token))

    def record_completion_arrival(reg,force=False,versions=None):
        """Queue changed intake without stealing a still-settling worker's claim."""
        with state_lock(reg):
            previous=review_state(reg)
            if previous.get("status") not in ("completed","partial","failed"):return previous
            if versions is not None:
                changed=_review_identity(versions)!=_review_identity(previous.get("attempt_versions") or {})
            else:
                source=_review_source_version(reg)
                changed=bool(previous.get("source_version") and previous["source_version"]!=source)
            if not force and not changed:return previous
            # Keep the token, successful watermark and coverage. The current
            # process will consume this after releasing its execution flock;
            # after a crash the existing bounded scan sees this durable queue.
            return write_review(reg,previous,"queued",{"pending_arrival":True,
                "next_retry_at":0,"retry_exhausted":False,"attempts":0,
                "reason":"New intake is queued while the previous review finishes"})

    def saved_review_exists(reg,previous):
        """A repeat must retain active legal rows, including signed finding IDs."""
        connector=ns.get("conn")
        if not callable(connector):return False
        db=connector()
        try:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "purchases" not in tables:return False
            columns={row[1] for row in db.execute("PRAGMA table_info(purchases)")}
            checked="COALESCE(documents_checked,0)" if "documents_checked" in columns else "0"
            purchase=db.execute("SELECT "+checked+" FROM purchases WHERE reg_number=?",(reg,)).fetchone()
            if (purchase and purchase[0]) or previous.get("successful_coverage") or previous.get("status")=="completed":return True
            return any(db.execute("SELECT 1 FROM "+table+" WHERE reg_number=? LIMIT 1",(reg,)).fetchone()
                       for table in ("findings","checks") if table in tables)
        finally:db.close()

    def prepare_review(reg,force,automatic,versions,reserve):
        if not REG.fullmatch(reg):raise HTTPException(400,"Invalid registry number")
        expected,received=review_counts(reg)
        if not expected or received==0:
            return ({"reg_number":reg,"status":"waiting_for_originals",
                     "originals_received":received,"originals_expected":expected},None,False)
        from v381_audit_scheduling import is_manual_review
        foreground=not automatic and is_manual_review(ns,reg)
        explicit_retry=force and not automatic
        execution=_try_execution_lock(reg)
        if execution is None:return record_completion_arrival(reg,force,versions),None,foreground
        try:
            with state_lock(reg):
                previous=review_state(reg)
                if float(previous.get("lease_until") or 0)>time.time():
                    return ({**previous,"originals_expected":expected},None,foreground)
                # Unknown legacy watermarks catch up in the delayed bounded scan,
                # while direct unchanged upload compatibility remains intact.
                source=versions.get("source_version") if versions else _review_source_version(reg)
                if (not force and previous.get("status")=="completed"
                    and previous.get("originals_received")==received and received>=expected
                    and (not previous.get("source_version") or previous["source_version"]==source)):
                    return ({**previous,"originals_expected":expected},None,foreground)
                if not force and previous.get("status") in ("failed","partial"):
                    if previous.get("source_version")==source and (previous.get("retry_exhausted") or
                                          float(previous.get("next_retry_at") or 0)>time.time()):
                        return ({**previous,"originals_expected":expected},None,foreground)
                # Preserve the real initializer for first intake and deployments
                # without a DB, but never replace active saved legal history.
                automatic=automatic or saved_review_exists(reg,previous) or _review_owned_elsewhere(ns,reg)
                token=secrets.token_hex(16) if reserve else None
                fields={"originals_received":received,"originals_expected":expected,"automatic":automatic,
                        "lease_token":token,"lease_until":time.time()+REVIEW_LEASE_SECONDS if reserve else 0,
                        "pending_arrival":False}
                if explicit_retry:
                    fields.update(next_retry_at=0,retry_exhausted=False,attempts=0)
                return write_review(reg,previous,"queued",fields),token,foreground
        finally:_release_execution_lock(execution)

    def active_reviews():
        return pending_admissions+sum(not task.done() for task in (*review_jobs.values(),*finish_jobs.values()))

    def start_review(reg,data,token,foreground,automatic):
        if token:
            automatic=bool(data.get("automatic",automatic))
            review_priorities[reg]=foreground
            review_jobs[reg]=asyncio.create_task(review_worker(reg,token,foreground,automatic))
            review_jobs[reg].add_done_callback(lambda task:review_finished(reg,token,task))
        return data

    def enqueue_review(reg,force=False,automatic=False,versions=None):
        # Public synchronous upload callback retains its existing contract.
        from v381_audit_scheduling import is_manual_review,promote_review
        task=review_jobs.get(reg)
        if task and not task.done():
            if not automatic and is_manual_review(ns,reg):
                review_priorities[reg]=True;promote_review(task)
            return record_completion_arrival(reg,force,versions)
        data,token,foreground=prepare_review(reg,force,automatic,versions,active_reviews()<MAX_REVIEW_TASKS)
        return start_review(reg,data,token,foreground,automatic)

    async def enqueue_automatic(reg,force=True,automatic=True,versions=None):
        # Serialize only this registry's admission (HTTP and periodic alike).
        # A sibling preparation must not briefly own the execution flock just
        # as its newly scheduled review_worker tries to acquire that flock.
        entry=admission_locks.setdefault(reg,[asyncio.Lock(),0])
        entry[1]+=1
        try:
            async with entry[0]:
                task=asyncio.create_task(_enqueue_automatic(reg,force,automatic,versions))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Keep the registry mutex and physical admission count
                    # until prepare_review has handed its lease to a reviewer.
                    # HTTP and periodic cancellation must both settle this.
                    while not task.done():
                        try:await asyncio.shield(task)
                        except asyncio.CancelledError:continue
                        except Exception:break
                    if not task.cancelled():task.exception()
                    raise
        finally:
            entry[1]-=1
            if not entry[1]:admission_locks.pop(reg,None)

    async def _enqueue_automatic(reg,force=True,automatic=True,versions=None):
        # All recurring receipt, state and fsync work runs off the event loop.
        nonlocal pending_admissions
        from v381_audit_scheduling import run_file_io
        if reg in review_jobs and not review_jobs[reg].done():
            if not automatic:
                from v381_audit_scheduling import is_manual_review,promote_review
                if await run_file_io(is_manual_review,ns,reg):
                    task=review_jobs.get(reg)
                    if task is not None and not task.done():
                        review_priorities[reg]=True;promote_review(task)
            return await run_file_io(record_completion_arrival,reg,force,versions)
        reserve=active_reviews()<MAX_REVIEW_TASKS
        if reserve:pending_admissions+=1
        try:
            data,token,foreground=await run_file_io(prepare_review,reg,force,automatic,versions,reserve)
        finally:
            if reserve:pending_admissions-=1
        if token and stopping:
            # Shutdown may begin during off-thread preparation. Leave a
            # restartable queue entry, never a late worker or orphaned lease.
            return await run_file_io(lambda:update_review(reg,"queued",expected_token=token,
                                                          lease_token=None,lease_until=0))
        return start_review(reg,data,token,foreground,automatic)

    async def enqueue_review_async(reg,force=False,automatic=False,versions=None):
        # HTTP intake uses the same bounded admission and durable file leases,
        # with the synchronous callback's defaults and manual promotion intact.
        return await enqueue_automatic(reg,force,automatic,versions)

    scan_sources=("purchases","eis223_official_review_jobs","eis223_official_archive_inbox",
                  "eis223_public_browser_intake","eis223_official_originals")

    async def _review_tick():
        """One bounded, restart-safe fair pass over existing intake only."""
        from v381_audit_scheduling import run_file_io
        async with scan_lock:
            scan_path=root()/"eis223_official_review_jobs"/".revisit-cursor.json"
            try:
                cursor=json.loads(await run_file_io(scan_path.read_text))
                if not isinstance(cursor,dict):cursor={}
            except (FileNotFoundError,ValueError,TypeError):cursor={}
            index=int(cursor.get("source_index") or 0)%len(scan_sources)
            source=scan_sources[index]
            after=str(cursor.get(source) or "")
            page=await run_file_io(_review_cohort_page,ns,source,after,REVIEW_SCAN_PAGE)
            admitted=0;visited=0;restored=0;repair_checks=0;started=time.monotonic()
            for reg in page:
                if (active_reviews()>=MAX_REVIEW_TASKS or repair_checks>=MAX_REVIEW_ADMISSIONS
                        or time.monotonic()-started>=5):break
                visited+=1;cursor[source]=reg
                if not REG.fullmatch(reg) or not await run_file_io(_has_review_intake,reg):continue
                if any(reg in jobs and not jobs[reg].done() for jobs in (review_jobs,finish_jobs)):continue
                previous=await run_file_io(review_state,reg)
                if float(previous.get("lease_until") or 0)>time.time():continue
                # Snapshot failures and pointer-repair failures share durable
                # backoff, so one corrupt intake cannot stall the page cursor.
                if float(previous.get("next_retry_at") or 0)>time.time():continue
                try:
                    # Restore at most two small pointer batches per tick,
                    # including general-owned purchases with no runnable row.
                    original_folder=root()/"eis223_official_originals"/reg
                    if await run_file_io(original_folder.is_dir):
                        repair_checks+=1
                        repaired=await run_file_io(_restore_intake_rows,ns,reg)
                        if repaired:
                            from v381_audit_scheduling import notify_evidence
                            notify_evidence(ns);restored+=1
                    direct_pending=previous.get("status") in ("queued","running") and previous.get("automatic") is False
                    if not direct_pending and await run_file_io(_review_owned_elsewhere,ns,reg):continue
                    versions=await run_file_io(_review_input_versions,ns,reg)
                    last={"source_version":previous.get("last_source_version"),
                          "semantic_version":previous.get("last_semantic_version"),
                          "engine_signature":previous.get("last_engine_signature")}
                    dirty=_review_identity(last)!=_review_identity(versions)
                    attempt=previous.get("attempt_versions") or {}
                    if previous.get("retry_exhausted") and _review_identity(attempt)==_review_identity(versions):continue
                    if not dirty and previous.get("status")=="completed":continue
                    state=await enqueue_automatic(reg,force=True,automatic=not direct_pending,versions=versions)
                    if reg in review_jobs and not review_jobs[reg].done():admitted+=1
                except Exception as exc:
                    reason=type(exc).__name__
                    await run_file_io(lambda:record_retry(reg,previous.get("status","failed"),reason,
                        expected_token=previous.get("lease_token") or ""))
                if admitted>=MAX_REVIEW_ADMISSIONS:break
            if not page or (visited==len(page) and len(page)<REVIEW_SCAN_PAGE):cursor[source]=""
            cursor["source_index"]=(index+1)%len(scan_sources)
            await run_file_io(save_atomic,scan_path,json.dumps(cursor,sort_keys=True).encode())
            return {"visited":visited,"admitted":admitted,"restored":restored,"source":source}

    async def review_tick():
        from v381_audit_scheduling import background_audit
        with background_audit():return await _review_tick()

    ns["_v295_enqueue_review"]=enqueue_review
    ns["_v295_enqueue_review_async"]=enqueue_review_async
    ns["_v295_review_tick"]=review_tick
    ns["_v295_review_state"]=public_review_state

    @app.on_event("startup")
    async def resume_official_uploads():
        nonlocal scan_task
        if scan_task is not None and not scan_task.done():return
        async def periodic_review():
            # Unknown legacy completions must not become a deployment burst.
            while True:
                await asyncio.sleep(REVIEW_SCAN_INTERVAL)
                try:await review_tick()
                except asyncio.CancelledError:raise
                except Exception as exc:
                    print("V295 REVISIT_ERROR "+type(exc).__name__,flush=True)
        scan_task=asyncio.create_task(periodic_review())

    @app.on_event("shutdown")
    async def stop_official_reviews():
        nonlocal stopping
        stopping=True
        tasks=[*review_jobs.values(),*finish_jobs.values()]
        if scan_task is not None:tasks.append(scan_task)
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

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
                    def digest_file(path):
                        with path.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
                    from v381_audit_scheduling import run_file_io
                    sha=await run_file_io(digest_file,p)
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
            raise HTTPException(413,"Original exceeds 64 MiB")
        raw=io.BytesIO();size=0
        try:
            async for part in request.stream():
                size+=len(part)
                if size>MAX_BYTES:raise HTTPException(413,"Original exceeds 64 MiB")
                raw.write(part)
        except ClientDisconnect:
            # A partial body must never reach format checks, retained original
            # bytes/receipts or review admission.
            raise HTTPException(400,"Incomplete original request body") from None
        advertised=request.headers.get("content-length", "")
        if advertised.isdigit() and int(advertised)!=size:
            raise HTTPException(400,"Incomplete original request body")
        data=raw.getvalue()
        entry=allowed[uid]
        try:assert_format(data,entry["ext"])
        except ValueError as exc:
            print(f"V295 ORIGINAL_REJECTED reg={reg} code={_rejection_code(exc)} bytes={size}",flush=True)
            raise HTTPException(422,str(exc)) from exc
        # Persist both the prior and incoming version provenance before the
        # active pointer advances. Concurrent captures cannot lose a receipt.
        from v381_audit_scheduling import run_file_io
        receipt,changed=await run_file_io(_store_original_version,reg,uid,entry,data)
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
                "counted_as_audited":False,"max_original_bytes":MAX_BYTES}

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
