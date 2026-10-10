"""Attach canonical violation list to detailed procurement HTML."""
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

PANEL = """<script id="v267-violations">
(function(){
 const m=window.location.pathname.match(/^\\/check-new\\/(\\d{10,20})/);
 if(!m)return;
 const reg=m[1];
 const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 async function refresh(){
  try{
   const response=await fetch('/api/v267/findings/'+reg,{cache:'no-store'});
   if(!response.ok)return;
   const data=await response.json();
   let panel=document.getElementById('v267-canonical');
   if(!panel){panel=document.createElement('section');panel.id='v267-canonical';panel.style.cssText='max-width:1150px;margin:16px auto;padding:16px;border:1px solid #d4dbd5;border-radius:8px;background:white';(document.querySelector('main')||document.body).prepend(panel);}
   panel.innerHTML='<h2>Полный перечень нарушений: '+Number(data.total)+'</h2>'+
    (data.items.length?'<ol>'+data.items.map(x=>'<li style="margin-bottom:12px"><b>'+esc(x.title||x.code||'Нарушение')+'</b><p>'+esc(x.evidence||'Основание не заполнено')+'</p>'+(x.document?'<small>'+esc(x.document)+'</small>':'')+'</li>').join('')+'</ol>':'<p>Нарушения в базе не обнаружены.</p>');
  }catch(err){console.warn('V267 findings view',err);}
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',refresh);else refresh();
 setInterval(()=>{if(!document.hidden)refresh()},60000);
})();
</script>"""

def install(ns):
    app=ns['app']
    class CardViolations(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!='GET' or not request.url.path.startswith('/check-new/') or 'text/html' not in response.headers.get('content-type',''):
                return response
            data=b''.join([piece async for piece in response.body_iterator])
            html=data.decode('utf-8')
            if 'id="v267-violations"' not in html:
                html=html.replace('</body>',PANEL+'</body>',1) if '</body>' in html else html+PANEL
            headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')}
            headers['Cache-Control']='no-store'
            return HTMLResponse(html, status_code=response.status_code, headers=headers)
    app.add_middleware(CardViolations)
