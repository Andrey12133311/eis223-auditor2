"""Rotate old/new document cohorts every five minutes to avoid starvation."""
from pathlib import Path
import hashlib
OLD="""sql+=" ORDER BY CASE WHEN COALESCE(d.local_path,'')<>'' THEN 0 ELSE 1 END, CASE WHEN d.metadata_json LIKE '%alt_urls%' THEN 0 ELSE 1 END, COALESCE(r.tries,0), d.id DESC LIMIT 1000" """
NEW="""# Alternate newer and older eligible document IDs every five minutes.
        # Do not bypass retry limits, availability checks, or legal validation.
        direction='ASC' if int(_t234.time()//300)%2 else 'DESC'
        sql+=" ORDER BY CASE WHEN COALESCE(d.local_path,'')<>'' THEN 0 ELSE 1 END, CASE WHEN d.metadata_json LIKE '%alt_urls%' THEN 0 ELSE 1 END, COALESCE(r.tries,0), d.id "+direction+" LIMIT 1000" """
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):raise RuntimeError('Protected')
    source=path.read_text(encoding='utf-8')
    if source.count(OLD)!=1:raise RuntimeError('Unexpected V275 source')
    source=source.replace(OLD,NEW,1)
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
