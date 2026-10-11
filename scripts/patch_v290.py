"""Bridge Roseltorg public document URLs into existing document recovery."""
from pathlib import Path
import hashlib
OLD="                                    await asyncio.to_thread(finish,job,result)"
NEW="""                                    await asyncio.to_thread(finish,job,result)
                                    if int(job.get('id') or 0)%8==0:
                                        try:
                                            await asyncio.wait_for(ns['_v290_roseltorg_attach'](reg),timeout=12)
                                        except (asyncio.TimeoutError,Exception) as bridge_error:
                                            print('V290 ROSELTORG_QUEUE_WAIT '+type(bridge_error).__name__,flush=True)"""
OLD_FAILURE="                                    await asyncio.to_thread(finish,job,error=error)"
NEW_FAILURE="""                                    await asyncio.to_thread(finish,job,error=error)
                                    try:
                                        await asyncio.wait_for(ns['_v290_roseltorg_attach'](reg),timeout=12)
                                    except Exception as alt_error:
                                        print('V290 ROSELTORG_ALT_WAIT '+type(alt_error).__name__,flush=True)"""
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Protected persistent storage')
    source=path.read_text(encoding='utf-8')
    if source.count(OLD)!=1:raise RuntimeError('V290 purchase anchor mismatch')
    source=source.replace(OLD,NEW,1)
    if source.count(OLD_FAILURE)!=1:raise RuntimeError('V290 failed-queue anchor mismatch')
    source=source.replace(OLD_FAILURE,NEW_FAILURE,1)
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
