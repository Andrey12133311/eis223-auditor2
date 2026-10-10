"""Adaptive queue pressure: reserve capacity for document workers during source failures."""
from pathlib import Path
import hashlib
OLD="""                    scan_batch = max(8,min(40,int(os.environ.get('EIS223_SCAN_BATCH','32'))))
                    parallel = max(2,min(8,int(os.environ.get('EIS223_SCAN_PARALLEL','8'))))"""
NEW="""                    # Failures indicate network congestion; reduce request pressure
                    # rather than launching eight timeouts simultaneously.
                    failures=int(stats.get('v272_empty_batches',0))
                    scan_batch = max(8,min(40,int(os.environ.get('EIS223_SCAN_BATCH','32'))))
                    parallel = max(2,min(8,int(os.environ.get('EIS223_SCAN_PARALLEL','8'))))
                    if failures >= 2:
                        scan_batch=min(scan_batch,8)
                        parallel=min(parallel,3)"""
LOG_OLD="""                        print('V263 SCAN_BATCH total=%s completed=%s parallel=%s'%(len(batch),len(results),parallel),flush=True)"""
LOG_NEW="""                        # Treat only usable results as progress, not requests that
                        # returned empty/error cards.
                        useful=sum(1 for r in results if isinstance(r,dict) and r.get('card')!='error')
                        stats['v272_empty_batches']=(0 if useful else min(100,int(stats.get('v272_empty_batches',0))+1))
                        print('V272 SCAN_BATCH total=%s usable=%s parallel=%s consecutive_empty=%s'%(len(batch),useful,parallel,stats['v272_empty_batches']),flush=True)"""
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):raise RuntimeError('protected source')
    src=path.read_text(encoding='utf8')
    for old,new in ((OLD,NEW),(LOG_OLD,LOG_NEW)):
        if src.count(old)!=1:raise RuntimeError('Unexpected queue source, refusing patch')
        src=src.replace(old,new,1)
    compile(src,str(path),'exec')
    path.write_text(src,encoding='utf8')
    return hashlib.sha256(src.encode()).hexdigest()
