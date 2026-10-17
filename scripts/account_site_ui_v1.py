"""Same-domain sign-in gateway and immediately visible personal workspace.

Pure HTML builders: no private user data embedded in shared-page markup.
Private data is fetched from owner-scoped APIs only after authentication.
"""
from html import escape


_STYLE = """
<style id="eis223-ui-refresh">
:root{--acct-ink:#17342d;--acct-muted:#698077;--acct-main:#087e71;--acct-line:#dfeae4}
.eis223-workspace{position:relative;z-index:5;color:var(--acct-ink);
 background:#fff;border-bottom:1px solid var(--acct-line);padding:12px clamp(14px,3vw,45px);
 font:14px/1.55 Inter,"Segoe UI",system-ui,-apple-system,sans-serif;box-sizing:border-box}
.eis223-workspace *{box-sizing:border-box}
.eis223-menu{max-width:1560px;margin:auto;display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.eis223-brand{display:flex;align-items:center;gap:10px;color:#24453b;font-size:16px;
 font-weight:800;letter-spacing:-.025em;margin-right:auto}
.eis223-brand-icon{height:37px;width:37px;display:grid;place-items:center;
 color:white;border-radius:12px;background:linear-gradient(155deg,#0b9e89,#075b56);
 box-shadow:0 5px 12px #07877924;font-weight:900;font-size:18px}
.eis223-workspace .eis223-menu button,.eis223-workspace .eis223-menu a{
 font:650 13px system-ui,sans-serif;min-height:42px;padding:9px 15px;
 border:1px solid #d5e4dc!important;border-radius:10px!important;
 background:#fff!important;color:#2b675b!important;cursor:pointer;
 display:inline-flex;justify-content:center;align-items:center;box-shadow:none!important}
.eis223-workspace .eis223-menu button:hover{background:#eff8f3!important}
.eis223-workspace .eis223-menu #eis223-toggle-cabinet{background:#eaf6f0!important;
 color:#086e60!important;border-color:#c7e6d9!important}
.eis223-workspace .eis223-menu #eis223-toggle-cabinet[aria-expanded=true]{
 background:#087e71!important;color:white!important;border-color:#087e71!important}
.eis223-workspace .eis223-menu #eis223-logout{background:#fff!important;color:#60776d!important;
 border-color:transparent!important}
.eis223-personal[hidden]{display:none!important}
.eis223-personal{max-width:1560px;margin:13px auto 4px;padding:clamp(18px,2.8vw,30px);
 background:linear-gradient(125deg,#fff,#f9fcfa);border:1px solid #dce9e3;border-radius:19px;
 box-shadow:0 12px 38px #153b2d10}
.eis223-kicker{color:#0f8576;font-size:11px;font-weight:800;letter-spacing:.13em;
 margin:0 0 5px;text-transform:uppercase}
.eis223-personal h2{font-size:clamp(23px,2.4vw,29px);font-weight:800;letter-spacing:-.04em;
 color:#22473c!important;margin:0 0 6px}
.eis223-personal .eis223-subtle{font-size:13px;color:#667b72;line-height:1.65;margin:0 0 17px}
.eis223-columns{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.eis223-tile{min-width:0;padding:18px;background:#fff;border:1px solid #e0ebe5;
 border-radius:15px;box-shadow:0 4px 12px #183a2c07}
.eis223-tile h3{font-size:16px!important;font-weight:780!important;letter-spacing:-.02em;
 color:#26463d!important;margin:0 0 14px}
.eis223-tile ul{list-style:none;margin:0 0 13px;padding:0;max-height:245px;overflow-y:auto}
.eis223-tile li{display:flex;gap:9px;align-items:center;flex-wrap:wrap;
 background:#f8fbf9;border:1px solid #e6eee9;padding:10px 11px;margin:7px 0;
 border-radius:10px;overflow-wrap:anywhere;line-height:1.4}
.eis223-tile li a{font-weight:650;color:#0b786a}
.eis223-check-desc{flex:1 1 150px;min-width:0;display:grid;gap:3px}
.eis223-check-desc strong{color:#23493d;font-size:13px}
.eis223-check-desc small{font-size:12px;color:#6b7e75;line-height:1.5}
.eis223-workspace a.eis223-all-checks{color:#087b6d;font-weight:750;
 text-decoration:none;display:inline-block;padding:9px 1px}
.eis223-workspace a.eis223-all-checks:hover{text-decoration:underline}
.eis223-tile form{display:flex;flex-wrap:wrap;gap:9px;align-items:stretch}
.eis223-workspace .eis223-tile input{flex:1;min-width:125px;font:inherit;
 border:1px solid #d2e0d9!important;color:#263e37!important;
 border-radius:10px!important;background:#fff!important;padding:10px 12px!important;min-height:43px}
.eis223-workspace .eis223-tile button{cursor:pointer;min-height:40px;
 background:#087e71!important;color:#fff!important;border:1px solid #087e71!important;
 border-radius:10px!important;padding:9px 13px!important;font-weight:700!important}
.eis223-workspace .eis223-tile button:hover:not(:disabled){background:#04695e!important}
.eis223-workspace .eis223-tile button:disabled{opacity:.6}
.eis223-tile li button{margin-left:auto;background:#fff!important;color:#995058!important;
 border-color:#eed8da!important}
.eis223-tile li button:hover:not(:disabled){background:#fff1f2!important}
.eis223-workspace [role=status]{font-size:12px;color:#677e74;margin:8px 0}
.eis223-workspace :is(button,input,a):focus-visible{outline:3px solid #99decd!important;outline-offset:2px}
@media(max-width:1020px){.eis223-columns{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:670px){
 .eis223-workspace{padding:11px 13px}
 .eis223-menu{gap:7px}
 .eis223-brand{flex-basis:100%;font-size:15px}
 .eis223-workspace .eis223-menu button{flex:1}
 .eis223-personal{padding:16px;border-radius:15px;margin-top:10px}
 .eis223-columns{grid-template-columns:minmax(0,1fr);gap:11px}
 .eis223-tile{padding:15px}
}
@media(prefers-reduced-motion:reduce){.eis223-workspace *{transition:none!important}}
</style>
"""


def site_header(role="user"):
    admin = '<a href="/account/admin">Заявки пользователей</a>' if role == "admin" else ""
    return (_STYLE + '''
<div class="eis223-workspace" data-eis223-account-nav="1">
<nav class="eis223-menu" aria-label="Навигация ЕИС Аудитор">
<span class="eis223-brand"><span class="eis223-brand-icon" aria-hidden="true">Е</span>ЕИС Аудитор</span>
<button type="button" id="eis223-toggle-cabinet" aria-controls="eis223-cabinet" aria-expanded="false">Личный кабинет</button>
''' + admin + '''
<button type="button" id="eis223-logout">Выйти</button>
<span id="eis223-logout-status" role="status"></span>
</nav>
<section class="eis223-personal" id="eis223-cabinet" aria-label="Мой личный кабинет" hidden>
<p class="eis223-kicker">Персональное пространство</p>
<h2>Личный кабинет</h2>
<p class="eis223-subtle">Личные закупки, запущенные вами тематические проверки и служебные записки — в одном месте.</p>
<p id="eis223-personal-summary" role="status">Загрузка данных…</p>
<div class="eis223-columns">
<div class="eis223-tile"><h3>Сохранённые закупки</h3>
<ul id="eis223-my-procurements"><li>Загрузка…</li></ul>
<form id="eis223-add-procurement">
<input aria-label="Номер ЕИС" placeholder="Номер закупки, 11 цифр"
name="reg" required pattern="3[0-9]{10}" maxlength="11" autocomplete="off">
<button type="submit">Добавить</button></form>
<p role="status" id="eis223-add-status"></p>
</div>
<div class="eis223-tile"><h3>Мои тематические проверки</h3>
<ul id="eis223-my-batches"><li>Загрузка…</li></ul>
<a class="eis223-all-checks" href="/account/thematic">Посмотреть все мои проверки →</a>
<p role="status" id="eis223-batch-status"></p>
</div>
<div class="eis223-tile"><h3>Служебные записки</h3>
<ul id="eis223-my-memos"><li>Загрузка…</li></ul>
<a class="eis223-all-checks" href="/account/memos">Посмотреть служебные записки →</a>
<p role="status" id="eis223-memo-status"></p>
</div>
</div></section></div>
<script>
(()=>{
const q=id=>document.getElementById(id);
q('eis223-toggle-cabinet').addEventListener('click',()=>{const panel=q('eis223-cabinet');panel.hidden=!panel.hidden;q('eis223-toggle-cabinet').setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden)refresh();});
const show=(id,msg)=>{const el=q(id);if(el)el.textContent=msg;};
async function get(url){
const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});
if(r.status===401){location.replace('/account/login');throw Error('Требуется вход')}
if(!r.ok)throw Error('Ошибка загрузки');return await r.json();
}
async function refresh(){
try{
const [summary,purchases,batches,memos]=await Promise.all([
get('/me/summary'),get('/me/procurements'),get('/api/thematic/history'),get('/me/memos')]);
const realBatches=batches.batches||[];
show('eis223-personal-summary','Сохранённые закупки: '+summary.procurements+
' · Тематические проверки: '+realBatches.length+' · Служебные записки: '+summary.memos);
const pl=q('eis223-my-procurements');pl.replaceChildren();
if(!purchases.length){const li=document.createElement('li');li.textContent='Пока нет закупок';pl.append(li)}
for(const item of purchases.slice(0,10)){
 const li=document.createElement('li'),a=document.createElement('a');
 a.href='/';a.textContent=item.reg_number;li.append(a);pl.append(li);
}
const bl=q('eis223-my-batches');bl.replaceChildren();
if(!realBatches.length){const li=document.createElement('li');li.textContent='Запущенных вами тематических проверок пока нет';bl.append(li)}
const categoryNames={dates:'Сроки',docs:'Документация',national:'Национальный режим',
security:'Обеспечение',sme:'МСП',access:'Участники',protocols:'Протоколы',
contract:'Договоры',notices:'Извещения',evaluation:'Оценка заявок'};
for(const item of realBatches){
 const li=document.createElement('li');
 const summary=document.createElement('div');summary.className='eis223-check-desc';
 const title=document.createElement('strong');
 const cats=(item.categories||[]).map(code=>categoryNames[code]||code);
 title.textContent=cats.length?cats.slice(0,3).join(' · '):'Тематическая проверка';
 const meta=document.createElement('small');
 const date=Date.parse(item.created_at);
 meta.textContent=(Number.isFinite(date)?new Date(date).toLocaleDateString('ru-RU')+' · ':'')+
 'Закупок: '+Number(item.purchases||0)+' · Нарушений: '+Number(item.violations||0)+
 (item.status==='active'?' · Выполняется':' · Остановлена');
 summary.append(title,meta);li.append(summary);
 const details=document.createElement('a');details.href='/thematic#'+encodeURIComponent(item.id);
 details.textContent='Открыть';li.append(details);
 const del=document.createElement('button');del.type='button';del.textContent='Удалить';
 del.onclick=async()=>{
 if(!confirm('Удалить выбранную тематическую проверку и её тематические результаты? Действие нельзя отменить.'))return;
 del.disabled=true;
 try{
  const resp=await fetch('/api/thematic/'+encodeURIComponent(item.id),{method:'DELETE',credentials:'same-origin'});
  if(!resp.ok)throw Error();await refresh();
 }catch{del.disabled=false;show('eis223-batch-status','Не удалось удалить проверку')}
 };
 li.append(del);bl.append(li);
}
const ml=q('eis223-my-memos');ml.replaceChildren();
if(!memos.length){const li=document.createElement('li');li.textContent='Служебных записок пока нет';ml.append(li)}
for(const memo of memos.slice(0,7)){
 const li=document.createElement('li');const link=document.createElement('a');
 link.href='/account/memos';link.textContent='Закупка № '+memo.reg_number;
 li.append(link);ml.append(li);
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
