"""Bounded, resumable re-audit of all locally verified EIS originals.

Never downloads missing files, modifies counters directly or deletes audit data.
Uses the same official EIS offline legal pipeline as individual reviews.
"""
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REG=re.compile(r"^[0-9]{11,20}$")

def install(ns):
    app=ns["app"]
    from v295_eis_originals import root, allowed_documents, saved_document
    checkpoint=root()/"v315_reaudit_progress.json"
    def write_status(data):
        tmp=checkpoint.with_suffix(".tmp")
        tmp.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
        os.replace(tmp,checkpoint)

    @app.on_event("startup")
    async def start_verified_reaudit():
        async def run():
            await asyncio.sleep(20)
            enqueue=ns.get("_v295_enqueue_review")
            if not callable(enqueue):
                print("V315 REAUDIT_BLOCKED enqueue unavailable",flush=True)
                return
            folders=set()
            for sub in ("eis223_official_archive_inbox","eis223_public_browser_intake"):
                base=root()/sub
                if base.is_dir():
                    folders.update(p.name for p in base.iterdir() if p.is_dir() and REG.fullmatch(p.name))
            regs=sorted(folders)
            completed=partial=failed=skipped=0
            print(f"V315 REAUDIT_STARTED candidates={len(regs)}",flush=True)
            for idx,reg in enumerate(regs):
                try:
                    allowed=await asyncio.to_thread(allowed_documents,reg)
                    if not allowed:
                        skipped+=1
                        continue
                    # Only fully received official originals qualify.
                    available=await asyncio.to_thread(
                        lambda: all(saved_document(reg,uid) is not None for uid in allowed))
                    if not available:
                        skipped+=1
                        continue
                    state=enqueue(reg,force=True)
                    if state.get("status")=="waiting_for_originals":
                        skipped+=1
                        continue
                    # Review worker serializes itself with the existing app lock.
                    # Poll persisted review state rather than launching an unbounded
                    # number of workers.
                    statefile=root()/"eis223_official_review_jobs"/(reg+".json")
                    for _ in range(300):
                        await asyncio.sleep(1)
                        try:
                            status=json.loads(statefile.read_text(encoding="utf-8")).get("status")
                        except (OSError,ValueError):
                            status=""
                        if status in ("completed","partial","failed") :
                            break
                    else:
                        status="timeout"
                    if status=="completed":
                        completed+=1
                    elif status=="partial":
                        partial+=1
                    else:
                        failed+=1
                except Exception as exc:
                    failed+=1
                    print(f"V315 REAUDIT_ERROR reg={reg} error={type(exc).__name__}",flush=True)
                if idx%10==0 or idx==len(regs)-1:
                    await asyncio.to_thread(write_status,{
                        "total_candidates":len(regs),"processed":idx+1,
                        "completed":completed,"partial":partial,"failed":failed,
                        "skipped_without_all_originals":skipped,
                        "updated_at":datetime.now(timezone.utc).isoformat(),
                        "note":"Results are real re-audits, not new procurements or files"})
            print(f"V315 REAUDIT_FINISHED candidates={len(regs)} completed={completed} partial={partial} failed={failed} skipped={skipped}",flush=True)
        asyncio.create_task(run())
    print("V315 VERIFIED_REAUDIT_READY",flush=True)
