#!/usr/bin/env python3
"""Start the restored live entry.py from an ephemeral V258 copy.

V256 and V257 verified patches are applied first; /data source remains intact.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

from railway_start_restored import restore
from patch_v256 import patch_file
from patch_v257 import patch as patch_v257
from patch_v258 import patch as patch_v258


def main() -> None:
    folder, count = restore()
    target = Path(tempfile.gettempdir()) / 'eis223_v258_private' / 'entry.py'
    patch_file(folder / 'entry.py', target)
    patch_v257(target)
    digest = patch_v258(target)
    sys.path.insert(0, str(target.parent))
    print('V258_SAFE_PATCH_READY', digest[:12], 'RESTORED_ENV_KEYS', count, flush=True)
    if '--verify-only' in sys.argv:
        return
    import uvicorn
    uvicorn.run('entry:app', host='0.0.0.0', port=int(os.environ.get('PORT','8080')))


if __name__ == '__main__':
    main()
