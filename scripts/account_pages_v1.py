"""Standalone HTML for staged user accounts (not mounted in production)."""
from html import escape


def page(title, body):
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + escape(title) + """ · ЕИС Аудитор</title>
<style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#17283a;background:#f4f7fa}
*{box-sizing:border-box}body{margin:0}header{background:#fff;border-bottom:1px solid #dde5ee;padding:18px 5vw;font-weight:750}
main{max-width:760px;margin:35px auto;padding:0 18px}.panel{background:#fff;border:1px solid #dce4ed;border-radius:16px;padding:28px;box-shadow:0 4px 18px #152c4810}
h1{font-size:25px;margin:0 0 10px}p{line-height:1.55;color:#52657b}
label{display:block;margin-top:18px;font-weight:650}input{display:block;width:100%;padding:13px;border:1px solid #b6c6d6;border-radius:9px;margin-top:7px;font:inherit}
button{margin-top:18px;border:0;border-radius:9px;background:#14599b;color:white;padding:12px 19px;font:inherit;cursor:pointer}
button:disabled{opacity:.55}a{color:#14599b}.message{margin-top:15px;min-height:22px}
table{border-collapse:collapse;width:100%;margin-top:15px}td,th{padding:12px;text-align:left;border-bottom:1px solid #e2e8ef}td button{margin:0 5px 0 0}
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

THEMATIC = page("Тематические проверки", """
<h1>Мои тематические проверки</h1>
<p>Сохранённые в вашем личном кабинете очереди. Добавление не запускает проверку.</p>
<p><a href="/account/home">← Личный кабинет</a></p>
<button id="create-personal" type="button">Создать личную очередь</button>
<form id="add-batch"><label>Идентификатор тематической очереди
<input name="batch" required maxlength="200"></label><button>Сохранить очередь</button></form>
<p id="message" role="status"></p>
<table><thead><tr><th>Очередь</th><th>Действие</th></tr></thead><tbody id="rows"></tbody></table>
<script>
async function loadBatches(){
 const m=document.getElementById('message');
 try{
 const r=await fetch('/me/thematic-batches',{credentials:'same-origin'});
 if(r.status===401){location.assign('/account/login');return}
 if(!r.ok){m.textContent='Не удалось получить очереди';return}
 const data=await r.json(),rows=document.getElementById('rows');rows.replaceChildren();
 for(const item of data){
 const tr=document.createElement('tr'),name=document.createElement('td'),action=document.createElement('td');
 name.textContent=item.batch_id;
 if(item.batch_id.startsWith('personal-')){
 const details=document.createElement('button');details.textContent='Статус';
 details.addEventListener('click',async()=>{const res=await fetch('/me/thematic-batches/'+encodeURIComponent(item.batch_id),{credentials:'same-origin'});
 if(!res.ok){m.textContent='Не удалось получить статус';return}
 const info=await res.json();m.textContent=info.execution_started?'Проверка выполняется':'Очередь сохранена, проверка ещё не запущена';});
 action.append(details);
 const add=document.createElement('button');add.textContent='Добавить закупку';
 add.addEventListener('click',async()=>{
 const reg=prompt('Номер закупки ЕИС (11 цифр, начинается с 3)');
 if(reg===null)return;
 if(!/^3[0-9]{10}$/.test(reg)){m.textContent='Некорректный номер ЕИС';return}
 add.disabled=true;
 try{const res=await fetch('/me/thematic-batches/'+encodeURIComponent(item.batch_id)+'/procurements/'+reg,{method:'POST',credentials:'same-origin'});
 m.textContent=res.ok?'Закупка добавлена в личную очередь. Проверка ещё не запущена':'Не удалось добавить закупку';
 }catch{m.textContent='Ошибка соединения'}finally{add.disabled=false}
 });action.append(add);
 const show=document.createElement('button');show.textContent='Закупки';
 show.addEventListener('click',async()=>{try{
 const res=await fetch('/me/thematic-batches/'+encodeURIComponent(item.batch_id)+'/procurements',{credentials:'same-origin'});
 if(!res.ok){m.textContent='Не удалось получить список закупок';return}
 const list=await res.json();m.textContent=list.length?'Закупки: '+list.map(x=>x.reg_number).join(', '):'В очереди пока нет закупок';
 }catch{m.textContent='Ошибка соединения'}});action.append(show);
 }
 const btn=document.createElement('button');btn.textContent='Удалить из моих';
 btn.addEventListener('click',async()=>{
 const res=await fetch('/me/thematic-batches/'+encodeURIComponent(item.batch_id),{method:'DELETE',credentials:'same-origin'});
 m.textContent=res.ok?'Очередь удалена из личного списка':'Ошибка удаления';
 if(res.ok)loadBatches();
 });action.append(btn);tr.append(name,action);rows.append(tr);
 }
 if(!data.length)m.textContent='Сохранённых очередей пока нет';
 }catch{m.textContent='Ошибка соединения'}
}
document.getElementById('create-personal').addEventListener('click',async()=>{
 const m=document.getElementById('message'),button=document.getElementById('create-personal');button.disabled=true;try{
 const r=await fetch('/me/thematic-batches',{method:'POST',credentials:'same-origin'});
 m.textContent=r.ok?'Личная очередь создана. Проверка ещё не запущена.':'Не удалось создать очередь';
 if(r.ok)loadBatches();
 }catch{m.textContent='Ошибка соединения'}finally{button.disabled=false}
});
document.getElementById('add-batch').addEventListener('submit',async e=>{
 e.preventDefault();const m=document.getElementById('message');
 const id=new FormData(e.currentTarget).get('batch');
 const r=await fetch('/me/thematic-batches/'+encodeURIComponent(id),{method:'POST',credentials:'same-origin'});
 m.textContent=r.ok?'Очередь сохранена':'Не удалось сохранить очередь';
 if(r.ok){e.currentTarget.reset();loadBatches()}
});
loadBatches();
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
