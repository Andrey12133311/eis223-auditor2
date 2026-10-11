"""Nonblocking Roseltorg metadata enrichment: never stall procurement workers."""
from pathlib import Path
import hashlib
OLD="                                    await asyncio.to_thread(finish,job,result)"
NEW="""                                    await asyncio.to_thread(finish,job,result)
                                    if reg:
                                        # Schedule fallback discovery independently; do not
                                        # hold the purchase worker for a 12-second network timeout.
                                        _v290_schedule_roseltorg(reg)"""
OLD_FAILURE="                                    await asyncio.to_thread(finish,job,error=error)"
NEW_FAILURE="""                                    await asyncio.to_thread(finish,job,error=error)
                                    if reg:
                                        _v290_schedule_roseltorg(reg)"""
HELPER="""
# Bound background enrichment to avoid accumulating stalled source requests.
_v290_background_tasks=set()
_v290_background_sem=asyncio.Semaphore(2)
def _v290_schedule_roseltorg(reg):
    if len(_v290_background_tasks)>=4:
        return
    async def run():
        async with _v290_background_sem:
            try:
                await asyncio.wait_for(ns['_v290_roseltorg_attach'](reg),timeout=9)
            except Exception as exc:
                print('V290 ROSELTORG_BACKGROUND '+type(exc).__name__,flush=True)
    task=asyncio.create_task(run())
    _v290_background_tasks.add(task)
    task.add_done_callback(_v290_background_tasks.discard)
"""
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Protected persistent storage')
    source=path.read_text(encoding='utf-8')
    for old,new in ((OLD,NEW),(OLD_FAILURE,NEW_FAILURE)):
        if source.count(old)!=1:raise RuntimeError('V290 queue anchor mismatch')
        source=source.replace(old,new,1)
    # Runtime globals resolve when the scheduler runs, after module import.
    source+=HELPER
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
