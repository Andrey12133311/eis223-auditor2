"""Fix main procurement filtering and native card navigation on mobile/desktop.

No old EIS queue calls; no changes to documents or persistent findings.
"""
from __future__ import annotations
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

CSS=r'''<style id="v303-main-clean">
#v300-eis-status{display:none!important}
body:has(#filters) #v302-search-panel,
body:has(#filters) #customer-progress-v233,
body:has(#filters) #runStatus,
body:has(#filters) form#manual,
body:has(#filters) #v302-connect{display:none!important}
body:has(#filters) .status#runStatus{display:none!important}
#filters{display:flex!important;gap:12px!important;flex-wrap:wrap!important;
  align-items:center!important;width:100%!important;max-width:100%!important;
  box-sizing:border-box!important}
#filters > input,#filters > select,#filters > details,
#filters > button{max-width:100%;min-width:0}
#filters #v257-violation-type{width:min(100%,430px)!important;flex:1 1 290px!important}
#filters #kind{min-width:170px;max-width:100%;flex:1 1 155px}
#filters #reset{background:#fff!important;color:#205781!important;border:1px solid #b6cde2!important}
#filters #reset:hover{background:#eaf4fd!important}
#filters [id="forms"]{max-height:280px;overflow:auto}
#rows button[data-open]{white-space:normal;min-height:40px}
@media(max-width:720px){
 #filters{display:grid!important;grid-template-columns:1fr 1fr!important;gap:10px!important}
 #filters > input,#filters > select,#filters > details{grid-column:span 2!important;
  width:100%!important;box-sizing:border-box!important}
 #filters > button{width:100%!important}
 #filters #v257-violation-type{max-width:100%!important;width:100%!important}
 #filters details summary{padding:10px 12px}
}
</style>'''

SCRIPT=r'''<script id="v303-functional-filters">
(function(){
'use strict';
if(window.__v303Functional)return;window.__v303Functional=true;
const $=id=>document.getElementById(id);
const path=location.pathname;
function pageMain(){return path==='/'}
function numberFrom(elem){
 if(!elem)return '';
 const candidates=[
  elem.getAttribute('data-open')||'',elem.getAttribute('data-reg')||'',
  elem.getAttribute('href')||'',
  elem.closest('[data-reg-number]')?.getAttribute('data-reg-number')||'',
  elem.closest('tr')?.querySelector('td:first-child')?.textContent||'',
  elem.closest('tr')?.textContent||'',
  elem.closest('[data-reg]')?.getAttribute('data-reg')||'',
  elem.closest('.purchase,.purchase-card,.card,article,li')?.textContent||''
 ];
 for(const val of candidates){
  const m=String(val).match(/(?:^|[^0-9])(3[0-9]{10})(?=$|[^0-9])/);
  if(m)return m[1];
 }
 return '';
}
// Capture at window: obsolete V234 document-level listener was directing
// "Карточка проверки" to /documents where the card is not shown.
window.addEventListener('click',function(event){
 const element=event.target?.closest?.('button,a,[role="button"]');
 if(!element)return;
 const text=(element.textContent||'').replace(/\s+/g,' ').trim().toLowerCase();
 const isCard=element.matches('[data-open]')||
   text==='карточка проверки'||text==='открыть проверку';
 if(!isCard)return;
 const reg=numberFrom(element);
 if(!reg){
  console.warn('V303: не удалось определить номер закупки для карточки',element);
  return;
 }
 event.preventDefault();event.stopPropagation();event.stopImmediatePropagation();
 location.assign('/check-new/'+encodeURIComponent(reg));
},true);
function reset(){
 if(!pageMain())return;
 const form=$('filters');
 if(!form)return;
 form.reset();
 for(const id of ['query','inn','min','max']){
  const el=$(id);if(el){el.value='';el.dispatchEvent(new Event('input',{bubbles:false}))}
 }
 const type=$('v257-violation-type');if(type)type.value='';
 const kind=$('kind');if(kind){kind.value='';if(kind.value!=='')kind.selectedIndex=0}
 form.querySelectorAll('input[type="checkbox"]').forEach(box=>box.checked=false);
 const forms=$('forms');
 if(forms)forms.querySelectorAll('input[type="checkbox"]').forEach(x=>x.checked=false);
 const cp=$('customer-progress-v233');if(cp)cp.textContent='';
 const notice=$('message');if(notice){notice.textContent='';notice.classList.remove('error')}
 const search=$('v302-inn');if(search)search.value='';
 // Trigger the existing dashboard load through its official submit handler,
 // not the old legacy search queue.
 form.dispatchEvent(new Event('submit',{cancelable:true,bubbles:false}));
}
window.addEventListener('click',function(e){
 if(!pageMain())return;
 if(!e.target?.closest?.('#reset'))return;
 e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();
 reset();
},true);
function removePriceColumn(){
 if(!pageMain())return;
 const rows=$('rows');
 if(!rows)return;
 const table=rows.closest('table')||rows.querySelector('table');
 if(!table)return;
 const headings=[...table.querySelectorAll('thead tr:first-child th')];
 for(const th of headings){if((th.textContent||'').trim().toLowerCase()==='проверка')th.textContent='Нарушения';}
 const indices=headings.map((th,i)=>/^(?:стоимость(?: закупки)?|нмцд|цена(?: договора| закупки)?|нмцк)$/i.test((th.textContent||'').replace(/\s+/g,' ').trim())?i:-1).filter(i=>i>=0);
 if(!indices.length)return;
 for(const tr of table.querySelectorAll('tr')){
  const cells=[...tr.children].filter(el=>el.matches('th,td'));
  for(const index of indices)if(cells[index])cells[index].style.setProperty('display','none','important');
 }
}
function canonicalize(){
 const select=$('v257-violation-type');
 if(!select)return;
 const seen=new Set();
 for(const item of [...select.options]){
  if(!item.value)continue;
  const title=item.textContent.replace(/\s*\(\d+\)\s*$/,'')
     .trim().toLocaleLowerCase('ru-RU').replace(/\s+/g,' ');
  if(seen.has(title))item.remove();else seen.add(title);
 }
 if(!pageMain())return;
 $('v300-eis-status')?.remove();
 $('v302-search-panel')?.remove();
 const legacy=$('manual');if(legacy)legacy.hidden=true;
}
function init(){
 canonicalize();removePriceColumn();
 let scheduled=false;
 const root=document.querySelector('main')||document.body;
 if(!root)return;
 new MutationObserver(()=>{
  if(!scheduled){scheduled=true;requestAnimationFrame(()=>{
    scheduled=false;canonicalize();removePriceColumn()
  })}
 }).observe(root,{childList:true,subtree:true});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
else init();
})();
</script>'''

def install(ns):
 app=ns['app']
 class FunctionalDashboard(BaseHTTPMiddleware):
  async def dispatch(self,request,call_next):
   response=await call_next(request)
   if request.method!='GET' or response.status_code!=200:
    return response
   if 'text/html' not in response.headers.get('content-type','').lower():
    return response
   path=request.url.path
   if path not in ('/','/manual-purchases') and not path.startswith('/check-new/'):
    return response
   if response.headers.get('content-encoding'):return response
   raw=b''.join([piece async for piece in response.body_iterator])
   try:html=raw.decode('utf-8')
   except UnicodeError:return response
   # Old V234 capture listener explicitly sent "Карточка проверки" to the
   # documents-only page. Fix that navigation at its SOURCE as well as with
   # the newer capture listener, so mobile links still work if JS order varies.
   html=html.replace(
       "window.location.href='/documents?reg='+encodeURIComponent(reg);",
       "window.location.href='/check-new/'+encodeURIComponent(reg);")
   if 'id="v303-main-clean"' not in html:
    html=html.replace('</head>',CSS+'</head>',1) if '</head>' in html else CSS+html
   if 'id="v303-functional-filters"' not in html:
    html=html.replace('</body>',SCRIPT+'</body>',1) if '</body>' in html else html+SCRIPT
   headers={k:v for k,v in response.headers.items()
            if k.lower() not in ('content-length','content-encoding','transfer-encoding')}
   headers['Cache-Control']='no-store'
   return HTMLResponse(html,status_code=200,headers=headers)
 app.add_middleware(FunctionalDashboard)
 print('V303 MAIN_FILTERS_ONLY card_route=/check-new reset_clears_all types_unique=True',flush=True)
