"""Standalone HTML for staged user accounts (not mounted in production)."""
from html import escape


def page(title, body):
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + escape(title) + """ · ЕИС Аудитор</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#203750;background:#f3f7fb;
 font-synthesis:none}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(ellipse at top right,#e9f4ff 0%,transparent 44%),#f3f7fb;
 -webkit-font-smoothing:antialiased}
header{background:#fff;border-bottom:1px solid #dfe7f0;padding:18px clamp(18px,5vw,64px);
 font-size:16px;font-weight:780;color:#183b5a;letter-spacing:-.02em}
main{width:min(100%,960px);margin:clamp(22px,4vw,44px) auto;padding:0 clamp(14px,3vw,24px) 40px}
.panel{background:#fff;border:1px solid #dfe8f1;border-radius:20px;
 padding:clamp(20px,4vw,38px);box-shadow:0 16px 45px #173d6110;min-width:0}
h1{font-size:clamp(24px,4.2vw,34px);font-weight:800;line-height:1.2;letter-spacing:-.035em;color:#193f63;margin:0 0 13px}
h2{font-size:21px;color:#213f5c}
p{line-height:1.65;color:#65788c;margin:12px 0}
nav{display:flex;gap:12px;flex-wrap:wrap;align-items:center;padding:12px 0}
a{color:#2469a4;font-weight:600;text-decoration:none}
a:hover{text-decoration:underline}
label{display:block;margin-top:17px;font-weight:650;color:#35516b}
input,select,textarea{display:block;width:100%;min-height:45px;
 padding:11px 13px;border:1px solid #c7d7e7;border-radius:11px;
 background:#fff;color:#203750;font:inherit;margin-top:7px}
input:focus{border-color:#3a83bd;outline:3px solid #d7e9fb}
button{min-height:41px;margin:9px 6px 5px 0;border:1px solid #2365a3;
 border-radius:10px;background:#2467a6;color:white;padding:10px 15px;
 font:650 14px system-ui,sans-serif;cursor:pointer;transition:background .15s,box-shadow .15s}
button:hover:not(:disabled){background:#164c81;box-shadow:0 4px 12px #1a558628}
button:disabled{opacity:.5;cursor:wait}
a:focus-visible,button:focus-visible{outline:3px solid #8ac2f0;outline-offset:3px}
.message,[role=status]{min-height:19px;font-size:13px;color:#59728c}
table{border-collapse:separate;border-spacing:0;width:100%;margin-top:18px;
 border:1px solid #dfe9f3;border-radius:14px;overflow:hidden}
th,td{padding:13px 15px;text-align:left;border-bottom:1px solid #ebf0f6;vertical-align:middle}
th{background:#f3f8fe;color:#47617b;font-size:13px;font-weight:750}
tr:last-child td{border-bottom:0}
tbody tr:hover{background:#f8fbff}
td button{margin:3px 5px 3px 0}
#rows td:last-child{min-width:210px}
#personal-procurements,#personal-findings{margin-top:18px;padding:15px;border-radius:13px;
 background:#f8fbff;border:1px solid #e4edf5}
@media(max-width:700px){
 header{padding:16px 18px}
 main{margin:20px auto;padding:0 12px 30px}
 .panel{padding:20px 16px;border-radius:16px}
 table{display:block;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
 th,td{padding:11px 12px;white-space:normal}
 td button{margin-top:5px}
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
setInterval(()=>{if(!document.hidden)refresh()},30000);
refresh();
})();
</script>""")

MEMOS = page("Служебные записки", """
<h1>Мои служебные записки</h1><p>Доступны только документы, принадлежащие вашей учётной записи.</p>
<p><a href="/account/home">← Личный кабинет</a></p>
<p id="message" role="status">Загружаем…</p><table><thead><tr><th>Документ</th><th>Действие</th></tr></thead><tbody id="rows"></tbody></table>
<script>
(async()=>{const m=document.getElementById('message');try{const r=await fetch('/me/memos',{credentials:'same-origin'});
if(r.status===401){location.assign('/account/login');return}if(!r.ok){m.textContent='Не удалось получить документы';return}
const data=await r.json();const rows=document.getElementById('rows');for(const item of data){const tr=document.createElement('tr'),td=document.createElement('td');
td.textContent=String('Служебная записка · '+item.reg_number);tr.append(td);rows.append(tr)}
m.textContent=data.length?'':'Документов пока нет';}catch{m.textContent='Ошибка соединения'}})();
</script>""")
