#!/usr/bin/env python3
"""Verify and patch private entry.py on ephemeral disk; boot V257."""
from __future__ import annotations
import os,sys,tempfile
from pathlib import Path
from railway_start_restored import restore
from patch_v256 import patch_file
from patch_v257 import patch

def main():
    folder,count=restore()
    target=Path(tempfile.gettempdir())/'eis223_v257_private'/'entry.py'
    patch_file(folder/'entry.py',target)
    digest=patch(target)
    sys.path.insert(0,str(target.parent))
    print('V257_SAFE_PATCH_READY',digest[:12],'RESTORED_ENV_KEYS',count,flush=True)
    if '--verify-only' in sys.argv:return
    import uvicorn
    uvicorn.run('entry:app',host='0.0.0.0',port=int(os.environ.get('PORT','8080')))

if __name__=='__main__':main()
