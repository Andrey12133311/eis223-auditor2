#!/usr/bin/env python3
"""V258 source patch: skip temporarily unavailable hosts, process cached originals first.

Only changes the V257 ephemeral entry.py, never /data snapshots or SQLite.
V256 already verifies original snapshot SHA256; V257 and V258 are fail-closed.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

CLAIM_OLD = """    def claim(lane='auto'):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT * FROM document_jobs_v204 WHERE lane=? AND status!='checked' AND due<=? ORDER BY priority DESC,attempts,due,reg,identity LIMIT 1",(lane,time.time())).fetchone()
            if not row:return None
            c.execute("UPDATE document_jobs_v204 SET status='running',due=?,attempts=attempts+1 WHERE reg=? AND identity=?",(time.time()+600,row['reg'],row['identity']))
            return dict(row)"""
CLAIM_NEW = """    def claim(lane='auto'):
        now_=time.time()
        blocked=[host for (source_lane,host), health in host_health.items()
                 if source_lane==lane and host and health.get('until',0)>now_]
        sql="SELECT * FROM document_jobs_v204 WHERE lane=? AND status!='checked' AND due<=?"
        params=[lane,now_]
        if blocked:
            sql+=" AND (COALESCE(local_path,'')<>'' OR has_alternative=1 OR source_host NOT IN ("+','.join('?' for _ in blocked)+"))"
            params.extend(blocked)
        # Cached originals first; files without readable text remain pending.
        sql+=" ORDER BY CASE WHEN COALESCE(local_path,'')<>'' OR COALESCE(json_extract(payload,'$.local'),'')<>'' THEN 0 ELSE 1 END, priority DESC, attempts, due, reg, identity LIMIT 1"
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute(sql,params).fetchone()
            if not row:return None
            c.execute("UPDATE document_jobs_v204 SET status='running',due=?,attempts=attempts+1 WHERE reg=? AND identity=?",(now_+600,row['reg'],row['identity']))
            return dict(row)"""

ENQUEUE_OLD = """                urls=[d.get('final_url'),(d.get('meta') or {}).get('mirror_url')]+list((d.get('meta') or {}).get('alt_urls') or [])
                alternative=any(u and urlparse(u).hostname and (urlparse(u).hostname or '').lower()!=host for u in urls)"""
ENQUEUE_NEW = """                meta=d.get('meta') if isinstance(d.get('meta'),dict) else {}
                alts=meta.get('alt_urls') or []
                if isinstance(alts,str):alts=[alts]
                if not isinstance(alts,(list,tuple)):alts=[]
                urls=[d.get('final_url'),meta.get('mirror_url')]+list(alts)
                alternative=False
                for u in urls:
                    if not isinstance(u,str) or not u.startswith(('https://','http://')):continue
                    try: alt_host=(urlparse(u).hostname or '').lower()
                    except ValueError: continue
                    if alt_host and alt_host!=host:
                        alternative=True
                        break"""
RULES = (
    (CLAIM_OLD, CLAIM_NEW),
    (ENQUEUE_OLD, ENQUEUE_NEW),
)

def patch_text(source: str) -> str:
    for before, after in RULES:
        count = source.count(before)
        if count != 1:
            raise RuntimeError("V258 unexpected source: patch anchor found " + str(count) + ": " + before[:90])
        source = source.replace(before, after, 1)
    compile(source, "entry.py", "exec")
    return source

def patch(path: Path) -> str:
    path = Path(path).resolve(strict=True)
    if str(path).startswith("/data/") or "/_eis223_runtime_recovery/" in str(path):
        raise ValueError("V258 must not modify the original recovery volume")
    before = path.read_text(encoding="utf-8")
    after = patch_text(before)
    path.write_text(after, encoding="utf-8")
    return hashlib.sha256(after.encode("utf-8")).hexdigest()
