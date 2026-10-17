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
<p>Регистрация свободная. На вашу почту придёт письмо со ссылкой подтверждения. После подтверждения адреса заявку рассмотрит администратор.</p>
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
 m.textContent=r.ok?'Проверьте почту: мы отправили ссылку для подтверждения адреса.':'Не удалось отправить заявку. Проверьте данные.';
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
 e.preventDefault();const f=e.currentTarget,m=document.getElementById('message');
 try{const r=await fetch('/auth/login',{method:'POST',credentials:'same-origin',
 headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(f)))});
 if(r.ok){location.assign('/account/home')}else{m.textContent='Неверные данные или аккаунт ещё не подтверждён.'}
 }catch{m.textContent='Ошибка соединения.'}
});
</script>""")

HOME = page("Мои закупки", """
<h1>Мой кабинет</h1><p>Ваши сохранённые закупки ЕИС.</p>
<form id="add"><label>Номер закупки<input name="reg" pattern="3[0-9]{10}" required placeholder="32616412317"></label>
<button>Добавить закупку</button></form>
<p class="message" id="message" role="status"></p><table><thead><tr><th>Номер ЕИС</th><th>Действие</th></tr></thead><tbody id="rows"></tbody></table>
<button id="logout">Выйти</button>
<script>
async function load(){const r=await fetch('/me/procurements',{credentials:'same-origin'});
 if(r.status===401){location.assign('/account/login');return}
 if(!r.ok)return;const data=await r.json(),tbody=document.getElementById('rows');tbody.replaceChildren();
 for(const item of data){const tr=document.createElement('tr'),td=document.createElement('td'),act=document.createElement('td');
 td.textContent=item.reg_number;const b=document.createElement('button');b.textContent='Удалить';
 b.addEventListener('click',async()=>{await fetch('/me/procurements/'+encodeURIComponent(item.reg_number),{method:'DELETE',credentials:'same-origin'});load()});
 act.append(b);tr.append(td,act);tbody.append(tr)}
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
 b.addEventListener('click',async()=>{b.disabled=true;const res=await fetch('/admin/accounts/'+encodeURIComponent(item.id)+'/'+name,{method:'POST',credentials:'same-origin'});
 m.textContent=res.ok?'Решение сохранено':'Не удалось сохранить решение';load()});actions.append(b)}
 tr.append(email,date,actions);tbody.append(tr)}
}
load();
</script>""")
