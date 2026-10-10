"""Show complete DB-backed legal findings on check cards, without mutating findings."""
from __future__ import annotations
import re,sqlite3
from contextlib import closing
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

SCRIPT=r'''<script id="v267-findings-consistency">
(()=>{const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const m=location.pathname.match(/^\\/check-new\\/(\\d{10,20})/);if(!m)return;
async function render(){try{const response=await fetch('/api/v267/findings/'+m[1],{cache:'no-store'});if(!response.ok)return;const d=await response.json();let panel=document.getElementById('v267-all-violations');if(!panel){panel=document.createElement('section');panel.id='v267-all-violations';panel.style.cssText='margin:14px auto;padding:16px;max-width:1150px;border:1px solid #d3dcd5;background:#fff;border-radius:8px';(document.querySelector('main')||document.body).prepend(panel)}
panel.innerHTML='<h2 style="font-size:18px;margin:0 0 12px">Все нарушения по закупке: '+Number(d.total)+'</h2>'+(d.items.length?'<ol style="padding-left:22px">'+d.items.map(x=>'<li style="margin-bottom:14px"><strong>'+esc(x.title||x.code||'Нарушение')+'</strong><div>'+esc(x.evidence||'Основание не указано')+'</div>'+(x.document?'<small>Документ: '+esc(x.document)+'</small>':'')+'</li>').join('')+'</ol>':'<p>Подтверждённые нарушения в базе не найдены.</p>');
}catch(e){console.warn('Ошибка сверки нарушений',e)}}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',render);else render();
setInterval(()=>{if(!document.hidden)render()},60000);
})();</script>'''
def install(ns):
    app=ns['app']; connection=ns['conn']
    @app.get('/api/v267/findings/{reg}',include_in_schema=False)
    def findings(reg:str):
        if not re.fullmatch(r'\\d{10,20}',reg):raise HTTPException(400,'Неверный номер закупки')
        with closing(connection()) as c:
            c.row_factory=sqlite3.Row
            rows=c.execute("SELECT code,title,evidence,document FROM findings WHERE reg_number=? AND kind='violation' ORDER BY id DESC",(reg,)).fetchall()
        return {'reg':reg,'total':len(rows),'items':[dict(r) for r in rows]}
    class Overlay(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            resp=await call_next(request)
            if request.method!='GET' or not request.url.path.startswith('/check-new/') or 'text/html' not in resp.headers.get('content-type',''):return resp
            try:
                original=b''.join([part async for part in resp.body_iterator])
                body=original.decode('utf-8')
                if 'id="v267-findings-consistency"' not in body:
                    body=body.replace('</body>',SCRIPT+'</body>',1) if '</body>' in body else body+SCRIPT
                headers={k:v for k,v in resp.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')}
                headers['Cache-Control']='no-store'
                return HTMLResponse(body,status_code=resp.status_code,headers=headers)
            except Exception as exc:
                print('V267 CARD_OVERLAY_ERROR '+repr(exc)[:200],flush=True)
                return resp
    app.add_middleware(Overlay)
