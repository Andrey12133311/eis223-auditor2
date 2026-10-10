#!/usr/bin/env python3
"""V262: don't repeatedly inspect auto purchases whose originals are unavailable.

Only the private runtime's disposable /tmp source is patched. The database,
already checked documents, manual queue, customer queue, and dashboard counts
remain unchanged. Failed auto jobs remain stored but are not re-claimed after
the limited source attempts (unless manually retried through the existing UI).
"""
from __future__ import annotations
import hashlib
from pathlib import Path

CLAIM_OLD = """        if lane=='auto':
            order+=" CASE WHEN has_alternative=1 THEN 0 ELSE 1 END,"
        sql+=" ORDER BY "+order+" priority DESC, attempts, due, reg, identity LIMIT 1"""

CLAIM_NEW = """        if lane=='auto':
            # An unusable link is not a procurement inspection.
            # Prefer a cached original or an allowed fallback source.
            sql+=" AND (COALESCE(local_path,'')<>'' OR COALESCE(json_extract(payload,'$.local'),'')<>'' OR COALESCE(source_host,'')<>'' OR has_alternative=1)"
            # Two failures without a fallback; three for saved/fallback files.
            # Never delete a job or change its 'checked' status to inflate stats.
            sql+=" AND attempts<CASE WHEN has_alternative=1 OR COALESCE(local_path,'')<>'' OR COALESCE(json_extract(payload,'$.local'),'')<>'' THEN 3 ELSE 2 END"
            order+=" CASE WHEN has_alternative=1 THEN 0 ELSE 1 END,"
        sql+=" ORDER BY "+order+" priority DESC, attempts, due, reg, identity LIMIT 1"""

RECOVERY_OLD = """        args=[]
        if reg:sql+=' AND d.reg_number=?';args.append(reg)
        if not force:sql+=' AND COALESCE(r.next_at,0)<=?';args.append(_t234.time())"""

RECOVERY_NEW = """        args=[]
        if reg:sql+=' AND d.reg_number=?';args.append(reg)
        if not force:
            # Auto restoration gets one first-original try; let V204 handle
            # bounded retries instead of running both retry loops endlessly.
            sql+=' AND COALESCE(r.next_at,0)<=? AND COALESCE(r.tries,0)=0'
            args.append(_t234.time())"""

RULES = ((CLAIM_OLD,CLAIM_NEW),(RECOVERY_OLD,RECOVERY_NEW))

def patch_text(source:str)->str:
    for old,new in RULES:
        matches=source.count(old)
        if matches!=1:
            raise RuntimeError(f'V262 source mismatch; {matches} anchors: {old[:80]!r}')
        source=source.replace(old,new,1)
    compile(source,'entry.py','exec')
    return source

def patch(path:Path)->str:
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise ValueError('Cannot patch original private Railway backup')
    source=path.read_text(encoding='utf-8')
    changed=patch_text(source)
    path.write_text(changed,encoding='utf-8')
    return hashlib.sha256(changed.encode('utf-8')).hexdigest()
