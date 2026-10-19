"""Preflight or queue an explicit, bounded list for the existing legal worker.

Dry-run is the default. This module never initializes a database, enumerates
procurements, downloads originals, invokes audit workers, or changes legal
results. Applying only resets eligible existing queue rows to pending.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

# Intentionally fixed: this is not a general-purpose campaign/bulk reset tool.
CAMPAIGN="2026-10-15-main-1253-identity-and-legal-v342"
MAX_REGS=20
TERMINAL_STATES=("completed","failed","no_readable_documents")
FILE_EXT={".pdf",".doc",".docx",".odt",".rtf",".xlsx",".xls",".ods",".xml",".txt",".csv"}
MAX_BYTES=25*1024*1024
MAX_DOC_ROWS=900


def validate_regs(values):
    """Reject the whole request before opening any database on bad scope."""
    if not isinstance(values,(list,tuple)) or not 1<=len(values)<=MAX_REGS:
        raise ValueError("Provide 1–20 explicit 223-FZ registry numbers")
    if any(not isinstance(value,str) or not re.fullmatch(r"3[0-9]{10}",value) for value in values):
        raise ValueError("Each registry number must be exactly 11 digits starting with 3")
    if len(set(values))!=len(values):raise ValueError("Duplicate registry numbers are not allowed")
    return tuple(values)


def _existing_path(value,label,directory=False):
    if not isinstance(value,(str,Path)) or not str(value):raise ValueError("Explicit "+label+" is required")
    try:path=Path(value).expanduser().resolve(strict=True)
    except (OSError,RuntimeError) as exc:raise ValueError(label+" must already exist") from exc
    if (not path.is_dir() if directory else not path.is_file()):
        raise ValueError(label+" must be an existing "+("directory" if directory else "file"))
    return path


def _connect(path,mode):
    # mode=rw, unlike sqlite3's default, cannot create a missing database.
    conn=sqlite3.connect(path.as_uri()+"?mode="+mode,uri=True,timeout=5,isolation_level=None)
    conn.row_factory=sqlite3.Row
    if mode=="ro":conn.execute("PRAGMA query_only=ON")
    return conn


def _validate_schema(conn):
    expected={"purchases":{"reg_number"},
              "docs":{"id","reg_number","local_path","status"},
              "v332_audit_queue":{"campaign","reg","state","next_retry_at","updated_at"}}
    for table,columns in expected.items():
        # Names are fixed constants, never caller-provided SQL identifiers.
        actual={row[1] for row in conn.execute("PRAGMA table_info("+table+")")}
        if not columns.issubset(actual):
            raise ValueError("Existing database lacks required "+table+" schema; no migration is performed")


def _local_file_exists(value,data_root):
    try:
        raw=Path(value)
        if not raw.is_absolute():return False
        path=raw.resolve(strict=True)
        return (path.is_relative_to(data_root) and path.is_file() and path.suffix.lower() in FILE_EXT
                and 0<path.stat().st_size<=MAX_BYTES)
    except (OSError,TypeError,ValueError,RuntimeError):return False


def _local_candidates(conn,reg,data_root):
    rows=conn.execute("""SELECT id,local_path FROM docs WHERE reg_number=?
        AND status IN ('checked','downloaded_no_text') AND COALESCE(local_path,'')<>''
        ORDER BY id LIMIT ?""",(reg,MAX_DOC_ROWS)).fetchall()
    # File existence is a scheduling precondition, not a new authenticity claim.
    # The existing worker still validates receipts, hashes and original bytes.
    return [(row["id"],row["local_path"]) for row in rows if _local_file_exists(row["local_path"],data_root)]


def _read_plan(path,regs,data_root):
    conn=_connect(path,"ro")
    try:
        _validate_schema(conn)
        plan=[]
        for reg in regs:
            purchase=conn.execute("SELECT 1 FROM purchases WHERE reg_number=?",(reg,)).fetchone()
            row=conn.execute("""SELECT state,updated_at,next_retry_at FROM v332_audit_queue
                WHERE campaign=? AND reg=?""",(CAMPAIGN,reg)).fetchone()
            item={"reg":reg,"state":row["state"] if row else None,"local_original_candidates":0,
                  "eligible":False,"action":"skipped","reason":""}
            candidates=_local_candidates(conn,reg,data_root) if purchase else []
            item["local_original_candidates"]=len(candidates)
            if not purchase:item["reason"]="missing_purchase"
            elif row is None:item["reason"]="missing_queue_row"
            elif row["state"] in ("pending","running"):item["reason"]="already_"+row["state"]
            elif row["state"] not in TERMINAL_STATES:item["reason"]="unsupported_queue_state"
            else:
                item["eligible"]=bool(candidates)
                item["action"]="would_queue" if candidates else "skipped"
                item["reason"]="terminal_with_local_original" if candidates else "no_local_original"
            plan.append({"item":item,"updated_at":row["updated_at"] if row else None,
                         "next_retry_at":row["next_retry_at"] if row else None,"candidates":candidates})
        return plan
    finally:conn.close()


def _only_queue_columns(action,table,column,database,trigger):
    """Fail closed if a DB trigger attempts a side effect outside this reset."""
    if action==sqlite3.SQLITE_UPDATE:
        return sqlite3.SQLITE_OK if (table=="v332_audit_queue" and column in ("state","next_retry_at")
                                    and trigger is None) else sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _apply_plan(path,plan,data_root):
    # Recheck local availability immediately before taking the short DB write
    # lock. File stats never hold the queue reservation transaction open.
    for entry in plan:
        if not entry["item"]["eligible"]:continue
        entry["candidates"]=[candidate for candidate in entry["candidates"]
                             if _local_file_exists(candidate[1],data_root)]
        if not entry["candidates"]:
            entry["item"].update(eligible=False,action="skipped",reason="no_local_original",local_original_candidates=0)
    conn=_connect(path,"rw")
    try:
        _validate_schema(conn)
        conn.set_authorizer(_only_queue_columns)
        conn.execute("BEGIN IMMEDIATE")
        for entry in plan:
            item=entry["item"]
            if not item["eligible"]:continue
            # Compare with preflight rather than overwriting any concurrent
            # reservation, completion, or change to retry timing.
            row=conn.execute("""SELECT state,updated_at,next_retry_at FROM v332_audit_queue
                WHERE campaign=? AND reg=?""",(CAMPAIGN,item["reg"])).fetchone()
            if row is None:
                item.update(action="skipped",reason="missing_queue_row",state=None);continue
            if row["state"] in ("pending","running"):
                item.update(action="skipped",reason="already_"+row["state"],state=row["state"]);continue
            if (row["state"]!=item["state"] or row["updated_at"]!=entry["updated_at"]
                    or row["next_retry_at"]!=entry["next_retry_at"]):
                item.update(action="skipped",reason="changed_since_preflight",state=row["state"]);continue
            if not conn.execute("SELECT 1 FROM purchases WHERE reg_number=?",(item["reg"],)).fetchone():
                item.update(action="skipped",reason="missing_purchase");continue
            if not any(conn.execute("""SELECT 1 FROM docs WHERE id=? AND reg_number=? AND local_path=?
                         AND status IN ('checked','downloaded_no_text')""",(doc_id,item["reg"],local_path)).fetchone()
                       for doc_id,local_path in entry["candidates"]):
                item.update(action="skipped",reason="original_changed_since_preflight");continue
            changed=conn.execute("""UPDATE v332_audit_queue SET state=?,next_retry_at=?
                WHERE campaign=? AND reg=? AND state=? AND updated_at IS ? AND next_retry_at IS ?
                AND state IN (?,?,?)""",("pending",0,CAMPAIGN,item["reg"],item["state"],
                    entry["updated_at"],entry["next_retry_at"],*TERMINAL_STATES)).rowcount
            if changed not in (0,1):
                raise ValueError("Queue row identity is not unique; scoped update was rolled back")
            item.update(action="queued" if changed==1 else "skipped",
                        reason="explicit_scoped_recheck" if changed==1 else "changed_since_preflight")
            if changed==1:item["state_after"]="pending"
        conn.commit()
    except BaseException:
        if conn.in_transaction:conn.rollback()
        raise
    finally:conn.close()


def recheck(database,regs,*,apply=False,originals_root=None):
    regs=validate_regs(regs)
    if not isinstance(apply,bool):raise ValueError("Apply must be an explicit boolean")
    path=_existing_path(database,"Database path")
    data_root=_existing_path(originals_root or path.parent,"Originals root",directory=True)
    plan=_read_plan(path,regs,data_root)
    if apply:_apply_plan(path,plan,data_root)
    items=[entry["item"] for entry in plan]
    return {"mode":"apply" if apply else "dry-run","database":str(path),"originals_root":str(data_root),
            "campaign":CAMPAIGN,"selected":len(items),"eligible":sum(item["eligible"] for item in items),
            "queued":sum(item["action"]=="queued" for item in items),
            "skipped":sum(item["action"]=="skipped" for item in items),"items":items}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",required=True,help="Explicit existing SQLite database file")
    parser.add_argument("--originals-root",help="Existing originals directory; defaults to the database directory")
    parser.add_argument("--reg",action="append",required=True,help="One exact registry number; repeat for 1–20 selections")
    parser.add_argument("--apply",action="store_true",help="Queue only eligible selected terminal rows; otherwise read-only preflight")
    args=parser.parse_args(argv)
    try:result=recheck(args.database,args.reg,apply=args.apply,originals_root=args.originals_root)
    except (ValueError,OSError,sqlite3.Error) as exc:
        parser.exit(2,"Scoped recheck refused: "+str(exc)+"\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
