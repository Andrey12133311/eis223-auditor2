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
from patch_v272 import patch as patch_v272
from patch_v273 import patch as patch_v273
from patch_v274 import patch as patch_v274
from patch_v275 import patch as patch_v275
from patch_v276 import patch as patch_v276
from patch_v277 import patch as patch_v277
import v293_hide_obsolete_controls
import v294_eis_bridge
import v295_eis_originals
import v298_eis_only
import v299_browser_intake
import v300_violation_ui
import v301_search_jobs
import v302_eis_modern_ui
import v303_dashboard_fixes
import v304_official_memo_odt
import v305_memo_card
import v303_mobile_fixes
import v311_verified_customer
import v311_customer_backfill
import v314_eis_metadata
import v324_fas_practice
import v330_amendments_223
import v331_broad_legal
import v332_all_procurements_audit
import v333_dedup_ui
import v334_findings_ledger
import v312_archive_selftest
from patch_v291 import patch as patch_v291
def main():
    folder,count=restore()
    os.environ["EIS223_SOURCE_MODE"]="OFFICIAL_EIS_BROWSER_ONLY"
    path=Path(tempfile.gettempdir())/'eis223_v282_private'/'entry.py'
    patch_file(folder/'entry.py',path)
    for fn in (patch_v257,patch_v258,patch_v259,patch_v260,patch_v261,patch_v262,patch_v263,patch_v264,patch_v265,patch_v266):
        digest=fn(path)
    digest=patch_v272(path)
    digest=patch_v273(path)
    digest=patch_v274(path)
    digest=patch_v275(path)
    digest=patch_v276(path)
    digest=patch_v277(path)
    # Disabled: DaMIA/TenderGuru discovery feed patches and Roseltorg retry scheduler.
    # Only browser-origin EIS ZIP and original document intake remains active.
    digest=patch_v291(path)
    sys.path.insert(0,str(path.parent))
    import importlib
    entry=importlib.import_module('entry')
    v267_findings.install(vars(entry))
    v267_card_ui.install(vars(entry))
    v269_inline_card.install(vars(entry))
    v270_count_display.install(vars(entry))
    v293_hide_obsolete_controls.install(vars(entry))
    v271_reconcile.install(vars(entry))
    # No legacy API connectors or Roseltorg / ETP discovery.
    v294_eis_bridge.install(vars(entry))
    v295_eis_originals.install(vars(entry))
    v299_browser_intake.install(vars(entry))
    v324_fas_practice.install(vars(entry))
    v330_amendments_223.install(vars(entry))
    v331_broad_legal.install(vars(entry))
    v298_eis_only.install(vars(entry))
    v300_violation_ui.install(vars(entry))
    v301_search_jobs.install(vars(entry))
    v302_eis_modern_ui.install(vars(entry))
    v303_dashboard_fixes.install(vars(entry))
    v304_official_memo_odt.install(vars(entry))
    v305_memo_card.install(vars(entry))
    v303_mobile_fixes.install(vars(entry))
    v311_verified_customer.install(vars(entry))
    v311_customer_backfill.install(vars(entry))
    v314_eis_metadata.install(vars(entry))
    # Supersede folder-only v315, which cannot examine partial uploads.
    v332_all_procurements_audit.install(vars(entry))
    v333_dedup_ui.install(vars(entry))
    v334_findings_ledger.install(vars(entry))
    try:
        v312_archive_selftest.run()
    except Exception as exc:
        print("V312 ARCHIVE_SELFTEST_FAIL "+type(exc).__name__+": "+str(exc)[:180],flush=True)
    print('V305 BOOT_UI_FIXED memo_format=odt-only filters=canonical card_nav=working',flush=True)
    print('V282_SAFE_PATCH_READY',digest[:12],'RESTORED_ENV_KEYS',count,flush=True)
    if '--verify-only' in sys.argv:return
    # Railway cannot reach EIS SOAP; do not launch repeated network probes.
    import uvicorn
    uvicorn.run(entry.app,host='0.0.0.0',port=int(os.environ.get('PORT','8080')))
if __name__=='__main__':main()
