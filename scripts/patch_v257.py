"""Apply bounded V257 feed improvements to the V256 ephemeral source only."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

EXACT=(
 ("                    for _ in range(2):\n                        if paused() or not storage_ready(): break",
  "                    for _ in range(min(6,max(2,int(os.environ.get('EIS223_FRESH_PER_CYCLE','4'))))):\n                        if paused() or not storage_ready(): break"),
 ("                        fresh = ns['_payload_items'](payload)\n                        progress['feed_error'] = None",
  """                        fresh = ns['_payload_items'](payload)
                        # Rotate through older pages; preserve the current first page every time.
                        if stats['cycles']%2==0 and len(fresh)>=20:
                            skip=min(int(ns.get('PAGE_SIZE',100)),100)*(1+(stats['cycles']//2)%20)
                            try:
                                backfill=await asyncio.wait_for(ns['jget'](client,'/fz223/purchases',{'limit':min(int(ns.get('PAGE_SIZE',100)),100),'skip':skip}),timeout=10)
                                seen={str(ns['regnum'](x) or '') for x in fresh}
                                fresh.extend(x for x in ns['_payload_items'](backfill) if str(ns['regnum'](x) or '') not in seen)
                            except Exception as e:
                                progress['backfill_error']=type(e).__name__+': '+str(e)[:140]
                        progress['feed_error'] = None"""),
)
BOOTSTRAP="\n# V257: UI and feature API, backed by the original /data storage.\nimport v257_features as _v257_features\n_v257_features.install(globals())\n"

def patch(temp_path:Path)->str:
    temp_path=Path(temp_path).resolve(strict=True)
    if '/_eis223_runtime_recovery/' in str(temp_path) or str(temp_path).startswith('/data/'):
        raise RuntimeError('Refusing to modify private recovery volume')
    source=temp_path.read_text(encoding='utf-8')
    for old,new in EXACT:
        if source.count(old)!=1:raise RuntimeError('V257 incompatible source anchor: '+old[:90])
        source=source.replace(old,new,1)
    if '_v257_features.install(globals())' in source:raise RuntimeError('V257 already installed')
    source+=BOOTSTRAP
    compile(source,str(temp_path),'exec')
    temp_path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
