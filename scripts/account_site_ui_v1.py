"""Same-domain sign-in gateway and immediately visible personal workspace.

Pure HTML builders: no private user data embedded in shared-page markup.
Private data is fetched from owner-scoped APIs only after authentication.
"""
from html import escape


_STYLE = """
<style>
:root{--eis-ink:#172a42;--eis-blue:#225c99;--eis-muted:#64748b}
.eis223-workspace{font:14px/1.5 system-ui,-apple-system,sans-serif;color:var(--eis-ink);
background:#f2f6fb;border-bottom:1px solid #d7e2ee;padding:14px clamp(15px,5vw,80px)}
.eis223-menu{display:flex;align-items:center;flex-wrap:wrap;gap:11px 22px}
.eis223-menu strong{font-size:16px}.eis223-menu a{color:#225c99;text-decoration:none;font-weight:650}
.eis223-menu button{font:inherit;border:1px solid #c7d7e7;border-radius:8px;
background:white;color:#225c99;padding:7px 12px;cursor:pointer}
.eis223-menu button:hover,.eis223-menu a:hover{text-decoration:underline}
.eis223-personal{margin-top:13px;border:1px solid #dce5ef;background:white;border-radius:13px;
padding:16px;box-shadow:0 4px 15px #20374f0b}
.eis223-personal h2{font-size:19px;margin:0 0 5px}
.eis223-personal .eis223-subtle{color:var(--eis-muted);margin:0 0 12px}
.eis223-columns{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.eis223-tile{border-radius:10px;background:#f7faff;padding:13px;min-width:0}
.eis223-tile h3{font-size:15px;margin:0 0 7px}
.eis223-tile ul{margin:4px 0 10px;padding-left:17px;max-height:110px;overflow:auto}
.eis223-tile li{padding:2px 0;overflow-wrap:anywhere}
.eis223-tile form{display:flex;gap:7px;flex-wrap:wrap}
.eis223-tile input{font:inherit;min-width:130px;flex:1;padding:8px;
border:1px solid #c3d3e5;background:white;border-radius:7px}
.eis223-tile button{font:inherit;padding:8px 12px;background:#225c99;color:white;
border:0;border-radius:7px;cursor:pointer}
.eis223-tile button:disabled{opacity:.5}
.eis223-workspace [role=status]{color:#3e627e;font-size:13px;margin:5px 0}
@media(max-width:650px){.eis223-workspace{padding:12px}.eis223-personal{padding:12px}}
</style>
"""


def site_header(role="user"):
    admin = '<a href="/account/admin">Заявки пользователей</a>' if role == "admin" else ""
    return (_STYLE + '''
<div class="eis223-workspace" data-eis223-account-nav="1">
<nav class="eis223-menu" aria-label="Навигация ЕИС Аудитор">
<strong>ЕИС Аудитор · Личный кабинет</strong>
<a href="/">Общая проверка</a>
<a href="/account/thematic">Мои тематические проверки</a>
<a href="/account/memos">Мои служебные записки</a>
<a href="/account/home">Мои закупки</a>
''' + admin + '''
<button type="button" id="eis223-logout">Выйти</button>
<span id="eis223-logout-status" role="status"></span>
</nav>
<section class="eis223-personal" aria-label="Мой личный кабинет">
<h2>Мой личный кабинет</h2>
<p class="eis223-subtle" id="eis223-personal-summary">Загрузка ваших данных…</p>
<div class="eis223-columns">
<div class="eis223-tile"><h3>Мои сохранённые закупки</h3>
<ul id="eis223-my-procurements"><li>Загрузка…</li></ul>
<form id="eis223-add-procurement">
<input aria-label="Номер ЕИС" placeholder="Номер закупки, 11 цифр"
name="reg" required pattern="3[0-9]{10}" maxlength="11" autocomplete="off">
<button type="submit">Добавить</button></form>
<p role="status" id="eis223-add-status"></p>
</div>
<div class="eis223-tile"><h3>Мои тематические очереди</h3>
<ul id="eis223-my-batches"><li>Загрузка…</li></ul>
<button type="button" id="eis223-new-batch">Создать очередь</button>
<p role="status" id="eis223-batch-status"></p>
</div>
</div></section></div>
<script>
(()=>{
const q=id=>document.getElementById(id);
const show=(id,msg)=>{const el=q(id);if(el)el.textContent=msg;};
async function get(url){
const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});
if(r.status===401){location.replace('/account/login');throw Error('Требуется вход')}
if(!r.ok)throw Error('Ошибка загрузки');return await r.json();
}
async function refresh(){
try{
const [summary,purchases,batches]=await Promise.all([
get('/me/summary'),get('/me/procurements'),get('/me/thematic-batches')]);
show('eis223-personal-summary','Сохранённые закупки: '+summary.procurements+
' · Тематические очереди: '+summary.thematic_batches+' · Служебные записки: '+summary.memos);
const pl=q('eis223-my-procurements');pl.replaceChildren();
if(!purchases.length){const li=document.createElement('li');li.textContent='Пока нет закупок';pl.append(li)}
for(const item of purchases.slice(0,10)){
 const li=document.createElement('li'),a=document.createElement('a');
 a.href='/';a.textContent=item.reg_number;li.append(a);pl.append(li);
}
const bl=q('eis223-my-batches');bl.replaceChildren();
if(!batches.length){const li=document.createElement('li');li.textContent='Очереди не созданы';bl.append(li)}
for(const item of batches.slice(0,10)){
 const li=document.createElement('li');li.textContent=item.batch_id;bl.append(li);
}
}catch{show('eis223-personal-summary','Личные данные временно недоступны. Общая проверка сохранена.')}
}
q('eis223-logout').addEventListener('click',async()=>{
const button=q('eis223-logout');button.disabled=true;
try{
const response=await fetch('/auth/logout',{method:'POST',credentials:'same-origin'});
if(!response.ok)throw Error('Ошибка выхода');
location.replace('/account/login');
}catch{button.disabled=false;show('eis223-logout-status','Не удалось выйти. Повторите попытку.')}
});
window.addEventListener('pageshow',event=>{if(event.persisted)location.reload()});
q('eis223-add-procurement').addEventListener('submit',async event=>{
event.preventDefault();const form=event.currentTarget;
const reg=String(new FormData(form).get('reg')||'').trim();
if(!/^3[0-9]{10}$/.test(reg))return show('eis223-add-status','Некорректный номер ЕИС');
const btn=form.querySelector('button');btn.disabled=true;
try{
const r=await fetch('/me/procurements/'+encodeURIComponent(reg),
{method:'POST',credentials:'same-origin'});
show('eis223-add-status',r.ok?'Закупка сохранена':'Не удалось сохранить');
if(r.ok){form.reset();await refresh()}
}catch{show('eis223-add-status','Ошибка соединения')}finally{btn.disabled=false}
});
q('eis223-new-batch').addEventListener('click',async()=>{
const b=q('eis223-new-batch');b.disabled=true;
try{const r=await fetch('/me/thematic-batches',{method:'POST',credentials:'same-origin'});
show('eis223-batch-status',r.ok?'Очередь создана. Откройте «Мои тематические проверки».':'Не удалось создать очередь');
if(r.ok)await refresh();
}catch{show('eis223-batch-status','Ошибка соединения')}finally{b.disabled=false}
});
refresh();
})();
</script>
''').encode("utf-8")


_GATEWAY_STYLE = """
<style>
*{box-sizing:border-box}html,body{margin:0;min-height:100%;font-family:system-ui,-apple-system,Arial,sans-serif}
body{background:linear-gradient(145deg,#102947,#1e538a);color:#19324e}
.layout{min-height:100vh;display:flex;justify-content:center;align-items:center;padding:24px}
.auth{background:white;width:min(480px,100%);border-radius:18px;padding:clamp(23px,5vw,36px);
box-shadow:0 18px 65px #08152552}
h1{font-size:26px;line-height:1.2;margin:0 0 9px}
p{color:#52677c;line-height:1.5;font-size:14px}
.tabs{display:flex;margin:24px 0 16px;border-radius:10px;background:#edf3fa;padding:4px}
.tabs button{border:0;background:transparent;border-radius:8px;padding:12px;flex:1;
font:600 15px system-ui;color:#35536e;cursor:pointer}
.tabs button[aria-selected=true]{background:white;color:#17589b;box-shadow:0 2px 8px #132c411c}
form[hidden]{display:none}
label{display:block;margin:15px 0 6px;font-weight:600;font-size:14px}
input{display:block;width:100%;border:1px solid #becdde;border-radius:9px;
padding:12px;font:inherit;background:white}
input:focus{outline:2px solid #7daada;outline-offset:1px}
.submit{width:100%;padding:13px;border:0;border-radius:9px;background:#1b5c9e;
color:#fff;font:600 16px system-ui;margin-top:20px;cursor:pointer}
.submit:disabled{opacity:.55}
#auth-status{min-height:26px;font-size:14px;color:#365b7c}
.small{font-size:12px;text-align:center;color:#6a7d91}
</style>
"""


def login_portal(initial_tab="login"):
    """The full-screen gateway contains no procurement dashboard data."""
    initial = "register" if initial_tab == "register" else "login"
    markup = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход и регистрация · ЕИС Аудитор</title>''' + _GATEWAY_STYLE + '''
</head><body><main class="layout"><section class="auth" role="dialog" aria-label="Вход на сайт">
<h1>ЕИС Аудитор 223-ФЗ</h1>
<p>Для просмотра закупок и результатов проверок войдите в аккаунт.
Регистрация доступна по заявке с подтверждением администратора.</p>
<div class="tabs" role="tablist" aria-label="Способ входа">
<button type="button" role="tab" id="tab-login" aria-selected="false">Войти</button>
<button type="button" role="tab" id="tab-register" aria-selected="false">Регистрация</button>
</div>
<form id="login-form" autocomplete="on">
<label for="login-email">Электронная почта</label>
<input id="login-email" name="email" type="email" autocomplete="email" required maxlength="254">
<label for="login-password">Пароль</label>
<input id="login-password" name="password" type="password" autocomplete="current-password" required>
<button class="submit" type="submit">Войти на сайт</button>
</form>
<form id="registration-form" autocomplete="on">
<label for="register-email">Электронная почта</label>
<input id="register-email" name="email" type="email" autocomplete="email" required maxlength="254">
<label for="register-password">Пароль (не менее 12 символов)</label>
<input id="register-password" name="password" type="password"
autocomplete="new-password" minlength="12" required>
<button class="submit" type="submit">Отправить заявку</button>
</form>
<p id="auth-status" role="status" aria-live="polite"></p>
<p class="small">После подтверждения заявки вы сможете войти и пользоваться общей проверкой и своим кабинетом.</p>
</section></main>
<script>
(()=>{
const forms={login:document.getElementById('login-form'),
register:document.getElementById('registration-form')};
const status=document.getElementById('auth-status');
function select(tab,updateUrl){
for(const type of ['login','register']){
 forms[type].hidden=type!==tab;
 document.getElementById('tab-'+type).setAttribute('aria-selected',String(type===tab));
}
status.textContent='';
if(updateUrl)history.replaceState(null,'',tab==='login'?'/account/login':'/account/register');
}
for(const tab of ['login','register']){
document.getElementById('tab-'+tab).addEventListener('click',()=>select(tab,true));
}
for(const [tab,path] of [['login','/auth/login'],['register','/auth/register']]){
forms[tab].addEventListener('submit',async event=>{
event.preventDefault();
const form=event.currentTarget,button=form.querySelector('button[type=submit]');
button.disabled=true;status.textContent=tab==='login'?'Выполняется вход…':'Отправляем заявку…';
try{
const response=await fetch(path,{method:'POST',credentials:'same-origin',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(Object.fromEntries(new FormData(form)))});
if(response.ok&&tab==='login'){location.replace('/');return}
if(response.ok){status.textContent='Заявка принята. Дождитесь одобрения администратора.';form.reset();return}
const errors={401:'Неверный пароль или учётная запись не подтверждена.',
403:'Доступ запрещён.',422:'Проверьте адрес почты и пароль.',429:'Слишком много попыток. Повторите позже.'};
status.textContent=errors[response.status]||'Не удалось выполнить действие. Попробуйте позже.';
}catch{status.textContent='Ошибка соединения с сайтом.'}
finally{button.disabled=false}
});
}
select('__INITIAL__',false);
// A cross-site link may initially omit the Strict cookie. This same-origin
// request restores an existing session without asking for credentials again.
fetch('/auth/me',{credentials:'same-origin',cache:'no-store'}).then(response=>{
if(response.ok)location.replace('/');
}).catch(()=>{});
})();
</script></body></html>'''
    return markup.replace("__INITIAL__", escape(initial))
