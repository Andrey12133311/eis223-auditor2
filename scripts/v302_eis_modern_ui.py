"""Modern EIS-inspired responsive presentation and priority search.

This is an independent analytical service, not the official EIS website.
"""
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

STYLE='''<style id="v302-modern-css">
:root{--eis-blue:#225b99;--eis-navy:#14395f;--eis-edge:#d9e5f1;--eis-bg:#f2f6fb}
html,body{background:var(--eis-bg)!important;color:#223950!important;
font-family:"Segoe UI",Inter,system-ui,-apple-system,sans-serif!important}
body{margin:0!important}
body>header,header:first-of-type{background:linear-gradient(110deg,#13395f,#2366a0)!important;
padding:20px clamp(15px,3vw,45px)!important;color:#fff!important;
box-shadow:0 4px 19px #142e4b26}
header h1{font-weight:750!important;color:white!important;
font-size:clamp(20px,2.4vw,29px)!important;margin:0 0 8px}
header nav{display:flex;flex-wrap:wrap;gap:14px}
header a,header nav a{color:#e8f4ff!important;text-decoration:none!important}
header a:hover{text-decoration:underline!important}
main{box-sizing:border-box;width:min(1550px,97%)!important;max-width:1550px!important;
margin:24px auto 55px!important;padding:0 9px!important}
section.card,.card,.stat,details,.filters,.detail{
background:white!important;border:1px solid var(--eis-edge)!important;
border-radius:13px!important;box-shadow:0 4px 19px #153d5c0b!important}
.card{padding:clamp(14px,2vw,25px)!important}
.stats{display:grid!important;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
gap:13px!important}
.stat{padding:17px 19px!important}
.stat small{font-size:12px!important;color:#637c91!important;letter-spacing:.03em}
.stat b{font-size:clamp(23px,2.2vw,32px)!important;color:#17466f!important}
.stat:has(#risks),#risks,.risk,option[value=risks],[data-risk]{display:none!important}
h2,h3,h4{color:#17456f!important}
input,select,textarea{box-sizing:border-box;min-height:41px;border-radius:9px!important;
border:1px solid #c8d9e9!important;background:white!important;color:#1c3855!important;
padding:10px 12px!important}
input:focus,select:focus,textarea:focus{outline:3px solid #d0e7fb!important;
border-color:#357fb9!important}
button,input[type=submit],.btn{border-radius:9px!important;background:#2466a3!important;
border:1px solid #2466a3!important;color:white!important;font-weight:650!important;
min-height:42px;cursor:pointer;padding:10px 17px!important}
button:hover:not(:disabled){background:#164d82!important}
button:disabled{opacity:.55;cursor:wait}
a{color:#2569a7}
table{width:100%;background:white!important;border:1px solid var(--eis-edge);
border-radius:12px;border-collapse:separate!important;border-spacing:0!important;
font-size:13.5px}
th{background:#edf3fa!important;color:#315879!important;padding:12px!important}
td{border-top:1px solid #eef2f7!important;padding:12px!important;vertical-align:top}
tbody tr:hover{background:#f6faff}
#v257-tabs a{border-radius:9px!important;background:white!important;color:#21537d!important;
border-color:#d7e2ed!important}
#v300-eis-status{border-radius:10px!important;background:#edf6ff!important;
border-color:#d9e8f5!important;color:#235781!important;font-size:13px!important}
#v302-search-panel{background:#fff;border:1px solid #d7e4f1;border-radius:16px;
overflow:hidden;box-shadow:0 9px 30px #194c7810;margin:0 0 23px}
#v302-search-panel *{box-sizing:border-box}
.v302-header{padding:18px 25px;background:linear-gradient(100deg,#e9f4ff,#f8fbff);
border-bottom:1px solid #dae7f3;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.v302-emblem{width:45px;height:45px;flex:none;border-radius:12px;background:#1d5893;
color:white;display:grid;place-items:center;font-weight:850}
.v302-title{flex:1;min-width:210px}
.v302-title strong{font-size:19px;color:#163e66}
.v302-title small{display:block;color:#668099;margin-top:5px;font-size:12px}
.v302-connect{font-size:12px;font-weight:700;border:1px solid #d9e4ee;background:white;
border-radius:20px;padding:8px 12px;color:#476884}
.v302-connect.online{background:#ecf8ef;color:#2d7447;border-color:#abd2b5}
.v302-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;padding:20px 25px}
.v302-box{background:#fbfdff;border:1px solid #e0e9f3;padding:19px;border-radius:12px}
.v302-box label{display:block;font-weight:750;font-size:14px;color:#234b72;margin-bottom:7px}
.v302-box p{font-size:12px;color:#6b7f93;margin:0 0 14px;min-height:36px}
.v302-inputs{display:flex;gap:9px}
.v302-inputs input{flex:1;min-width:0;height:44px}
.v302-inputs button{white-space:nowrap;flex:none}
#v302-search-result{display:none;padding:16px 21px;margin:0 25px 18px;
background:#eff7ff;border:1px solid #cbdff3;border-radius:10px;
font-size:13px;line-height:1.65;color:#274f73}
#v302-search-result.error{background:#fff5f5;border-color:#ecc2c2;color:#a22b31}
#v302-search-result.ok{background:#f1faf3;border-color:#badcc3;color:#24603d}
.v302-links{padding:12px 25px;background:#fafcff;border-top:1px solid #e9eef4;
display:flex;gap:17px;flex-wrap:wrap;color:#708299;font-size:12px}
.v302-links a{font-weight:650;color:#245f99}
.v302-results{list-style:none;margin:10px 0;padding:0;max-height:260px;overflow:auto}
.v302-results li{display:flex;gap:12px;justify-content:space-between;
padding:9px;border:1px solid #dce8f3;background:#fff;margin-bottom:5px;
border-radius:7px;align-items:center}
.v302-results li small{color:#647b8f}
@media(max-width:900px){
.v302-grid{grid-template-columns:1fr;padding:16px}
.v302-header{padding:17px}
.v302-links{padding:13px 17px}
#v302-search-result{margin:0 16px 16px}
main{width:100%!important;padding:0 12px!important}
table{display:block;overflow:auto}
.v302-inputs{flex-wrap:wrap}.v302-inputs button{flex:1}
}
</style>'''

SCRIPT='''<script id="v302-modern-js">
(function(){
 if(window.__v302Active)return;window.__v302Active=true;
 const by=id=>document.getElementById(id);
 const page=['/','/manual-purchases','/manual-documents','/documents'].includes(location.pathname);
 const escape=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const digits=s=>String(s||'').replace(/[^0-9]/g,'');
 async function req(u,opt){
  const r=await fetch(u,{cache:'no-store',...(opt||{})});
  let v={};try{v=await r.json()}catch(e){}
  if(!r.ok)throw Error(v.detail||('HTTP '+r.status));
  return v;
 }
 function notice(content,kind){
  const node=by('v302-search-result');if(!node)return;
  node.style.display='block';node.className=kind||'';node.innerHTML=content;
 }
 function money(p){
  if(p.price_num!==null&&p.price_num!==undefined&&p.price_num!==''){
   const n=Number(p.price_num);
   if(Number.isFinite(n)&&n>=0)
    return new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(n)+' ₽';
  }
  return String(p.price||'не установлена');
 }
 function rows(items){
  if(!items?.length)return '<p>В базе пока нет проверенных документов по запросу. Поиск не считается юридической проверкой.</p>';
  return '<ul class="v302-results">'+items.slice(0,35).map(p=>
    '<li><span><a href="/check-new/'+encodeURIComponent(p.reg_number)+'">'+escape(p.reg_number)+'</a>'+
    '<small> · '+escape(p.title||p.customer||'Документы ЕИС')+'</small>'+
    '<small> · Стоимость закупки: '+escape(window.eis223PriceText?window.eis223PriceText(p):money(p))+'</small></span>'+
    '<span>Обработано документов: '+Number(p.documents_checked||0)+' · Нарушений: <b>'+
    Number(p.violations||0)+'</b></span></li>').join('')+'</ul>';
 }
 async function bridge(){
  try{
   const s=await req('/api/v301/bridge/status');const el=by('v302-connect');
   if(!el)return;
   el.classList.toggle('online',!!s.active);
   el.textContent=s.active?'● ЕИС подключён · в очереди: '+s.queued:
     '○ Вкладка ЕИС не подключена · ожидают: '+s.queued;
  }catch(e){}
 }
 let seq=0;
 async function submit(kind,value){
  const i=++seq;value=digits(value);
  if(kind==='reg'&&!/^3[0-9]{10}$/.test(value))
    return notice('Введите номер закупки 223-ФЗ из 11 цифр.','error');
  if(kind==='inn'&&!/^(?:[0-9]{10}|[0-9]{12})$/.test(value))
    return notice('Введите ИНН заказчика из 10 или 12 цифр.','error');
  const button=by(kind==='reg'?'v302-reg-submit':'v302-inn-submit');
  if(button)button.disabled=true;
  notice('Запускаю поиск. Сначала проверяю сохранённые оригиналы ЕИС…');
  try{
    const r=await req('/api/v301/search',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({kind,value})});
    if(kind==='reg')window.dispatchEvent(new Event('eis223:manual-input'));
    if(i!==seq)return;
    notice('<strong>Запрос '+escape(value)+' поставлен в приоритетную очередь.</strong> '+
      (r.bridge?.active?'Мост ЕИС активен, загрузка начнётся в ближайшие секунды.':
       'Чтобы получить новые документы, запустите браузерный мост во вкладке zakupki.gov.ru.')+
      (kind==='reg'?'<p><a href="/check-new/'+encodeURIComponent(value)+
       '">Открыть проверку →</a></p>':'')+
      '<p id="v302-task-status">Ожидание ЕИС…</p><div id="v302-task-items">'+rows(r.existing)+'</div>',
      r.bridge?.active?'ok':'');
    bridge();
    for(let k=0;k<85;k++){
      if(seq!==i)return;
      await new Promise(ok=>setTimeout(ok,k===0?1700:5000));
      try{
        const j=await req('/api/v301/job/'+r.id);
        const state=j.job?.status||'queued';
        const labels={queued:'В очереди',leased:'ЕИС получает документы',
          done:'Поиск ЕИС завершён',partial:'Загружено частично',
          error:'Ошибка получения',deferred:'Ожидает повтор'};
        const el=by('v302-task-status');
        const audit=j.audit;
        let message=labels[state]||state;
        if(kind==='reg'){
          if(audit?.audit_complete)
            message='Проверено: все полученные оригиналы прошли юридическую проверку';
          else if(audit?.audit_status==='auditing')
            message='Юридическая проверка оригиналов выполняется';
          else if(audit?.audit_status==='waiting')
            message='Ожидаются оригиналы ЕИС';
          else if(state==='done')
            message='Поиск ЕИС завершён; юридическая проверка НЕ подтверждена';
          if(audit?.originals_expected>0)
            message+=' · получено оригиналов: '+audit.originals_received+'/'+audit.originals_expected;
        }else if(state==='done'){
          message='Поиск закупок по ИНН завершён. Документы ещё проверяются в отдельной очереди';
        }
        if(el)el.textContent=message+
          (state==='error'&&j.job?.detail?' — '+j.job.detail:'')+
          (kind==='inn'?' · закупок с данными в базе: '+j.total_existing:'');
        const list=by('v302-task-items');if(list)list.innerHTML=rows(j.existing);
        if(state==='error'||kind==='reg'&&audit?.audit_complete)break;
        if(state==='done'&&kind==='inn')break;
        if(state==='done'&&kind==='reg'&&k>=5)break;
        if(state==='partial'&&k>=5)break;
      }catch(e){if(k>3)break}
    }
  }catch(e){if(seq===i)notice('Не удалось выполнить поиск: '+escape(e.message),'error')}
  finally{if(button)button.disabled=false}
 }
 function initialize(){
  const host=document.querySelector('main')||document.body;
  if(page&&location.pathname!=='/'&&!by('v302-search-panel')){
    const node=document.createElement('section');node.id='v302-search-panel';
    node.setAttribute('aria-label','Поиск закупок по 223-ФЗ');
    node.innerHTML=[
      '<div class="v302-header"><span class="v302-emblem">223</span>',
      '<span class="v302-title"><strong>Контроль закупок по 223-ФЗ</strong>',
      '<small>Независимый аналитический сервис · ЕИС · документальная проверка нарушений</small></span>',
      '<span id="v302-connect" class="v302-connect">Проверка подключения ЕИС…</span></div>',
      '<div class="v302-grid">',
      '<form id="v302-reg-form" class="v302-box"><label for="v302-reg">По номеру закупки ЕИС</label>',
      '<p>Загрузка извещения и всех доступных оригиналов, затем юридическая проверка.</p>',
      '<div class="v302-inputs"><input id="v302-reg" inputmode="numeric" maxlength="11" ',
      'placeholder="Например: 32616440427" required>',
      '<button type="submit" id="v302-reg-submit">Проверить</button></div></form>',
      '<form id="v302-inn-form" class="v302-box"><label for="v302-inn">По ИНН заказчика</label>',
      '<p>Поиск всех закупок заказчика в ЕИС и приоритетное получение документов.</p>',
      '<div class="v302-inputs"><input id="v302-inn" inputmode="numeric" maxlength="12" ',
      'placeholder="ИНН из 10 или 12 цифр" required>',
      '<button type="submit" id="v302-inn-submit">Найти закупки</button></div></form></div>',
      '<div id="v302-search-result" role="status" aria-live="polite"></div>',
      '<div class="v302-links"><span>Официальный источник: только ЕИС, без ЭТП и агрегаторов</span>',
      '<a href="https://zakupki.gov.ru/epz/main/public/home.html" target="_blank" rel="noopener noreferrer">Открыть ЕИС ↗</a>',
      '<a href="/api/v299/eis223/collector.js" target="_blank" rel="noopener noreferrer">Открыть код подключения</a>',
      '<a href="#" id="v302-copy-bridge">Скопировать код</a></div>'
    ].join('');
    host.insertBefore(node,host.firstChild);
    by('v302-copy-bridge').addEventListener('click',async e=>{
      e.preventDefault();
      try{
        const r=await fetch('/api/v299/eis223/collector.js',{cache:'no-store'});
        if(!r.ok)throw Error('HTTP '+r.status);
        const code=await r.text();
        await navigator.clipboard.writeText(code);
        notice('Код скопирован. Откройте <a target="_blank" rel="noopener noreferrer" href="https://zakupki.gov.ru/epz/main/public/home.html">ЕИС</a>, нажмите F12 → Консоль, вставьте код и введите ключ Railway. Оставьте вкладку открытой.','ok');
      }catch(err){
        notice('Не удалось скопировать автоматически. Откройте ссылку «Открыть код подключения» и скопируйте его вручную.','error');
      }
    });
    by('v302-reg-form').addEventListener('submit',e=>{e.preventDefault();submit('reg',by('v302-reg').value)});
    by('v302-inn-form').addEventListener('submit',e=>{e.preventDefault();submit('inn',by('v302-inn').value)});
    const inn=by('inn');if(inn&&/^[0-9]{10}([0-9]{2})?$/.test(inn.value))by('v302-inn').value=inn.value;
    bridge();
    setInterval(()=>{if(!document.hidden)bridge()},12000);
  }
  // Capture phase overrides the disabled legacy manual queue, which would
  // otherwise call the historic v204 API and report a misleading result.
  const old=by('start');
  if(old&&!old.dataset.v302Hook){
    old.dataset.v302Hook='yes';
    old.addEventListener('submit',e=>{
      e.preventDefault();e.stopImmediatePropagation();
      submit('reg',old.querySelector('#reg')?.value||'');
    },true);
  }
  const b=by('v257-inn-search');
  if(b&&!b.dataset.v302Hook){
    b.dataset.v302Hook='yes';
    b.addEventListener('click',e=>{
      if(!by('inn'))return;
      e.preventDefault();e.stopImmediatePropagation();submit('inn',by('inn').value);
    },true);
  }
 }
 if(document.readyState==='loading')
   document.addEventListener('DOMContentLoaded',initialize,{once:true});
 else initialize();
})();
</script>'''

def install(ns):
    app=ns['app']
    class ModernLayout(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!='GET' or response.status_code!=200:
                return response
            if 'text/html' not in response.headers.get('content-type','').lower():
                return response
            path=request.url.path
            if path not in ('/','/manual-purchases','/manual-documents','/documents') and not path.startswith(('/check-new/','/purchase/')):
                return response
            if response.headers.get('content-encoding'):return response
            data=b''.join([part async for part in response.body_iterator])
            try:html=data.decode('utf8')
            except UnicodeError:return response
            # Remove the stale risk metric from the legacy manual-check
            # JavaScript template as well as from the dashboard widgets.
            html=html.replace("+' · рисков: '+Number(p.risks||0)","")
            if 'id="v302-modern-css"' not in html:
                html=html.replace('</head>',STYLE+'</head>',1) if '</head>' in html else STYLE+html
            if 'id="v302-modern-js"' not in html:
                html=html.replace('</body>',SCRIPT+'</body>',1) if '</body>' in html else html+SCRIPT
            headers={k:v for k,v in response.headers.items()
                     if k.lower() not in ('content-length','transfer-encoding','content-encoding')}
            headers['Cache-Control']='no-store'
            return HTMLResponse(html,status_code=200,headers=headers)
    app.add_middleware(ModernLayout)
    print('V302 MODERN_EIS_LAYOUT_READY search_reg=True search_inn=True',flush=True)
