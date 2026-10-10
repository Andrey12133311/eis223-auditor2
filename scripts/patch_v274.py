"""Prioritize saved originals and metadata-backed alternative URLs in recovery."""
import hashlib
from pathlib import Path
OLD="""sql+=" ORDER BY CASE WHEN COALESCE(d.local_path,'')<>'' THEN 0 ELSE 1 END, d.id DESC LIMIT 250" """
NEW="""sql+=" ORDER BY CASE WHEN COALESCE(d.local_path,'')<>'' THEN 0 ELSE 1 END, CASE WHEN d.metadata_json LIKE '%alt_urls%' THEN 0 ELSE 1 END, COALESCE(r.tries,0), d.id DESC LIMIT 1000" """
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Protected persistent data')
    before=path.read_text(encoding='utf-8')
    if before.count(OLD)!=1:raise RuntimeError('Unexpected V273 source')
    after=before.replace(OLD,NEW,1)
    compile(after,str(path),'exec')
    path.write_text(after,encoding='utf-8')
    return hashlib.sha256(after.encode()).hexdigest()
