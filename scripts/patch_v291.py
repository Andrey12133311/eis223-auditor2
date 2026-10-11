"""Skip direct EIS document URL in recovery; keep EIS procurement metadata intact."""
from pathlib import Path
import hashlib
OLD="            source=[r.get('url')]"
NEW="""            # EIS card/notice metadata remains enabled; only direct original-file
            # downloads from zakupki.gov.ru are excluded from this recovery pass.
            _file_url=r.get('url')
            if isinstance(_file_url,str) and (
                'zakupki.gov.ru' in _file_url.lower() or 'zakupki.gov.ru' in _file_url.lower().replace('%2e','.')
            ):
                _file_url=None
            source=[_file_url]"""
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Protected storage')
    before=path.read_text(encoding='utf-8')
    if before.count(OLD)!=1:raise RuntimeError('Document source anchor mismatch')
    after=before.replace(OLD,NEW,1)
    compile(after,str(path),'exec')
    path.write_text(after,encoding='utf-8')
    return hashlib.sha256(after.encode()).hexdigest()
