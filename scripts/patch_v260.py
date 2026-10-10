#!/usr/bin/env python3
"""V260: let new procurements obtain their first original via recovery worker.
Fail closed if original snapshot code differs; never touch counters or private /data.
"""
from pathlib import Path

OLD = """_prev_pending236=_pending234
def _pending234(n=12,reg=None,force=False):
    rows=_prev_pending236(max(n*8,50),reg,force)
    good=_verified_set235()
    return [r for r in rows if str(r.get('reg_number')) in good][:n]"""

NEW = """_prev_pending236=_pending234
def _pending234(n=12,reg=None,force=False):
    # V260: reserve slots for fresh purchases with allowed original links.
    # Previously V236 excluded every purchase without a checked original.
    limit=min(max(int(n),1),30)
    verified=_verified_set235()
    rows=_prev_pending236(max(limit*12,120),reg,force)
    old=[r for r in rows if str(r.get('reg_number')) in verified]
    fresh=[]
    c=conn()
    try:
        sql='''SELECT d.id,d.reg_number,d.name,d.url,d.doc_type,d.metadata_json,d.local_path,
                    COALESCE(r.tries,0) tries
             FROM docs d
             JOIN purchases p ON p.reg_number=d.reg_number
             LEFT JOIN retry_docs_v234 r ON r.doc_id=d.id
             WHERE (d.status!='checked' OR COALESCE(d.text_chars,0)<3)
               AND COALESCE(r.tries,0)<3'''
        args=[]
        if reg:sql+=' AND d.reg_number=?';args.append(reg)
        if not force:sql+=' AND COALESCE(r.next_at,0)<=?';args.append(_t234.time())
        sql+=' ORDER BY d.id DESC LIMIT 250'
        for row in c.execute(sql,args):
            r=dict(row)
            if str(r['reg_number']) in verified:continue
            source=[r.get('url')]
            try:
                meta=_j234.loads(r.get('metadata_json') or '{}')
                if isinstance(meta,dict):
                    extra=meta.get('alt_urls') or []
                    if isinstance(extra,str):extra=[extra]
                    if isinstance(extra,(list,tuple)):source.extend(extra)
            except (ValueError,TypeError):pass
            if (r.get('local_path') and _P234(r['local_path']).is_file()) or any(
                isinstance(u,str) and _allowed234(u) for u in source):
                fresh.append(r)
                if len(fresh)>=limit:break
    finally:c.close()
    if reg or force:return (fresh+old)[:limit]
    reserve=max(1,limit//2)
    selected=fresh[:reserve]+old[:limit-reserve]
    if len(selected)<limit:
        seen={r['id'] for r in selected}
        selected.extend(r for r in (fresh[reserve:]+old[limit-reserve:]) if r['id'] not in seen)
    return selected[:limit]"""

def patch_text(source):
    count=source.count(OLD)
    if count!=1:raise RuntimeError('Unexpected verified-only recovery wrapper; found '+str(count))
    output=source.replace(OLD,NEW,1)
    compile(output,'entry.py','exec')
    return output

def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):
        raise ValueError('Never patch the private production original')
    path.write_text(patch_text(path.read_text(encoding='utf-8')),encoding='utf-8')
