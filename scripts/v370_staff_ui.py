"""Modern staff workbench integrated with real EIS queues and owner-only data."""
from __future__ import annotations

CSS=r"""
<style>
:root{font-family:Inter,"Segoe UI",Arial,sans-serif;--blue:#175b88;--ink:#28495f;--line:#dbe7ef;--soft:#f3f7fa}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:#f2f6fa;font:14px/1.55 Inter,"Segoe UI",Arial,sans-serif}
a{color:#176397;text-decoration:none}a:hover{text-decoration:underline}
header{background:#153f64;color:#fff;display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;padding:17px clamp(14px,4vw,55px)}
header strong{font-size:19px}header a{color:white;font-size:12px;font-weight:690;border:1px solid #8cadc8;padding:10px;border-radius:8px}
main{max-width:1300px;margin:auto;padding:clamp(12px,3vw,30px)}
h1{font-size:clamp(23px,3vw,31px);margin:0;color:#1c4b72}h2{color:#235374;font-size:18px;margin:0 0 13px}h3{color:#29587c;font-size:15px;margin:11px 0 7px}
p{font-size:12px;color:#667e8f;margin:6px 0 14px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.metric{border:1px solid var(--line);border-radius:12px;padding:17px;background:#fff}
.metric small{display:block;color:#708797;font-size:12px}.metric strong{display:block;color:#255c87;font-size:29px;font-variant-numeric:tabular-nums}
.tabs{display:flex;overflow-x:auto;gap:7px;padding:20px 0 12px}
.tabs button{white-space:nowrap;border-radius:8px;padding:10px 13px;background:#fff;border:1px solid #d4e1ec;color:#225d89;font-size:12px;font-weight:700}
.tabs button[aria-selected=true]{background:#175b88;color:#fff;border-color:#175b88}
button{font:700 12px Inter,"Segoe UI",system-ui;cursor:pointer}
button:disabled{opacity:.55;cursor:wait}
button.primary{color:#fff;background:#176397;border:1px solid #176397;padding:11px 15px;border-radius:8px}
button.outline{color:#176397;background:#fff;border:1px solid #c9dfed;padding:9px 12px;border-radius:8px}
button.danger{color:#a44c4c;background:#fff;border:1px solid #ebcbcc;padding:8px 10px;border-radius:8px}
section.panel{background:#fff;padding:clamp(16px,2.5vw,27px);border:1px solid var(--line);border-radius:12px;margin-bottom:14px}
section.panel[hidden]{display:none!important}
fieldset{border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:14px}
legend{font-size:12px;font-weight:750;color:#285f87}
label{font-size:12px;font-weight:650;color:#4b6e84;display:block}
label input,label select,label textarea{display:block;margin-top:5px}
input,select,textarea{width:100%;padding:11px 12px;min-height:40px;color:#264e65;border:1px solid #cedde9;border-radius:8px;background:white;font:13px Inter,"Segoe UI",Arial}
input[type=checkbox]{height:17px;width:17px;min-height:0;margin:0;accent-color:#176397}
textarea{min-height:95px;resize:vertical}
form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;align-items:end;margin-bottom:14px}
form .wide{grid-column:1/-1}
.check-options{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}
.check-options label{display:flex;gap:8px;align-items:center;border:1px solid var(--line);border-radius:9px;padding:9px;cursor:pointer;font-size:12px}
.notice{background:#edf7fc;padding:11px 14px;color:#3b6d8a;border-left:3px solid #3f87b9;border-radius:7px;font-size:12px;margin:14px 0}
.entry{border:1px solid #dfeaf2;border-radius:10px;padding:14px;margin:11px 0}
.entry h3{margin-top:0}
.entry small{display:block;font-size:11px;color:#7a8a94}
.action-row{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.action-row a{padding:7px 10px;background:#eff7fc;border:1px solid #d4e6f1;border-radius:8px;font-size:12px}
.pill{display:inline-block;background:#e9f4fa;color:#24638c;font-size:11px;font-weight:760;border-radius:80px;padding:4px 9px}
.progress{width:100%;height:14px;accent-color:#2985b9}
table{border-collapse:collapse;width:100%;font-size:12px}
th{background:#eaf3fa;text-align:left;color:#3e627b;font-size:11px}
td,th{padding:12px 10px;border-bottom:1px solid #e2edf3}
.table-scroll{max-width:100%;overflow-x:auto}
#staff-status{font-size:12px;min-height:20px;color:#2d7098}
.staff-layout{display:grid;grid-template-columns:205px minmax(0,1fr);gap:18px;margin-top:22px;align-items:start}
.staff-layout>.tabs{position:sticky;top:14px;display:flex;flex-direction:column;gap:5px;overflow:visible;padding:10px;background:#fff;border:1px solid var(--line);border-radius:14px}
.staff-layout>.tabs button{width:100%;white-space:normal;text-align:left;line-height:1.35;border:0;background:transparent;color:#53748c;padding:13px 12px}
.staff-layout>.tabs button[aria-selected=true]{background:#eaf4fb;color:#165781;box-shadow:inset 3px 0 #247aae}
.staff-panels{min-width:0}
.staff-columns{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(260px,1fr);gap:14px}
.staff-subpanel{border:1px solid var(--line);border-radius:11px;padding:16px;background:#fbfdff;margin:0 0 13px;min-width:0}
.staff-subpanel h3{margin:0 0 12px}
.staff-smallrow{padding:12px 0;border-bottom:1px solid #e1eaf1;display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start;justify-content:space-between}
.staff-smallrow:last-child{border:0}
.staff-smallrow .staff-rowbody{flex:1;min-width:160px}
.staff-smallrow .staff-rowbody strong{font-size:13px;display:block;color:#234f73}
.staff-smallrow .staff-rowbody small{font-size:11px;color:#698295;display:block;margin-top:3px}
.staff-status-pill{display:inline-flex;padding:4px 8px;background:#eaf3fa;color:#276b95;border-radius:30px;font-size:11px;font-weight:730}
.staff-status-pill.attention{background:#fff2e7;color:#a26420}
.staff-status-pill.good{background:#e7f6ee;color:#23764e}
.staff-tools{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px}
.staff-tools button,.staff-tools a{padding:7px 9px;font-size:11px;min-height:34px}
.staff-message-unread{border-left:3px solid #267cb1;padding-left:10px}
.staff-statline{font-size:11px;color:#66839b;margin:3px 0 13px}
.staff-search-result{padding:11px;border:1px solid #dce8f0;border-radius:9px;margin-top:8px}
.staff-search-result small{color:#6a8191}
.staff-auto{display:flex;align-items:center;gap:8px;background:#eaf7f1;color:#226f52;border:1px solid #d2e9df;border-radius:9px;padding:10px 13px;font-size:12px;font-weight:650;margin-bottom:12px}
.staff-dot{width:9px;height:9px;background:#29835b;border-radius:50%;flex:none}
.staff-hero{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.staff-hero h1{margin:0}
.staff-hero p{margin:4px 0 0}
.staff-section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:13px}
.staff-section-heading h2{margin:0}
.staff-finding-state{margin:9px 0;color:#315d78;font-size:12px}
@media(max-width:980px){.staff-layout{grid-template-columns:1fr;gap:10px}.staff-layout>.tabs{position:static;flex-direction:row;overflow-x:auto;flex-wrap:nowrap}.staff-layout>.tabs button{white-space:nowrap;width:auto;flex:none;border:1px solid #dbe5ed}.staff-columns{grid-template-columns:1fr}}
@media(max-width:760px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}form{grid-template-columns:minmax(0,1fr)}.tabs{padding-top:10px}.staff-smallrow{display:block}.staff-smallrow .staff-tools{margin-top:10px}}
:focus-visible{outline:3px solid #92bddb;outline-offset:3px}
</style>
"""
JS=r"""
<script>
(()=>{
const el=id=>document.getElementById(id),msg=text=>{el('staff-status').textContent=text};
const regOK=x=>/^3[0-9]{10}$/.test(x);
async function showName(){
 try{
  const r=await api('/me/profile');
  el('staff-user-name').textContent=r.full_name||'ФИО не заполнено — укажите данные в профиле';
  if(r.role==='admin'||r.role==='manager'){
   const place=el('staff-account-actions');
   if(place&&!el('staff-manager-link')){const l=link('/account/manager',r.role==='admin'?'Управление пользователями':'Кабинет руководителя');l.id='staff-manager-link';place.prepend(l)}
  }
 }catch{el('staff-user-name').textContent='Имя сотрудника недоступно'}
}

const stateNames={pending:"Не проверено",verified:"Проверено",needs_attention:"Требует внимания",not_applicable:"Не применяется"};
const make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n};
const link=(url,text)=>{const a=make('a',text);a.href=url;return a};
async function api(url,method="GET",body){
 const r=await fetch(url,{method,credentials:"same-origin",cache:"no-store",
  headers:body===undefined?{}:{"Content-Type":"application/json"},
  body:body===undefined?undefined:JSON.stringify(body)});
 if(r.status===401){location.href="/account/login";throw Error("Требуется вход")}
 const data=await r.json().catch(()=>({}));
 if(!r.ok)throw Error(typeof data.detail==="string"?data.detail:"Ошибка HTTP "+r.status);
 return data;
}
async function loadToday(){
 const status=el('today-status');status.textContent='Получение данных…';
 try{
  const data=await api('/me/center/dashboard');
  for(const [id,value] of [['today-tasks',data.task_active],['today-campaigns',data.campaign_active],
   ['today-manual',data.manual_checks],['today-alerts',data.unread_notifications]]){
   el(id).textContent=Number(value||0).toLocaleString('ru-RU');
  }
  const fill=(target,items,format)=>{
   const root=el(target);root.replaceChildren();
   if(!items.length)root.append(make('p','Нет активных записей'));
   for(const item of items.slice(0,12)){
    const row=make('div');row.className='staff-smallrow';
    row.append(make('strong',item.title||item.reg_number||'Проверка'),
     make('small',format(item)));
    root.append(row);
   }
  };
  fill('today-task-list',data.tasks||[],x=>'№ '+(x.reg_number||'—')+' · '+x.status);
  fill('today-campaign-list',data.campaigns||[],x=>'Цель: '+x.target_violations+' нарушений · '+x.status);
  status.textContent='Данные обновлены';
 }catch(error){status.textContent='Не удалось получить данные: '+error.message}
}
function section(tab){
 document.querySelectorAll('[data-staff-section]').forEach(x=>x.hidden=x.dataset.staffSection!==tab);
 document.querySelectorAll('[data-staff-tab]').forEach(x=>x.setAttribute("aria-selected",String(x.dataset.staffTab===tab)));
 if(tab==='goals')campaigns();
 if(tab==='activity')loadHistory();
 if(tab==='overview')loadDashboard();
 if(tab==='library')loadLibrary();
 if(tab==='today')loadToday();
}
document.querySelectorAll('[data-staff-tab]').forEach(x=>x.onclick=()=>section(x.dataset.staffTab));
el('staff-refresh').addEventListener('click',()=>{
 const current=document.querySelector('[data-staff-tab][aria-selected="true"]');
 const tab=current?.dataset.staffTab||'overview';
 section(tab);
 if(tab==='overview')reloadOverview();
 msg('Обновление данных запущено');
});
function list(root,items,build){
 const node=el(root);node.replaceChildren();
 if(!items.length)node.append(make("p","Записей пока нет"));
 for(const item of items)node.append(build(item));
}
async function reloadOverview(){
 try{
  const r=await api('/me/workbench/overview');
  for(const [id,number] of Object.entries({
   checks:r.checks_total,complete:r.reviews_completed,found:r.unique_confirmed_findings,
   documents:r.documents_checked,waiting:r.attention_required,
   memos:r.memo_count
  }))el('staff-'+id).textContent=Number(number||0).toLocaleString("ru-RU");
 }catch(e){msg(e.message)}
}
const taskNames={assigned:'Назначено',in_progress:'В работе',submitted:'Передано руководителю',
 returned:'Возвращено на доработку',completed:'Завершено',cancelled:'Отменено'};
function createAction(text,onClick,kind='outline'){
 const button=make('button',text);button.type='button';button.className=kind;
 button.onclick=async()=>{button.disabled=true;try{await onClick()}catch(error){msg(error.message)}finally{button.disabled=false}};
 return button;
}
async function loadDashboard(){
 try{
  const data=await api('/me/center/dashboard');
  for(const [id,count] of Object.entries({
   'active-tasks':data.task_active,'active-campaigns':data.campaign_active,
   'unread':data.unread_notifications,'reviewed':data.finding_decisions?.confirmed||0
  }))if(el('staff-'+id))el('staff-'+id).textContent=Number(count).toLocaleString('ru-RU');
  el('staff-updated').textContent='Данные обновлены: '+new Date(data.as_of).toLocaleTimeString('ru-RU');
  if(data.full_name)el('staff-hello').textContent='Здравствуйте, '+data.full_name;
  list('staff-task-list',data.tasks.filter(x=>!['completed','cancelled'].includes(x.status)),x=>{
   const row=make('div');row.className='staff-smallrow';
   const body=make('div');body.className='staff-rowbody';
   body.append(make('strong',x.title),make('small','Закупка № '+x.reg_number+
    (x.due_date?' · до '+x.due_date:'')+' · '+(taskNames[x.status]||x.status)));
   const actions=make('div');actions.className='staff-tools';
   const go=link('/check-new/'+x.reg_number,'Карточка ЕИС');actions.append(go);
   if(x.status==='assigned'||x.status==='returned')
    actions.append(createAction('Принять',async()=>{await api('/me/assignments/'+x.id+'/take','POST');await loadDashboard()}));
   if(x.status==='in_progress')
    actions.append(createAction('Передать результат',async()=>{
     if(!confirm('Передать руководителю только завершённую проверку?'))return;
     await api('/me/assignments/'+x.id+'/submit','POST');await loadDashboard();
    }));
   row.append(body,actions);return row;
  });
  list('staff-goal-summary',data.campaigns.filter(x=>x.status==='active'||x.status==='submitted'),x=>{
   const box=make('div');box.className='staff-smallrow';
   const body=make('div');body.className='staff-rowbody';
   body.append(make('strong',x.title),make('small','План: '+x.target_violations+
    ' · '+(taskNames[x.status]||x.status)));
   box.append(body,createAction('Нарушения',async()=>section('goals')));
   return box;
  });
  const statuses=data.memo_reviews||{};
  el('staff-memo-reviews').textContent='На рассмотрении: '+(statuses.submitted||0)+
   ' · Согласовано: '+(statuses.approved||0)+' · На доработке: '+(statuses.returned||0);
 }catch(error){msg('Обзор сотрудника: '+error.message)}
}
async function loadProgress(){
 try{
  const info=await api('/me/center/progress');
  if(!info.available){el('staff-progress-note').textContent=info.note;return}
  el('staff-progress-note').textContent=info.note;
  list('staff-progress-list',info.checks,x=>{
   const row=make('div');row.className='staff-smallrow';
   const body=make('div');body.className='staff-rowbody';
   body.append(make('strong','Закупка № '+x.reg_number),
    make('small',x.stage+' · оригиналов проверено: '+x.documents_checked+
       ' · фактических нарушений: '+x.violations));
   const tool=make('div');tool.className='staff-tools';
   tool.append(createAction('Открыть',async()=>{
     section('case');el('staff-reg').value=x.reg_number;await loadCase()
   }));
   const badge=make('span',x.stage);badge.className='staff-status-pill'+
       (x.legal_review_completed?' good':x.job_status==='failed'?' attention':'');
   row.append(body,tool);return row;
  });
 }catch(error){msg('Этапы проверок: '+error.message)}
}
async function loadNotifications(){
 try{
  const items=await api('/me/notifications');
  const unread=items.filter(x=>!x.read).length;
  el('staff-notification-count').textContent=unread?unread+' непрочитанных':'Нет новых';
  list('staff-notification-list',items.slice(0,25),x=>{
   const row=make('div');row.className='staff-smallrow'+(!x.read?' staff-message-unread':'');
   const body=make('div');body.className='staff-rowbody';
   body.append(make('strong',x.title),make('small',x.detail||''),
    make('small',new Date(x.created_at).toLocaleString('ru-RU')));
   row.append(body);
   if(!x.read)row.append(createAction('Прочитано',async()=>{
    await api('/me/notifications/'+x.id+'/read','POST');await loadNotifications();await loadDashboard();
   }));
   return row;
  });
 }catch(error){msg('Уведомления: '+error.message)}
}
async function searchAll(event){
 event.preventDefault();
 const q=el('staff-global-query').value.trim();
 if(q.length<2)return msg('Введите не менее двух символов для поиска');
 try{
  const data=await api('/me/center/search?q='+encodeURIComponent(q));
  list('staff-global-results',data.results||[],x=>{
   const box=make('div');box.className='staff-search-result';
   box.append(make('strong',x.title),make('small',x.status||''));
   if(x.reg_number&&regOK(x.reg_number))
    box.append(link('/check-new/'+x.reg_number,'Открыть карточку закупки'));
   if(x.batch_id)box.append(createAction('Результаты',async()=>section('goals')));
   return box;
  });
  msg((data.results||[]).length+' результатов. '+data.scope);
 }catch(error){msg(error.message)}
}
async function loadSavedSearches(){
 try{
  const items=await api('/me/center/saved-searches');
  list('staff-saved-searches',items,x=>{
   const row=make('div');row.className='staff-smallrow';
   const term=createAction(x.phrase,async()=>{
    el('staff-global-query').value=x.phrase;
    await searchAll({preventDefault(){}});
   });
   const del=createAction('Удалить',async()=>{
    await api('/me/center/saved-searches/'+x.id,'DELETE');
    await loadSavedSearches()
   },'danger');
   row.append(term,del);return row;
  });
 }catch(error){msg('Сохранённые запросы: '+error.message)}
}
let autosaveHandle=null;
async function loadDraft(reg){
 try{
  const data=await api('/me/center/draft/'+reg);
  const field=el('staff-case-draft');
  field.dataset.reg=reg;field.value=data.body||'';
  el('staff-draft-saved').textContent=data.updated_at?
    'Последнее сохранение: '+new Date(data.updated_at).toLocaleString('ru-RU'):'Черновик пуст';
 }catch(error){msg('Черновик: '+error.message)}
}
el('staff-case-draft').addEventListener('input',()=>{
 clearTimeout(autosaveHandle);
 const field=el('staff-case-draft'),reg=field.dataset.reg,text=field.value;
 if(!regOK(reg))return;
 el('staff-draft-saved').textContent='Сохранение изменений…';
 autosaveHandle=setTimeout(async()=>{
  try{await api('/me/center/draft/'+reg,'PUT',{body:text});
   if(field.dataset.reg===reg&&field.value===text)
    el('staff-draft-saved').textContent='Изменения автоматически сохранены';
  }catch(error){msg('Не удалось сохранить черновик: '+error.message);
   el('staff-draft-saved').textContent='Ошибка автосохранения'}
 },800);
});
el('staff-search-save').onclick=async()=>{
 const phrase=el('staff-global-query').value.trim();
 if(phrase.length<2)return msg('Сначала укажите поисковый запрос');
 try{await api('/me/center/saved-searches','POST',{phrase});
  await loadSavedSearches();msg('Поисковый запрос сохранён для вашего аккаунта')}
 catch(error){msg(error.message)}
};
async function campaigns(){
 try{
  const [goals,result]=await Promise.all([
   api('/me/campaign-goals'),api('/me/campaign-goals/violations')
  ]);
  // Progress is secondary; individual EIS violations are the main result.
  list('staff-campaign-list',goals,item=>{
   const box=make("article");box.className="entry";
   box.append(make("h3",item.title),
    make("p","Проверяются категории: "+item.category_names.join(", ")+
      "; формы заказчиков: "+item.legal_forms.join(", ")),
    make("p","Найдено "+item.actual_violations+" / план "+item.target_violations+
      " · закупок: "+item.verified_procurements));
   const pr=make("progress");pr.className="progress";pr.max=100;pr.value=item.progress_percent;
   box.append(pr,make("small","Срок: "+(item.due_date||"не установлен")+
    " · статус проверки ЕИС: "+item.engine_status));
   if(item.status==="active"){
    const send=make("button","Передать фактические результаты руководителю");
    send.className="outline";
    send.onclick=async()=>{
     if(!confirm("Передать результаты без изменения количества нарушений?"))return;
     try{await api('/me/campaign-goals/'+item.id+'/submit','POST');await campaigns()}
     catch(e){msg(e.message)}
    };
    box.append(send);
   }
   return box;
  });
  el('staff-violations-title').textContent=
   'Нарушения · '+result.count+(result.truncated?' (показана часть)':'');
  list('staff-thematic-findings',result.findings||[],item=>{
   const card=make("article");card.className="entry";
   const label=make("span","Карточка нарушения");label.className="pill";
   card.append(label,make("h3",item.title),
    make("p","Закупка № "+item.reg_number+" · "+(item.law||"Правовая норма требует уточнения")));
   const evidence=make("div",item.evidence||"Доказательства в анализаторе отсутствуют");
   evidence.className="notice";evidence.style.whiteSpace="pre-wrap";card.append(evidence);
   if(item.document)card.append(make("small","Документ: "+item.document));
   if(item.needs_review)card.append(make("p","Необходимо проверить полноту доказательств"));
   const actions=make("div");actions.className="action-row";
   if(item.law_url){
    const law=link(item.law_url,"Статья закона · КонсультантПлюс ↗");
    law.target="_blank";law.rel="noopener noreferrer";actions.append(law);
   }
   if(item.source_url){
    const source=link(item.source_url,"Оригинал ЕИС ↗");
    source.target="_blank";source.rel="noopener noreferrer";actions.append(source);
   }
   if(item.batch_id&&/^3[0-9]{10}$/.test(item.reg_number)){
    actions.append(link('/thematic/card/'+encodeURIComponent(item.batch_id)+
     '/'+item.reg_number,'Карточка проверки'));
   }
   const stateNames={unreviewed:'Не рассмотрено',confirmed:'Подтверждено сотрудником',
    rejected:'Отклонено сотрудником',recheck:'Требует дополнительной проверки'};
   const status=make('div','Решение сотрудника: '+(stateNames[item.review_status]||'Не рассмотрено'));
   status.className='staff-finding-state';
   card.append(status);
   if(item.review_comment)card.append(make('small','Комментарий: '+item.review_comment));
   if(item.finding_id&&/^[0-9a-f]{32}$/.test(item.finding_id)){
    for(const [value,label] of [['confirmed','Подтвердить'],['recheck','На допроверку'],
     ['rejected','Отклонить'],['unreviewed','Сбросить решение']]){
     if(value===item.review_status)continue;
     actions.append(createAction(label,async()=>{
      const comment=(value==='unreviewed')?'':(prompt('Комментарий к решению (можно оставить пустым)','')||'');
      if(value==='rejected'&&!confirm('Отклонить это нарушение только в личной оценке, сохранив исходные данные ЕИС?'))return;
      await api('/me/center/findings/'+item.finding_id+'/decision','POST',
       {reg_number:item.reg_number,decision:value,comment});
      await campaigns();await loadDashboard();
      msg('Решение сохранено; исходные результаты ЕИС не изменялись');
     }));
    }
   }
   actions.append(link('/account/memos','Подготовить служебную записку'));
   card.append(actions);
   return card;
  });
  msg("Нарушения отображаются из реальных тематических проверок ЕИС; повторные записи объединены.");
 }catch(e){msg(e.message)}
}
async function templates(){
 try{
  const data=await api('/me/workbench/templates');
  list('staff-template-list',data,item=>{
   const box=make("div");box.className="entry";box.append(make("h3",item.name),
    make("small","Категории: "+item.categories.join(", ")+" · формы: "+item.legal_forms.join(", ")));
   const button=make("button","Применить настройки");button.className="outline";
   button.onclick=()=>{const root=el("staff-template-form");
    root.querySelector('[name="name"]').value=item.name;
    for(const checkbox of root.querySelectorAll("input[type=checkbox]"))
       checkbox.checked=(checkbox.name==="categories"?item.categories:item.legal_forms).includes(checkbox.value);
    msg("Настройки шаблона перенесены в форму");root.scrollIntoView({behavior:"smooth"});
   };
   const launch=make("button","Запустить тематическую проверку");launch.className="primary";
   launch.onclick=async()=>{
    launch.disabled=true;
    try{
     const result=await api('/me/workbench/templates/'+item.id+'/start','POST');
     msg('Создана настоящая тематическая проверка ЕИС: '+result.batch_id);
     section('goals');await campaigns();
    }catch(e){msg(e.message)}finally{launch.disabled=false}
   };
   const del=make("button","Удалить");del.className="danger";
   del.onclick=async()=>{if(!confirm("Удалить личный шаблон?"))return;
    try{await api("/me/workbench/templates/"+item.id,"DELETE");await templates()}catch(e){msg(e.message)}};
   const row=make("div");row.className="action-row";row.append(button,launch,del);box.append(row);return box;
  });
 }catch(e){msg(e.message)}
}
async function options(){
 const data=await api('/me/workbench/options').catch(()=>null);
 const opts=data||{
  categories:{dates:"Сроки и разъяснения",docs:"Документация",national:"Национальный режим",
   security:"Обеспечение",sme:"Закупки МСП",access:"Участники",protocols:"Протоколы",
   contract:"Договоры",notices:"Извещения",evaluation:"Оценка заявок"},
  legal_forms:{ООО:"ООО",АО:"АО",ПАО:"ПАО",ОАО:"ОАО",ЗАО:"ЗАО"}
 };
 for(const [id,values,name] of [["staff-cats",opts.categories,"categories"],["staff-forms",opts.legal_forms,"legal_forms"]]){
  const box=el(id);box.replaceChildren();
  for(const [key,value] of Object.entries(values)){
   const label=make("label"),input=make("input");input.type="checkbox";input.name=name;input.value=key;
   input.checked=name==="categories"?["dates","docs"].includes(key):["ООО","АО"].includes(key);
   label.append(input,make("span",value));box.append(label);
  }
 }
}
el("staff-bulk-form").onsubmit=async event=>{
 event.preventDefault();
 const raw=el("staff-bulk-input").value;
 const regs=[...new Set(raw.split(/[\s,;]+/).filter(Boolean))];
 if(!regs.length||regs.length>30||regs.some(x=>!regOK(x))){
  msg("Введите от одного до 30 уникальных 11-значных номеров ЕИС");return}
 const button=event.currentTarget.querySelector("button");button.disabled=true;
 try{
  const result=await api('/me/workbench/bulk','POST',{reg_numbers:regs});
  msg("Поставлено на загрузку ЕИС: "+result.accepted+" из "+result.requested);
  await reloadOverview();
 }catch(e){msg(e.message)}finally{button.disabled=false}
};
el("staff-template-form").onsubmit=async event=>{
 event.preventDefault();
 const data=new FormData(event.currentTarget);
 const categories=data.getAll("categories"),legal_forms=data.getAll("legal_forms");
 if(!categories.length||!legal_forms.length){msg("Выберите категории и формы заказчика");return}
 try{await api('/me/workbench/templates','POST',{
   name:String(data.get('name')||''),categories,legal_forms});
   await templates();msg("Шаблон сохранён")
 }catch(e){msg(e.message)}
};
async function loadCase(){
 const reg=el("staff-reg").value.trim();
 if(!regOK(reg)){msg("Укажите номер ЕИС из 11 цифр");return}
 el("staff-case").hidden=false;
 el("staff-case-link").href='/me/cases/'+reg;
 el("staff-case-download").href='/me/workbench/'+reg+'/export.zip';
 for(const cb of [loadChecklist,loadManual,loadRelated,loadDiscussion,loadPreferences,loadVersions,loadDraft])await cb(reg);
}
async function loadChecklist(reg){
 try{
  const questions=await api('/me/workbench/'+reg+'/checklist');
  list('staff-checklist',questions,q=>{
   const item=make("article");item.className="entry";
   item.append(make("h3",q.question));
   const select=make("select");
   for(const [value,text] of Object.entries(stateNames)){
    const option=make("option",text);option.value=value;select.append(option)
   }
   select.value=q.state;
   const comment=make("input");comment.placeholder="Проверенный факт или причина замечания";
   comment.value=q.comment;
   const button=make("button","Сохранить вопрос");button.className="outline";
   button.onclick=async()=>{try{
     await api('/me/workbench/'+reg+'/checklist/'+q.key,'PUT',
       {state:select.value,comment:comment.value});msg("Контрольный вопрос сохранён");
   }catch(e){msg(e.message)}};
   const form=make("div");form.className="action-row";form.append(select,comment,button);item.append(form);
   return item;
  })
 }catch(e){msg(e.message)}
}
async function loadManual(reg){
 try{
  const issues=await api('/me/workbench/'+reg+'/manual-findings');
  list('staff-manual-findings',issues,item=>{
   const box=make("article");box.className="entry";
   box.append(make("h3",item.title),make("p",item.law),
    make("p",item.evidence.slice(0,300)),make("small","Статус: "+item.status));
   const a=link(item.source_url,"Оригинал ЕИС ↗");a.target="_blank";a.rel="noopener noreferrer";
   box.append(a);return box;
  });
 }catch(e){msg(e.message)}
}
el("staff-manual-form").onsubmit=async event=>{
 event.preventDefault();const reg=el('staff-reg').value.trim();
 const data=Object.fromEntries(new FormData(event.currentTarget).entries());
 try{
  await api('/me/workbench/'+reg+'/manual-findings','POST',data);
  event.currentTarget.reset();await loadManual(reg);
  msg("Нарушение передано на проверку и пока не увеличивает число подтверждённых")
 }catch(e){msg(e.message)}
};
async function loadRelated(reg){
 try{
  const related=await api('/me/workbench/'+reg+'/related');
  list("staff-related",related.purchases||[],x=>{
   const box=make("div");box.className="entry";
   box.append(link('/check-new/'+x.reg_number,'№ '+x.reg_number),
    make("p",x.title||"Без наименования"),make("small",x.customer||""));
   return box;
  });
 }catch(e){msg(e.message)}
}
el("staff-search-form").onsubmit=async event=>{
 event.preventDefault();const reg=el("staff-reg").value.trim(),
   query=String(new FormData(event.currentTarget).get("q")||"").trim();
 try{
  const result=await api('/me/workbench/'+reg+'/search?q='+encodeURIComponent(query));
  list('staff-search-results',result.results||[],x=>{
   const box=make("div");box.className="entry";
   box.append(make("small",x.type),make("p",x.excerpt));return box;
  });
  msg(result.scope);
 }catch(e){msg(e.message)}
};
async function loadDiscussion(reg){
 try{
  const messages=await api('/me/workbench/'+reg+'/discussion');
  list('staff-discussion',messages,x=>{
   const box=make("div");box.className="entry";
   box.append(make("b",x.author),make("small",new Date(x.created_at).toLocaleString('ru-RU')),
    make("p",x.body));return box;
  })
 }catch(e){msg(e.message)}
}
el("staff-message-form").onsubmit=async event=>{
 event.preventDefault();const reg=el("staff-reg").value.trim();
 const body=String(new FormData(event.currentTarget).get("body")||"");
 try{
  await api('/me/workbench/'+reg+'/discussion','POST',{body});
  event.currentTarget.reset();await loadDiscussion(reg);msg("Сообщение сохранено")
 }catch(e){msg(e.message)}
};
async function loadPreferences(reg){
 try{
  const pref=await api('/me/workbench/preferences');
  el("staff-priority").checked=pref.priority_regs.includes(reg);
  // Settings persisted in PostgreSQL; the calendar is deliberately not shown.
 }catch(e){msg(e.message)}
}
el("staff-save-pref").onclick=async()=>{
 const reg=el("staff-reg").value.trim();
 if(!regOK(reg)){msg("Сначала выберите проверку");return}
 try{
  const old=await api('/me/workbench/preferences');
  const next=old.priority_regs.filter(x=>x!==reg);
  if(el("staff-priority").checked)next.unshift(reg);
  await api('/me/workbench/preferences','PUT',{
   priority_regs:next.slice(0,30),
   pinned_legal_categories:old.pinned_legal_categories,
   notification_days:old.notification_days});
  msg("Персональные настройки сохранены")
 }catch(e){msg(e.message)}
};
el("staff-case-load").onclick=loadCase;
el("staff-recheck").onclick=async()=>{
 const reg=el("staff-reg").value.trim();
 if(!regOK(reg))return msg('Выберите номер своей закупки');
 try{const result=await api('/me/workbench/'+reg+'/recheck','POST');
  msg('Повторная загрузка оригиналов ЕИС: '+result.status+'. История сохранена')}
 catch(e){msg(e.message)}
};
async function loadVersions(reg){
 try{
  const result=await api('/me/workbench/'+reg+'/versions');
  const left=el('staff-version-left'),right=el('staff-version-right');
  left.replaceChildren();right.replaceChildren();
  for(const group of result.groups||[]){
   for(const version of group.versions){
    for(const select of [left,right]){
     const option=make('option',version.name+' · '+(version.published_at||'дата неизвестна'));
     option.value=version.document_id;select.append(option);
    }
   }
  }
  if(right.options.length>1)right.selectedIndex=1;
  el('staff-version-note').textContent=result.note;
 }catch(e){msg(e.message)}
}
el('staff-version-compare').onclick=async()=>{
 const reg=el('staff-reg').value.trim(),a=el('staff-version-left').value,b=el('staff-version-right').value;
 if(!a||!b||a===b)return msg('Выберите две разные редакции');
 try{const result=await api('/me/workbench/'+reg+'/compare?left='+encodeURIComponent(a)+'&right='+encodeURIComponent(b));
  const root=el('staff-version-diff');root.replaceChildren();
  for(const row of result.diff||[]){const node=make('div',row);node.style.whiteSpace='pre-wrap';
   node.style.background=row.startsWith('+')?'#e5f5e9':row.startsWith('-')?'#fff0ef':'#fff';
   root.append(node)}
  msg(result.note+(result.truncated?' · Вывод сокращён':''));
 }catch(e){msg(e.message)}
};
el('staff-original-search').onsubmit=async event=>{
 event.preventDefault();const reg=el('staff-reg').value.trim();
 const phrase=String(new FormData(event.currentTarget).get('q')||'').trim();
 try{
  const result=await api('/me/workbench/'+reg+'/search-originals?q='+encodeURIComponent(phrase));
  list('staff-original-results',result.results||[],x=>{
   const box=make('article');box.className='entry';
   box.append(make('h3',x.name),make('p',x.fragment));return box});
  msg(result.scope+' · просмотрено файлов: '+result.documents_scanned);
 }catch(e){msg(e.message)}
};
async function loadHistory(){
 try{
  const data=await api('/me/workbench/history');
  list('staff-history-list',data,x=>{
   const box=make('div');box.className='entry';
   box.append(make('small',new Date(x.date).toLocaleString('ru-RU')),
    make('h3',x.kind+' · '+x.action),make('p',x.detail||x.reference));return box;
  })
 }catch(e){msg(e.message)}
}
async function loadLibrary(){
 try{
  const data=await api('/me/workbench/approved-library');
  list('staff-library-list',data,x=>{
   const box=make('div');box.className='entry';
   box.append(make('h3',x.title),make('p',x.law),make('small','№ '+x.reg_number+' · согласовано руководителем'));
   box.append(link('/check-new/'+x.reg_number,'Открыть закупку'));return box;
  })
 }catch(e){msg(e.message)}
}
loadHistory();loadLibrary();
showName();
el('staff-global-search').addEventListener('submit',searchAll);
options().catch(e=>msg(e.message));
reloadOverview();loadDashboard();loadProgress();loadNotifications();loadSavedSearches();campaigns();templates();
// Live server-side jobs continue independently of the browser; this only refreshes the view.
setInterval(()=>{
 if(document.hidden)return;
 reloadOverview();loadDashboard();loadProgress();loadNotifications();
 if(document.querySelector('[data-staff-tab="goals"]')?.getAttribute('aria-selected')==='true')campaigns();
},60000);
})();
</script>
"""
PAGE=r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Рабочее место сотрудника — ЕИС Аудитор</title>__CSS__</head>
<body><header><div><strong>ЕИС Аудитор · Рабочее место сотрудника</strong>
<div id="staff-user-name" style="font-size:12px;color:#d9e9f3">ФИО: загрузка…</div></div>
<div class="action-row" id="staff-account-actions"><a href="/account/profile">Моё ФИО</a>
<a href="/">← В личный кабинет</a></div></header>
<main>
<div class="staff-hero"><div><h1 id="staff-hello">Моё рабочее пространство</h1>
<p>Собственные проверки, поручения и нарушения по реальным данным ЕИС. Без дублирования результатов.</p></div>
<button id="staff-refresh" type="button" class="outline">↻ Обновить данные</button></div>
<div class="grid">
<div class="metric"><small>Самостоятельные проверки</small><strong id="staff-checks">—</strong></div>
<div class="metric"><small>Завершён анализ</small><strong id="staff-complete">—</strong></div>
<div class="metric"><small>Нарушения</small><strong id="staff-found">—</strong></div>
<div class="metric"><small>Прочитано документов</small><strong id="staff-documents">—</strong></div>
<div class="metric"><small>Ожидают документы</small><strong id="staff-waiting">—</strong></div>
<div class="metric"><small>Служебные записки</small><strong id="staff-memos">—</strong></div>
</div>
<div class="staff-layout">
<nav class="tabs" aria-label="Разделы рабочего места">
<button type="button" data-staff-tab="overview" aria-selected="true">Обзор</button><button type="button" data-staff-tab="today" aria-selected="false">Работа на сегодня</button>
<button type="button" data-staff-tab="goals" aria-selected="false">Нарушения и поручения</button>
<button type="button" data-staff-tab="bulk" aria-selected="false">Массовая проверка</button>
<button type="button" data-staff-tab="templates" aria-selected="false">Мои шаблоны</button>
<button type="button" data-staff-tab="case" aria-selected="false">Контроль закупки</button>
<button type="button" data-staff-tab="activity" aria-selected="false">История работы</button>
<button type="button" data-staff-tab="library" aria-selected="false">База нарушений</button>
</nav>
<div class="staff-panels">
<section class="panel" data-staff-section="today" hidden>
<h2>Работа на сегодня</h2>
<p>Актуальные поручения, самостоятельные и тематические проверки, уведомления и служебные записки.</p>
<div class="grid">
<div class="metric"><small>Активные поручения</small><strong id="today-tasks">—</strong></div>
<div class="metric"><small>Тематические проверки</small><strong id="today-campaigns">—</strong></div>
<div class="metric"><small>Самостоятельные проверки</small><strong id="today-manual">—</strong></div>
<div class="metric"><small>Непрочитанные уведомления</small><strong id="today-alerts">—</strong></div>
</div>
<div class="action-row">
<a href="/account/home">Мои проверки</a><a href="/account/thematic">Тематические проверки</a>
<a href="/account/memos">Служебные записки ODT</a>
</div>
<h3>Поручения руководителя</h3><div id="today-task-list"></div>
<h3>Тематические проверки</h3><div id="today-campaign-list"></div>
<p class="notice" id="today-status" role="status">Загрузка при открытии раздела.</p>
</section>
<section class="panel" data-staff-section="overview">
<div class="staff-section-heading"><h2>Рабочая панель</h2><small id="staff-updated" class="staff-statline">Получение актуальных данных</small></div>
<div class="staff-auto"><span class="staff-dot" aria-hidden="true"></span>
Автоматический анализ закупок выполняется сервером. Карточки и показатели обновляются без перезагрузки.</div>
<div class="grid" style="margin-bottom:18px">
<div class="metric"><small>Активные поручения</small><strong id="staff-active-tasks">—</strong></div>
<div class="metric"><small>Тематические проверки</small><strong id="staff-active-campaigns">—</strong></div>
<div class="metric"><small>Новые уведомления</small><strong id="staff-unread">—</strong></div>
<div class="metric"><small>Лично подтверждено</small><strong id="staff-reviewed">—</strong></div>
</div>
<div class="staff-columns">
<div>
<div class="staff-subpanel"><h3>Мои поручения</h3><p>Изменения статусов сохраняются и доступны руководителю.</p>
<div id="staff-task-list"></div></div>
<div class="staff-subpanel"><h3>Ход самостоятельных проверок</h3>
<p id="staff-progress-note">Получаю этапы работы с ЕИС…</p><div id="staff-progress-list"></div>
</div></div>
<div>
<div class="staff-subpanel"><h3>Тематические поручения</h3><div id="staff-goal-summary"></div>
</div>
<div class="staff-subpanel"><div class="staff-section-heading"><h3>Уведомления</h3>
<span class="staff-status-pill" id="staff-notification-count">Загрузка…</span></div>
<div id="staff-notification-list"></div></div>
<div class="staff-subpanel"><h3>Мои служебные записки</h3>
<p id="staff-memo-reviews"></p>
<a class="pill" href="/account/memos">Открыть документы и проверку качества →</a></div>
</div></div>
<div class="staff-subpanel"><h3>Быстрый поиск по моим материалам</h3>
<form id="staff-global-search"><label class="wide">Номер ЕИС, название поручения или обнаруженного нарушения
<input id="staff-global-query" type="search" minlength="2" maxlength="80" placeholder="32616412317 или срок подачи заявок" required></label>
<button class="primary" type="submit">Найти</button>
<button class="outline" type="button" id="staff-search-save">Сохранить запрос</button></form>
<div id="staff-saved-searches"></div>
<div id="staff-global-results"></div>
<p><a href="/">Поиск по общей базе ЕИС и расширенные фильтры ИНН, ОГРН и цены →</a></p></div>
</section>
<section class="panel" data-staff-section="goals" hidden>
<h2>Нарушения тематических проверок</h2>
<p class="notice">Все подтверждённые анализатором эпизоды отображаются как обычные нарушения:
с доказательствами, нормами закона и ссылками на ЕИС. Цель руководителя не влияет
на количество и не создаёт искусственных результатов.</p>
<h3 id="staff-violations-title">Нарушения</h3>
<div id="staff-thematic-findings"></div>
<h3>Параметры проверки, заданные руководителем</h3>
<div id="staff-campaign-list"></div>
<div class="action-row"><a href="/account/home">Мои самостоятельные проверки</a>
<a href="/account/thematic">Мои тематические проверки</a>
<a href="/account/memos">Служебные записки</a></div>
</section>
<section class="panel" data-staff-section="bulk" hidden>
<h2>Массовая самостоятельная проверка</h2>
<p>До 30 реальных номеров ЕИС за одно действие. Новые задания запускают существующий загрузчик;
повторные ожидающие задания не дублируются.</p>
<form id="staff-bulk-form">
<label class="wide">Номера закупок — по строкам или через запятую
<textarea id="staff-bulk-input" required maxlength="800"
 placeholder="32616412317&#10;32616411351"></textarea></label>
<button type="submit" class="primary">Добавить в очередь ЕИС</button></form>
<p class="notice">Запуск задания не означает, что оригиналы скачаны или нарушение подтверждено.</p>
</section>
<section class="panel" data-staff-section="templates" hidden>
<h2>Шаблоны тематических проверок</h2>
<form id="staff-template-form">
<label class="wide">Наименование шаблона<input name="name" minlength="3" maxlength="120" required
 placeholder="Контроль сроков, МСП и договоров"></label>
<fieldset class="wide"><legend>Категории проверки</legend><div id="staff-cats" class="check-options"></div></fieldset>
<fieldset class="wide"><legend>Формы заказчиков</legend><div id="staff-forms" class="check-options"></div></fieldset>
<button class="primary" type="submit">Сохранить шаблон</button></form>
<h3>Мои сохранённые шаблоны</h3><div id="staff-template-list"></div>
</section>
<section class="panel" data-staff-section="case" hidden>
<h2>Контроль собственной закупки</h2>
<p>Выберите одну из закупок, которые вы сами запустили. Доступ к чужим данным закрыт.</p>
<div class="action-row"><label>Регистрационный номер ЕИС
<input id="staff-reg" pattern="3[0-9]{10}" inputmode="numeric" maxlength="11"
 placeholder="32616412317"></label>
<button class="primary" id="staff-case-load" type="button">Открыть инструменты</button></div>
<div id="staff-case" hidden>
<div class="staff-subpanel"><h3>Мои рабочие заметки</h3>
<p>Черновик сохраняется автоматически только в вашей учётной записи и не изменяет оригиналы ЕИС.</p>
<textarea id="staff-case-draft" maxlength="3000" aria-label="Черновик заметок к проверке"
 placeholder="Зафиксируйте вопросы и обстоятельства, которые нужно дополнительно проверить"></textarea>
<small id="staff-draft-saved" aria-live="polite">Черновик не загружен</small></div>
<div class="action-row">
<a id="staff-case-link" href="#">Электронное дело и оригиналы ЕИС</a>
<a id="staff-case-download" href="#">Выгрузить материалы ZIP</a>
<button class="outline" id="staff-recheck" type="button">Перепроверить оригиналы ЕИС</button>
<a href="/account/memos">Архив служебных записок</a></div>
<fieldset><legend>Персональные настройки</legend>
<label class="check-options"><input id="staff-priority" type="checkbox"> Приоритетная закупка</label>
<button class="outline" type="button" id="staff-save-pref">Сохранить приоритет</button></fieldset>
<h2>Чек-лист проверки по 223-ФЗ</h2>
<div id="staff-checklist"></div>
<h2>Зафиксировать нарушение вручную</h2>
<p class="notice">Это предложение к проверке, а не подтверждённое нарушение. Нужны конкретная норма и первоисточник ЕИС.</p>
<form id="staff-manual-form">
<label class="wide">Название нарушения<input name="title" minlength="8" maxlength="220" required></label>
<label>Статья, часть, пункт закона<input name="law" minlength="6" maxlength="400" required></label>
<label>Оригинальная ссылка ЕИС<input type="url" name="source_url" required></label>
<label class="wide">Подтверждающие обстоятельства
<textarea name="evidence" minlength="30" maxlength="4000" required></textarea></label>
<button class="primary" type="submit">Передать на проверку</button></form>
<div id="staff-manual-findings"></div>
<h2>Связанные закупки заказчика</h2><div id="staff-related"></div>
<h2>Сравнение доступных редакций</h2>
<p>Для сохранённых документов с одинаковым названием можно проверить различия в извлечённом тексте.</p>
<div class="action-row"><label>Первый документ<select id="staff-version-left"></select></label>
<label>Второй документ<select id="staff-version-right"></select></label>
<button class="outline" type="button" id="staff-version-compare">Показать различия</button></div>
<p id="staff-version-note"></p>
<div class="entry" id="staff-version-diff"></div>
<h2>Поиск внутри сохранённых оригиналов</h2>
<form id="staff-original-search">
<label class="wide">Точное слово или фраза<input name="q" minlength="3" maxlength="100" required
placeholder="Срок оплаты договора"></label>
<button class="primary">Поиск в файлах ЕИС</button></form>
<div id="staff-original-results"></div>
<h2>Поиск по сохранённым доказательствам и документам</h2>
<form id="staff-search-form"><label class="wide">Искомая фраза
<input name="q" minlength="3" maxlength="110" required placeholder="Обеспечение заявки"></label>
<button class="primary">Найти фрагменты</button></form>
<div id="staff-search-results"></div>
<h2>Обсуждение с руководителем</h2>
<div id="staff-discussion"></div>
<form id="staff-message-form"><label class="wide">Комментарий
<textarea name="body" minlength="4" maxlength="2000" required></textarea></label>
<button class="primary" type="submit">Отправить сообщение</button></form>
</div></section>
<section class="panel" data-staff-section="activity" hidden>
<h2>История работы</h2>
<p>Действия сотрудника и руководителя, сохранённые замечания, решения и контрольные вопросы.</p>
<div id="staff-history-list"></div></section>
<section class="panel" data-staff-section="library" hidden>
<h2>Моя база согласованных нарушений</h2>
<p>Только подтверждённые руководителем выводы, отдельно от автоматических находок анализатора.</p>
<div id="staff-library-list"></div>
<h2>Проверка оформления служебной записки</h2>
<p>Откройте архив и нажмите «Проверить записку» рядом с нужным документом.
Никаких идентификаторов вводить не требуется.</p>
<p><a href="/account/memos">Открыть архив и проверить мои ODT →</a></p></section>
</div></div>
<div id="staff-status" role="status" aria-live="polite"></div>
</main>__JS__</body></html>"""

def render_workbench():
    return PAGE.replace("__CSS__",CSS).replace("__JS__",JS)
