"""Disk diagnostics and explicit low-storage response for EIS browser bridge.

No originals, SQLite databases, receipts, legal findings or reports are deleted.
"""
from __future__ import annotations
import os
import sqlite3
import shutil
import time
from pathlib import Path
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

MIN_FREE = 64 * 1024 * 1024
BRIDGE_PREFIXES = (
    "/api/v301/bridge/",
    "/api/v299/eis223/heartbeat",
    "/api/v299/eis223/public-manifest",
    "/api/v310/eis223/card-metadata",
    "/api/v295/eis223/",
)

def free_bytes():
    try:
        return shutil.disk_usage(os.getenv("EIS223_DATA_DIR") or os.getenv("DATA_DIR") or "/data").free
    except (OSError, ValueError):
        return None

def inspect_disk(root="/data", max_files=120000, max_seconds=18):
    """Bounded read-only file inventory to locate regenerable space."""
    base=Path(root)
    start=time.monotonic()
    try:
        free=shutil.disk_usage(base)
        print(f"V357_STORAGE free={free.free} used={free.used} total={free.total}",flush=True)
    except OSError as exc:
        print("V357_STORAGE_INSPECTION_ERROR "+str(type(exc).__name__),flush=True)
        return
    if not base.is_dir():return
    totals={}
    counts={}
    children=[]
    try:
        children=[p for p in base.iterdir() if not p.is_symlink()]
    except OSError:return
    scanned=0
    for top in children:
        size=0
        count=0
        stack=[top]
        while stack and scanned<max_files and time.monotonic()-start<max_seconds:
            item=stack.pop()
            try:
                if item.is_symlink():continue
                if item.is_dir():
                    with os.scandir(item) as it:
                        stack.extend(Path(e.path) for e in it if not e.is_symlink())
                elif item.is_file():
                    size+=item.stat().st_size
                    count+=1
                    scanned+=1
            except OSError:pass
        totals[top.name]=size
        counts[top.name]=count
    for name,size in sorted(totals.items(),key=lambda p:-p[1])[:30]:
        print(f"V357_STORAGE_DIR {name!r} bytes={size} files={counts[name]}",flush=True)
    print(f"V357_STORAGE_DONE files={scanned} elapsed_s={time.monotonic()-start:.2f}",flush=True)

def install(ns):
    app=ns["app"]

    async def storage_guard(request, call_next):
        route=request.url.path
        if route.startswith(BRIDGE_PREFIXES):
            free=free_bytes()
            if free is not None and free<MIN_FREE:
                resp=JSONResponse(
                    {"detail":"Хранилище Railway заполнено. Сохранённые документы не удалены. Освободите или расширьте /data.",
                     "code":"EIS223_STORAGE_FULL","free_bytes":free},
                    status_code=507)
                if request.headers.get("origin")=="https://zakupki.gov.ru":
                    resp.headers["access-control-allow-origin"]="https://zakupki.gov.ru"
                    resp.headers["vary"]="Origin"
                resp.headers["cache-control"]="no-store"
                return resp
        try:
            return await call_next(request)
        except (sqlite3.OperationalError,OSError) as exc:
            if ("database or disk is full" not in str(exc).lower()
                and "no space left on device" not in str(exc).lower()
                and getattr(exc,"errno",None)!=28):
                raise
            resp=JSONResponse({"detail":"Хранилище /data заполнено; повторите после освобождения места.",
                               "code":"EIS223_STORAGE_FULL"},status_code=507)
            if request.headers.get("origin")=="https://zakupki.gov.ru":
                resp.headers["access-control-allow-origin"]="https://zakupki.gov.ru"
                resp.headers["vary"]="Origin"
            return resp

    app.add_middleware(BaseHTTPMiddleware,dispatch=storage_guard)
    print("V357_BRIDGE_STORAGE_GUARD_READY no_deletions=True",flush=True)
