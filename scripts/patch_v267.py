#!/usr/bin/env python3
from pathlib import Path
import hashlib
ANCHOR="_v257_features.install(globals())"
def patch(path:Path)->str:
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):raise RuntimeError('Protected')
    source=path.read_text(encoding='utf-8')
    if source.count(ANCHOR)!=1:raise RuntimeError('Missing V257 hook')
    source+="\n# V267 read-only canonical findings panel\nimport v267_features as _v267_features\n_v267_features.install(globals())\n"
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
