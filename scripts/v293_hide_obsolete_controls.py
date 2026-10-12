"""Hide two obsolete dashboard controls without touching data or APIs."""
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

SCRIPT = """<script id="v293-hide-obsolete-controls">
(function(){
 const targets=new Set(['Самостоятельная проверка документов','Автоматические закупки']);
 function hide(){
  document.querySelectorAll('button,a,[role="button"]').forEach(function(el){
   if(targets.has((el.textContent||'').replace(/\\s+/g,' ').trim())){
    el.style.setProperty('display','none','important');
    el.setAttribute('aria-hidden','true');
    el.tabIndex=-1;
   }
  });
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',hide);
 else hide();
 new MutationObserver(hide).observe(document.documentElement,{childList:true,subtree:true});
})();
</script>"""

def install(ns):
 app=ns['app']
 class HideObsoleteControls(BaseHTTPMiddleware):
  async def dispatch(self,request,call_next):
   response=await call_next(request)
   if request.method!='GET' or response.status_code!=200:
    return response
   ctype=response.headers.get('content-type','').lower()
   if 'text/html' not in ctype or response.headers.get('content-encoding'):
    return response
   body=b''.join([part async for part in response.body_iterator])
   try:
    html=body.decode('utf-8')
    if 'id="v293-hide-obsolete-controls"' not in html:
     if '</body>' in html:html=html.replace('</body>',SCRIPT+'</body>',1)
     else:html+=SCRIPT
    body=html.encode('utf-8')
   except UnicodeError:
    pass
   headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','transfer-encoding')}
   return Response(content=body,status_code=response.status_code,headers=headers,media_type='text/html')
 app.add_middleware(HideObsoleteControls)
