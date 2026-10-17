"""Same-domain sign-in gateway and immediately visible personal workspace.

Pure HTML builders: no private user data embedded in shared-page markup.
Private data is fetched from owner-scoped APIs only after authentication.
"""
from html import escape


_STYLE = """
<style id="eis223-ui-refresh">
.eis223-workspace{
 position:relative;z-index:6;background:#fff;color:#24435a;
 font:14px/1.5 Inter,"Segoe UI",system-ui,-apple-system,Arial,sans-serif;
 box-sizing:border-box;border-bottom:1px solid #dae5ed;
 box-shadow:0 2px 8px #163f5a08}
.eis223-workspace *{box-sizing:border-box}
.eis223-menu{
 background:#174c76;padding:10px clamp(13px,3vw,45px);
 display:flex;align-items:center;gap:9px;flex-wrap:wrap;
 min-height:65px}
.eis223-brand{
 color:#fff!important;display:inline-flex;align-items:center;gap:11px;
 margin-right:auto;min-width:170px}
.eis223-brand-icon{
 width:43px;height:43px;flex:none;display:grid;place-items:center;
 background:#ffffff16;border:1px solid #ffffff50;border-radius:9px;
 color:#fff;font-size:14px;font-weight:850;letter-spacing:-.04em}
.eis223-brand-copy{display:flex;flex-direction:column;gap:1px}
.eis223-brand-name{font-size:18px;font-weight:780;letter-spacing:-.03em;line-height:1.15}
.eis223-brand-sub{font-size:10px;color:#c6dfed;letter-spacing:.025em}
.eis223-menu button,.eis223-menu a{
 display:inline-flex;align-items:center;justify-content:center;
 cursor:pointer;min-height:40px;font:670 13px system-ui,sans-serif;
 border-radius:8px!important;padding:9px 14px!important;
 text-decoration:none;transition:background .15s,border-color .15s}
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet{
 color:#184b70!important;background:#fff!important;border:1px solid #fff!important;
 box-shadow:none!important}
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet[aria-expanded=true]{
 color:#fff!important;background:#126594!important;border:1px solid #86b9dc!important}
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet:hover{background:#eaf4fb!important}
.eis223-workspace .eis223-menu button#eis223-logout{
 color:#e5f1fa!important;background:#ffffff12!important;
 border:1px solid #ffffff47!important;box-shadow:none!important}
.eis223-workspace .eis223-menu button#eis223-logout:hover{background:#ffffff26!important}
.eis223-workspace .eis223-menu a{color:#f4faff!important;border:1px solid #ffffff30}
.eis223-workspace [role=status]{color:#527087;font-size:12px}
#eis223-logout-status:not(:empty){color:#fff}
.eis223-personal[hidden]{display:none!important}
.eis223-personal{
 background:#f5f8fb;max-width:none;width:100%;margin:0;
 padding:clamp(16px,3vw,32px) clamp(12px,3vw,44px) clamp(18px,3vw,30px);
 border:0;border-bottom:1px solid #d8e3ec;box-shadow:inset 0 1px #fff}
.eis223-cabinet-heading{display:flex;gap:12px;justify-content:space-between;
 align-items:flex-start;flex-wrap:wrap;margin-bottom:13px}
.eis223-personal h2{
 font-weight:780!important;color:#204d71!important;letter-spacing:-.035em;
 font-size:clamp(23px,2.4vw,30px)!important;margin:0 0 4px}
.eis223-kicker{font-size:10px;font-weight:780;letter-spacing:.13em;
 text-transform:uppercase;color:#397cad;margin:0 0 4px}
.eis223-personal .eis223-subtle{
 margin:0;color:#6a7e8e;font-size:13px;line-height:1.6}
.eis223-panel-mark{display:inline-flex;align-items:center;gap:6px;
 color:#637f92;background:#e9f2f8;border:1px solid #d6e6f1;
 border-radius:8px;padding:7px 11px;font-size:11px}
.eis223-panel-mark:before{content:"";width:6px;height:6px;border-radius:50%;background:#4789b3}
.eis223-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
 gap:10px;margin:15px 0 16px}
.eis223-overview>span{padding:12px 14px;background:#fff;border:1px solid #dae6ee;
 border-radius:10px;box-shadow:0 2px 8px #1a4a6906;min-width:0}
.eis223-overview b{display:block;color:#1a527a;line-height:1.2;
 font-size:23px;font-weight:790;font-variant-numeric:tabular-nums}
.eis223-overview small{display:block;color:#718493;font-size:11px;margin-top:4px}
.eis223-columns{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
 align-items:start;gap:12px}
.eis223-tile{
 background:#fff;border:1px solid #dce6ee;border-radius:11px;
 padding:18px;min-width:0;box-shadow:0 4px 15px #19446309}
.eis223-tile-head{display:flex;gap:10px;align-items:flex-start;margin:0 0 14px}
.eis223-tile-number{flex:none;width:34px;height:34px;display:grid;place-items:center;
 border:1px solid #d6e6f2;border-radius:9px;background:#edf5fa;
 color:#2c6b99;font-size:12px;font-weight:800}
.eis223-tile h3{margin:0 0 4px;color:#234e72!important;
 font-size:15px!important;font-weight:760!important}
.eis223-tile-sub{margin:0;font-size:11px;color:#718798;line-height:1.5}
.eis223-tile ul{
 margin:0 0 11px;padding:0;list-style:none;min-height:55px;
 max-height:240px;overflow-y:auto}
.eis223-tile li{
 display:flex;align-items:center;flex-wrap:wrap;gap:8px;
 border-bottom:1px solid #ebf0f4;padding:10px 3px;margin:0;
 font-size:12px;overflow-wrap:anywhere}
.eis223-tile li:last-child{border-bottom:0}
.eis223-tile li a{color:#195f93!important;text-decoration:none;font-weight:690}
.eis223-tile li a:hover{text-decoration:underline}
.eis223-check-desc{flex:1 1 155px;min-width:0;display:grid;gap:4px}
.eis223-check-desc strong{font-size:12px;color:#214b6d}
.eis223-check-desc small{font-size:11px;color:#728796;line-height:1.5}
.eis223-workspace a.eis223-all-checks{
 display:inline-flex;gap:6px;align-items:center;font-size:12px;font-weight:750;
 color:#185d91;text-decoration:none;padding:10px 0 3px}
.eis223-workspace a.eis223-all-checks:hover{text-decoration:underline}
.eis223-tile form{display:flex;flex-wrap:wrap;gap:7px}
.eis223-workspace .eis223-tile input{
 background:#fff!important;color:#244458!important;flex:1;min-width:125px;
 min-height:42px;font-size:13px!important;padding:9px 11px!important;
 border:1px solid #cddce6!important;border-radius:8px!important}
.eis223-workspace .eis223-tile button{
 color:#fff!important;background:#165b8b!important;border:1px solid #165b8b!important;
 min-height:40px;border-radius:8px!important;cursor:pointer;
 padding:8px 11px!important;font-size:12px!important;font-weight:690!important}
.eis223-workspace .eis223-tile button:hover:not(:disabled){background:#114870!important}
.eis223-workspace .eis223-tile button:disabled{opacity:.6;cursor:wait}
.eis223-workspace .eis223-tile li button{
 margin-left:auto;background:#fff!important;color:#995052!important;
 border:1px solid #e6cccf!important}
.eis223-workspace .eis223-tile li button:hover{background:#fff3f3!important}
.eis223-more{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:13px}
.eis223-feature{background:#fff;border:1px solid #dce6ee;border-radius:11px;
 box-shadow:0 4px 15px #19446308;padding:18px;min-width:0}
.eis223-feature h3{font-size:15px;color:#234e72;margin:0 0 5px}
.eis223-feature p{font-size:12px;color:#718696;margin:4px 0 13px;line-height:1.5}
.eis223-feature ul{padding:0;margin:0;list-style:none;max-height:190px;overflow-y:auto}
.eis223-feature li{padding:9px 5px;border-bottom:1px solid #e8eef4;
 display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.eis223-feature li:last-child{border-bottom:0}
.eis223-feature li strong{display:block;font-size:12px;color:#254e70}
.eis223-feature li small{display:block;font-size:11px;color:#708597}
.eis223-feature li button{border:1px solid #cfdeec!important;color:#245d88!important;
 background:#f1f7fc!important;min-height:31px;font-size:11px;border-radius:7px;padding:6px 10px;
 cursor:pointer}
.eis223-feature form{display:grid;grid-template-columns:110px minmax(0,1fr);
 gap:8px;margin-bottom:12px}
.eis223-feature input,.eis223-feature select{padding:10px;min-height:40px;border:1px solid #cfdfe9;
 border-radius:8px;background:#fff;color:#233f52;min-width:0}
.eis223-feature form button{grid-column:1/-1;min-height:39px;border:1px solid #17618f;
 background:#17618f;color:white;border-radius:8px;cursor:pointer;font-weight:690}
.eis223-notification-dot[hidden]{display:none!important}
.eis223-notification-dot{display:inline-grid;min-width:20px;min-height:20px;
 padding:2px 4px;place-items:center;border-radius:999px;background:#b94645;color:#fff;
 font-size:11px;font-weight:760;margin-left:7px}
.eis223-feature .eis223-note{padding:7px 10px;border-radius:7px;
 background:#f6fafc;border:1px solid #e4edf2}
@media(max-width:740px){.eis223-more{grid-template-columns:minmax(0,1fr)}}
@media(max-width:390px){.eis223-feature form{grid-template-columns:minmax(0,1fr)}}
.eis223-workspace :is(button,a,input):focus-visible{
 outline:3px solid #86bfdf!important;outline-offset:2px}
.eis223-tile li.eis223-empty{background:#f7fafc;color:#6c8192;
 border:1px dashed #d2e2ec;border-radius:8px;padding:15px 12px}
.eis223-personal .eis223-tile{transition:box-shadow .15s,border-color .15s}
.eis223-personal .eis223-tile:hover{border-color:#c5dbea;box-shadow:0 8px 22px #204e7010}
.eis223-personal .eis223-tile li a{white-space:nowrap;font-size:12px}
.eis223-personal .eis223-tile li .eis223-check-desc{gap:5px}
.eis223-personal .eis223-tile-head{padding-bottom:9px;border-bottom:1px solid #edf2f5}
.eis223-workspace .eis223-tile input{max-width:100%}
@media(max-width:1100px){.eis223-columns{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:660px){
 .eis223-menu{padding:9px 12px;gap:7px}
 .eis223-brand{width:100%;margin-bottom:4px}
 .eis223-brand-icon{height:36px;width:36px}
 .eis223-brand-name{font-size:16px}
 .eis223-brand-sub{font-size:10px}
 .eis223-menu button{flex:1}
 .eis223-personal{padding:16px 11px}
 .eis223-panel-mark{display:none}
 .eis223-overview{gap:7px}
 .eis223-overview>span{padding:10px 9px}
 .eis223-overview b{font-size:20px}
 .eis223-overview small{font-size:10px}
 .eis223-columns{grid-template-columns:minmax(0,1fr);gap:10px}
 .eis223-tile{padding:14px}
}
@media(max-width:375px){
 .eis223-overview{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(prefers-reduced-motion:reduce){.eis223-workspace *{transition:none!important}}
</style>
"""


def site_header(role="user"):
    admin = '<a href="/account/admin">Заявки пользователей</a>' if role == "admin" else ""
    director = ('<a class="eis223-manager-link" href="/account/manager">Кабинет руководителя</a>'
                if role in ("manager", "admin") else "")
    return (_STYLE + '''
<div class="eis223-workspace" data-eis223-account-nav="1">
<nav class="eis223-menu" aria-label="Навигация ЕИС Аудитор">
<span class="eis223-brand"><span class="eis223-brand-icon" aria-hidden="true">223</span><span class="eis223-brand-copy"><span class="eis223-brand-name">ЕИС Аудитор</span><span class="eis223-brand-sub">Независимая аналитическая система</span></span></span>
<button type="button" id="eis223-toggle-cabinet" aria-controls="eis223-cabinet" aria-expanded="false">Личный кабинет <span id="eis223-unread" class="eis223-notification-dot" hidden aria-label="Новые уведомления"></span></button>
''' + admin + director + '''
<button type="button" id="eis223-logout">Выйти</button>
<span id="eis223-logout-status" role="status"></span>
</nav>
<section class="eis223-personal" id="eis223-cabinet" aria-label="Мой личный кабинет" hidden>
<div class="eis223-cabinet-heading">
<div><p class="eis223-kicker">Персональное пространство</p>
<h2>Личный кабинет</h2>
<p class="eis223-subtle" id="eis223-owner-fullname">Фамилия, имя, отчество: загрузка…</p>
<a class="eis223-all-checks" href="/account/profile">Редактировать ФИО →</a>
<p class="eis223-subtle">Самостоятельные и тематические проверки, документы и результаты в одном месте</p></div>
<span class="eis223-panel-mark">Доступно только вашей учётной записи</span></div>
<div id="eis223-personal-summary" class="eis223-overview" role="status" aria-live="polite">
<span>Загрузка сведений…</span></div>
<div class="eis223-columns">
<div class="eis223-tile"><div class="eis223-tile-head"><span class="eis223-tile-number" aria-hidden="true">01</span>
<div><h3>Самостоятельные проверки</h3><p class="eis223-tile-sub">Закупки, которые вы запустили на проверку</p></div></div>
<ul id="eis223-my-procurements" aria-label="Мои самостоятельные проверки"><li>Загрузка…</li></ul>
<a class="eis223-all-checks" href="/account/home">Все самостоятельные проверки →</a>
<a class="eis223-all-checks" href="/account/workbench">Рабочее место сотрудника · 30 инструментов →</a>
<form id="eis223-add-procurement">
<input aria-label="Номер ЕИС" placeholder="Номер закупки, 11 цифр"
name="reg" required pattern="3[0-9]{10}" maxlength="11" autocomplete="off">
<button type="submit">Запустить проверку</button></form>
<p role="status" id="eis223-add-status"></p>
</div>
<div class="eis223-tile"><div class="eis223-tile-head"><span class="eis223-tile-number" aria-hidden="true">02</span>
<div><h3>Тематические проверки</h3><p class="eis223-tile-sub">Запущенные под вашей учётной записью</p></div></div>
<ul id="eis223-my-batches"><li>Загрузка…</li></ul>
<a class="eis223-all-checks" href="/account/thematic">Посмотреть все мои проверки →</a>
<p role="status" id="eis223-batch-status"></p>
</div>
<div class="eis223-tile"><div class="eis223-tile-head"><span class="eis223-tile-number" aria-hidden="true">03</span>
<div><h3>Служебные записки</h3><p class="eis223-tile-sub">Сохранённые документы и проекты</p></div></div>
<ul id="eis223-my-memos"><li>Загрузка…</li></ul>
<a class="eis223-all-checks" href="/account/memos">Посмотреть служебные записки →</a>
<p role="status" id="eis223-memo-status"></p>
</div>
</div>
<div class="eis223-more">
<section class="eis223-feature" aria-label="Уведомления по проверкам">
<h3>Уведомления <span id="eis223-alert-count" class="eis223-notification-dot" hidden></span></h3>
<p>Новые подтверждённые нарушения и завершение обработки документов. Обновляются при открытии сайта.</p>
<ul id="eis223-alert-list"><li>Загрузка уведомлений…</li></ul>
<p class="eis223-note" role="status" id="eis223-alert-status"></p>
</section>
<section class="eis223-feature" aria-label="Избранные закупки и заказчики">
<h3>Избранное</h3>
<p>Сохраните номер закупки или ИНН заказчика для быстрого доступа.</p>
<form id="eis223-favorite-form">
<select name="kind" aria-label="Тип избранного"><option value="procurement">Закупка</option><option value="customer">Заказчик</option></select>
<input name="key" required inputmode="numeric" placeholder="Номер ЕИС или ИНН" aria-label="Номер ЕИС или ИНН">
<input name="label" style="grid-column:1/-1" maxlength="130" placeholder="Название (необязательно)" aria-label="Название">
<button type="submit">Добавить в избранное</button></form>
<ul id="eis223-favorite-list"><li>Загрузка избранного…</li></ul>
<p role="status" id="eis223-favorite-status"></p>
</section>
<section class="eis223-feature" aria-label="Обычные поручения">
<h3>Поручения руководителя</h3>
<p>Только отдельные задания по конкретным закупкам и сроки их выполнения.</p>
<ul id="eis223-assignment-list"><li>Загрузка поручений…</li></ul>
<p id="eis223-assignment-status" role="status"></p>
</section>
<section class="eis223-feature" aria-label="Нарушения тематических проверок">
<h3>Нарушения тематических проверок</h3>
<p>Обычные карточки нарушений с правовой нормой, доказательством и оригиналом ЕИС. План руководителя не заменяет фактические результаты.</p>
<ul id="eis223-manager-goals"><li>Загрузка нарушений…</li></ul>
<a class="eis223-all-checks" href="/account/workbench">Все нарушения и тематические проверки →</a>
<p id="eis223-thematic-status" role="status"></p>
</section>
</div></section></div>
<script>
(()=>{
const q=id=>document.getElementById(id);
q('eis223-toggle-cabinet').addEventListener('click',()=>{
const panel=q('eis223-cabinet');panel.hidden=!panel.hidden;
q('eis223-toggle-cabinet').setAttribute('aria-expanded',String(!panel.hidden));
if(!panel.hidden){refresh();refreshAlerts();refreshFavorites();refreshAssignments();refreshCampaignGoals();refreshMyName()}
});
const show=(id,msg)=>{const el=q(id);if(el)el.textContent=msg;};
async function get(url){
const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});
if(r.status===401){location.replace('/account/login');throw Error('Требуется вход')}
if(!r.ok)throw Error('Ошибка загрузки');return await r.json();
}
async function refresh(){
try{
const [sumResponse,manualResponse,batchResponse,memoResponse]=await Promise.allSettled([
get('/me/summary'),get('/me/manual-checks'),get('/api/thematic/history'),get('/me/memo-archive')]);
const summary=sumResponse.status==='fulfilled'?sumResponse.value:{};
const purchases=manualResponse.status==='fulfilled'&&Array.isArray(manualResponse.value)?
 manualResponse.value:[];
const batches=batchResponse.status==='fulfilled'?batchResponse.value:{batches:[]};
const memos=memoResponse.status==='fulfilled'&&Array.isArray(memoResponse.value)?
 memoResponse.value:[];
const realBatches=Array.isArray(batches.batches)?batches.batches:[];
const overview=q('eis223-personal-summary');overview.replaceChildren();
const counters=[
 ['Самостоятельные',sumResponse.status==='fulfilled'?
  summary.manual_checks:(manualResponse.status==='fulfilled'?purchases.length:undefined)],
 ['Тематические проверки',batchResponse.status==='fulfilled'?realBatches.length:undefined],
 ['Служебные записки',sumResponse.status==='fulfilled'?
  summary.memos:(memoResponse.status==='fulfilled'?memos.length:undefined)]];
for(const [label,value] of counters){
 const chip=document.createElement('span'),number=document.createElement('b');
 const caption=document.createElement('small');
 const numberValue=Number(value);
 number.textContent=Number.isFinite(numberValue)&&numberValue>=0?String(numberValue):'—';
 caption.textContent=label;chip.append(number,caption);overview.append(chip);
}
const pl=q('eis223-my-procurements');pl.replaceChildren();
if(!purchases.length){
 const li=document.createElement('li');li.className='eis223-empty';
 li.textContent=manualResponse.status==='fulfilled'?
 'Пока нет самостоятельных проверок. Запустите проверку по номеру ЕИС.':
 'Список самостоятельных проверок временно недоступен. Попробуйте обновить.';
 pl.append(li);
}
const statusLabels={queued:'Ожидает загрузки',leased:'Получение данных ЕИС',
 done:'Загрузка завершена',partial:'Частично загружено',deferred:'Ожидает источника',
 error:'Ошибка источника',unknown:'Проверяется статус'};
for(const item of purchases.slice(0,8)){
 const li=document.createElement('li'),body=document.createElement('div');
 body.className='eis223-check-desc';
 const num=document.createElement('strong');num.textContent='№ '+item.reg_number;
 const meta=document.createElement('small');
 const stamp=Date.parse(item.started_at||'');
 const when=Number.isFinite(stamp)?new Date(stamp).toLocaleDateString('ru-RU')+' · ':'';
 const checked=Math.max(0,Number(item.documents_checked||0));
 const found=Math.max(0,Number(item.violations||0));
 meta.textContent=when+(statusLabels[item.job_status]||'Задание ЕИС')+
 ' · Документов: '+checked+' · Нарушений: '+found;
 body.append(num,meta);li.append(body);
 const open=document.createElement('a');open.href=item.card_available?
 '/check-new/'+encodeURIComponent(item.reg_number):'/account/home';
 open.textContent=item.card_available?'Карточка':'Подробности';li.append(open);
 if(item.card_available){const dossier=document.createElement('a');
 dossier.href='/me/cases/'+encodeURIComponent(item.reg_number);
 dossier.textContent='Электронное дело';li.append(dossier);}
 const del=document.createElement('button');del.type='button';del.textContent='Убрать';
 del.title='Убрать из личного кабинета, не удаляя документы и результаты проверки';
 del.onclick=async()=>{
  if(!confirm('Убрать эту самостоятельную проверку из вашего кабинета? Оригиналы ЕИС и результаты не удалятся.'))return;
  del.disabled=true;
  try{
   const response=await fetch('/me/manual-checks/'+encodeURIComponent(item.reg_number),
    {method:'DELETE',credentials:'same-origin'});
   if(!response.ok)throw Error('Ошибка удаления');
   await refresh();
  }catch{del.disabled=false;show('eis223-add-status','Не удалось убрать проверку')}
 };
 li.append(del);pl.append(li);
}
const bl=q('eis223-my-batches');bl.replaceChildren();
if(!realBatches.length){const li=document.createElement('li');
li.textContent=batchResponse.status==='fulfilled'?
 'Запущенных вами тематических проверок пока нет':'Не удалось загрузить тематические проверки';
bl.append(li)}
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
if(!memos.length){const li=document.createElement('li');
li.textContent=memoResponse.status==='fulfilled'?
 'Служебных записок пока нет':'Список служебных записок временно недоступен';
ml.append(li)}
for(const memo of memos.slice(0,7)){
 const li=document.createElement('li');const link=document.createElement('a');
 link.href='/account/memos';link.textContent='Закупка № '+memo.reg_number;
 li.append(link);
 if(memo.downloadable){
  for(const format of ['odt','pdf']){
   const download=document.createElement('a');
   download.href='/me/memo-archive/'+encodeURIComponent(memo.id)+'/'+format;
   download.textContent=format.toUpperCase();li.append(download);
  }
 }else{
  const note=document.createElement('small');note.textContent='Файл не сохранён';li.append(note);
 }
 ml.append(li);
}
}catch{show('eis223-personal-summary','Личные данные временно недоступны. Попробуйте открыть кабинет ещё раз.')}
}

async function refreshAlerts(){
 try{
  const notices=await get('/me/notifications'),ul=q('eis223-alert-list');
  ul.replaceChildren();
  const unread=notices.filter(x=>!x.read).length;
  for(const id of ['eis223-alert-count','eis223-unread']){
   const indicator=q(id);indicator.hidden=!unread;
   indicator.textContent=unread>99?'99+':String(unread);
  }
  if(!notices.length){const li=document.createElement('li');li.textContent='Новых событий пока нет';ul.append(li)}
  for(const item of notices.slice(0,20)){
   const li=document.createElement('li'),body=document.createElement('div');
   body.className='eis223-check-desc';
   const title=document.createElement('strong');title.textContent=item.title;
   const details=document.createElement('small');
   details.textContent=item.detail+' · '+new Date(item.created_at).toLocaleDateString('ru-RU');
   body.append(title,details);li.append(body);
   if(!item.read){
    const ack=document.createElement('button');ack.type='button';ack.textContent='Прочитано';
    ack.onclick=async()=>{
     ack.disabled=true;
     try{
      const response=await fetch('/me/notifications/'+encodeURIComponent(item.id)+'/read',
       {method:'POST',credentials:'same-origin'});
      if(!response.ok)throw Error();await refreshAlerts();
     }catch{ack.disabled=false;show('eis223-alert-status','Не удалось обновить уведомление')}
    };
    li.append(ack);
   }ul.append(li);
  }
  show('eis223-alert-status','Уведомления только для вашей учётной записи');
 }catch{show('eis223-alert-status','Уведомления временно недоступны')}
}
async function refreshFavorites(){
 try{
  const favorites=await get('/me/favorites'),list=q('eis223-favorite-list');
  list.replaceChildren();
  if(!favorites.length){const li=document.createElement('li');li.textContent='Ничего не добавлено';list.append(li)}
  for(const f of favorites){
   const li=document.createElement('li'),body=document.createElement('div');
   body.className='eis223-check-desc';
   const title=document.createElement('strong');
   title.textContent=(f.kind==='customer'?'Заказчик · ИНН ':'Закупка № ')+f.key;
   const detail=document.createElement('small');detail.textContent=f.label||'';
   body.append(title,detail);li.append(body);
   const link=document.createElement('a');
   link.href=f.kind==='procurement'?'/check-new/'+encodeURIComponent(f.key):'/?favorite_inn='+encodeURIComponent(f.key);
   link.textContent=f.kind==='procurement'?'Карточка':'Поиск';
   li.append(link);
   const del=document.createElement('button');del.type='button';del.textContent='Убрать';
   del.onclick=async()=>{
    del.disabled=true;
    try{
     const response=await fetch('/me/favorites/'+encodeURIComponent(f.kind)+'/'+encodeURIComponent(f.key),
       {method:'DELETE',credentials:'same-origin'});
     if(!response.ok)throw Error();await refreshFavorites();
    }catch{del.disabled=false;show('eis223-favorite-status','Не удалось удалить избранное')}
   };
   li.append(del);list.append(li);
  }
 }catch{show('eis223-favorite-status','Избранное временно недоступно')}
}
q('eis223-favorite-form').addEventListener('submit',async event=>{
 event.preventDefault();const form=event.currentTarget;
 const data=new FormData(form),kind=String(data.get('kind')||'');
 const key=String(data.get('key')||'').trim(),label=String(data.get('label')||'').trim();
 const valid=kind==='procurement'?/^3[0-9]{10}$/.test(key):/^[0-9]{10}([0-9]{2})?$/.test(key);
 if(!valid)return show('eis223-favorite-status','Укажите корректный номер ЕИС или ИНН');
 const btn=form.querySelector('button');btn.disabled=true;
 try{
  const url='/me/favorites/'+encodeURIComponent(kind)+'/'+encodeURIComponent(key)+
    '?label='+encodeURIComponent(label);
  const response=await fetch(url,{method:'POST',credentials:'same-origin'});
  if(!response.ok)throw Error();form.reset();await refreshFavorites();
  show('eis223-favorite-status','Добавлено в избранное');
 }catch{show('eis223-favorite-status','Не удалось сохранить избранное')}
 finally{btn.disabled=false}
});
window.setInterval(()=>{if(!document.hidden)refreshAlerts()},90000);
refreshAlerts();

async function refreshMyName(){
 try{
  const person=await get('/me/profile');
  q('eis223-owner-fullname').textContent=person.full_name||'ФИО ещё не указано';
 }catch{q('eis223-owner-fullname').textContent='Данные профиля временно недоступны'}
}
async function refreshCampaignGoals(){
 const list=q('eis223-manager-goals');list.replaceChildren();
 try{
  const data=await get('/me/campaign-goals/violations');
  const findings=Array.isArray(data.findings)?data.findings:[];
  if(!findings.length){
   const li=document.createElement('li');li.className='eis223-empty';
   li.textContent='Подтверждённых анализатором нарушений по тематическим проверкам пока нет';
   list.append(li);return;
  }
  for(const finding of findings.slice(0,8)){
   const li=document.createElement('li'),info=document.createElement('div');
   info.className='eis223-check-desc';
   const title=document.createElement('strong');
   title.textContent='Нарушение · '+(finding.title||'Без названия');
   const meta=document.createElement('small');
   meta.textContent='Закупка № '+finding.reg_number+' · '+
     (finding.law||'Правовая норма требует уточнения');
   const evidence=document.createElement('small');
   evidence.textContent=String(finding.evidence||'Доказательство не найдено').slice(0,320);
   info.append(title,meta,evidence);
   if(finding.needs_review){
    const warning=document.createElement('small');
    warning.textContent='Требует проверки доказательств';
    info.append(warning);
   }
   li.append(info);
   if(finding.source_url&&/^https:\/\/(?:[a-z0-9-]+\.)?zakupki\.gov\.ru\//i.test(finding.source_url)){
    const link=document.createElement('a');link.href=finding.source_url;
    link.textContent='Оригинал ЕИС';link.target='_blank';link.rel='noopener noreferrer';li.append(link);
   }
   if(finding.law_url&&finding.law_url.startsWith('https://www.consultant.ru/')){
    const law=document.createElement('a');law.href=finding.law_url;
    law.textContent='Статья закона';law.target='_blank';law.rel='noopener noreferrer';li.append(law);
   }
   list.append(li);
  }
  if(data.truncated||findings.length>8)show('eis223-thematic-status','Показаны первые 8 нарушений. Полный список — в рабочем месте сотрудника.');
  else show('eis223-thematic-status','Нарушения сформированы по реальным документам ЕИС; одинаковые эпизоды объединены.');
 }catch{
  show('eis223-thematic-status','Нарушения тематических проверок временно недоступны');
 }
}
async function refreshAssignments(){
 try{
  const data=await get('/me/assignments'),ul=q('eis223-assignment-list');
  ul.replaceChildren();
  if(!data.length){const li=document.createElement('li');li.textContent='Новых поручений нет';ul.append(li);return}
  const states={assigned:'Назначено',in_progress:'В работе',submitted:'На согласовании',
                returned:'На доработке',completed:'Завершено',cancelled:'Отменено'};
  for(const item of data.slice(0,12)){
   const li=document.createElement('li'),body=document.createElement('div');
   body.className='eis223-check-desc';
   const title=document.createElement('strong');title.textContent=item.title||'Проверка закупки';
   const desc=document.createElement('small');
   desc.textContent='№ '+item.reg_number+' · '+(states[item.status]||item.status)+
     (item.due_date?' · до '+new Date(item.due_date).toLocaleDateString('ru-RU'):'');
   body.append(title,desc);li.append(body);
   const link=document.createElement('a');link.href='/account/home';
   link.textContent='Проверить';li.append(link);
   const action=item.status==='assigned'||item.status==='returned'?'take':
                item.status==='in_progress'?'submit':null;
   if(action){
    const button=document.createElement('button');button.type='button';
    button.textContent=action==='take'?'Взять':'На согласование';
    button.onclick=async()=>{
     button.disabled=true;
     try{
      const response=await fetch('/me/assignments/'+encodeURIComponent(item.id)+'/'+action,
       {method:'POST',credentials:'same-origin'});
      if(!response.ok){
       const data=await response.json().catch(()=>({}));
       throw Error(data.detail||'Не удалось обновить поручение');
      }
      await refreshAssignments();
      show('eis223-assignment-status','Статус поручения обновлён');
     }catch(e){button.disabled=false;show('eis223-assignment-status',e.message)}
    };
    li.append(button);
   }
   ul.append(li);
  }
 }catch{show('eis223-assignment-status','Поручения временно недоступны')}
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
const r=await fetch('/me/manual-checks/'+encodeURIComponent(reg),
{method:'POST',credentials:'same-origin'});
show('eis223-add-status',r.ok?'Самостоятельная проверка запущена':'Не удалось запустить проверку');
if(r.ok){form.reset();await refresh()}
}catch{show('eis223-add-status','Ошибка соединения')}finally{btn.disabled=false}
});

refresh();
})();
</script>
''').encode("utf-8")


_GATEWAY_STYLE = """
<style id="eis223-signin-style">
:root{font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#2a4356;
 --auth-ink:#213f57;--auth-border:#dbe7ef;--auth-muted:#687e8e}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;font-family:inherit}
body{background:linear-gradient(155deg,#eaf2f8 0%,#f4f7fa 42%,#edf3f8 100%);
 color:var(--auth-ink)}
.layout{min-height:100vh;display:flex;justify-content:center;align-items:center;
 padding:26px 15px}
.auth{position:relative;overflow:hidden;background:#fff;width:min(490px,100%);
 border:1px solid var(--auth-border);border-radius:14px;
 padding:clamp(23px,4vw,36px);box-shadow:0 16px 56px #204e7119}
.auth:before{position:absolute;top:0;left:0;right:0;content:"";height:5px;
 background:linear-gradient(90deg,#174c76,#3483b7,#7fb4d4)}
h1{font-size:clamp(24px,2.8vw,29px);line-height:1.17;
 font-weight:790;letter-spacing:-.035em;color:#204c70;margin:10px 0 11px}
p{font-size:13px;color:var(--auth-muted);line-height:1.65}
.tabs{display:flex;gap:4px;padding:4px;margin:22px 0 19px;
 border-radius:9px;background:#eef4f9}
.tabs button{flex:1;min-height:43px;padding:9px;border:0;
 border-radius:7px;cursor:pointer;font:700 14px system-ui;
 background:transparent;color:#637d92}
.tabs button[aria-selected=true]{background:#fff;color:#175b8b;box-shadow:0 1px 7px #244b6812}
form[hidden]{display:none!important}
label{display:block;margin:15px 0 6px;font-size:13px;color:#425f74;font-weight:670}
input{width:100%;display:block;min-height:45px;border-radius:8px;
 background:#fff;color:#263f53;border:1px solid #cbdce8;
 padding:11px 13px;font:inherit}
input:focus{outline:3px solid #c8e3f6;border-color:#3b80ae}
.submit{min-height:46px;width:100%;border:1px solid #175f92;
 background:#175f92;color:#fff;border-radius:8px;
 font:700 14px system-ui;margin-top:21px;cursor:pointer}
.submit:hover:not(:disabled){background:#104971}
.submit:disabled{opacity:.55}
#auth-status{min-height:24px;font-size:13px;color:#406985;margin:12px 0}
.small{font-size:11px;text-align:center;color:#8598a8;margin-top:14px}
:focus-visible{outline:3px solid #8dbfe0;outline-offset:2px}
@media(max-width:500px){.layout{align-items:flex-start;padding-top:8vh}.auth{border-radius:11px}}
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
<p>Войдите с помощью электронной почты и пароля, чтобы открыть вашу рабочую область.
Новые учётные записи подтверждает администратор.</p>
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
<label for="register-last-name">Фамилия</label>
<input id="register-last-name" name="last_name" autocomplete="family-name" maxlength="80" required>
<label for="register-first-name">Имя</label>
<input id="register-first-name" name="first_name" autocomplete="given-name" maxlength="80" required>
<label for="register-middle-name">Отчество (если есть)</label>
<input id="register-middle-name" name="patronymic" autocomplete="additional-name" maxlength="80">
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
// Login is intentionally never restored by a background /auth/me request.
// The password form is always shown on an explicit visit to this page.
})();
</script></body></html>'''
    return markup.replace("__INITIAL__", escape(initial))
