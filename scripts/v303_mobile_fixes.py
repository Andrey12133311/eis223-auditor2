"""Mobile dashboard cleanup and reliable official audit-card navigation.

Keep only the existing filters and procurement list on the main dashboard.
Preserve full search tools in the dedicated 'Самостоятельные проверки' area.
"""
from __future__ import annotations
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

CSS = '''<style id="v303-mobile-fixes">
#v300-eis-status, .v302-connect {display:none!important}
body:has(#filters) #v302-search-panel, body:has(#filters) form#manual,
body:has(#filters) #customer-progress-v233 {display:none!important}
#filters{display:flex;flex-wrap:wrap;gap:10px!important;align-items:center}
#filters>*{min-width:0}
#filters select{max-width:100%;white-space:normal}
#v257-violation-type{max-width:min(98vw,570px)!important;width:100%!important;
min-height:44px;overflow-wrap:anywhere;white-space:normal}
#reset{visibility:visible!important;opacity:1!important;display:inline-flex!important;
align-items:center;justify-content:center;flex-shrink:0}
#rows a.v303-card-link{display:inline-flex;align-items:center;justify-content:center;
min-height:42px;background:#2466a3;border-radius:9px;padding:10px 14px;
color:white!important;text-decoration:none!important;font-weight:650}
#rows a.v303-card-link:hover{background:#154a7f}
@media(max-width:650px){
#filters{display:grid!important;grid-template-columns:minmax(0,1fr) minmax(0,1fr);
gap:9px!important;min-width:0!important}
#filters input,#filters select,#filters details,#filters>button,
#v257-inn-search{width:100%!important;min-width:0!important;max-width:100%!important}
#filters #query,#filters #inn,#filters #kind,#filters #v257-violation-type,
#filters details,#filters #min,#filters #max{grid-column:span 2}
#filters button{grid-column:span 1}
#filters #v257-inn-search{grid-column:span 2}
#filters #reset,#filters button[type="submit"]{min-height:44px!important;padding:9px 6px!important}
#filters #v257-violation-type{font-size:14px;min-width:0!important}
}
</style>'''

JS = r'''<script id="v303-mobile-fixes-script">
(function(){
 "use strict";
 if(window.__v303Fixes)return; window.__v303Fixes=true;
 const $=id=>document.getElementById(id);
 const reg=/^[0-9]{11,20}$/;
 let observer=null;
 function clean(){
  const status=$('v300-eis-status');if(status)status.remove();
  const badge=$('v302-connect');if(badge)badge.remove();
  if(location.pathname==='/'){
    $('v302-search-panel')?.remove();
    $('manual')?.remove();
    $('customer-progress-v233')?.remove();
  }
  const rows=$('rows');if(rows){
    rows.querySelectorAll('button[data-open]').forEach(b=>{
      const n=(b.getAttribute('data-open')||'').trim();
      if(!reg.test(n))return;
      const a=document.createElement('a');
      a.href='/check-new/'+encodeURIComponent(n);
      a.className='v303-card-link';a.textContent='Карточка проверки';
      a.dataset.reg=n;
      b.replaceWith(a);
    });
  }
 }
 function enableReset(){
  const b=$('reset'),form=$('filters');if(!b||!form||b.dataset.v303Fixed)return;
  b.dataset.v303Fixed='1';
  b.addEventListener('click',e=>{
    e.preventDefault();
    e.stopImmediatePropagation();
    form.querySelectorAll('input,select').forEach(input=>{
      if(input.type==='checkbox'||input.type==='radio'){input.checked=false}
      else if(input.tagName==='SELECT'){input.selectedIndex=0}
      else{input.value=''}
    });
    const v=$('v257-violation-type');if(v)v.value='';
    const f=$('forms');if(f)f.querySelectorAll('input').forEach(x=>{x.checked=false});
    const kind=$('kind');if(kind)kind.value='';
    form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  },true);
 }
 function fix(){
  clean();enableReset();
  const types=$('v257-violation-type');
  if(types){
    // Legacy clients may have appended duplicate options. Keep one per
    // normalized title; the server also groups codes under the same title.
    const seen=new Set();
    types.querySelectorAll('option').forEach((o,i)=>{
      if(i===0)return;
      const text=(o.textContent||'').replace(/\\s*\\([0-9]+\\)\\s*$/,'').trim().toLocaleLowerCase('ru');
      if(seen.has(text))o.remove();else seen.add(text);
    });
  }
 }
 document.addEventListener('click',e=>{
    const el=e.target.closest('a.v303-card-link,button[data-open]');
    if(!el)return;
    const target=(el.dataset?.reg||el.dataset?.open||'').trim();
    if(!reg.test(target))return;
    // Ensure the click ALWAYS opens a full audit page. Legacy bubbling
    // handlers sometimes conflict with the modal on mobile devices.
    e.preventDefault(); e.stopImmediatePropagation();
    location.assign('/check-new/'+encodeURIComponent(target));
 },true);
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>{
   fix();
   observer=new MutationObserver(fix);
   observer.observe(document.body,{childList:true,subtree:true});
 });
 else{
   fix();
   observer=new MutationObserver(fix);
   observer.observe(document.body,{childList:true,subtree:true});
 }
})();
</script>'''

def install(ns):
    app=ns['app']
    class SimplifiedDashboard(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if (request.method!='GET' or response.status_code!=200 or
                    'text/html' not in response.headers.get('content-type','') or
                    response.headers.get('content-encoding')):return response
            if request.url.path not in ('/','/manual-purchases') and not request.url.path.startswith('/check-new/'):
                return response
            try:
                raw=b''.join([x async for x in response.body_iterator])
                html=raw.decode('utf8')
            except UnicodeError:
                return response
            if 'id="v303-mobile-fixes"' not in html:
                html=html.replace('</head>',CSS+'</head>',1) if '</head>' in html else CSS+html
            if 'id="v303-mobile-fixes-script"' not in html:
                html=html.replace('</body>',JS+'</body>',1) if '</body>' in html else html+JS
            headers={k:v for k,v in response.headers.items() if k.lower() not in
                ('content-length','transfer-encoding','content-encoding')}
            headers['Cache-Control']='no-store'
            return HTMLResponse(html,status_code=response.status_code,headers=headers)
    app.add_middleware(SimplifiedDashboard)
    print('V303 MOBILE_UI_READY clean_bridge=True reset=True card_navigation=True',flush=True)
