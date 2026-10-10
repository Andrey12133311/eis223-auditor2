#!/usr/bin/env python3
"""V263 bounded concurrent search and rolling feed paging.

Patch disposable /tmp entry.py only; never modify user data or private backup.
For compatibility, both exact original segments are fingerprint-verified.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

START = "                    for _ in range(min(6,max(2,int(os.environ.get('EIS223_FRESH_PER_CYCLE','4'))))):"
END = "                            print('V217 PURCHASE_WAIT reg='+reg+' error='+error,flush=True)"
BLOCK_HASH = 'd377ffd5364754308ed959645bbb990db567a24e8bc5316717d92810033d5ba3'
FEED_START = '                        # Backfill older EIS pages without starving the latest feed.'
FEED_END = "                        progress['feed_error'] = None"
FEED_HASH = '425a5219c0268a3578e29e5994588ddac882e0f67b5fcb676261a3ca9896de0b'

BLOCK_NEW = """                    # I/O overlaps in a bounded batch. Download jobs and legal
                    # evidence still follow the existing independent pipeline.
                    scan_batch = max(4,min(20,int(os.environ.get('EIS223_SCAN_BATCH','16'))))
                    parallel = max(2,min(4,int(os.environ.get('EIS223_SCAN_PARALLEL','4'))))
                    batch=[]
                    for _ in range(scan_batch):
                        if paused() or not storage_ready():break
                        job=await asyncio.to_thread(claim)
                        if not job:break
                        batch.append(job)
                    if batch:
                        slot=asyncio.Semaphore(parallel)
                        completed=set()
                        async def run_purchase(job):
                            async with slot:
                                reg=job['reg']
                                state['current_reg']=stats['last_reg']=progress['last_reg']=reg
                                try:
                                    result=await asyncio.wait_for(
                                        ns['process_purchase'](client,reg,json.loads(job['payload']) if job['payload'] else None),timeout=60)
                                    await asyncio.to_thread(finish,job,result)
                                    completed.add(reg)
                                    results.append(result)
                                    stats['checked']=stats.get('checked',0)+1
                                    progress['saved']+=1
                                    print('V263 PURCHASE_SAVED reg='+reg+' card='+str(result.get('card')),flush=True)
                                except asyncio.CancelledError:
                                    raise
                                except Exception as exc:
                                    error=type(exc).__name__+': '+str(exc)[:500]
                                    await asyncio.to_thread(finish,job,error=error)
                                    completed.add(reg)
                                    errors.append(reg+': '+error)
                                    stats['failed']=stats.get('failed',0)+1
                                    print('V263 PURCHASE_WAIT reg='+reg+' error='+error,flush=True)
                        try:
                            await asyncio.gather(*(run_purchase(job) for job in batch))
                        except asyncio.CancelledError:
                            # Preserve every unfinished claim on graceful shutdown.
                            for job in batch:
                                if job['reg'] not in completed:
                                    await asyncio.to_thread(finish,job,error='shutdown: retry on next run')
                            raise
                        print('V263 SCAN_BATCH total=%s completed=%s parallel=%s'%(len(batch),len(results),parallel),flush=True)"""

FEED_NEW = """                        # Up to three feed pages per scan, not a huge unbounded
                        # burst. Historic pages rotate while the newest page
                        # is always fetched first.
                        if len(fresh)>=20:
                            seen={str(ns['regnum'](x) or '') for x in fresh}
                            page_size=min(int(ns.get('PAGE_SIZE',100)),100)
                            extras=max(0,min(3,int(os.environ.get('EIS223_FEED_EXTRA_PAGES','2'))))
                            for pg in range(extras):
                                skip=page_size*(1+(stats['cycles']*extras+pg)%80)
                                try:
                                    backfill=await asyncio.wait_for(
                                        ns['jget'](client,'/fz223/purchases',{'limit':page_size,'skip':skip}),timeout=8)
                                    for item in ns['_payload_items'](backfill):
                                        reg=str(ns['regnum'](item) or '')
                                        if reg and reg not in seen:
                                            seen.add(reg)
                                            fresh.append(item)
                                except (asyncio.TimeoutError,ns['httpx'].TransportError) as exc:
                                    progress['backfill_error']=type(exc).__name__+': '+str(exc)[:140]
                                    break
                                except Exception as exc:
                                    progress['backfill_error']=type(exc).__name__+': '+str(exc)[:140]
                                    break
"""

def patch_text(source: str) -> str:
    if source.count(START)!=1 or source.count(END)!=1 or source.count(FEED_START)!=1:
        raise RuntimeError('V263 expected V257/V262 source anchors exactly once')
    a=source.index(START)
    b=source.index(END,a)+len(END)
    if hashlib.sha256(source[a:b].encode('utf-8')).hexdigest()!=BLOCK_HASH:
        raise RuntimeError('V263 refuses changed private purchase queue code')
    source=source[:a]+BLOCK_NEW+source[b:]
    a=source.index(FEED_START)
    b=source.index(FEED_END,a)
    if hashlib.sha256(source[a:b].encode('utf-8')).hexdigest()!=FEED_HASH:
        raise RuntimeError('V263 refuses changed private feed code')
    source=source[:a]+FEED_NEW+source[b:]
    compile(source,'entry.py','exec')
    return source

def patch(path:Path)->str:
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Refusing to edit recovery snapshot or data volume')
    before=path.read_text(encoding='utf-8')
    after=patch_text(before)
    path.write_text(after,encoding='utf-8')
    return hashlib.sha256(after.encode('utf-8')).hexdigest()
