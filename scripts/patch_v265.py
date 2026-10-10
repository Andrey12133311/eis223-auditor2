#!/usr/bin/env python3
"""V265 bounded queue throughput upgrade with shorter per-purchase timeout."""
from __future__ import annotations
import hashlib
from pathlib import Path

CHANGES = (
    ("scan_batch = max(8,min(32,int(os.environ.get('EIS223_SCAN_BATCH','24'))))",
     "scan_batch = max(8,min(40,int(os.environ.get('EIS223_SCAN_BATCH','32'))))"),
    ("parallel = max(2,min(6,int(os.environ.get('EIS223_SCAN_PARALLEL','6'))))",
     "parallel = max(2,min(8,int(os.environ.get('EIS223_SCAN_PARALLEL','8'))))"),
    ("extras=max(0,min(4,int(os.environ.get('EIS223_FEED_EXTRA_PAGES','3'))))",
     "extras=max(0,min(5,int(os.environ.get('EIS223_FEED_EXTRA_PAGES','4'))))"),
    ("ns['process_purchase'](client,reg,json.loads(job['payload']) if job['payload'] else None),timeout=60)",
     "ns['process_purchase'](client,reg,json.loads(job['payload']) if job['payload'] else None),timeout=max(35,min(75,int(os.environ.get('EIS223_PURCHASE_TIMEOUT','45')))))"),
)
def patch(path:Path)->str:
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Refusing persistent data or snapshot edits')
    source=path.read_text(encoding='utf-8')
    for old,new in CHANGES:
        if source.count(old)!=1:
            raise RuntimeError('V265 expected V264 anchor exactly once: '+old[:60])
        source=source.replace(old,new,1)
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
