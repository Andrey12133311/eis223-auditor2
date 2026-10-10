#!/usr/bin/env python3
"""Boot verified Railway runtime with V264 bounded six-way ingestion."""
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
from patch_v262 import patch as patch_v262
from patch_v263 import patch as patch_v263
from patch_v264 import patch as patch_v264

def main():
    folder, count = restore()
    path = Path(tempfile.gettempdir())/'eis223_v264_private'/'entry.py'
    patch_file(folder/'entry.py', path)
    patch_v257(path)
    patch_v258(path)
    patch_v259(path)
    patch_v260(path)
    patch_v261(path)
    patch_v262(path)
    patch_v263(path)
    digest = patch_v264(path)
    sys.path.insert(0, str(path.parent))
    print('V264_SAFE_PATCH_READY', digest[:12], 'RESTORED_ENV_KEYS', count, flush=True)
    if '--verify-only' in sys.argv:
        return
    import uvicorn
    uvicorn.run('entry:app',host='0.0.0.0',port=int(os.environ.get('PORT','8080')))

if __name__=='__main__':
    main()
