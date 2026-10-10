#!/usr/bin/env python3
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
from patch_v263 import patch as patch_v263
from patch_v264 import patch as patch_v264
from patch_v265 import patch as patch_v265
from patch_v266 import patch as patch_v266
import v267_findings
import v267_card_ui
import v269_inline_card
import v270_count_display
import v271_reconcile
def main():
    folder,count=restore()
    path=Path(tempfile.gettempdir())/'eis223_v271_private'/'entry.py'
    patch_file(folder/'entry.py',path)
    for fn in (patch_v257,patch_v258,patch_v259,patch_v260,patch_v261,patch_v262,patch_v263,patch_v264,patch_v265,patch_v266):
        digest=fn(path)
    sys.path.insert(0,str(path.parent))
    import importlib
    entry=importlib.import_module('entry')
    v267_findings.install(vars(entry))
    v267_card_ui.install(vars(entry))
    v269_inline_card.install(vars(entry))
    v270_count_display.install(vars(entry))
    v271_reconcile.install(vars(entry))
    print('V271_SAFE_PATCH_READY',digest[:12],'RESTORED_ENV_KEYS',count,flush=True)
    if '--verify-only' in sys.argv:return
    import uvicorn
    uvicorn.run(entry.app,host='0.0.0.0',port=int(os.environ.get('PORT','8080')))
if __name__=='__main__':main()
