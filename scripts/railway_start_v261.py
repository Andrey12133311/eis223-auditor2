#!/usr/bin/env python3
"""Boot private verified V261; patch only a disposable /tmp copy of entry.py."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
from railway_start_restored import restore
from patch_v256 import patch_file
from patch_v257 import patch as patch_v257
from patch_v258 import patch as patch_v258
from patch_v259 import patch as patch_v259
from patch_v260 import patch as patch_v260
from patch_v261 import patch as patch_v261

def main():
    folder, count = restore()
    output=Path(tempfile.gettempdir())/'eis223_v261_private'/'entry.py'
    patch_file(folder/'entry.py',output)
    patch_v257(output)
    patch_v258(output)
    patch_v259(output)
    patch_v260(output)
    digest=patch_v261(output)
    sys.path.insert(0,str(output.parent))
    print('V261_SAFE_PATCH_READY',digest[:12],'RESTORED_ENV_KEYS',count,flush=True)
    if '--verify-only' in sys.argv:return
    import uvicorn
    uvicorn.run('entry:app',host='0.0.0.0',port=int(os.environ.get('PORT','8080')))

if __name__=='__main__':main()
