"""Render canonical violations beside the existing audit, not at the page top."""
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

SCRIPT = """<script id="v269-inline-findings">
(function(){
 const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let current='',busy=false;
 async function sync(){
  const detail=document.getElementById('detail');
  const regInTitle=(document.getElementById('detailTitle')?.textContent||'').match(/[0-9]{10,20}/);
  const inPath=location.pathname.match(/[0-9]{10,20}/);
  const reg=regInTitle?.[0]||inPath?.[0];
  if(!reg || busy)return;
  const area=detail&&regInTitle?detail:(document.querySelector('main')||document.body);
  if(!area)return;
  const old=area.querySelector('#v269-all-findings');
  if(current===reg && old)return;
  busy=true;
  try{
   const response=await fetch('/api/v267/findings/'+reg,{cache:'no-store'});
   if(!response.ok)return;
   const result=await response.json();
   if(document.getElementById('detailTitle') && regInTitle && !(document.getElementById('detailTitle').textContent||'').includes(reg))return;
   if(old)old.remove();
   const panel=document.createElement('section');
   panel.id='v269-all-findings';
   panel.style.cssText='margin:14px 0;padding:16px;border:2px solid #c77b7b;border-radius:8px;background:white;line-height:1.5';
   panel.innerHTML='<h3 style="margin-top:0">Все нарушения из базы: '+Number(result.total)+'</h3>' +
      (result.items.length?'<ol style="padding-left:22px">'+result.items.map((x)=>'<li style="margin-bottom:14px"><strong>'+esc(x.title||x.code||'Нарушение')+'</strong><p>'+esc(x.evidence||'Основание отсутствует')+'</p>'+(x.document?'<small>Документ: '+esc(x.document)+'</small>':'')+'</li>').join('')+'</ol>':'<p>Нарушения не найдены.</p>');
   const headings=Array.from(area.querySelectorAll('h2,h3,h4')).filter(x=>/Служебная записка/i.test(x.textContent||''));
   const memo=headings[0]?.closest('section,.card,article')||headings[0];
   if(memo && memo!==area) memo.insertAdjacentElement('beforebegin',panel);
   else if(area.querySelector('.detailmeta'))area.querySelector('.detailmeta').insertAdjacentElement('afterend',panel);
   else area.prepend(panel);
   current=reg;
  }catch(e){console.warn('V269 findings',e)}
  finally{busy=false}
 }
 let queued=false;
 new MutationObserver(()=>{if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;sync()})}}).observe(document.documentElement,{childList:true,subtree:true});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',sync);else sync();
 setInterval(()=>{current='';if(!document.hidden)sync()},60000);
})();
</script>"""

def install(ns):
    app=ns['app']
    class CardOverlay(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!='GET' or (request.url.path!='/' and not request.url.path.startswith('/check-new/')) or 'text/html' not in response.headers.get('content-type',''):
                return response
            raw=b''.join([x async for x in response.body_iterator])
            html=raw.decode('utf-8')
            if 'id="v269-inline-findings"' not in html:
                html=html.replace('</body>',SCRIPT+'</body>',1) if '</body>' in html else html+SCRIPT
            headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')}
            headers['Cache-Control']='no-store'
            return HTMLResponse(html,status_code=response.status_code,headers=headers)
    app.add_middleware(CardOverlay)
