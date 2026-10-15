"""Read-only dashboard and procurement-card reconciliation.

Only actual, active 'violation' rows participate. No old historical rows are
reclassified, original findings/documents are never deleted. The same shared
grouping drives the dashboard counter and the general card.
"""
from collections import defaultdict
import difflib
import re
from v333_dedup_ui import issue_key, _proof, _different_fact

def _normalized_law(row):
    return re.sub(r"\s+"," ",str(row.get("law") or "").casefold().replace("ё","е")).strip()

def _common_event(a,b):
    if issue_key(a.get("title") or a.get("item"))!=issue_key(b.get("title") or b.get("item")):
        return False
    la,lb=_normalized_law(a),_normalized_law(b)
    if la and lb and la!=lb:return False
    pa,pb=_proof(a.get("evidence")),_proof(b.get("evidence"))
    if not pa or not pb:return bool(pa)==bool(pb) and (
        a.get("document")==b.get("document") and a.get("source_url")==b.get("source_url")
    )
    if _different_fact(pa,pb):return False
    if pa==pb:return True
    short,long=sorted((pa,pb),key=len)
    if len(short)<90:return False
    if short in long and len(short)>=90:return True
    # Remove nonessential wrappers and truncated lead-in from displayed quotes,
    # but require a substantial *identical clause*. This is not fuzzy matching
    # merely on the law/title, and never merges conflicting dates or deadlines.
    m=difflib.SequenceMatcher(None,pa[:1800],pb[:1800],autojunk=False).find_longest_match()
    return m.size>=145 and m.size>=.82*len(short)

def _choose(a,b):
    def quality(d):
        return (int(bool(d.get("document")))*2000+
                int(bool(d.get("source_url")))*2000+
                int(bool(d.get("law")))*1000+
                min(len(str(d.get("evidence") or "")),1200))
    return b if quality(b)>quality(a) else a

def collapse_legal_events(rows):
    """One visual card per established act; retain every source reference."""
    clusters=defaultdict(list)
    for item in rows:
        if not isinstance(item,dict):continue
        row=dict(item)
        row["title"]=row.get("title") or row.get("item") or row.get("code") or "Нарушение"
        key=issue_key(row["title"])
        if not key:continue
        matched=next((c for c in clusters[key] if _common_event(c["best"],row)),None)
        src={"document":str(row.get("document") or ""),
             "url":str(row.get("source_url") or "")}
        if matched is None:
            clusters[key].append({"best":row,"sources":[src] if any(src.values()) else [],
                                  "raw_count":1})
        else:
            matched["raw_count"]+=1
            if any(src.values()) and src not in matched["sources"]:
                matched["sources"].append(src)
            matched["best"]=_choose(matched["best"],row)
    result=[]
    for cs in clusters.values():
        for c in cs:
            best=dict(c["best"])
            best["supporting_sources"]=c["sources"]
            best["raw_count"]=c["raw_count"]
            result.append(best)
    return result

def load_counts(conn,regs):
    """Batched legal rows; no request-per-procurement N+1 scans."""
    regs=list(dict.fromkeys(str(x) for x in regs))
    grouped=defaultdict(list)
    for offset in range(0,len(regs),350):
        batch=regs[offset:offset+350]
        if not batch:continue
        marks=",".join("?" for _ in batch)
        for row in conn.execute(
            "SELECT reg_number,title,law,evidence,document,source_url,code "
            "FROM findings WHERE kind='violation' AND reg_number IN ("+marks+")",
            batch):
            d=dict(row);grouped[d.pop("reg_number")].append(d)
        for row in conn.execute(
            "SELECT reg_number,item AS title,law,evidence,document,source_url "
            "FROM checks WHERE status='violation' AND reg_number IN ("+marks+")",
            batch):
            d=dict(row);grouped[d.pop("reg_number")].append(d)
    return {reg:len(collapse_legal_events(grouped.get(reg,()))) for reg in regs}

def dashboard_counts(conn,sql,args,page_offset,page_limit):
    regs=[r[0] for r in conn.execute("SELECT p.reg_number"+sql,args).fetchall()]
    counts=load_counts(conn,regs)
    summaries=dict(conn.execute(
      "SELECT COALESCE(SUM(p.risks),0) risks,"
      "COALESCE(SUM(p.documents_found),0) documents_found,"
      "COALESCE(SUM(p.documents_checked),0) documents_checked,"
      "COALESCE(SUM(p.violations),0) legacy_violations_cache"+sql,args).fetchone())
    summaries["purchases"]=len(regs)
    summaries["violations"]=sum(counts.values())
    records=[dict(row) for row in conn.execute(
        "SELECT p.*"+sql+" ORDER BY p.last_seen DESC LIMIT ? OFFSET ?",
        list(args)+[page_limit,page_offset]).fetchall()]
    for row in records:
        row["violations"]=counts.get(row["reg_number"],0)
    return summaries,records
