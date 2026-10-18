"""Content-addressed reuse of text extracted from verified local EIS files.

Does not certify a document as an EIS original: provenance and hash checks
remain the responsibility of the legal audit pipeline. A cache hit is never
used as legal proof on its own. No network, no modification of originals.
"""
from __future__ import annotations
import hashlib
import os
import tempfile
from pathlib import Path
import threading
from functools import wraps

EXTENSIONS={".pdf",".doc",".docx",".odt",".rtf",".xlsx",".xls",
            ".ods",".xml",".txt",".csv"}
MAX_ORIGINAL=25*1024*1024
MAX_TEXT=3*1024*1024
CACHE_VERSION="eis223-extractor-v347"
_locks=tuple(threading.RLock() for _ in range(128))
_guard=threading.Lock()
_m={"hits":0,"misses":0,"extracted":0,"skipped":0}

def cache_stats():
    with _guard:return dict(_m)

def cached_extract(source, original, *, namespace=CACHE_VERSION, data_root=None):
    """Reuse extraction by bytes, format and parser revision, never provenance.

    Hash fresh bytes on every read; receipt authentication remains upstream.
    The DOCX evidence-order parser uses its own namespace, not plain extraction.
    Fixed lock stripes bound memory even when millions of documents are seen.
    """
    from v295_eis_originals import root
    data_root = root().resolve() if data_root is None else data_root
    try:
        path=Path(source).resolve(strict=True)
        before=path.stat()
        if (not path.is_file() or not path.is_relative_to(data_root)
            or path.suffix.lower() not in EXTENSIONS
            or not 0<before.st_size<=MAX_ORIGINAL):
            raise ValueError("Unsupported cache source")
        with path.open("rb") as original_bytes:
            sha=hashlib.file_digest(original_bytes,"sha256").hexdigest()
        key=hashlib.sha256((namespace+":"+path.suffix.lower()+":"+sha).encode()).hexdigest()
    except (OSError,ValueError,TypeError):
        with _guard:_m["skipped"]+=1
        return original(source)
    target=Path(tempfile.gettempdir())/"eis223_extracted_text_cache_v347"/key[:2]/(key+".txt")
    lock=_locks[int(key[:8],16)%len(_locks)]
    with lock:
        try:
            # Check size before allocating a cache file, including corrupt files.
            if 0<target.stat().st_size<=MAX_TEXT:
                text=target.read_text(encoding="utf-8")
                if len(text.strip())>=3:
                    with _guard:_m["hits"]+=1
                    return text
        except (OSError,UnicodeError):
            pass
        with _guard:_m["misses"]+=1
        text=original(source)
        if not isinstance(text,str):text=str(text or "")
        if len(text.strip())<3 or len(text.encode("utf-8"))>MAX_TEXT:
            return text
        try:
            after=path.stat()
            # A replacement during extraction must never poison the old key.
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(
                after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                return text
            target.parent.mkdir(parents=True,exist_ok=True)
            tmp=target.with_name(target.name+".tmp."+str(os.getpid())+"."+str(threading.get_ident()))
            tmp.write_text(text,encoding="utf-8")
            os.replace(tmp,target)
            with _guard:_m["extracted"]+=1
        except OSError:
            pass
        return text


def install(ns):
    original=ns.get("extract_text")
    if not callable(original):raise RuntimeError("No installed text extraction function")
    if getattr(original,"__v347_cache__",False):return

    from v295_eis_originals import root
    data_root=root().resolve()

    @wraps(original)
    def reusable(source,*args,**kwargs):
        if args or kwargs or not isinstance(source,(str,os.PathLike)):
            with _guard:_m["skipped"]+=1
            return original(source,*args,**kwargs)
        return cached_extract(source,original,data_root=data_root)
    reusable.__v347_cache__=True
    ns["extract_text"]=reusable
    print("V347_EIS_TEXT_CACHE_READY saved_originals_only=True content_hash=True",flush=True)
