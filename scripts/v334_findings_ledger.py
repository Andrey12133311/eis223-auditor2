"""Non-destructive findings history and audit-counter reconciliation.

Legacy save() replaces findings and checks wholesale on each recheck. Preserve
evidence BEFORE any replacement and expose prior results as review candidates,
never as newly confirmed violations.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from functools import wraps
from fastapi import HTTPException

REG = re.compile(r"^[0-9]{10,20}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS v334_violation_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 reg_number TEXT NOT NULL,
 record_type TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 first_saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 payload_json TEXT NOT NULL,
 UNIQUE(reg_number,record_type,fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_v334_hist_reg ON v334_violation_history(reg_number);
CREATE TABLE IF NOT EXISTS v334_recheck_changes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 reg_number TEXT NOT NULL,
 checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 before_count INTEGER NOT NULL,
 after_count INTEGER NOT NULL,
 missing_count INTEGER NOT NULL,
 new_count INTEGER NOT NULL,
 missing_fingerprints_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v334_changes_reg ON v334_recheck_changes(reg_number);
"""

def _records(db,reg=None):
    result=[]
    for kind,table,where in (
        ("finding","findings","kind='violation'"),
        ("check","checks","status='violation'"),
    ):
        query="SELECT * FROM "+table+" WHERE "+where
        values=()
        if reg is not None:
            query+=" AND reg_number=?"
            values=(reg,)
        for row in db.execute(query,values):
            record={key:row[key] for key in row.keys() if key!="id"}
            number=str(record.get("reg_number") or "")
            if not REG.fullmatch(number):
                continue
            payload=json.dumps(record,ensure_ascii=False,sort_keys=True,default=str)
            fingerprint=hashlib.sha256(payload.encode("utf-8")).hexdigest()
            result.append((number,kind,fingerprint,payload))
    return result

def snapshot(connect,reg=None):
    db=connect()
    try:
        db.row_factory=sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        current=_records(db,reg)
        db.executemany("""INSERT OR IGNORE INTO v334_violation_history
            (reg_number,record_type,fingerprint,payload_json)
            VALUES(?,?,?,?)""",current)
        db.commit()
        return {(kind,digest) for _,kind,digest,_ in current}
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise
    finally:
        db.close()

def _track(connect,reg,before,after):
    disappeared=before-after
    added=after-before
    if not disappeared and not added:
        return
    db=connect()
    try:
        with db:
            db.execute("""INSERT INTO v334_recheck_changes
                (reg_number,before_count,after_count,missing_count,new_count,missing_fingerprints_json)
                VALUES(?,?,?,?,?,?)""",
                (reg,len(before),len(after),len(disappeared),len(added),
                 json.dumps(sorted(k+":"+v for k,v in disappeared),ensure_ascii=False)))
    finally:
        db.close()
    if disappeared:
        print("V334 HISTORY_PRESERVED reg="+reg+
              " disappeared_or_altered="+str(len(disappeared))+
              " new_or_altered="+str(len(added)),flush=True)

def summary(connect):
    db=connect()
    try:
        db.row_factory=sqlite3.Row
        def scalar(sql):
            return int(db.execute(sql).fetchone()[0] or 0)
        findings=scalar("SELECT COUNT(*) FROM findings WHERE kind='violation'")
        checks=scalar("SELECT COUNT(*) FROM checks WHERE status='violation'")
        # These are diagnostic groups, not a legal determination: multiple
        # incidents can legitimately share one short heading.
        from v333_dedup_ui import issue_key
        fgroup={(str(r[0]),issue_key(r[1])) for r in db.execute(
            "SELECT reg_number,title FROM findings WHERE kind='violation'")
            if issue_key(r[1])}
        cgroup={(str(r[0]),issue_key(r[1])) for r in db.execute(
            "SELECT reg_number,item FROM checks WHERE status='violation'")
            if issue_key(r[1])}
        return {
            "purchase_counter":scalar("SELECT COALESCE(SUM(violations),0) FROM purchases"),
            "purchases":scalar("SELECT COUNT(*) FROM purchases"),
            "findings_rows":findings,
            "violation_check_rows":checks,
            "raw_rows_before_cross_table_review":findings+checks,
            "same_title_groups_across_tables":len(fgroup & cgroup),
            "issue_title_groups_in_both_tables":len(fgroup | cgroup),
            "archived_finding_versions":scalar("SELECT COUNT(*) FROM v334_violation_history WHERE record_type='finding'"),
            "archived_check_versions":scalar("SELECT COUNT(*) FROM v334_violation_history WHERE record_type='check'"),
            "changed_rechecks":scalar("SELECT COUNT(*) FROM v334_recheck_changes WHERE missing_count>0"),
            "same_title_extra_rows_not_proven_duplicates":scalar("""SELECT COALESCE(SUM(n-1),0) FROM
                (SELECT COUNT(*) n FROM findings WHERE kind='violation'
                 GROUP BY reg_number,LOWER(TRIM(title)) HAVING COUNT(*)>1)"""),
            "note":"Archive stores past source rows for review, not established current violations."
        }
    finally:
        db.close()


def compare_same_titles(connect,limit=100):
    """Diagnostic of duplicate candidates; no deletion or requalification."""
    from v333_dedup_ui import issue_key
    db=connect()
    try:
        db.row_factory=sqlite3.Row
        tables=[]
        for name,column,condition in (("findings","title","kind='violation'"),
                                      ("checks","item","status='violation'")):
            groups={}
            for row in db.execute("SELECT reg_number,"+column+
                                  " title,document,evidence,law,source_url FROM "+name+
                                  " WHERE "+condition):
                key=(str(row["reg_number"]),issue_key(row["title"]))
                if key[1]:groups.setdefault(key,[]).append(dict(row))
            tables.append(groups)
        left,right=tables
        def norm(x):
            return re.sub(r"\s+"," ",str(x or "").casefold().replace("ё","е")).strip()
        def same(a,b):
            # A legacy check can name multiple sources as
            # "name.docx; Карточка закупки". Split on the delimiter so
            # a true shared original can be compared on its exact name.
            docs_a={norm(x) for x in str(a.get("document") or "").split(";") if norm(x)}
            docs_b={norm(x) for x in str(b.get("document") or "").split(";") if norm(x)}
            url_a,url_b=norm(a.get("source_url")),norm(b.get("source_url"))
            shared_document=bool((docs_a & docs_b) or
                                 (url_a and url_a==url_b))
            x,y=norm(a.get("evidence")),norm(b.get("evidence"))
            same_excerpt=bool(x and y and (x==y or
                (min(len(x),len(y))>=70 and (x in y or y in x))))
            law_a,law_b=norm(a.get("law")),norm(b.get("law"))
            return shared_document and same_excerpt and not (
                law_a and law_b and law_a!=law_b)
        items=[]
        for key in sorted(left.keys()&right.keys()):
            matched=any(same(a,b) for a in left[key] for b in right[key])
            record={
                "reg_number":key[0],
                "issue":left[key][0]["title"],
                "finding_rows":len(left[key]),
                "check_rows":len(right[key]),
                "classification":("matching_documentary_excerpt" if matched else
                                  "evidence_review_required"),
                "finding_documents":sorted({str(r.get("document") or "") for r in left[key]})[:5],
                "check_documents":sorted({str(r.get("document") or "") for r in right[key]})[:5]
            }
            if not matched:
                # Only ambiguous pairs are output for expert review. Do not
                # expose every procurement's complete document contents.
                record["finding_proofs"]=[{"law":r.get("law"),"evidence":str(r.get("evidence") or "")[:360]}
                                           for r in left[key][:3]]
                record["check_proofs"]=[{"law":r.get("law"),"evidence":str(r.get("evidence") or "")[:360]}
                                         for r in right[key][:4]]
            items.append(record)
        matched=sum(x["classification"]=="matching_documentary_excerpt" for x in items)
        return {"shared_procurement_issue_groups":len(items),
                "matching_documentary_excerpt_groups":matched,
                "groups_requiring_evidence_review":len(items)-matched,
                "items":items[:min(max(int(limit),0),300)],
                "note":"Names alone do not prove duplicates. No rows were deleted."}
    finally:
        db.close()


def install(ns):
    connect=ns["conn"]
    app=ns["app"]
    existing_save=ns.get("save")
    if not callable(existing_save):
        raise RuntimeError("V334: original save() unavailable")
    db=connect()
    try:
        db.executescript(SCHEMA)
        db.commit()
    finally:
        db.close()
    # Snapshot is normally mandatory before any legacy rewriting of findings.
    # If the EIS volume is exhausted, retain all existing records and allow
    # read-only pages/diagnostics to start. Protected writes below continue
    # to fail closed until sufficient capacity is restored.
    try:
        snapshot(connect)
    except sqlite3.OperationalError as exc:
        if "database or disk is full" not in str(exc).lower():
            raise
        print("V354_EVIDENCE_LEDGER_WRITE_DEFERRED volume_full=True "+
              "existing_evidence_preserved=True",flush=True)
    print("V334 BASELINE "+json.dumps(summary(connect),ensure_ascii=False),flush=True)
    overlap=compare_same_titles(connect,100)
    print("V334 CROSS_TABLE "+json.dumps({k:v for k,v in overlap.items() if k!="items"},ensure_ascii=False),flush=True)
    unsettled=[i for i in overlap["items"] if i["classification"]=="evidence_review_required"]
    if unsettled:
        print("V334 EVIDENCE_REVIEW "+json.dumps(unsettled[:10],ensure_ascii=False),flush=True)

    if not getattr(existing_save,"__v334_protected__",False):
        @wraps(existing_save)
        def protected_save(*args,**kwargs):
            p=args[0] if args else kwargs.get("p")
            reg=str(p.get("reg") or "") if isinstance(p,dict) else ""
            if not REG.fullmatch(reg):
                return existing_save(*args,**kwargs)
            before=snapshot(connect,reg)
            result=existing_save(*args,**kwargs)
            after=snapshot(connect,reg)
            _track(connect,reg,before,after)
            return result
        protected_save.__v334_protected__=True
        ns["save"]=protected_save

    @app.get("/api/v334/violation-ledger/summary",include_in_schema=False)
    def ledger_summary():
        return summary(connect)

    @app.get("/api/v334/violation-ledger/overlaps",include_in_schema=False)
    def ledger_overlaps(limit:int=100):
        return compare_same_titles(connect,limit)

    @app.get("/api/v334/violation-ledger/changes",include_in_schema=False)
    def ledger_changes(limit:int=50):
        db=connect()
        try:
            db.row_factory=sqlite3.Row
            rows=db.execute("""SELECT reg_number,checked_at,before_count,
                after_count,missing_count,new_count FROM v334_recheck_changes
                ORDER BY id DESC LIMIT ?""",(min(max(1,limit),100),)).fetchall()
            return {"items":[dict(r) for r in rows]}
        finally:
            db.close()

    @app.get("/api/v334/violation-ledger/purchase/{reg}",include_in_schema=False)
    def ledger_purchase(reg:str,limit:int=150):
        if not REG.fullmatch(reg):
            raise HTTPException(422,"Некорректный номер закупки")
        db=connect()
        try:
            db.row_factory=sqlite3.Row
            active={(k,d) for _,k,d,_ in _records(db,reg)}
            rows=db.execute("""SELECT record_type,fingerprint,first_saved_at,payload_json
                FROM v334_violation_history WHERE reg_number=?
                ORDER BY id DESC LIMIT ?""",(reg,min(max(1,limit),300))).fetchall()
            items=[]
            for row in rows:
                old=json.loads(row["payload_json"])
                items.append({"record_type":row["record_type"],
                    "currently_present":(row["record_type"],row["fingerprint"]) in active,
                    "first_saved_at":row["first_saved_at"],
                    "title":old.get("title") or old.get("item") or "",
                    "law":old.get("law") or "",
                    "document":old.get("document") or "",
                    "evidence":old.get("evidence") or "",
                    "source_url":old.get("source_url") or "",
                    "status":"historical_review" if (row["record_type"],row["fingerprint"]) not in active
                             else "active_record"})
            return {"reg_number":reg,"items":items,
                "note":"Historical records require verification before restoring as confirmed violations."}
        finally:
            db.close()
    print("V334_LEDGER_READY non_destructive=True",flush=True)
