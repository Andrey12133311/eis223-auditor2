"""Bridge DaMIA discovery into the existing queued EIS validation path."""
from pathlib import Path
import hashlib
ANCHOR="                        fresh = ns['_payload_items'](payload)"
INJECT="""                        fresh = ns['_payload_items'](payload)
                        try:
                            alternative = await _v280_discovery.pull()
                            known={str(ns['regnum'](x) or '') for x in fresh}
                            for row in alternative:
                                number=str(row.get('regNumber') or '')
                                if number and number not in known:
                                    known.add(number)
                                    fresh.append(row)
                        except Exception as exc:
                            print('V280 FEED_BRIDGE_ERROR '+type(exc).__name__,flush=True)"""
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise RuntimeError('Protected')
    source=path.read_text(encoding='utf8')
    if source.count(ANCHOR)!=1:raise RuntimeError('Expected one feed anchor')
    source=source.replace(ANCHOR,INJECT,1)
    source+="\nimport v280_discovery as _v280_discovery\n"
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf8')
    return hashlib.sha256(source.encode()).hexdigest()
