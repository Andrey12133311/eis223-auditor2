#!/usr/bin/env python3
"""V261: favor cached and alternative original sources while retaining ALL jobs.

Fail closed if private entry.py differs from the inspected V260 source.
Never touch recovery snapshot, legal findings, stats, or existing UI.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

ORDER_OLD = """        if blocked:
            sql+=" AND (COALESCE(local_path,'')<>'' OR has_alternative=1 OR source_host NOT IN ("+','.join('?' for _ in blocked)+"))"
            params.extend(blocked)
        # Cached originals first; files without readable text remain pending.
        sql+=" ORDER BY CASE WHEN COALESCE(local_path,'')<>'' OR COALESCE(json_extract(payload,'$.local'),'')<>'' THEN 0 ELSE 1 END, priority DESC, attempts, due, reg, identity LIMIT 1"""

ORDER_NEW = """        if blocked:
            sql+=" AND (COALESCE(local_path,'')<>'' OR has_alternative=1 OR COALESCE(source_host,'') NOT IN ("+','.join('?' for _ in blocked)+"))"
            params.extend(blocked)
        # A reachable, saved copy first. On auto lane prefer alternative ETP
        # sources over single-host EIS backlogs. Never delete or mark jobs checked.
        order="CASE WHEN COALESCE(local_path,'')<>'' OR COALESCE(json_extract(payload,'$.local'),'')<>'' THEN 0 ELSE 1 END,"
        if lane=='auto':
            order+=" CASE WHEN has_alternative=1 THEN 0 ELSE 1 END,"
        sql+=" ORDER BY "+order+" priority DESC, attempts, due, reg, identity LIMIT 1"""

ALTERNATIVE_OLD = """                    try: alt_host=(urlparse(u).hostname or '').lower()
                    except ValueError: continue
                    if alt_host and alt_host!=host:
                        alternative=True
                        break"""

ALTERNATIVE_NEW = """                    try:
                        alt_host=(urlparse(u).hostname or '').lower()
                        safe=bool(ns.get('safe_host') and ns['safe_host'](u))
                    except (ValueError,TypeError):
                        continue
                    if safe and alt_host and alt_host!=host:
                        alternative=True
                        break"""

RULES=((ORDER_OLD, ORDER_NEW),(ALTERNATIVE_OLD,ALTERNATIVE_NEW))

def patch_text(source: str) -> str:
    for old,new in RULES:
        count=source.count(old)
        if count!=1:
            raise RuntimeError(f"V261 unexpected source anchor ({count} matches): {old[:100]!r}")
        source=source.replace(old,new,1)
    compile(source,"entry.py","exec")
    return source

def patch(path: Path) -> str:
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise ValueError("Refusing to change original Railway snapshot")
    original=path.read_text(encoding='utf-8')
    changed=patch_text(original)
    path.write_text(changed,encoding='utf-8')
    return hashlib.sha256(changed.encode('utf-8')).hexdigest()
