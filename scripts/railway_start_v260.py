#!/usr/bin/env python3
"""Start private V260 runtime. All patches are applied to /tmp, never /data."""
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

def main():
    folder,count=restore()
    target=Path(tempfile.gettempdir())/'eis223_v260_private'/'entry.py'
    patch_file(folder/'entry.py',target)
    patch_v257(target)
    patch_v258(target)
    patch_v259(target)
    patch_v260(target)
    sys.path.insert(0,str(target.parent))
    print('V260_SAFE_PATCH_READY RESTORED_ENV_KEYS',count,flush=True)
    if '--verify-only' in sys.argv:return
    import uvicorn
    uvicorn.run('entry:app',host='0.0.0.0',port=int(os.environ.get('PORT','8080')))

if __name__=='__main__':main()
