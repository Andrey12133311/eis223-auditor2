"""Read-only dashboard and procurement-card reconciliation.

Only actual, active 'violation' rows participate. No old historical rows are
reclassified, original findings/documents are never deleted. The same shared
grouping drives the dashboard counter and the general card.
"""
from collections import OrderedDict, defaultdict
import difflib
from datetime import datetime
import hashlib
import json
import re
import threading
from v333_dedup_ui import issue_key, _proof, _different_fact
from v365_consultant_refs import legal_identity
from v342_identity import organization_display_name
from v348_cross_document_legal import finding_clarification_number, finding_protocol_number

def _normalized_law(row):
    # Equivalent ways to cite the same article/part and Government decree are
    # the same legal basis; different parts or acts must not be merged.
    return legal_identity(row.get("law"),row.get("code") or row.get("field_path"))

_DATE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(20\d{2})(?!\d)")
def _normalized_proof(text):
    proof=_proof(text)
    def iso(match):
        try:return datetime.strptime(match.group(0),"%d.%m.%Y").strftime("%Y-%m-%d")
        except ValueError:return match.group(0)
    return _DATE.sub(iso,proof)

def _dates(text):
    return frozenset(re.findall(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)",text))

def _common_event(a,b):
    """One incident: same legal basis and demonstrably matching factual episode.

    Different dates, money/payment terms or independently established clauses
    cannot be merged solely because a title, law article or detector is shared.
    """
    left_code = str(a.get("code") or a.get("field_path") or "")
    right_code = str(b.get("code") or b.get("field_path") or "")
    for family, identifier in (("CLARIFICATION-", finding_clarification_number),
                               ("PROTOCOL-", finding_protocol_number)):
        if family in left_code and family in right_code:
            left_number, right_number = identifier(a), identifier(b)
            if left_number and right_number and left_number != right_number:
                return False
    ta=issue_key(a.get("title") or a.get("item") or a.get("code"))
    tb=issue_key(b.get("title") or b.get("item") or b.get("code"))
    same_title=bool(ta and ta==tb)
    la,lb=_normalized_law(a),_normalized_law(b)
    if la and lb and la!=lb:return False
    pa,pb=_normalized_proof(a.get("evidence")),_normalized_proof(b.get("evidence"))
    if not pa or not pb:
        return (same_title and pa==pb and bool(pa) and
                a.get("document")==b.get("document") and
                a.get("source_url")==b.get("source_url"))
    if _different_fact(pa,pb):return False
    da,db=_dates(pa),_dates(pb)
    if da and db and da!=db:return False
    if not same_title:
        # Alternative analyzer titles describe a common act only when a
        # specific legal provision and a long identical original are present.
        same_law=bool(la and lb and la==lb)
        same_code=bool(a.get("code") and a.get("code")==b.get("code"))
        if not(same_law or same_code):return False
        if da!=db:return False
        short,long=sorted((pa,pb),key=len)
        if len(short)<120:return False
        if short==long:return True
        if short in long and len(short)>=160:return True
        m=difflib.SequenceMatcher(None,pa[:1800],pb[:1800],
                                  autojunk=False).find_longest_match()
        return m.size>=175 and m.size>=.94*len(short)
    if pa==pb:
        # Tiny generic text with different documents is insufficient evidence.
        return len(pa)>=45 or (
            a.get("document")==b.get("document") and
            a.get("source_url")==b.get("source_url"))
    short,long=sorted((pa,pb),key=len)
    if len(short)<90:return False
    if short in long:return True
    m=difflib.SequenceMatcher(None,pa[:1800],pb[:1800],
                              autojunk=False).find_longest_match()
    return m.size>=145 and m.size>=.82*len(short)

def _choose(a,b):
    def quality(d):
        return (int(bool(d.get("document")))*2000+
                int(bool(d.get("source_url")))*2000+
                int(bool(d.get("law")))*1000+
                min(len(str(d.get("evidence") or "")),1200))
    return b if quality(b)>quality(a) else a

def collapse_legal_events(rows):
    """One card per proven act, retaining citations and every original source.

    Deliberately read-only: returned clusters do not modify any saved finding,
    verification row, document, audit counter or prior historic evidence.
    """
    clusters=[]
    by_key=defaultdict(list)
    for item in rows:
        if not isinstance(item,dict):continue
        row=dict(item)
        row["title"]=row.get("title") or row.get("item") or row.get("code") or "Нарушение"
        title=issue_key(row["title"])
        if not title:continue
        norm=_normalized_law(row)
        keys=["title:"+title]
        if norm:keys.append("law:"+norm)
        eligible=[]
        visited=set()
        for key in keys:
            for cluster in by_key[key]:
                idx=id(cluster)
                if idx not in visited:
                    eligible.append(cluster);visited.add(idx)
        matched=next((c for c in eligible if _common_event(c["best"],row)),None)
        src={"document":str(row.get("document") or ""),
             "url":str(row.get("source_url") or "")}
        if matched is None:
            matched={"best":row,"sources":[src] if any(src.values()) else [],
                     "raw_count":1}
            clusters.append(matched)
        else:
            matched["raw_count"]+=1
            if any(src.values()) and src not in matched["sources"]:
                matched["sources"].append(src)
            matched["best"]=_choose(matched["best"],row)
        for key in keys:
            if all(x is not matched for x in by_key[key]):
                by_key[key].append(matched)
    result=[]
    for cluster in clusters:
        best=dict(cluster["best"])
        best["supporting_sources"]=cluster["sources"]
        best["raw_count"]=cluster["raw_count"]
        result.append(best)
    return result

_COUNT_CACHE = OrderedDict()
_COUNT_CACHE_LOCK = threading.Lock()
_COUNT_CACHE_LIMIT = 8192
_COUNT_FIELDS = ("title", "law", "evidence", "document", "source_url", "code")


def _saved_event_count(reg, rows):
    """Reuse only an unchanged, complete evidence snapshot's exact count.

    Hash every field used by collapse_legal_events, in the same row order.
    Edits, deletions, status changes and replacements invalidate immediately;
    neither a TTL nor a row-count/MAX(id) shortcut can miss an evidence edit.
    The bounded shared cache retains digests and integers, never user data or
    original evidence. Its value is a pure function of the saved rows.
    """
    if not rows:
        return 0
    payload = json.dumps([tuple(row.get(field) for field in _COUNT_FIELDS)
                          for row in rows], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    key = (reg, hashlib.sha256(payload).digest())
    with _COUNT_CACHE_LOCK:
        if key in _COUNT_CACHE:
            _COUNT_CACHE.move_to_end(key)
            return _COUNT_CACHE[key]
    count = len(collapse_legal_events(rows))
    with _COUNT_CACHE_LOCK:
        _COUNT_CACHE[key] = count
        _COUNT_CACHE.move_to_end(key)
        while len(_COUNT_CACHE) > _COUNT_CACHE_LIMIT:
            _COUNT_CACHE.popitem(last=False)
    return count


def load_counts(conn,regs):
    """Exact full-filter counts without repeatedly reconciling old evidence."""
    regs=list(dict.fromkeys(str(x) for x in regs))
    counts={}
    for offset in range(0,len(regs),350):
        batch=regs[offset:offset+350]
        if not batch:continue
        grouped=defaultdict(list)
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
        counts.update((reg,_saved_event_count(reg,grouped.get(reg,())))
                      for reg in batch)
    return counts

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
        row["customer"]=organization_display_name(row.get("customer"))
    return summaries,records
