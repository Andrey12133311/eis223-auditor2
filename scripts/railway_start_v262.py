#!/usr/bin/env python3
"""Private snapshot recovery with V262 capped automatic retries."""
from __future__ import annotations
import os,sys,tempfile
from pathlib import Path
from railway_start_restored import restore
from patch_v256 import patch_file
from patch_v257 import patch as patch_v257
from patch_v258 import patch as patch_v258
from patch_v259 import patch as patch_v259
from patch_v260 import patch as patch_v260
from patch_v261 import patch as patch_v261
from patch_v262 import patch as patch_v262

def main():
    folder,count=restore()
    target=Path(tempfile.gettempdir())/'eis223_v262_private'/'entry.py'
    patch_file(folder/'entry.py',target)
    patch_v257(target)
    patch_v258(target)
    patch_v259(target)
    patch_v260(target)
    patch_v261(target)
    digest=patch_v262(target)
    sys.path.insert(0,str(target.parent))
    print('V262_SAFE_PATCH_READY',digest[:12],'RESTORED_ENV_KEYS',count,flush=True)
    if '--verify-only' in sys.argv:return
    import uvicorn
    uvicorn.run('entry:app',host='0.0.0.0',port=int(os.environ.get('PORT','8080')))

if __name__=='__main__':main()
