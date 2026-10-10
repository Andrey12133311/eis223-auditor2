"""Fair document recovery: one fresh unverified document per procurement per batch."""
from pathlib import Path
import hashlib
OLD="""    fresh=[]
    c=conn()"""
NEW="""    fresh=[]
    fresh_reg_seen=set()
    c=conn()"""
OLD2="""            if str(r['reg_number']) in verified:continue
            source=[r.get('url')]"""
NEW2="""            if str(r['reg_number']) in verified or str(r['reg_number']) in fresh_reg_seen:continue
            source=[r.get('url')]"""
OLD3="""                fresh.append(r)
                if len(fresh)>=limit:break"""
NEW3="""                fresh.append(r)
                fresh_reg_seen.add(str(r['reg_number']))
                if len(fresh)>=limit:break"""
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):raise RuntimeError('Protected')
    source=path.read_text(encoding='utf-8')
    for old,new in ((OLD,NEW),(OLD2,NEW2),(OLD3,NEW3)):
        if source.count(old)!=1:raise RuntimeError('Unexpected source anchor: '+old[:80])
        source=source.replace(old,new,1)
    compile(source,str(path),'exec')
    path.write_text(source,encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()
