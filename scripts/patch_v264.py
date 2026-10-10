#!/usr/bin/env python3
"""V264 bounded throughput upgrade on top of the V263 verified runtime."""
from __future__ import annotations
import hashlib
from pathlib import Path

CHANGES = (
    ("scan_batch = max(4,min(20,int(os.environ.get('EIS223_SCAN_BATCH','16'))))",
     "scan_batch = max(8,min(32,int(os.environ.get('EIS223_SCAN_BATCH','24'))))"),
    ("parallel = max(2,min(4,int(os.environ.get('EIS223_SCAN_PARALLEL','4'))))",
     "parallel = max(2,min(6,int(os.environ.get('EIS223_SCAN_PARALLEL','6'))))"),
    ("extras=max(0,min(3,int(os.environ.get('EIS223_FEED_EXTRA_PAGES','2'))))",
     "extras=max(0,min(4,int(os.environ.get('EIS223_FEED_EXTRA_PAGES','3'))))"),
)
def patch(path: Path) -> str:
    path = Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Refusing to edit persistent user data or recovery snapshot')
    before = path.read_text(encoding='utf-8')
    after = before
    for old, new in CHANGES:
        if after.count(old) != 1:
            raise RuntimeError('V264 expected V263 code anchor exactly once: '+old[:60])
        after = after.replace(old, new, 1)
    compile(after, str(path), 'exec')
    path.write_text(after, encoding='utf-8')
    return hashlib.sha256(after.encode('utf-8')).hexdigest()
