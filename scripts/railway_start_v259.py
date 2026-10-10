#!/usr/bin/env python3
"""Boot from intact private Railway recovery snapshot with V256-V259 patches."""
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

def main():
    folder,count=restore()
    dest=Path(tempfile.gettempdir())/'eis223_v259_private'/'entry.py'
    patch_file(folder/'entry.py',dest)
    patch_v257(dest)
    patch_v258(dest)
    patch_v259(dest)
    sys.path.insert(0,str(dest.parent))
    print('V259_SAFE_PATCH_READY',count,flush=True)
    if '--verify-only' in sys.argv:return
    import uvicorn
    uvicorn.run('entry:app',host='0.0.0.0',port=int(os.environ.get('PORT','8080')))

if __name__=='__main__':main()
