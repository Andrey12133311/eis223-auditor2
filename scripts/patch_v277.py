"""Bound additional Gosplan feed pages on free test API without touching data."""
from pathlib import Path
import hashlib

OLD="""extras=max(0,min(5,int(os.environ.get('EIS223_FEED_EXTRA_PAGES','4'))))"""
NEW="""extras=max(0,min(5,int(os.environ.get('EIS223_FEED_EXTRA_PAGES','4'))))
                            # Free Gosplan test endpoint is limited to 10 requests/minute.
                            # Use at most one extra page per cycle on that endpoint.
                            # Main-page polling and detail requests need the remaining budget.
                            if 'v2test.gosplan.info' in os.environ.get('GOSPLAN_BASE','').lower():
                                extras=min(extras,1)"""

def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Refusing to modify persistent data')
    src=path.read_text(encoding='utf-8')
    if src.count(OLD)!=1:raise RuntimeError('V277 feed anchor mismatch')
    src=src.replace(OLD,NEW,1)
    compile(src,str(path),'exec')
    path.write_text(src,encoding='utf-8')
    return hashlib.sha256(src.encode()).hexdigest()
