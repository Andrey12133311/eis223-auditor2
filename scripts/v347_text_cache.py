"""Content-addressed reuse of text extracted from verified local EIS files.

Does not certify a document as an EIS original: provenance and hash checks
remain the responsibility of the legal audit pipeline. A cache hit is never
used as legal proof on its own. No network, no modification of originals.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import threading
from functools import wraps

EXTENSIONS={".pdf",".doc",".docx",".odt",".rtf",".xlsx",".xls",
            ".ods",".xml",".txt",".csv"}
MAX_ORIGINAL=25*1024*1024
MAX_TEXT=3*1024*1024
CACHE_VERSION="eis223-extractor-v347"
_locks={}
_guard=threading.Lock()
_m={"hits":0,"misses":0,"extracted":0,"skipped":0}

def cache_stats():
    with _guard:return dict(_m)

def install(ns):
    original=ns.get("extract_text")
    if not callable(original):raise RuntimeError("No installed text extraction function")
    if getattr(original,"__v347_cache__",False):return
    from v295_eis_originals import root
    data_root=root().resolve()
    cache_dir=data_root/"eis223_extracted_text_cache_v347"

    @wraps(original)
    def reusable(source,*args,**kwargs):
        # Other forms of extraction (bytes, stream) keep their old semantics.
        if args or kwargs or not isinstance(source,(str,os.PathLike)):
            with _guard:_m["skipped"]+=1
            return original(source,*args,**kwargs)
        try:
            path=Path(source).resolve(strict=True)
            size=path.stat().st_size
            if (not path.is_file() or not path.is_relative_to(data_root)
                or path.suffix.lower() not in EXTENSIONS or not 0<size<=MAX_ORIGINAL):
                raise ValueError("Unsupported cache source")
            sha=hashlib.file_digest(path.open("rb"),"sha256").hexdigest()
            key=hashlib.sha256((CACHE_VERSION+":"+sha).encode()).hexdigest()
        except (OSError,ValueError,TypeError):
            with _guard:_m["skipped"]+=1
            return original(source)
        target=cache_dir/key[:2]/(key+".txt")
        with _guard:
            lock=_locks.setdefault(key,threading.Lock())
        with lock:
            try:
                cached=target.read_bytes()
                if cached and len(cached)<=MAX_TEXT:
                    text=cached.decode("utf-8")
                    if len(text.strip())>=3:
                        with _guard:_m["hits"]+=1
                        return text
            except (OSError,UnicodeError):
                pass
            with _guard:_m["misses"]+=1
            text=original(source)
            if not isinstance(text,str):
                text=str(text or "")
            # Never cache an extraction failure. A later corrected extractor
            # or an added OCR language must still be able to retry.
            if len(text.strip())<3 or len(text.encode("utf-8"))>MAX_TEXT:
                return text
            try:
                target.parent.mkdir(parents=True,exist_ok=True)
                tmp=target.with_name(target.name+".tmp."+str(os.getpid())+"."+str(threading.get_ident()))
                tmp.write_text(text,encoding="utf-8")
                os.replace(tmp,target)
                with _guard:_m["extracted"]+=1
            except OSError:
                pass
            return text
    reusable.__v347_cache__=True
    ns["extract_text"]=reusable
    print("V347_EIS_TEXT_CACHE_READY saved_originals_only=True content_hash=True",flush=True)
