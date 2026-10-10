#!/usr/bin/env python3
"""V259: keep link-backed purchase evidence until an original can be read.

Never change the snapshot or existing UI counters. Unverified purchases stay
hidden by V235/V236/V257 verified-only dashboard filters.
"""
from pathlib import Path

RULES = (
    (
        "WHERE last_seen<? AND COALESCE(documents_checked,0)=0\n          ORDER BY last_seen ASC LIMIT 120",
        "WHERE last_seen<? AND COALESCE(documents_checked,0)=0\n            AND NOT EXISTS (SELECT 1 FROM document_jobs_v204 j WHERE j.reg=purchases.reg_number\n              AND j.status!='checked' AND j.attempts<8 AND COALESCE(j.source_host,'')!='')\n          ORDER BY last_seen ASC LIMIT 120",
    ),
    (
        "def _discard239(reg):\n    if _registered239(reg):return False",
        "def _discard239(reg):\n    if _registered239(reg):return False\n    if _pending_verified_source259(reg):\n        print('V259 HIDDEN_PENDING_DOCS reg='+str(reg),flush=True)\n        return False",
    ),
    (
        "    if not available:\n        print('V239 SAVE_REJECTED_UNREAD reg='+reg,flush=True)\n        return None\n    return _old_save239(p,docs,finds,checks,card_payload)",
        "    if not available:\n        # Stage only link-backed attachment metadata; hide until a real original is verified.\n        if any(_source_candidate259(d) for d in (docs or [])):\n            print('V259 STAGED_PENDING_ORIGINAL reg='+reg+' docs='+str(len(docs)),flush=True)\n            return _old_save239(p,docs,[],[],card_payload)\n        print('V239 SAVE_REJECTED_UNREAD reg='+reg,flush=True)\n        return None\n    return _old_save239(p,docs,finds,checks,card_payload)",
    ),
)

BOOTSTRAP = '''
# V259 evidence-only staging helpers. Existing verified-only UI unchanged.
def _source_candidate259(d):
    if not isinstance(d,dict):return False
    meta=d.get('meta') if isinstance(d.get('meta'),dict) else {}
    alternatives=meta.get('alt_urls') or []
    if isinstance(alternatives,str):alternatives=[alternatives]
    if not isinstance(alternatives,(tuple,list)):alternatives=[]
    urls=[d.get('url'),d.get('final_url'),meta.get('mirror_url')]+list(alternatives)
    for u in urls:
        if not isinstance(u,str):continue
        try:
            if safe_host(u):return True
        except (ValueError,TypeError):continue
    return False

def _pending_verified_source259(reg):
    try:
        c=conn()
        try:
            row=c.execute("SELECT 1 FROM document_jobs_v204 WHERE reg=? AND status!='checked' AND attempts<8 AND COALESCE(source_host,'')!='' LIMIT 1",(str(reg),)).fetchone()
            return bool(row)
        finally:c.close()
    except Exception as e:
        print('V259 PENDING_GUARD_ERROR '+repr(e)[:250],flush=True)
        return False
'''

def patch_text(source):
    for old, new in RULES:
        count=source.count(old)
        if count!=1:raise RuntimeError(f'V259 anchor mismatch {count}: {old[:85]!r}')
        source=source.replace(old,new,1)
    source+=BOOTSTRAP
    compile(source,'entry.py','exec')
    return source

def patch(path):
    p=Path(path).resolve(strict=True)
    if str(p).startswith('/data/') or '/_eis223_runtime_recovery/' in str(p):
        raise ValueError('Do not patch the private original or data volume')
    p.write_text(patch_text(p.read_text(encoding='utf-8')),encoding='utf-8')
