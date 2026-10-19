"""Retry transient SQLite contention without rerunning expensive legal analysis."""
import asyncio
import sqlite3
import time


def is_busy(error):
    code=getattr(error,"sqlite_errorcode",None)
    return isinstance(error,sqlite3.OperationalError) and (
        (code is not None and (code & 255) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED))
        or str(error).lower() in ("database is locked","database table is locked"))


def run_db_sync(operation,*args,stage="audit",retry_seconds=30):
    """DB-only retry for synchronous analyzer wrappers running in a worker thread."""
    deadline=time.monotonic()+retry_seconds
    attempts=0
    while True:
        try:
            return operation(*args)
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):raise
            attempts+=1
            remaining=deadline-time.monotonic()
            if remaining<=0:
                print(f"AUDIT_DB_BUSY_EXHAUSTED stage={stage} attempts={attempts}",flush=True)
                raise
            print(f"AUDIT_DB_BUSY_RETRY stage={stage} attempt={attempts}",flush=True)
            time.sleep(min(.25*attempts,2,remaining))


async def run_db(operation,*args,stage="audit",retry_seconds=30):
    """Each attempt owns and closes its connection; sleeping holds no DB lock."""
    deadline=time.monotonic()+retry_seconds
    attempts=0
    while True:
        try:
            return await asyncio.to_thread(operation,*args)
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):raise
            attempts+=1
            remaining=deadline-time.monotonic()
            if remaining<=0:
                print(f"AUDIT_DB_BUSY_EXHAUSTED stage={stage} attempts={attempts}",flush=True)
                raise
            print(f"AUDIT_DB_BUSY_RETRY stage={stage} attempt={attempts}",flush=True)
            await asyncio.sleep(min(.25*attempts,2,remaining))
