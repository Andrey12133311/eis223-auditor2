#!/usr/bin/env python3
"""V266: reduce queue stall time while retaining bounded concurrency and retries."""
from pathlib import Path
import hashlib
OLD="ns['process_purchase'](client,reg,json.loads(job['payload']) if job['payload'] else None),timeout=max(35,min(75,int(os.environ.get('EIS223_PURCHASE_TIMEOUT','45')))))"
NEW="ns['process_purchase'](client,reg,json.loads(job['payload']) if job['payload'] else None),timeout=max(30,min(75,int(os.environ.get('EIS223_PURCHASE_TIMEOUT','35')))))"
def patch(path:Path)->str:
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):raise RuntimeError('unsafe patch target')
    before=path.read_text(encoding='utf-8')
    if before.count(OLD)!=1:raise RuntimeError('V266 source mismatch')
    after=before.replace(OLD,NEW,1)
    compile(after,str(path),'exec')
    path.write_text(after,encoding='utf-8')
    return hashlib.sha256(after.encode()).hexdigest()
