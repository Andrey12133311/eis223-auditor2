"""Standalone HTML for staged user accounts (not mounted in production)."""
from html import escape


def page(title, body):
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + escape(title) + """ · ЕИС Аудитор</title>
<style id="eis223-pages-modern">
:root{font-family:Inter,"Segoe UI",system-ui,-apple-system,Arial,sans-serif;
 color:#264053;background:#f4f7fa;--accent:#165b8a;--accent-dark:#104772;
 --line:#dbe6ee;--muted:#687e8e;--page:#fff;font-synthesis:none}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#f4f7fa;color:#263f50;
 font:14px/1.55 Inter,"Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
header{
 color:#fff;background:#174c76;border-bottom:3px solid #6bb4dc;
 font-size:16px;font-weight:800;letter-spacing:-.025em;
 padding:18px clamp(15px,5vw,65px)}
main{width:min(100%,1110px);margin:clamp(20px,3.2vw,34px) auto 60px;
 padding:0 clamp(11px,3vw,24px)}
.panel{background:#fff;border:1px solid var(--line);border-radius:12px;
 box-shadow:0 5px 22px #244e710b;padding:clamp(18px,3vw,33px);min-width:0}
h1{font-size:clamp(23px,2.8vw,31px);font-weight:780;letter-spacing:-.03em;
 color:#1e4b6d;line-height:1.2;margin:0 0 10px}
h2{color:#204e70;font-size:20px;letter-spacing:-.02em}
p{color:var(--muted);line-height:1.65;font-size:13px;margin:11px 0}
nav{display:flex;gap:10px;align-items:center;flex-wrap:wrap;
 margin:12px 0;padding:10px 0;border-bottom:1px solid var(--line)}
nav a{padding:7px 10px;border-radius:7px;border:1px solid #dbe6ee;
 background:#f4f8fb}
a{color:#185f91;font-weight:650;text-decoration:none}
a:hover{text-decoration:underline}
label{display:block;font-weight:690;margin-top:16px;color:#3c5e78}
input,select,textarea{display:block;max-width:100%;width:100%;min-height:43px;
 background:#fff;color:#273f53;border:1px solid #cad9e5;
 border-radius:8px;margin-top:7px;padding:10px 13px;font:inherit}
input:focus{outline:3px solid #d2e8f7;border-color:#438abb}
button{background:#175e92;color:#fff;border:1px solid #175e92;
 border-radius:8px;padding:10px 13px;min-height:40px;
 font:680 13px system-ui;cursor:pointer;margin:8px 5px 4px 0;
 transition:background .15s}
button:hover:not(:disabled){background:#0d466f}
button:disabled{opacity:.55;cursor:wait}
.message,[role=status]{font-size:12px;color:#52748a;min-height:20px}
table{width:100%;margin-top:17px;border:1px solid var(--line);
 border-collapse:separate;border-spacing:0;border-radius:10px;overflow:hidden;
 background:white;font-variant-numeric:tabular-nums}
th,td{padding:12px 13px;border-bottom:1px solid #e9f0f5;
 text-align:left;vertical-align:middle;line-height:1.5}
th{font-size:12px;color:#345c7c;background:#eaf2f8;font-weight:750}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#f2f8fc}
td button{margin:4px 5px 4px 0}
#rows td:last-child{min-width:180px}
#personal-procurements,#personal-findings{
 margin-top:17px;background:#f9fbfd;border:1px solid #dde9f1;
 border-radius:10px;padding:14px}
.eis-my-check{background:#fff!important;border:1px solid #dae6ef!important;
 border-radius:10px!important;box-shadow:0 3px 10px #143e6109}
.eis-my-check h2{color:#1d5279!important}
.eis-my-actions a{background:#176092!important;border-color:#176092!important;color:white!important}
.eis-my-actions a:hover{background:#0d496f!important}
.eis-my-actions button{color:#984347!important;border:1px solid #e7cdd1!important;background:#fff!important}
.eis-pill{background:#e7f3fb!important;color:#236a99!important}
:is(button,input,select,textarea,a):focus-visible{outline:3px solid #89bbdc;outline-offset:2px}
@media(max-width:720px){
 header{padding:15px 16px}
 main{margin:16px auto 32px;padding:0 11px}
 .panel{padding:17px 14px;border-radius:11px}
 nav{gap:7px}
 nav a{font-size:12px;padding:7px 9px}
 table{display:block;overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}
 th,td{padding:10px 11px}
}
@media(prefers-reduced-motion:reduce){button{transition:none}}
</style></head><body><header>ЕИС Аудитор · Личный кабинет</header>
<main><section class="panel">""" + body + """</section></main></body></html>"""


REGISTER = page("Регистрация", """
<h1>Создать аккаунт</h1>
<p>Регистрация свободная. После регистрации заявка поступит администратору. Подтверждение почты не требуется.</p>
<form id="registration">
<label>Электронная почта<input type="email" name="email" required autocomplete="email"></label>
<label>Пароль<input type="password" name="password" required minlength="12" autocomplete="new-password"></label>
<button type="submit">Отправить заявку</button></form>
<p class="message" id="message" role="status"></p><p><a href="/account/login">Уже зарегистрированы? Войти</a></p>
<script>
document.getElementById('registration').addEventListener('submit',async e=>{
 e.preventDefault();const f=e.currentTarget,b=f.querySelector('button'),m=document.getElementById('message');
 b.disabled=true;m.textContent='Отправляем заявку…';
 try{const r=await fetch('/auth/register',{method:'POST',credentials:'same-origin',
 headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(f)))});
 m.textContent=r.ok?'Заявка отправлена администратору. Дождитесь одобрения.':'Не удалось отправить заявку. Проверьте данные.';
 if(r.ok)f.reset();
 }catch{m.textContent='Ошибка соединения. Попробуйте позже.'}finally{b.disabled=false}
});
</script>""")

LOGIN = page("Вход", """
<h1>Войти в личный кабинет</h1>
<p>Доступ предоставляется после подтверждения администратором.</p>
<form id="login">
<label>Электронная почта<input type="email" name="email" required autocomplete="email"></label>
<label>Пароль<input type="password" name="password" required autocomplete="current-password"></label>
<button type="submit">Войти</button></form>
<p class="message" id="message" role="status"></p><p><a href="/account/register">Создать аккаунт</a></p>
<script>
document.getElementById('login').addEventListener('submit',async e=>{
 e.preventDefault();const f=e.currentTarget,m=document.getElementById('message'),b=f.querySelector('button');
 b.disabled=true;
 try{const r=await fetch('/auth/login',{method:'POST',credentials:'same-origin',
 headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(f)))});
 if(r.ok){location.assign('/account/home')}else{m.textContent='Неверные данные или аккаунт ещё не подтверждён.'}
 }catch{m.textContent='Ошибка соединения.'}finally{b.disabled=false}
});
</script>""")

HOME = page("Мои самостоятельные проверки", """
<style>
.manual-head{display:flex;flex-wrap:wrap;align-items:flex-start;
 justify-content:space-between;gap:10px;margin-bottom:16px}
.manual-head h1{margin-bottom:5px}
.manual-note{font-size:12px;color:#607b90;line-height:1.65;
 padding:11px 13px;border-left:3px solid #468ec2;background:#f2f7fb;border-radius:5px}
.manual-form{display:flex;gap:9px;align-items:end;flex-wrap:wrap;
 padding:16px 17px;margin:18px 0 20px;border:1px solid #d9e6f0;border-radius:10px;background:#f8fbfd}
.manual-form label{flex:1 1 220px;margin:0}
.manual-form input{margin-top:5px}
.manual-form button{margin:0 0 1px}
.manual-filter-area{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;
 padding:16px;background:#f7fafc;border:1px solid #dbe8f2;border-radius:10px;align-items:end}
.manual-filter-area label{margin:0;font-size:12px}.manual-only-confirmed{display:flex!important;align-items:center;gap:8px;padding:8px}
.manual-only-confirmed input{width:16px;height:16px;min-height:0;margin:0}
#manual-filters-reset{background:#fff;border:1px solid #cbdfea;color:#22618d}
.manual-summary{display:flex;align-items:center;justify-content:space-between;
 flex-wrap:wrap;gap:8px;margin:12px 0}
.manual-summary strong{color:#245875;font-size:15px}
.manual-chip{display:inline-flex;align-items:center;border-radius:100px;padding:5px 9px;
 font-size:11px;font-weight:750;border:1px solid #d4e4ef;background:#edf5fa;color:#356787}
.manual-chip.fail{background:#fff4f2;border-color:#efddd8;color:#ab544a}
.manual-chip.wait{background:#fef8e9;border-color:#f1e5c6;color:#946d24}
.manual-actions{display:flex;flex-wrap:wrap;gap:7px}
.manual-actions a{display:inline-flex;align-items:center;justify-content:center;
 padding:7px 11px;border-radius:8px;background:#eaf3fa;
 border:1px solid #d6e7f2;min-height:39px}
.manual-actions button{background:#fff;border:1px solid #e4d5d8;color:#98494b;
 margin:0;padding:7px 11px}
.manual-actions button:hover:not(:disabled){background:#fff3f3}
#manual-rows td:first-child{font-weight:750;color:#205580}
#manual-rows td small{display:block;color:#6b8394;font-weight:500;font-size:11px}
#manual-empty{background:#f6fafc;border:1px dashed #cadfec;border-radius:10px;
 padding:18px;text-align:center;color:#688095}
@media(max-width:720px){
 .manual-form{padding:13px;align-items:stretch}
 .manual-form button{flex:1}
 table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
 th,td{white-space:normal;min-width:100px}
 td:last-child{min-width:160px}
}
</style>
<p><a href="/">← На главную / В личный кабинет</a></p>
<div class="manual-head"><div>
<h1>Самостоятельные проверки</h1>
<p>Здесь находятся закупки, проверку которых вы запустили под своей учётной записью.</p>
</div></div>
<div class="manual-note">Сохранённый номер закупки сам по себе не считается проведённой проверкой.
Статус задания, количество реально проверенных документов и подтверждённых нарушений показаны отдельно.</div>
<form id="manual-add" class="manual-form">
<label>Номер закупки ЕИС
<input name="reg" aria-label="Номер закупки ЕИС" pattern="3[0-9]{10}" required
 maxlength="11" inputmode="numeric" autocomplete="off" placeholder="32616412317"></label>
<button type="submit">Запустить проверку</button>
</form>
<div class="manual-filter-area" aria-label="Фильтры самостоятельных проверок">
<label>Закупка / заказчик / ИНН<input type="search" id="manual-search" placeholder="Поиск"></label>
<label>Статус<select id="manual-status"><option value="">Все</option>
<option value="queued">В очереди</option><option value="leased">Загружается ЕИС</option>
<option value="done">Загрузка завершена</option><option value="partial">Частично</option>
<option value="deferred">Ожидает источник</option><option value="error">Ошибка</option></select></label>
<label>Дата с<input type="date" id="manual-date-start"></label>
<label>Дата по<input type="date" id="manual-date-end"></label>
<label>Вид нарушения<select id="manual-violation-type"><option value="">Все виды</option></select></label>
<label class="manual-only-confirmed"><input type="checkbox" id="manual-only-violations"> Есть нарушения</label>
<button type="button" id="manual-filters-reset">Сбросить фильтры</button></div>
<p id="manual-message" class="message" role="status" aria-live="polite"></p>
<div class="manual-summary"><strong id="manual-total">Загрузка проверок…</strong>
<button type="button" id="manual-refresh">Обновить данные</button></div>
<div id="manual-empty" hidden>Вы ещё не запускали самостоятельных проверок через личный кабинет.</div>
<table aria-label="История самостоятельных проверок" id="manual-table">
<thead><tr><th>Закупка и дата</th><th>Состояние</th><th>Документы</th>
<th>Нарушения</th><th>Действия</th></tr></thead>
<tbody id="manual-rows"></tbody></table>
<script>
(()=>{
const el=id=>document.getElementById(id);
const state={queued:'В очереди',leased:'Загружаются данные ЕИС',
 done:'Загрузка завершена',partial:'Частичная загрузка',
 deferred:'Ожидает источник',error:'Ошибка загрузки',unknown:'Статус уточняется'};
let loading=false;
async function refresh(){
 if(loading)return;loading=true;
 try{
 const response=await fetch('/me/manual-checks',{credentials:'same-origin',cache:'no-store'});
 if(response.status===401){location.replace('/account/login');return}
 if(!response.ok)throw Error('Не удалось загрузить проверки');
 const allData=await response.json();
 if(!Array.isArray(allData))throw Error('Некорректный ответ сервера');
 const select=el('manual-violation-type'),selected=select.value;
 const kinds=[...new Set(allData.flatMap(x=>x.violation_types||[]))].sort((a,b)=>a.localeCompare(b,'ru'));
 select.replaceChildren();
 const allOption=document.createElement('option');allOption.value='';allOption.textContent='Все виды';select.append(allOption);
 for(const kind of kinds){const opt=document.createElement('option');opt.value=kind;opt.textContent=kind;select.append(opt)}
 select.value=kinds.includes(selected)?selected:'';
 const term=el('manual-search').value.trim().toLowerCase();
 const status=el('manual-status').value;
 const from=el('manual-date-start').value,to=el('manual-date-end').value;
 const only=el('manual-only-violations').checked;
 const data=allData.filter(item=>{
  const customer=[item.reg_number,item.customer,item.customer_inn].join(' ').toLowerCase();
  const day=(item.started_at||'').slice(0,10);
  return (!term||customer.includes(term))&&(!status||item.job_status===status)&&
    (!from||(day&&day>=from))&&(!to||(day&&day<=to))&&
    (!only||Number(item.violations||0)>0)&&
    (!select.value||(item.violation_types||[]).includes(select.value));
 });
 const rows=el('manual-rows');rows.replaceChildren();
 el('manual-total').textContent='Показано: '+data.length+' из '+allData.length;
 el('manual-empty').hidden=!!data.length;
 el('manual-table').hidden=!data.length;
 for(const item of data){
  const tr=document.createElement('tr'),reg=document.createElement('td'),
    status=document.createElement('td'),docs=document.createElement('td'),
    violations=document.createElement('td'),actions=document.createElement('td');
  reg.textContent='№ '+item.reg_number;
  const date=document.createElement('small');const stamp=Date.parse(item.started_at||'');
  date.textContent=Number.isFinite(stamp)?new Date(stamp).toLocaleString('ru-RU'):'';
  reg.append(date);
  if(item.customer){const name=document.createElement('small');name.textContent=item.customer;reg.append(name)}
  if(item.customer_inn){const inn=document.createElement('small');inn.textContent='ИНН '+item.customer_inn;reg.append(inn)}
  const label=document.createElement('span');label.className='manual-chip';
  label.textContent=state[item.job_status]||'Проверяется';
  if(['queued','deferred','leased'].includes(item.job_status))label.classList.add('wait');
  if(item.job_status==='error')label.classList.add('fail');
  status.append(label);
  docs.textContent=String(Math.max(0,Number(item.documents_checked||0)))+' проверено';
  violations.textContent=String(Math.max(0,Number(item.violations||0)));
  const wrapper=document.createElement('div');wrapper.className='manual-actions';
  const open=document.createElement('a');
  open.href=item.card_available?'/check-new/'+encodeURIComponent(item.reg_number):'/';
  open.textContent=item.card_available?'Открыть проверку':'На главную';
  const dossier=document.createElement('a');
  dossier.href='/me/cases/'+encodeURIComponent(item.reg_number);
  dossier.textContent='Электронное дело';
  const remove=document.createElement('button');remove.type='button';remove.textContent='Убрать';
  remove.title='Убрать из личного кабинета; сохранённые оригиналы и результаты остаются';
  remove.onclick=async()=>{
    if(!confirm('Убрать эту самостоятельную проверку из кабинета? Документы и результаты сохранятся.'))return;
    remove.disabled=true;
    try{
      const result=await fetch('/me/manual-checks/'+encodeURIComponent(item.reg_number),
      {method:'DELETE',credentials:'same-origin'});
      if(!result.ok)throw Error('Ошибка удаления');
      await refreshAfterAction();
    }catch(error){el('manual-message').textContent=error.message;remove.disabled=false}
  };
  const favorite=document.createElement('button');favorite.type='button';favorite.textContent='☆ В избранное';
  favorite.onclick=async()=>{
   favorite.disabled=true;
   try{const response=await fetch('/me/favorites/procurement/'+encodeURIComponent(item.reg_number),
     {method:'POST',credentials:'same-origin'});
    if(!response.ok)throw Error();el('manual-message').textContent='Закупка добавлена в избранное';
   }catch{el('manual-message').textContent='Не удалось добавить в избранное'}
   finally{favorite.disabled=false}
  };
  wrapper.append(open,dossier,favorite,remove);actions.append(wrapper);
  tr.append(reg,status,docs,violations,actions);rows.append(tr);
 }
 }catch(error){el('manual-message').textContent=error.message}
 finally{loading=false}
}
async function refreshAfterAction(){loading=false;await refresh()}
el('manual-refresh').onclick=refresh;
const filterNames=['manual-search','manual-status','manual-date-start','manual-date-end',
                   'manual-violation-type','manual-only-violations'];
let filterTimer;
for(const id of filterNames){
 const field=el(id);
 const handler=()=>{clearTimeout(filterTimer);filterTimer=setTimeout(refresh,150)};
 field.addEventListener('input',handler);field.addEventListener('change',handler);
}
el('manual-filters-reset').onclick=()=>{
 for(const id of filterNames){const field=el(id);if(field.type==='checkbox')field.checked=false;else field.value=''}
 refresh();
};
el('manual-add').addEventListener('submit',async event=>{
 event.preventDefault();const form=event.currentTarget;
 const number=String(new FormData(form).get('reg')||'').trim();
 if(!/^3[0-9]{10}$/.test(number)){el('manual-message').textContent='Неверный номер ЕИС';return}
 const button=form.querySelector('button');button.disabled=true;
 try{
  const res=await fetch('/me/manual-checks/'+encodeURIComponent(number),
   {method:'POST',credentials:'same-origin'});
  if(!res.ok)throw Error('Не удалось запустить проверку ЕИС');
  el('manual-message').textContent='Задание проверки принято, ожидайте обработки документов.';
  form.reset();await refresh();
 }catch(error){el('manual-message').textContent=error.message}
 finally{button.disabled=false}
});
refresh();
})();
</script>""")

ADMIN = page("Заявки пользователей", """
<h1>Подтверждение новых пользователей</h1><p>Только для администратора. Решения сохраняются в журнале действий.</p>
<p class="message" id="message" role="status"></p>
<table><thead><tr><th>Email</th><th>Дата</th><th>Решение</th></tr></thead><tbody id="rows"></tbody></table>
<script>
async function load(){const r=await fetch('/admin/accounts/pending',{credentials:'same-origin'});
 const m=document.getElementById('message');if(!r.ok){m.textContent='Нет доступа или ошибка загрузки';return}
 const data=await r.json(),tbody=document.getElementById('rows');tbody.replaceChildren();
 for(const item of data){const tr=document.createElement('tr'),email=document.createElement('td'),date=document.createElement('td'),actions=document.createElement('td');
 email.textContent=item.email;date.textContent=new Date(item.created_at).toLocaleString('ru-RU');
 for(const [name,label] of [['approve','Одобрить'],['reject','Отклонить']]){
 const b=document.createElement('button');b.textContent=label;
 b.addEventListener('click',async()=>{b.disabled=true;try{
 const res=await fetch('/admin/accounts/'+encodeURIComponent(item.id)+'/'+name,{method:'POST',credentials:'same-origin'});
 if(res.ok){await load();m.textContent='Решение сохранено'}else{m.textContent='Не удалось сохранить решение';b.disabled=false}
 }catch{m.textContent='Ошибка соединения';b.disabled=false}});actions.append(b)}
 tr.append(email,date,actions);tbody.append(tr)}
}
load();
</script>""")

THEMATIC = page("Мои тематические проверки", """
<style>
.manual-filter-area{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:9px;
 padding:13px;background:#f7fafc;border:1px solid #dfe9f2;border-radius:10px}
.manual-filter-area label{margin:0;font-size:12px}
#thematic-reset{background:#fff;color:#265f8c;border:1px solid #cbdfea}
.eis-my-checks{display:grid;gap:14px;margin-top:22px}
.eis-my-check{padding:18px 20px;background:linear-gradient(135deg,#fff,#f8fbff);
 border:1px solid #dce8f3;border-radius:15px}
.eis-my-check h2{font-size:17px;margin:0 0 7px;color:#1c476d}
.eis-my-check .eis-meta{font-size:13px;color:#60768a;margin:8px 0 12px}
.eis-my-actions{display:flex;align-items:center;flex-wrap:wrap;gap:9px}
.eis-my-actions a{display:inline-flex;min-height:41px;align-items:center;padding:9px 14px;
 background:#2568a5;border:1px solid #2568a5;color:white;border-radius:10px;text-decoration:none;font-size:14px}
.eis-my-actions a:hover{background:#174c7c}
.eis-my-actions button{background:#fff;color:#943a48;border:1px solid #f0d3d9;margin:0}
.eis-my-actions button:hover:not(:disabled){background:#fff2f4}
.eis-empty{border:1px dashed #c8d9ea;border-radius:14px;padding:26px;background:#f8fbff;color:#637c93}
.eis-pill{display:inline-block;padding:4px 10px;border-radius:100px;background:#edf4ff;color:#225d91;
 font-size:12px;font-weight:700}
@media(max-width:650px){.eis-my-check{padding:16px}.eis-my-actions a,.eis-my-actions button{flex:1;justify-content:center}}
</style>
<p><a href="/">← На главную</a></p>
<h1>Мои тематические проверки</h1>
<p>Здесь автоматически отображаются только реальные тематические проверки, которые вы запускали под своей учётной записью. Никаких идентификаторов или отдельных очередей создавать не требуется.</p>
<div id="thematic-filters" class="manual-filter-area" aria-label="Фильтры тематических проверок">
<label>Заказчик<input id="thematic-customer" type="search" placeholder="Наименование заказчика"></label>
<label>Статус<select id="thematic-filter-status"><option value="">Все</option><option value="active">Выполняется</option><option value="stopped">Остановлена</option><option value="completed">Завершена</option></select></label>
<label>Дата с<input id="thematic-from" type="date"></label>
<label>Дата по<input id="thematic-to" type="date"></label>
<label>Вид нарушения<select id="thematic-type"><option value="">Все</option></select></label>
<button type="button" id="thematic-reset">Сбросить фильтры</button></div>
<p id="thematic-status" role="status" aria-live="polite">Загружаем ваши проверки…</p>
<div id="real-thematic-list" class="eis-my-checks" aria-label="Тематические проверки"></div>
<section id="unassigned-section" hidden style="margin-top:28px;border-top:1px solid #e4edf5;padding-top:21px">
<h2>Ранее запущенные проверки</h2>
<p>Некоторые старые тематические проверки были созданы до появления личных кабинетов. Администратор может привязать такую проверку к своей учётной записи одним нажатием. Сохранённые результаты не изменяются.</p>
<div id="unassigned-list" class="eis-my-checks"></div>
</section>
<script>
(()=>{
const holder=document.getElementById('real-thematic-list');
const status=document.getElementById('thematic-status');
const labels={dates:'Сроки закупки и разъяснений',docs:'Документация',national:'Национальный режим',
 security:'Обеспечение',sme:'МСП',access:'Требования к участникам',protocols:'Протоколы',
 contract:'Договоры',notices:'Извещения',evaluation:'Оценка заявок'};
let busy=false;
async function refresh(){
 if(busy)return;busy=true;
 try{
  const response=await fetch('/api/thematic/history',{credentials:'same-origin',cache:'no-store'});
  if(response.status===401){location.assign('/account/login');return}
  if(!response.ok)throw Error('Не удалось получить тематические проверки');
  const data=await response.json();
  const allBatches=Array.isArray(data.batches)?data.batches:[];
  const control=document.getElementById('thematic-type'),previous=control.value;
  const types=[...new Set(allBatches.flatMap(b=>b.violation_types||[]))]
      .sort((a,b)=>a.localeCompare(b,'ru'));
  control.replaceChildren();
  const opt=document.createElement('option');opt.value='';opt.textContent='Все';control.append(opt);
  for(const kind of types){const child=document.createElement('option');
    child.value=kind;child.textContent=kind;control.append(child)}
  control.value=types.includes(previous)?previous:'';
  const customer=document.getElementById('thematic-customer').value.trim().toLowerCase(),
   wanted=document.getElementById('thematic-filter-status').value,
   from=document.getElementById('thematic-from').value,
   to=document.getElementById('thematic-to').value;
  const batches=allBatches.filter(b=>{
   const clients=(b.customers||[]).join(' ').toLowerCase();
   const day=(b.created_at||'').slice(0,10);
   return (!customer||clients.includes(customer))&&(!wanted||b.status===wanted)&&
    (!from||(day&&day>=from))&&(!to||(day&&day<=to))&&
    (!control.value||(b.violation_types||[]).includes(control.value));
  });
  holder.replaceChildren();
  status.textContent='Показано: '+batches.length+' из '+allBatches.length;
  if(!batches.length){
   const empty=document.createElement('div');empty.className='eis-empty';
   empty.textContent='Проверки, запущенные вами в разделе «Тематическая проверка», появятся здесь автоматически.';
   holder.append(empty);return;
  }
  for(const check of batches){
   const card=document.createElement('article');card.className='eis-my-check';
   const title=document.createElement('h2');
   const categories=(check.categories||[]).map(id=>labels[id]||id);
   title.textContent=categories.length?categories.join(' · '):'Тематическая проверка';
   const state=document.createElement('span');state.className='eis-pill';
   state.textContent=check.status==='active'?'Выполняется':'Остановлена';
   const meta=document.createElement('p');meta.className='eis-meta';
   const timestamp=Date.parse(check.created_at);
   meta.textContent=(Number.isFinite(timestamp)?'Запущена: '+new Date(timestamp).toLocaleString('ru-RU')+' · ':'')+
     'Закупок: '+Number(check.purchases||0)+' · Подтверждённых нарушений: '+Number(check.violations||0)+
     ((check.customers||[]).length?' · Заказчики: '+check.customers.slice(0,2).join(', '):'');
   const actions=document.createElement('div');actions.className='eis-my-actions';
   const open=document.createElement('a');open.href='/thematic#'+encodeURIComponent(check.id);open.textContent='Открыть результаты';
   const remove=document.createElement('button');remove.type='button';remove.textContent='Удалить проверку';
   remove.onclick=async()=>{
    if(!confirm('Удалить эту тематическую проверку и сохранённые тематические результаты? Действие нельзя отменить. Оригиналы ЕИС и общие проверки останутся.'))return;
    remove.disabled=true;
    try{
     const res=await fetch('/api/thematic/'+encodeURIComponent(check.id),
       {method:'DELETE',credentials:'same-origin'});
     if(!res.ok)throw Error('Не удалось удалить проверку');
     await refreshAfterDelete();
    }catch(error){status.textContent=error.message;remove.disabled=false}
   };
   actions.append(open,remove);card.append(title,state,meta,actions);holder.append(card);
  }
 }catch(error){status.textContent=error.message}
 finally{busy=false}
}
async function refreshAfterDelete(){busy=false;await refresh()}
async function loadHistorical(){
 try{
  const response=await fetch('/api/thematic/unassigned',{credentials:'same-origin',cache:'no-store'});
  if(response.status===403)return;
  if(!response.ok)return;
  const data=await response.json();
  const items=Array.isArray(data.batches)?data.batches:[];
  const section=document.getElementById('unassigned-section');
  const list=document.getElementById('unassigned-list');
  list.replaceChildren();section.hidden=!items.length;
  for(const check of items){
   const card=document.createElement('article');card.className='eis-my-check';
   const h=document.createElement('h2');
   const cats=(check.categories||[]).map(key=>labels[key]||key);
   h.textContent=cats.length?cats.join(' · '):'Тематическая проверка';
   const meta=document.createElement('p');meta.className='eis-meta';
   const date=Date.parse(check.created_at);
   meta.textContent=(Number.isFinite(date)?new Date(date).toLocaleString('ru-RU')+' · ':'')+
      'Закупок: '+Number(check.purchases||0)+' · Нарушений: '+Number(check.violations||0);
   const btn=document.createElement('button');btn.type='button';btn.textContent='Привязать к моему кабинету';
   btn.onclick=async()=>{
    if(!confirm('Привязать выбранную ранее запущенную тематическую проверку к вашему кабинету?'))return;
    btn.disabled=true;
    try{
     const result=await fetch('/api/thematic/adopt/'+encodeURIComponent(check.id),
       {method:'POST',credentials:'same-origin'});
     if(!result.ok)throw Error('Не удалось привязать проверку. Возможно, она уже принадлежит другой учётной записи.');
     await refresh();await loadHistorical();
    }catch(error){status.textContent=error.message;btn.disabled=false}
   };
   card.append(h,meta,btn);list.append(card);
  }
 }catch(error){/* An unavailable historical migration never hides actual owned checks. */}
}
let pendingFilter;
for(const id of ['thematic-customer','thematic-filter-status','thematic-from','thematic-to','thematic-type']){
 const input=document.getElementById(id);
 const handler=()=>{clearTimeout(pendingFilter);pendingFilter=setTimeout(refresh,180)};
 input.addEventListener('input',handler);input.addEventListener('change',handler);
}
document.getElementById('thematic-reset').onclick=()=>{
 for(const id of ['thematic-customer','thematic-filter-status','thematic-from','thematic-to','thematic-type'])
  document.getElementById(id).value='';
 refresh();
};
setInterval(()=>{if(!document.hidden){refresh();loadHistorical()}},30000);
refresh();loadHistorical();
})();
</script>""")

MEMOS = page("Архив служебных записок", """
<style>
.memo-archive-form{display:flex;align-items:end;gap:11px;flex-wrap:wrap;padding:16px;
 border:1px solid #dae7f1;border-radius:10px;background:#f8fbfe;margin:17px 0}
.memo-archive-form label{margin:0;flex:1 1 215px}
.memo-archive-form button{margin:0 0 1px}
.memo-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));
 align-items:end;gap:10px;padding:14px;margin:18px 0;border:1px solid #dce9f3;
 border-radius:10px;background:#f7fafc}
.memo-filters label{margin:0}
.memo-filters button{background:#fff;color:#24618c;border:1px solid #cedeeb}
.memo-action{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.memo-action button,.memo-action a{min-height:37px;padding:8px 11px;border-radius:8px;
 font-weight:690;font-size:12px}
.memo-action a{display:inline-flex;align-items:center;background:#eaf4fb;
 border:1px solid #d2e5f3;color:#195d90}
.memo-legacy{font-size:12px;color:#946a2e}
@media(max-width:700px){.memo-archive-form button{flex:1}#memo-table td:last-child{min-width:190px}}
</style>
<p><a href="/">← На главную / Личный кабинет</a></p>
<h1>Архив служебных записок</h1>
<p>Здесь сохраняются подготовленные по проверенным нарушениям документы в формате ODT.
Для сохранённых оригиналов доступно скачивание ODT и преобразование в PDF через LibreOffice.</p>
<form id="memo-save" class="memo-archive-form">
<label>Создать и сохранить служебную записку по закупке
<input type="text" name="reg" inputmode="numeric" pattern="3[0-9]{10}" maxlength="11"
 required autocomplete="off" placeholder="Введите номер ЕИС"></label>
<button type="submit">Сформировать и сохранить</button>
</form>
<p id="memo-status" role="status" aria-live="polite">Загружаем архив…</p>
<div class="memo-filters" aria-label="Поиск в архиве">
<label>Номер закупки<input id="memo-search" type="search" placeholder="Номер ЕИС"></label>
<label>Дата с<input id="memo-from" type="date"></label>
<label>Дата по<input id="memo-to" type="date"></label>
<button type="button" id="memo-reset">Сбросить фильтры</button>
</div>
<div id="memo-count" role="status"></div>
<table id="memo-table" aria-label="Архив служебных записок">
<thead><tr><th>Закупка</th><th>Дата создания</th><th>Документ</th><th>Действия</th></tr></thead>
<tbody id="memo-rows"></tbody></table>
<script>
(()=>{
const el=id=>document.getElementById(id);
let cache=[],busy=false,decisions={};
function render(){
 const search=el('memo-search').value.trim(),from=el('memo-from').value,to=el('memo-to').value;
 const data=cache.filter(x=>{
   const day=(x.created_at||'').slice(0,10);
   return (!search||x.reg_number.includes(search))&&(!from||(day&&day>=from))&&
    (!to||(day&&day<=to));
 });
 const rows=el('memo-rows');rows.replaceChildren();
 el('memo-count').textContent='Показано: '+data.length+' из '+cache.length;
 for(const item of data){
  const tr=document.createElement('tr'),reg=document.createElement('td'),
   date=document.createElement('td'),state=document.createElement('td'),
   action=document.createElement('td');
  reg.textContent='№ '+item.reg_number;
  const stamp=Date.parse(item.created_at);
  date.textContent=Number.isFinite(stamp)?new Date(stamp).toLocaleString('ru-RU'):'—';
  const actions=document.createElement('div');actions.className='memo-action';
  if(item.downloadable){
   const decision=decisions[item.id];
   state.textContent='Сохранённый ODT'+
    (decision?' · '+({submitted:'На согласовании',approved:'Согласована',
     returned:'На доработке',rejected:'Отклонена'}[decision.status]||decision.status):'');
   for(const fmt of ['odt','pdf']){
    const link=document.createElement('a');link.href='/me/memo-archive/'+encodeURIComponent(item.id)+'/'+fmt;
    link.textContent='Скачать '+fmt.toUpperCase();link.setAttribute('download','memo_'+item.reg_number+'.'+fmt);
    link.onclick=async event=>{
     event.preventDefault();link.style.pointerEvents='none';
     try{
      const r=await fetch(link.href,{credentials:'same-origin',cache:'no-store'});
      if(!r.ok)throw Error(fmt==='pdf'?'Не удалось создать PDF. Проверьте LibreOffice.':'Документ временно недоступен');
      const data=await r.blob();
      const objectURL=URL.createObjectURL(data);
      const a=document.createElement('a');a.href=objectURL;
      a.download='memo_'+item.reg_number+'.'+fmt;document.body.append(a);a.click();a.remove();
      setTimeout(()=>URL.revokeObjectURL(objectURL),30000);
      el('memo-status').textContent='Документ '+fmt.toUpperCase()+' подготовлен';
     }catch(error){el('memo-status').textContent=error.message}
     finally{link.style.pointerEvents=''}
    };
    actions.append(link);
   }
   const decision=decisions[item.id];
   if(!decision||decision.status==='returned'){
    const button=document.createElement('button');button.type='button';
    button.textContent=decision?'Повторно на согласование':'Отправить руководителю';
    button.onclick=async()=>{
      button.disabled=true;
      try{
       const response=await fetch('/me/assignments/memo/'+encodeURIComponent(item.id)+'/submit',
        {method:'POST',credentials:'same-origin'});
       if(!response.ok){
        const message=await response.json().catch(()=>({}));
        throw Error(message.detail||'Не удалось передать записку');
       }
       await refresh();el('memo-status').textContent='Записка передана руководителю';
      }catch(err){el('memo-status').textContent=err.message;button.disabled=false}
    };
    actions.append(button);
   }
   if(decision&&decision.comment){
    const note=document.createElement('small');note.textContent=decision.comment;
    note.className='memo-legacy';actions.append(note);
   }
  }else{
   state.textContent='Старый архив: файл не был сохранён';
   state.className='memo-legacy';
   const button=document.createElement('button');button.type='button';button.textContent='Создать заново';
   button.onclick=()=>{el('memo-save').querySelector('input').value=item.reg_number;
     el('memo-save').scrollIntoView({behavior:'smooth',block:'center'})};
   actions.append(button);
  }
  action.append(actions);tr.append(reg,date,state,action);rows.append(tr);
 }
 if(!data.length){const tr=document.createElement('tr'),td=document.createElement('td');
   td.colSpan=4;td.textContent=cache.length?'По фильтрам документы не найдены':'Архив пока пуст';
   tr.append(td);rows.append(tr)}
}
async function refresh(){
 if(busy)return;busy=true;
 try{
  const r=await fetch('/me/memo-archive',{credentials:'same-origin',cache:'no-store'});
  if(r.status===401){location.replace('/account/login');return}
  if(!r.ok)throw Error('Не удалось получить архив');
  const data=await r.json();if(!Array.isArray(data))throw Error('Некорректный ответ сервера');
  cache=data;
  try{
   const reviews=await fetch('/me/assignments/memos',{credentials:'same-origin',cache:'no-store'});
   if(reviews.ok){
     const rows=await reviews.json();
     decisions=Object.fromEntries(rows.map(x=>[x.id,x]));
   }
  }catch{}
  render();
  el('memo-status').textContent='Архив вашей учётной записи. Доступ к чужим запискам закрыт.';
 }catch(error){el('memo-status').textContent=error.message}
 finally{busy=false}
}
for(const id of ['memo-search','memo-from','memo-to']){
 el(id).addEventListener('input',render);el(id).addEventListener('change',render)
}
el('memo-reset').onclick=()=>{
 for(const id of ['memo-search','memo-from','memo-to'])el(id).value='';
 render();
};
el('memo-save').addEventListener('submit',async event=>{
 event.preventDefault();const form=event.currentTarget;
 const reg=String(new FormData(form).get('reg')||'').trim();
 if(!/^3[0-9]{10}$/.test(reg))return;
 const button=form.querySelector('button');button.disabled=true;
 el('memo-status').textContent='Формируется служебная записка по подтверждённым нарушениям…';
 try{
  const response=await fetch('/me/memo-archive/'+encodeURIComponent(reg),
    {method:'POST',credentials:'same-origin'});
  if(!response.ok){
   const data=await response.json().catch(()=>({}));
   throw Error(typeof data.detail==='string'?data.detail:'Не удалось сформировать ODT');
  }
  form.reset();await refresh();
  el('memo-status').textContent='Служебная записка сохранена. Доступны ODT и PDF.';
 }catch(error){el('memo-status').textContent=error.message}
 finally{button.disabled=false}
});
refresh();
})();
</script>""")
