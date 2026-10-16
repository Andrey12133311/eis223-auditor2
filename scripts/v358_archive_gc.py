"""Conservative reclamation of ONLY regenerable, unreferenced archive extraction files.

Original archives and receipts, SQLite DB and all legal findings are untouched.
The candidate must have a validated locally preserved archive source.
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
import time
import shutil
from pathlib import Path

ARCHIVES={".zip",".rar",".7z",".tar",".gz",".tgz",".bz2",".xz",".tbz2",".txz",".cab"}
EXTRACTED={".pdf",".docx",".doc",".xlsx",".xls",".odt",".ods",".rtf",".xml",".txt",".csv"} | ARCHIVES
HEX=re.compile(r"^[0-9a-f]{64}$")
REG=re.compile(r"^3[0-9]{10}$")
UID=re.compile(r"^[0-9A-F]{32}$")


def reclaim(base=Path("/data"),minimum_free=512*1024*1024,minimum_age=24*3600):
    base=Path(base)
    target=base/"eis223_archive_members"
    if target.is_symlink() or not target.is_dir():return
    before=shutil.disk_usage(base).free
    if before>=minimum_free:return
    db_path=base/"eis223.db"
    try:
        if not db_path.is_file() or db_path.is_symlink():return
        # Read-only mode prevents touching the SQLite DB or its journals.
        db=sqlite3.connect("file:"+str(db_path)+"?mode=ro",uri=True,timeout=5)
        try:
            cols={c[1] for c in db.execute("PRAGMA table_info(docs)")}
            if not {"local_path","annotated_path"}.issubset(cols):
                print("V358_ARCHIVE_RECLAIM_SKIPPED missing_docs_schema",flush=True)
                return
            referenced=set()
            for local,annotated in db.execute(
                "SELECT local_path,annotated_path FROM docs WHERE local_path LIKE ? OR annotated_path LIKE ?",
                (str(target)+"/%",str(target)+"/%")
            ):
                for raw in (local,annotated):
                    if raw and str(raw).startswith(str(target)+"/"):
                        referenced.add(str(raw))
        finally:db.close()
    except (sqlite3.Error,OSError) as exc:
        print("V358_ARCHIVE_RECLAIM_SKIPPED DB_READ_ERROR "+type(exc).__name__,flush=True)
        return

    released=0
    removed=0
    referenced_count=0
    failed=0
    cutoff=time.time()-minimum_age
    archive_status={}
    try:
        for reg_folder in target.iterdir():
            if not REG.fullmatch(reg_folder.name) or reg_folder.is_symlink() or not reg_folder.is_dir():continue
            reg=reg_folder.name
            for uid_folder in reg_folder.iterdir():
                if not UID.fullmatch(uid_folder.name) or uid_folder.is_symlink() or not uid_folder.is_dir():continue
                uid=uid_folder.name
                archive_folder=base/"eis223_official_originals"/reg/uid
                valid=archive_status.get((reg,uid))
                if valid is None:
                    valid=False
                    try:
                        receipt_path=archive_folder/"receipt.json"
                        if not archive_folder.is_symlink() and not receipt_path.is_symlink():
                            receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
                            sha=receipt.get("sha256")
                            ext=receipt.get("extension")
                            original=archive_folder/(str(sha)+str(ext))
                            valid=(receipt.get("reg_number")==reg and receipt.get("uid")==uid
                                   and ext in ARCHIVES and isinstance(sha,str) and HEX.fullmatch(sha)
                                   and original.is_file() and not original.is_symlink()
                                   and original.stat().st_size==receipt.get("bytes"))
                    except (OSError,ValueError,TypeError):pass
                    archive_status[(reg,uid)]=valid
                if not valid:continue
                for shard in uid_folder.iterdir():
                    if shard.is_symlink() or not shard.is_dir() or not re.fullmatch("[0-9a-f]{2}",shard.name):continue
                    for candidate in shard.iterdir():
                        try:
                            if candidate.is_symlink() or not candidate.is_file():continue
                            ext=candidate.suffix.lower()
                            sha=candidate.name[:-len(ext)] if ext else ""
                            if ext not in EXTRACTED or not HEX.fullmatch(sha) or not sha.startswith(shard.name):continue
                            if str(candidate) in referenced:
                                referenced_count+=1
                                continue
                            stat=candidate.stat()
                            if not stat.st_size or stat.st_mtime>cutoff:continue
                            # Only extracted duplicates with their source archive still saved.
                            candidate.unlink()
                            removed+=1
                            released+=stat.st_size
                            if removed>=25000 or shutil.disk_usage(base).free>=minimum_free:
                                break
                        except OSError:failed+=1
                    if removed>=25000 or shutil.disk_usage(base).free>=minimum_free:break
                if removed>=25000 or shutil.disk_usage(base).free>=minimum_free:break
            if removed>=25000 or shutil.disk_usage(base).free>=minimum_free:break
    except OSError as exc:
        print("V358_ARCHIVE_RECLAIM_SCAN_ERROR "+type(exc).__name__,flush=True)
    after=shutil.disk_usage(base).free
    print("V358_ARCHIVE_RECLAIM recovered_bytes="+str(max(0,after-before))+
          " files="+str(removed)+" bytes_unlinked="+str(released)+
          " referenced_preserved="+str(referenced_count)+
          " failed="+str(failed)+" free_after="+str(after)+
          " originals_untouched=True database_untouched=True findings_untouched=True",flush=True)
