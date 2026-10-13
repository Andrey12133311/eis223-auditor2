"""Remove risk *presentation* from EIS 223 dashboards and show honest 5-min collector status.

Historical rows stay unchanged. Only documented legal violations are emphasized.
"""
from __future__ import annotations
import re
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

STYLE = r'''<style id="v300-no-risk">
.stat:has(#risks), #risks, .risk, [data-risk], option[value="risks"] {display:none!important}
.violation, #violations {font-weight:700}
#v300-eis-status {margin:12px 0;padding:12px 15px;border:1px solid #d4ded5;
 border-radius:8px;background:transparent;font-size:14px;line-height:1.5}
#v300-eis-status a{color:inherit;text-decoration:underline}
</style>'''

SCRIPT = r'''<script id="v300-eis-status-script">
(function(){
  let scheduled=false;
  function cleanRisks(){
    try{
      document.getElementById('risks')?.closest('.stat')?.remove();
      document.querySelectorAll('option[value="risks"],.risk,[data-risk]').forEach(el=>el.remove());
      document.querySelectorAll('button,[role="tab"],h1,h2,h3,h4,summary').forEach(el=>{
         const t=(el.textContent||'').replace(/\s+/g,' ').trim();
         if(t==='Нарушения и риски')el.textContent='Нарушения';
      });
      document.querySelectorAll('tr').forEach(tr=>{
        // A risk status alone is not a proven procurement violation.
        const cells=tr.querySelectorAll('td,th');
        if([...cells].some(c=>(c.textContent||'').trim()==='Риск'))
          tr.remove();
      });
    }catch(e){}
  }
  function sync(){
    scheduled=false;cleanRisks();
  }
  function schedule(){
    if(scheduled)return;
    scheduled=true;
    requestAnimationFrame(sync);
  }
  function setup(){
    cleanRisks();
    const main=document.querySelector('main')||document.body;
    if(!document.getElementById('v300-eis-status')){
      const div=document.createElement('div');
      div.id='v300-eis-status';
      div.textContent='ЕИС: проверяю состояние пятиминутного загрузчика...';
      if(main.firstElementChild)main.insertBefore(div,main.firstElementChild);
      else main.append(div);
    }
    async function update(){
      const el=document.getElementById('v300-eis-status');
      if(!el)return;
      try{
        const r=await fetch('/api/v299/eis223/monitor-status',{cache:'no-store'});
        if(!r.ok)throw Error('status HTTP '+r.status);
        const s=await r.json();
        const when=s.last_scan?new Date(s.last_scan).toLocaleString('ru-RU'):'нет запусков';
        el.textContent=s.browser_collector_active?
          'ЕИС: загрузчик активен. Проверка новых закупок примерно каждые 5 минут; последний цикл: '+when+
          '. Новых закупок в цикле: '+(s.last_new_purchases||0)+
          ', оригиналов: '+(s.last_originals_uploaded||0)+'.':
          'ЕИС: пятиминутный загрузчик неактивен. Чтобы новые закупки поступали автоматически, загрузчик должен быть запущен во вкладке zakupki.gov.ru. Последний цикл: '+when+'.';
      }catch(e){el.textContent='ЕИС: пока нет данных о пятиминутном загрузчике.';}
    }
    update();
    setInterval(()=>{if(!document.hidden)update()},60000);
    new MutationObserver(schedule).observe(main,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup,{once:true});
  else setup();
})();
</script>'''

def install(ns):
    app=ns["app"]
    class ViolationOnlyPresentation(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!='GET' or response.status_code!=200:
                return response
            ctype=response.headers.get('content-type','').lower()
            if 'text/html' not in ctype or response.headers.get('content-encoding'):
                return response
            path=request.url.path
            # Only the main dashboard and procurement cards; leave external
            # official EIS/third-party pages and generated document bytes alone.
            if path!='/' and not path.startswith(('/check-new/','/purchase/','/documents')):
                return response
            raw=b''.join([x async for x in response.body_iterator])
            try:
                html=raw.decode('utf-8')
            except UnicodeError:
                return HTMLResponse(raw.decode('utf-8',errors='replace'),status_code=response.status_code)
            html=re.sub(r'<div\s+class="stat">\s*<small>\s*Риски\s*</small>\s*<b\s+id="risks">[^<]*</b>\\s*</div>',
                        '',html,flags=re.I)
            html=re.sub(r'<option\s+value="risks">[^<]*</option>','',html,flags=re.I)
            html=html.replace('Нарушения и риски','Нарушения')
            if 'id="v300-no-risk"' not in html:
                html=html.replace('</head>',STYLE+'</head>',1) if '</head>' in html else STYLE+html
            if 'id="v300-eis-status-script"' not in html:
                html=html.replace('</body>',SCRIPT+'</body>',1) if '</body>' in html else html+SCRIPT
            headers={k:v for k,v in response.headers.items()
                     if k.lower() not in ('content-length','transfer-encoding','content-encoding')}
            headers['Cache-Control']='no-store'
            return HTMLResponse(content=html,status_code=200,headers=headers)

    app.add_middleware(ViolationOnlyPresentation)
    print('V300 VIOLATIONS_UI_ACTIVE risk_indicator_hidden=True historical_risk_data_preserved=True',flush=True)
