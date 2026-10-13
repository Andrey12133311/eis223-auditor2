"""Provide only ODT memo download from the procurement check card.

Do not surface an "established violation" if audit has no documented findings.
"""
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

SCRIPT=r'''<script id="v305-template-memo-link">
(function(){
 'use strict';
 const path=location.pathname;
 const reg=(path.match(/^\/check-new\/(3[0-9]{10})(?:\/|$)/)||[])[1];
 if(!reg)return;
 const href='/api/v304/memo/'+reg+'.odt';
 function panel(){
  let el=document.getElementById('v305-memo-action');
  if(el)return el;
  const target=document.querySelector('main')||document.body;
  el=document.createElement('section');el.id='v305-memo-action';
  el.style.cssText='padding:14px 18px;margin:14px auto;max-width:1140px;'
    +'background:#fff;border:1px solid #d2e2f0;border-radius:10px;'
    +'box-shadow:0 4px 13px #1f57900a';
  target.prepend(el);
  return el;
 }
 async function status(){
  try{
   const r=await fetch('/api/v267/findings/'+reg,{cache:'no-store'});
   if(!r.ok)return;
   const data=await r.json();
   const area=panel();
   if(!data.total){
     area.innerHTML='<strong>Служебная записка</strong>'
      +'<p>Не формируется: документально подтверждённые нарушения не выявлены.</p>';
     return;
   }
   const tpl=await fetch('/api/v304/memo-template/status',{cache:'no-store'}).then(x=>x.json());
   area.innerHTML='<strong>Служебная записка по образцу ФАС</strong>'
    +'<p>Подтверждённых нарушений: '+Number(data.total)
    +'. Текст и оформление берутся из приложенного образца. Формат — только ODT.</p>';
   const btn=document.createElement('a');
   btn.textContent=tpl.template_installed?'Скачать служебную записку ODT':'Установить ODT-шаблон один раз';
   btn.href=tpl.template_installed?href:'/api/v304/memo-template/upload';
   btn.style.cssText='display:inline-flex;align-items:center;padding:11px 16px;'
    +'color:#fff;background:#1d5c95;border-radius:9px;font-weight:650;'
    +'text-decoration:none';
   area.append(btn);
  }catch(e){console.warn('V305 memo status',e)}
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',status);
 else status();
})();
</script>'''

def install(ns):
 app=ns["app"]
 class MemoOnCard(BaseHTTPMiddleware):
  async def dispatch(self,request,call_next):
   response=await call_next(request)
   if request.method!="GET" or response.status_code!=200 or \
      not request.url.path.startswith("/check-new/") or \
      "text/html" not in response.headers.get("content-type",""):
    return response
   raw=b"".join([x async for x in response.body_iterator])
   try:html=raw.decode("utf-8")
   except UnicodeError:return response
   if 'id="v305-template-memo-link"' not in html:
    html=html.replace("</body>",SCRIPT+"</body>",1) if "</body>" in html else html+SCRIPT
   headers={k:v for k,v in response.headers.items()
            if k.lower() not in ("content-length","content-encoding","transfer-encoding")}
   return HTMLResponse(html,status_code=200,headers=headers)
 app.add_middleware(MemoOnCard)
 print("V305 MEMO_CARD_ONLY_ODT_READY",flush=True)
