"""Prefer already downloaded originals in the recovery worker; no data changes."""
from pathlib import Path
import hashlib
OLD="sql+=' ORDER BY d.id DESC LIMIT 250'"
NEW="""# Process locally saved originals first and avoid long network waits.
        sql+=" ORDER BY CASE WHEN COALESCE(d.local_path,'')<>'' THEN 0 ELSE 1 END, d.id DESC LIMIT 250" """
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Refusing to edit persistent data')
    source=path.read_text(encoding='utf-8')
    if source.count(OLD)!=1:
        raise RuntimeError('V273 unexpected recovery source')
    source=source.replace(OLD,NEW,1)
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
