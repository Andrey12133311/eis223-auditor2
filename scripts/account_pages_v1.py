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

HOME = page("Мои закупки", """
<h1>Мой кабинет</h1><p>Ваши сохранённые закупки ЕИС.</p><nav aria-label="Разделы личного кабинета"><a href="/account/home">Мои закупки</a> · <a href="/account/thematic">Тематические проверки</a> · <a href="/account/memos">Служебные записки</a></nav>
<p id="summary" role="status">Загружаем сводку личного кабинета…</p>
<form id="add"><label>Номер закупки<input name="reg" pattern="3[0-9]{10}" required placeholder="32616412317"></label>
<button>Добавить закупку</button></form>
<p class="message" id="message" role="status"></p><table><thead><tr><th>Номер ЕИС</th><th>Действие</th></tr></thead><tbody id="rows"></tbody></table>
<button id="logout">Выйти</button>
<script>
async function loadSummary(){try{const r=await fetch('/me/summary',{credentials:'same-origin'});if(!r.ok)return;const x=await r.json();document.getElementById('summary').textContent='Мои закупки: '+x.procurements+' · Тематические очереди: '+x.thematic_batches+' · Служебные записки: '+x.memos;}catch{document.getElementById('summary').textContent='Сводка временно недоступна'}}
async function load(){const r=await fetch('/me/procurements',{credentials:'same-origin'});
 if(r.status===401){location.assign('/account/login');return}
 if(!r.ok)return;const data=await r.json(),tbody=document.getElementById('rows');tbody.replaceChildren();
 for(const item of data){const tr=document.createElement('tr'),td=document.createElement('td'),act=document.createElement('td');
 td.textContent=item.reg_number;const b=document.createElement('button');b.textContent='Удалить';
 b.addEventListener('click',async()=>{await fetch('/me/procurements/'+encodeURIComponent(item.reg_number),{method:'DELETE',credentials:'same-origin'});load()});
 act.append(b);tr.append(td,act);tbody.append(tr)}loadSummary();
}
document.getElementById('add').addEventListener('submit',async e=>{e.preventDefault();
 const reg=new FormData(e.currentTarget).get('reg');
 const r=await fetch('/me/procurements/'+encodeURIComponent(reg),{method:'POST',credentials:'same-origin'});
 document.getElementById('message').textContent=r.ok?'Закупка добавлена':'Не удалось добавить закупку';
 if(r.ok)load();
});
document.getElementById('logout').addEventListener('click',async()=>{await fetch('/auth/logout',{method:'POST',credentials:'same-origin'});location.assign('/account/login')});
load();
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
  const batches=Array.isArray(data.batches)?data.batches:[];
  holder.replaceChildren();
  status.textContent='Ваших тематических проверок: '+batches.length;
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
     'Закупок: '+Number(check.purchases||0)+' · Подтверждённых нарушений: '+Number(check.violations||0);
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
setInterval(()=>{if(!document.hidden){refresh();loadHistorical()}},30000);
refresh();loadHistorical();
})();
</script>""")

MEMOS = page("Служебные записки", """
<h1>Мои служебные записки</h1><p>Документы, сохранённые за вашей учётной записью. Перейти сюда можно из личного кабинета на главной странице.</p>
<p><a href="/account/home">← Личный кабинет</a></p>
<p id="message" role="status">Загружаем…</p><table><thead><tr><th>Документ</th><th>Действие</th></tr></thead><tbody id="rows"></tbody></table>
<script>
(async()=>{const m=document.getElementById('message');try{const r=await fetch('/me/memos',{credentials:'same-origin'});
if(r.status===401){location.assign('/account/login');return}if(!r.ok){m.textContent='Не удалось получить документы';return}
const data=await r.json();const rows=document.getElementById('rows');for(const item of data){const tr=document.createElement('tr'),td=document.createElement('td');
td.textContent=String('Служебная записка · '+item.reg_number);tr.append(td);rows.append(tr)}
m.textContent=data.length?'':'Документов пока нет';}catch{m.textContent='Ошибка соединения'}})();
</script>""")
