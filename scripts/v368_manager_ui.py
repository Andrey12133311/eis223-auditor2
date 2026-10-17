"""Dedicated EIS-inspired director workspace: role-gated, real-data UI."""
import json
from html import escape

STYLE=r"""
<style>
:root{--navy:#153f64;--navy2:#1c5684;--blue:#176397;--light:#f2f6fa;--edge:#dce5ed;
 --muted:#688093;--ink:#233d52;--white:#fff;--danger:#a4484b;--green:#237455}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;font-family:Inter,"Segoe UI",Arial,system-ui,sans-serif;color:var(--ink);background:#f3f6f9}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
button,input,select,textarea{font:inherit}
button{cursor:pointer}
button:disabled{cursor:wait;opacity:.55}
:focus-visible{outline:3px solid #97c9e7;outline-offset:2px}
.mgr-top{background:#153f64;color:#fff;padding:16px clamp(16px,4vw,58px);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;border-bottom:3px solid #5395c4}
.mgr-brand{display:flex;gap:12px;align-items:center}
.mgr-logo{height:44px;width:44px;display:grid;place-items:center;background:#ffffff20;border:1px solid #ffffff55;border-radius:10px;font-weight:800;color:#fff}
.mgr-brand strong{display:block;font-size:17px;letter-spacing:-.025em}
.mgr-brand small{font-size:11px;color:#c6dcec}
.mgr-top a{color:#f4faff;border:1px solid #7399b9;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:700}
.mgr-shell{display:grid;grid-template-columns:225px minmax(0,1fr);max-width:1740px;margin:0 auto;min-height:calc(100vh - 79px)}
.mgr-side{background:#fff;border-right:1px solid var(--edge);padding:22px 11px;display:flex;flex-direction:column;gap:5px}
.mgr-side-heading{font-size:10px;color:#7c8d9d;letter-spacing:.11em;font-weight:800;padding:8px 12px}
.mgr-tab{padding:13px 15px;border:1px solid transparent;background:transparent;text-align:left;border-radius:9px;color:#456277;font-size:13px;font-weight:680;display:flex;align-items:center;gap:10px}
.mgr-tab:hover{background:#f2f7fb}
.mgr-tab[aria-selected=true]{background:#e9f3fa;border-color:#d6e7f2;color:#155986}
.mgr-tab-icon{height:20px;width:20px;flex:none;display:grid;place-items:center;font-size:15px}
.mgr-work{padding:clamp(18px,3vw,38px);min-width:0}
.mgr-head{display:flex;align-items:end;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:21px}
.mgr-head h1{font-size:clamp(23px,2.4vw,32px);margin:0 0 4px;letter-spacing:-.04em;color:#204d70}
.mgr-head p{font-size:13px;color:var(--muted);margin:0}
.mgr-role{border:1px solid #d6e5f0;border-radius:8px;background:#eaf4fb;color:#2b6792;padding:9px 13px;font-size:12px;font-weight:700}
.mgr-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.mgr-kpi{background:#fff;border:1px solid var(--edge);border-radius:12px;padding:17px;box-shadow:0 5px 15px #21476909;min-width:0}
.mgr-kpi small{display:block;color:#637c8f;font-size:12px;font-weight:650;line-height:1.5}
.mgr-kpi strong{display:block;color:#21547c;font-size:clamp(25px,2.3vw,34px);line-height:1.4;letter-spacing:-.04em;font-variant-numeric:tabular-nums}
.mgr-kpi span{font-size:10px;color:#7b91a1}
.mgr-panels{display:grid;grid-template-columns:1.5fr 1fr;gap:13px}
.mgr-card{background:#fff;border:1px solid var(--edge);border-radius:12px;padding:19px;margin-bottom:15px;box-shadow:0 5px 17px #224d7009;min-width:0}
.mgr-card h2{color:#204f74;font-size:18px;letter-spacing:-.03em;margin:0 0 13px}
.mgr-card p{color:#708494;line-height:1.6;margin:0 0 14px;font-size:12px}
.mgr-muted{font-size:12px;color:var(--muted);line-height:1.55}
.mgr-banner{background:#eff7fc;border-left:3px solid #397eb0;padding:12px 15px;border-radius:5px;margin-bottom:17px;color:#376686;font-size:12px;line-height:1.55}
.mgr-row{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:10px 0}
.mgr-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;align-items:end;margin:0 0 14px}
.mgr-form label{font-size:12px;color:#526c81;font-weight:650;display:block;min-width:0}
.mgr-form :is(input,select,textarea){display:block;width:100%;min-height:42px;padding:9px 11px;margin-top:5px;border:1px solid #cadce9;border-radius:8px;background:#fff;color:#264659}
.mgr-form textarea{resize:vertical;min-height:75px}
.mgr-form .wide{grid-column:1/-1}
.mgr-primary,.mgr-outline,.mgr-danger{min-height:39px;border-radius:8px;font-size:12px;font-weight:720;padding:9px 13px}
.mgr-primary{background:#176397;color:#fff;border:1px solid #176397}
.mgr-primary:hover{background:#104e79}
.mgr-outline{background:#fff;color:#24638f;border:1px solid #cedfea}
.mgr-outline:hover{background:#eff6fb}
.mgr-danger{background:#fff;color:#a1454c;border:1px solid #e6cfd3}
.mgr-danger:hover{background:#fff2f3}
.mgr-table-wrap{overflow-x:auto;max-width:100%;border:1px solid var(--edge);border-radius:10px}
.mgr-table{width:100%;border-collapse:collapse;font-size:12px;min-width:650px}
.mgr-table th{background:#eaf3f9;color:#466782;text-align:left;font-size:11px;padding:12px;white-space:nowrap}
.mgr-table td{border-top:1px solid #eaf0f4;padding:11px 12px;vertical-align:top;line-height:1.55}
.mgr-table tbody tr:hover{background:#f9fcfe}
.mgr-actions{display:flex;gap:6px;flex-wrap:wrap}
.mgr-actions button,.mgr-actions a{font-size:11px;min-height:34px;padding:6px 9px;border-radius:7px}
.mgr-chip{display:inline-block;font-weight:740;color:#286c92;background:#eaf4fb;border:1px solid #d6e8f3;padding:5px 9px;border-radius:100px;font-size:11px}
.mgr-chip.warn{background:#fff7e8;color:#996421;border-color:#f0dfbd}
.mgr-chip.bad{background:#fff1f1;color:#aa4c4b;border-color:#ecced0}
.mgr-chip.good{background:#eaf7ef;color:#2b7954;border-color:#d2ebdb}
.mgr-entry{border:1px solid #e0e8ef;border-radius:10px;padding:15px;margin:10px 0;background:#fff}
.mgr-entry h3{font-size:14px;color:#214d71;margin:0 0 8px}
.mgr-goal-options{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:8px;margin:8px 0}
.mgr-goal-option{display:flex!important;align-items:center;gap:8px;background:#f5f9fc;border:1px solid #dfe9f0;border-radius:8px;padding:9px 10px;font-size:12px!important;cursor:pointer}
.mgr-goal-option input{height:17px;width:17px;accent-color:#176397;flex:none}
.mgr-goal-figures{font-weight:700;color:#205d8b!important;margin:13px 0 5px!important}
.mgr-entry .mgr-evidence{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;background:#f7fafc;border:1px solid #e6eef4;border-radius:8px;padding:12px;font-size:12px;max-height:250px;overflow:auto}
.mgr-entry .mgr-evidence small{color:#708799}
.mgr-warning{font-size:12px;color:#a45d34;background:#fff6ee;border-left:3px solid #cf995b;padding:10px}
.mgr-bars{display:flex;align-items:end;gap:7px;min-height:150px;padding-top:20px;border-bottom:1px solid #d9e7f0}
.mgr-bar{background:#4b95c9;border-radius:5px 5px 0 0;min-width:13px;flex:1;position:relative}
.mgr-bar:hover{background:#216b9f}
.mgr-bar span{position:absolute;bottom:100%;left:50%;transform:translateX(-50%);font-size:10px;color:#638095}
.mgr-bar-labels{display:flex;gap:7px;margin-top:5px}
.mgr-bar-labels span{flex:1;min-width:13px;text-align:center;font-size:9px;color:#70899a}
.mgr-hidden{display:none!important}
.mgr-toast{position:fixed;bottom:20px;right:20px;background:#173e5f;color:#fff;padding:13px 17px;border-radius:10px;box-shadow:0 12px 30px #143d5b30;font-size:12px;z-index:50;max-width:320px}
.mgr-alert{border-bottom:1px solid #e6eef4;padding:12px 2px;font-size:12px}
.mgr-alert:last-child{border:0}
.mgr-alert strong{display:block;color:#214f73;margin-bottom:4px}
.mgr-quality{font-size:12px;margin-top:9px;padding:8px 10px;border-radius:7px;background:#f3f7fa}
.mgr-body-link{display:inline-flex;gap:5px;padding:5px 0;font-size:12px}
.mgr-footnote{font-size:11px;color:#8193a2;margin-top:18px;line-height:1.55}
@media(max-width:1180px){.mgr-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.mgr-panels{grid-template-columns:1fr}}
@media(max-width:900px){.mgr-shell{grid-template-columns:1fr}.mgr-side{display:flex;flex-direction:row;overflow-x:auto;padding:10px;border-bottom:1px solid var(--edge);border-right:0;gap:6px}.mgr-side-heading{display:none}.mgr-tab{white-space:nowrap;font-size:12px;padding:10px}.mgr-work{padding:16px}}
@media(max-width:520px){.mgr-top{padding:12px}.mgr-brand strong{font-size:15px}.mgr-work{padding:12px}.mgr-grid{gap:8px}.mgr-kpi{padding:13px}.mgr-kpi strong{font-size:24px}.mgr-form{grid-template-columns:1fr}.mgr-card{padding:14px}.mgr-role{font-size:11px}.mgr-tab-icon{display:none}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
"""

SCRIPT=r"""
<script>
(()=>{
const ROLE=__ROLE__;
const $=id=>document.getElementById(id);
async function refreshOwnName(){
 try{
  const result=await api('/me/profile');
  $('mgr-my-name').textContent=result.full_name||'ФИО не заполнено — укажите в профиле';
 }catch(e){$('mgr-my-name').textContent='Имя руководителя временно недоступно'}
}
const labels={assigned:"Назначено",in_progress:"В работе",submitted:"На согласовании",returned:"Возвращено",completed:"Завершено",cancelled:"Отменено",approved:"Согласовано",rejected:"Отклонено"};
let cache={tasks:[],team:[],memos:[],alerts:[],thematic:[],campaigns:[],options:null,quality:null,dashboard:null,users:[]};
let currentTab="overview";
const tag=(name,cls="",value="")=>{const x=document.createElement(name);if(cls)x.className=cls;if(value!==undefined)x.textContent=String(value);return x};
const status=(s)=>{const text=labels[s]||s||"Неизвестно";const cls=["completed","approved"].includes(s)?"good":["returned","cancelled","rejected"].includes(s)?"bad":["submitted"].includes(s)?"warn":"";return tag("span","mgr-chip "+cls,text)};
const fmt=d=>d?new Date(d).toLocaleDateString("ru-RU"):"—";
function toast(t){const b=$("mgr-toast");b.textContent=t;b.classList.remove("mgr-hidden");clearTimeout(window._mgrToast);window._mgrToast=setTimeout(()=>b.classList.add("mgr-hidden"),4000)}
async function api(url,method="GET",body){
 const opts={method,credentials:"same-origin",cache:"no-store",headers:{}};
 if(body!==undefined){opts.headers["Content-Type"]="application/json";opts.body=JSON.stringify(body)}
 const res=await fetch(url,opts);
 if(res.status===401){location.href="/account/login";throw Error("Войдите в учётную запись")}
 const payload=await res.json().catch(()=>({}));
 if(!res.ok)throw Error(typeof payload.detail==="string"?payload.detail:("Ошибка запроса "+res.status));
 return payload;
}
function mountRows(id,items){const root=$(id);root.replaceChildren();for(const x of items)root.append(x)}
function link(href,label){const a=tag("a","mgr-body-link",label);a.href=href;return a}
function action(label,cls,callback){const b=tag("button",cls,label);b.type="button";b.onclick=async()=>{b.disabled=true;try{await callback()}catch(e){toast(e.message)}finally{b.disabled=false}};return b}
function cell(content){const td=tag("td");if(content&&typeof content==="object"&&content.nodeType)td.append(content);else td.textContent=String(content??"—");return td}
function show(name){
 currentTab=name;
 document.querySelectorAll(".mgr-tab").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.tab===name)));
 document.querySelectorAll(".mgr-section").forEach(x=>x.classList.toggle("mgr-hidden",x.dataset.section!==name));
 const titles={overview:"Панель управления",tasks:"Поручения и сроки",findings:"Согласование нарушений",thematic:"Тематические проверки подразделения",quality:"Контроль качества",memos:"Служебные записки",team:"Сотрудники",reports:"Отчёт руководителя",history:"Журнал действий"};
 $("mgr-title").textContent=titles[name]||"Кабинет руководителя";
 if(name==="findings")fillFindingTarget();
}
document.querySelectorAll(".mgr-tab").forEach(x=>x.onclick=()=>show(x.dataset.tab));
function metrics(d){
 for(const [name,value] of [["staff",d.staff],["tasks",d.tasks_total],["overdue",d.tasks_overdue],
   ["pending",d.tasks_pending],["findings",d.real_confirmed_findings],
   ["accepted",d.manager_approved_findings],["documents",d.real_documents_checked],
   ["memos",d.memos_pending]]){
  $( "mgr-kpi-"+name).textContent=value===null||value===undefined?"—":Number(value).toLocaleString("ru-RU");
 }
 $("mgr-accuracy").textContent=d.metric_note;
 const bars=$("mgr-bars"),labels=$("mgr-bars-labels");bars.replaceChildren();labels.replaceChildren();
 const records=[...(d.tasks_by_day||[])].reverse(),max=Math.max(1,...records.map(x=>x.count));
 if(!records.length){bars.append(tag("p","mgr-muted","Поручения ещё не назначались."));return}
 for(const r of records){
  const bar=tag("div","mgr-bar");bar.style.height=Math.max(7,r.count/max*110)+"px";bar.title=fmt(r.day)+": "+r.count;
  bar.append(tag("span","",r.count));bars.append(bar);
  labels.append(tag("span","",String(r.day).slice(5)));
 }
}
function alerts(data){
 const root=$("mgr-alerts");root.replaceChildren();
 if(!data.length){root.append(tag("p","mgr-muted","Срочных действий пока нет."));return}
 for(const a of data.slice(0,8)){
  const line=tag("div","mgr-alert");
  line.append(tag("strong","",a.title),tag("div","mgr-muted",a.description||""));
  root.append(line);
 }
}
function populateWorkers(){
 const fields=[$("mgr-worker"),$("mgr-finding-worker"),$("mgr-goal-employee")];
 const team=cache.team.filter(x=>x.role==="user"&&x.status==="approved");
 for(const field of fields){
  const previous=field.value;field.replaceChildren();
  const first=document.createElement("option");first.value="";first.textContent="Выберите сотрудника";field.append(first);
  for(const item of team){const o=document.createElement("option");o.value=item.id;o.textContent=item.full_name?item.full_name+' · '+item.email:item.email;field.append(o)}
  field.value=team.some(x=>x.id===previous)?previous:"";
 }
}
function renderTasks(){
 const body=$("mgr-task-rows"),items=[];
 const raw=$("mgr-task-search").value.trim().toLowerCase(),filter=$("mgr-task-status").value;
 for(const t of cache.tasks.filter(x=>(!filter||x.status===filter)&&(!raw||
    [x.employee,x.reg_number,x.title].join(" ").toLowerCase().includes(raw)))){
  const row=tag("tr");
  const title=tag("div");
  title.append(tag("strong","",t.title),tag("div","mgr-muted",t.reg_number));
  row.append(cell(title),cell(displayEmployee(t.employee)),cell(t.due_date?fmt(t.due_date):"Без срока"),cell(status(t.status)));
  const commands=tag("div","mgr-actions");
  commands.append(action("Нарушения","mgr-outline",async()=>{await openFindings(t.user_id,t.reg_number);show("findings")}));
  if(t.status==="submitted"){
   commands.append(action("Завершить","mgr-primary",async()=>{
    if(!confirm("Закрыть поручение после рассмотрения результатов?"))return;
    await api("/manager/tasks/"+t.id+"/decision","POST",{action:"complete",comment:""});
    toast("Поручение закрыто");await reload();
   }));
   commands.append(action("Вернуть","mgr-outline",async()=>{
    const comment=prompt("Укажите замечания сотруднику (не менее 5 символов)");
    if(comment===null)return;
    await api("/manager/tasks/"+t.id+"/decision","POST",{action:"return",comment});
    toast("Поручение возвращено");await reload();
   }));
  }
  if(!["completed","cancelled"].includes(t.status)){
   commands.append(action("Отменить","mgr-danger",async()=>{
    if(!confirm("Отменить поручение?"))return;
    await api("/manager/tasks/"+t.id+"/decision","POST",{action:"cancel",comment:""});
    toast("Поручение отменено");await reload();
   }));
  }
  row.append(cell(commands));items.push(row);
 }
 mountRows("mgr-task-rows",items);
 $("mgr-task-empty").classList.toggle("mgr-hidden",!!items.length);
}
function renderMemos(){
 const rows=[];
 for(const memo of cache.memos){
  const tr=tag("tr");
  tr.append(cell("№ "+memo.reg_number),cell(memo.employee),cell(fmt(memo.submitted_at)),cell(status(memo.status)));
  const commands=tag("div","mgr-actions");
  for(const format of ["odt","pdf"]){const a=link("/manager/memos/"+memo.id+"/download/"+format,format.toUpperCase());a.className="mgr-outline";commands.append(a)}
  if(memo.status==="submitted"){
   commands.append(action("Согласовать","mgr-primary",async()=>{
    if(!confirm("Согласовать служебную записку?"))return;
    await api("/manager/memos/"+memo.id+"/decision","POST",{decision:"approved",comment:""});
    toast("Согласовано");await reload();
   }));
   for(const [decision,label] of [["returned","Доработка"],["rejected","Отклонить"]]){
    commands.append(action(label,"mgr-outline",async()=>{
     const comment=prompt("Причина решения (не менее 5 символов)");
     if(comment===null)return;
     await api("/manager/memos/"+memo.id+"/decision","POST",{decision,comment});
     toast("Решение сохранено");await reload();
    }));
   }
  }else if(memo.comment){commands.append(tag("span","mgr-muted",memo.comment))}
  tr.append(cell(commands));rows.push(tr);
 }
 mountRows("mgr-memo-rows",rows);
 $("mgr-memo-empty").classList.toggle("mgr-hidden",!!rows.length);
}
function renderTeam(){
 const rows=[];
 for(const person of cache.team){
  const tr=tag("tr");
  tr.append(cell(person.full_name||'ФИО не заполнено'),cell(person.role==="manager"?"Руководитель":"Сотрудник"),
    cell(person.manager_email||"Не назначен"),cell(person.status));
  const commands=tag("div","mgr-actions");
  commands.append(action("Поручения","mgr-outline",async()=>{
    $("mgr-task-search").value=person.email;show("tasks");renderTasks();
  }));
  tr.append(cell(commands));rows.push(tr);
 }
 mountRows("mgr-team-rows",rows);
 $("mgr-team-empty").classList.toggle("mgr-hidden",!!rows.length);
}
async function renderMailStatus(){
 if(ROLE!=="admin")return;
 const note=$("mgr-mail-status");
 const button=$("mgr-mail-test");
 if(!note||!button)return;
 try{
  const result=await api("/manager/admin/mail/status");
  button.disabled=!result.configured;
  note.textContent=result.configured
   ?"Отправка через "+(result.provider==="resend"?"Resend HTTPS":"SMTP")+
    ". Принято сервисом: "+result.accepted+", ожидают: "+result.pending+
    ", исчерпали попытки: "+result.failed+"."
   :"Почта не настроена. Для Railway Hobby требуются EIS223_RESEND_API_KEY и EIS223_RESEND_FROM.";
 }catch(error){button.disabled=true;note.textContent="Не удалось проверить очередь писем: "+error.message}
}
function renderAdmin(){
 const root=$("mgr-admin-section");root.classList.toggle("mgr-hidden",ROLE!=="admin");
 if(ROLE!=="admin")return;
 const managers=cache.users.filter(x=>x.role==="manager"&&x.status==="approved"),rows=[];
 const query=($("mgr-admin-search")?.value||"").trim().toLocaleLowerCase("ru-RU");
 const selectedStatus=$("mgr-admin-status")?.value||"all";
 for(const person of cache.users){
  if(query&&!(person.full_name+" "+person.email+" "+person.role).toLocaleLowerCase("ru-RU").includes(query))continue;
  if(selectedStatus!=="all"&&person.status!==selectedStatus)continue;
  const tr=tag("tr");
  const roleLabels={user:"Сотрудник",manager:"Руководитель",admin:"Администратор"};
  tr.append(cell(person.full_name||person.email),cell(person.status),cell(roleLabels[person.role]||person.role));
  const controls=tag("div","mgr-actions");
  if(person.status==="approved"&&!person.is_self){
   const roleSelect=tag("select");
   roleSelect.setAttribute("aria-label","Роль пользователя "+person.email);
   for(const [value,label] of Object.entries(roleLabels)){
     const option=tag("option","",label);option.value=value;roleSelect.append(option);
   }
   roleSelect.value=person.role;
   roleSelect.style.maxWidth="175px";
   controls.append(roleSelect,action("Назначить роль","mgr-primary",async()=>{
     const next=roleSelect.value;
     if(next===person.role){toast("Роль не изменилась");return}
     const warning=next==="admin"
       ?"Предоставить ПОЛНЫЕ права администратора пользователю "+person.email+"?"
       :"Назначить роль «"+roleLabels[next]+"» пользователю "+person.email+"?";
     if(!confirm(warning)){roleSelect.value=person.role;return}
     await api("/manager/admin/users/"+person.id+"/role","POST",{role:next});
     toast("Роль назначена. Почтовое уведомление будет отправлено, если отправитель настроен.");
     await reload();
   }));
   if(person.role==="user"){
    const select=document.createElement("select");
    select.setAttribute("aria-label","Назначить руководителя");
    select.style.maxWidth="170px";
    const empty=document.createElement("option");empty.value="";empty.textContent="Без руководителя";select.append(empty);
    for(const leader of managers){const option=document.createElement("option");option.value=leader.id;option.textContent=leader.email;select.append(option)}
    select.value=person.manager_id||"";
    controls.append(select,action("Сохранить","mgr-primary",async()=>{
       await api("/manager/admin/team/"+person.id,"PUT",{manager_id:select.value||null});
       toast("Подразделение обновлено");await reload();
    }));
   }
  }
  tr.append(cell(controls));rows.push(tr);
 }
 mountRows("mgr-admin-rows",rows);
 renderMailStatus();
}
function renderCampaignOptions(){
 if(!cache.options)return;
 function fill(id,values){
  const root=$(id);if(root.childElementCount)return;
  for(const [code,name] of Object.entries(values)){
   const label=tag("label","mgr-goal-option");
   const check=document.createElement("input");check.type="checkbox";check.value=code;
   check.checked=["dates","docs","ООО","АО"].includes(code);
   label.append(check,tag("span","",name));root.append(label);
  }
 }
 fill("mgr-goal-categories",cache.options.categories||{});
 fill("mgr-goal-forms",cache.options.legal_forms||{});
}
function displayEmployee(email){
 const person=cache.team.find(x=>x.email===email);
 return person?.full_name?person.full_name+' · '+email:email;
}
function renderCampaigns(){
 const root=$("mgr-goal-results");root.replaceChildren();
 const items=cache.campaigns||[];
 if(!items.length){root.append(tag("p","mgr-muted","Тематических поручений пока нет."));return}
 for(const item of items){
  const card=tag("article","mgr-entry");
  card.append(tag("h3","",item.title),
   tag("div","mgr-muted","Исполнитель: "+displayEmployee(item.employee)+" · Формы: "+item.legal_forms.join(", ")),
   tag("div","mgr-muted",item.category_names.join(", ")));
  const result=tag("p","mgr-goal-figures",
   "Подтверждено "+item.actual_violations+" из целевых "+item.target_violations+
   " · закупок в очереди: "+item.verified_procurements);
  card.append(result);
  const progress=document.createElement("progress");progress.max=100;progress.value=item.progress_percent;
  progress.style.width="100%";progress.setAttribute("aria-label","Выполнение планового показателя");
  card.append(progress);
  const state=tag("div","mgr-muted",
   "Задание: "+(labels[item.status]||item.status)+
   " · ЕИС: "+item.engine_status+
   (item.due_date?" · срок "+fmt(item.due_date):""));
  card.append(state);
  if(item.overdue)card.append(tag("div","mgr-warning","Срок поручения истёк"));
  const commands=tag("div","mgr-actions");
  if(item.status==="setup_failed"||item.status==="preparing")
   commands.append(action("Повторить запуск","mgr-primary",async()=>{
    await api("/manager/campaigns/"+item.id+"/retry","POST");
    toast("Тематическая очередь запущена");await reload();
   }));
  if(item.status==="submitted"){
   commands.append(action("Принять результат","mgr-primary",async()=>{
    if(!confirm("Завершить проверку по фактическим результатам? Цель не гарантируется."))return;
    await api("/manager/campaigns/"+item.id+"/decision","POST",{action:"complete",comment:""});
    await reload();
   }));
   commands.append(action("Вернуть на доработку","mgr-outline",async()=>{
    const comment=prompt("Причина доработки (не менее 5 символов)");
    if(comment===null)return;
    await api("/manager/campaigns/"+item.id+"/decision","POST",{action:"return",comment});
    await reload();
   }));
  }
  if(["active","submitted","setup_failed"].includes(item.status))
   commands.append(action("Остановить","mgr-danger",async()=>{
    if(!confirm("Остановить поручение без удаления сохранённых оригиналов?"))return;
    await api("/manager/campaigns/"+item.id+"/decision","POST",{action:"cancel",comment:""});
    await reload();
   }));
  card.append(commands);root.append(card);
 }
}
$("mgr-goal-form").onsubmit=async event=>{
 event.preventDefault();const form=event.currentTarget,button=form.querySelector('button[type="submit"]');
 const picked=id=>[...$(id).querySelectorAll("input:checked")].map(x=>x.value);
 const categories=picked("mgr-goal-categories"),legal_forms=picked("mgr-goal-forms");
 const data=Object.fromEntries(new FormData(form).entries());
 data.categories=categories;data.legal_forms=legal_forms;
 data.target_violations=Number(data.target_violations);
 data.due_date=data.due_date||null;
 if(!categories.length||!legal_forms.length){toast("Отметьте категории и формы заказчиков");return}
 button.disabled=true;
 try{
  const result=await api("/manager/campaigns","POST",data);
  $("mgr-goal-status").textContent="Поручение создано. Проверка ЕИС: "+result.status;
  toast("Тематическая проверка запущена");await reload();
 }catch(e){$("mgr-goal-status").textContent=e.message;toast(e.message)}
 finally{button.disabled=false}
};
function renderThematic(){
 const rows=[];
 for(const item of cache.thematic||[]){
  const tr=tag("tr");
  const cats=Array.isArray(item.categories)?item.categories.join(", "):"Тематическая проверка";
  tr.append(cell(item.employee),cell(cats||"Тематическая проверка"),
    cell(fmt(item.created_at)),cell(item.purchases||0),cell(item.violations||0),cell(status(item.status)));
  rows.push(tr);
 }
 mountRows("mgr-thematic-rows",rows);
 $("mgr-thematic-empty").classList.toggle("mgr-hidden",!!rows.length);
}
function renderQuality(){
 const data=cache.quality,root=$("mgr-quality-items");root.replaceChildren();
 if(!data){root.append(tag("p","mgr-muted","Состояние доказательств временно недоступно"));return}
 $("mgr-quality-summary").textContent="Проверено последних закупок: "+data.checked_latest_procurements+
 " · Выявленных эпизодов: "+data.reviewed_finding_episodes+
 " · Требуют уточнения доказательств: "+data.needs_human_verification;
 const issues=data.issues||[];
 if(!issues.length){root.append(tag("p","mgr-muted","В просмотренном объёме дополнительных замечаний нет."));return}
 for(const item of issues){
  const card=tag("article","mgr-entry");
  card.append(tag("h3","",item.title),tag("p","mgr-muted",
    "№ "+item.reg_number+" · "+item.employee));
  card.append(tag("p","mgr-warning",(item.warnings||[]).join(" · ")));
  card.append(action("Открыть доказательства","mgr-outline",async()=>{
    await openFindings(item.user_id,item.reg_number);show("findings");
  }));
  root.append(card);
 }
}
function reportNumbers(data){
 const root=$("mgr-report-summary");root.replaceChildren();
 const lines=[
  ["Сотрудники",data.employees],
  ["Самостоятельные проверки",data.manual_checks_started],
  ["Закупок без повторного подсчёта",data.unique_procurements],
  ["Завершено проверок",data.verified_legal_reviews],
  ["Прочитано документов",data.documents_checked],
  ["Нарушений",data.confirmed_violations],
  ["Поручения",data.tasks_created],
  ["Просрочено поручений",data.tasks_overdue],
  ["Согласовано нарушений",data.finding_decisions?.approved||0],
  ["Согласовано записок",data.memo_decisions?.approved||0]
 ];
 for(const [label,value] of lines){
  const el=tag("div","mgr-kpi");el.append(tag("small","",label));
  el.append(tag("strong","",value===null?"—":value));
  root.append(el);
 }
 const rows=[];
 for(const person of data.per_employee||[]){
  const tr=tag("tr");
  tr.append(cell(person.employee),cell(person.manual_checks),cell(person.procurements_reviewed),
            cell(person.documents_checked),cell(person.confirmed_violations),cell(person.assigned_tasks));
  rows.push(tr);
 }
 mountRows("mgr-report-rows",rows);
 $("mgr-report-note").textContent=data.metric_note+" "+data.limits;
 $("mgr-report-status").textContent="Отчёт построен по реальным сохранённым данным.";
}
$("mgr-report-form").onsubmit=async event=>{
 event.preventDefault();
 const fd=new FormData(event.currentTarget),from=String(fd.get("from")||""),
  to=String(fd.get("to")||"");
 if(!from||!to||from>to)return toast("Выберите корректный период");
 const query="?"+new URLSearchParams({from,to}).toString();
 const button=$("mgr-report-submit");button.disabled=true;
 try{
  reportNumbers(await api("/manager/report"+query));
  $("mgr-report-csv").href="/manager/report.csv"+query;
  $("mgr-report-csv").classList.remove("mgr-hidden");
 }catch(e){toast(e.message)}finally{button.disabled=false}
};
function renderHistory(items){
 const rows=[];
 for(const item of items){
  const tr=tag("tr");
  tr.append(cell(fmt(item.created_at)),cell(item.actor||"—"),
    cell(item.type+" · "+item.action),cell(item.details||"—"));
  rows.push(tr);
 }
 mountRows("mgr-history-rows",rows);
}
function fillFindingTarget(){
 if($("mgr-finding-reg").value&&$("mgr-finding-worker").value)return;
 const first=cache.tasks.find(x=>["submitted","in_progress","returned"].includes(x.status));
 if(first){$("mgr-finding-worker").value=first.user_id;$("mgr-finding-reg").value=first.reg_number}
}
async function openFindings(employee,reg){
 $("mgr-finding-worker").value=employee;$("mgr-finding-reg").value=reg;
 await loadFindings();
}
async function loadFindings(){
 const employee=$("mgr-finding-worker").value,reg=$("mgr-finding-reg").value.trim();
 if(!employee||!/^3[0-9]{10}$/.test(reg))return toast("Выберите сотрудника и номер закупки ЕИС");
 const data=await api("/manager/findings/"+encodeURIComponent(employee)+"/"+reg);
 const root=$("mgr-finding-items");root.replaceChildren();
 let personalNotes=[];
 try{personalNotes=await api("/manager/cases/"+encodeURIComponent(employee)+
  "/"+encodeURIComponent(reg)+"/notes")}catch(e){/* Notes may be unavailable while evidence loads */}
 $("mgr-finding-note").textContent="Найдено подтверждённых анализатором эпизодов: "+data.findings.length+". Решения руководителя сохраняются отдельно.";
 await renderProposals(employee,reg);
 await renderDiscussion(employee,reg);
 if(!data.findings.length){root.append(tag("p","mgr-muted","Для этой закупки подтверждённых нарушений пока нет."));return}
 for(const item of data.findings){
  const card=tag("article","mgr-entry");
  card.append(tag("span","mgr-chip","Карточка нарушения"),tag("h3","",item.title),
   tag("div","mgr-muted","Норма: "+(item.law||"требует уточнения")));
  const correlated=personalNotes.filter(n=>!n.finding_key||n.finding_key===item.finding_key);
 for(const note of correlated.slice(0,8)){
  card.append(tag("div","mgr-quality",
   "Замечание сотрудника · "+fmt(note.created_at)+": "+note.body));
 }
 const evidence=tag("div","mgr-evidence",item.evidence||"Доказательство отсутствует");
  card.append(evidence);
  if(item.document||item.source_url){
   const note=tag("div","mgr-muted","Документ: "+(item.document||"—"));
   card.append(note);
   if(item.source_url&&item.source_url.startsWith("https://zakupki.gov.ru/"))
    card.append(link(item.source_url,"Открыть оригинал ЕИС ↗"));
  }
  for(const ref of item.references||[]){
   if(ref.url&&ref.url.startsWith("https://www.consultant.ru/")){
    const a=link(ref.url,ref.label+" · КонсультантПлюс ↗");
    a.target="_blank";a.rel="noopener noreferrer";card.append(a);
   }
  }
  if(item.warnings.length)card.append(tag("p","mgr-warning",item.warnings.join(" · ")));
  const current=item.review;
  if(current){
   card.append(tag("div","mgr-quality","Решение руководителя: "+(labels[current.decision]||current.decision)+
     (current.comment?" · "+current.comment:"")));
  }
  const buttons=tag("div","mgr-actions");
  for(const [decision,label,css] of [["approved","Подтвердить","mgr-primary"],["returned","На доработку","mgr-outline"],["rejected","Отклонить","mgr-danger"]]){
   buttons.append(action(label,css,async()=>{
    if(decision==="approved"&&item.warnings.length)return toast("Для утверждения сначала устраните замечания к доказательствам");
    let comment="";
    if(decision!=="approved"){
     comment=prompt("Обоснование решения (не менее 5 символов)")??"";
     if(!comment)return;
    }else if(!confirm("Подтвердить нарушение по указанному доказательству?"))return;
    await api("/manager/findings/"+employee+"/"+reg+"/"+item.finding_key+"/decision",
      "POST",{decision,comment});
    toast("Решение сохранено, исходные материалы не изменены");await loadFindings();
   }));
  }
  card.append(buttons);root.append(card);
 }
}
async function renderProposals(employee,reg){
 const node=$("mgr-manual-proposals");node.replaceChildren();
 try{
  const rows=await api('/manager/workbench/'+employee+'/'+reg+'/proposals');
  if(!rows.length){node.append(tag('p','mgr-muted','Ручных предложений сотрудника нет'));return}
  for(const item of rows){
   const card=tag("article","mgr-entry");
   card.append(tag("h3","",item.title),tag("p","mgr-muted","Статья: "+item.law),
    tag("div","mgr-evidence",item.evidence),
    tag("div","mgr-muted","Статус: "+item.status));
   if(item.source_url.startsWith('https://zakupki.gov.ru/')){
    const linkEl=link(item.source_url,'Оригинал ЕИС ↗');
    linkEl.target='_blank';linkEl.rel='noopener noreferrer';card.append(linkEl)
   }
   if(item.status==="needs_review"){
    const commands=tag('div','mgr-actions');
    for(const [decision,label] of [['approved','Подтвердить вручную'],['rejected','Отклонить']]){
     commands.append(action(label,'mgr-outline',async()=>{
      const comment=decision==='rejected'?prompt('Укажите причину отклонения'):prompt('Комментарий к согласованию (по желанию)');
      if(comment===null)return;
      await api('/manager/workbench/'+employee+'/'+reg+'/proposals/'+item.id+'/decision',
        'POST',{decision,comment});
      toast('Решение сохранено отдельно от автоматических нарушений');
      await renderProposals(employee,reg);
     }));
    }
    card.append(commands);
   }
   node.append(card);
  }
 }catch(e){node.append(tag('p','mgr-muted','Ручные предложения временно недоступны'))}
}
async function renderDiscussion(employee,reg){
 const node=$("mgr-case-discussion");node.replaceChildren();
 try{
  const entries=await api('/manager/workbench/'+employee+'/'+reg+'/discussion');
  for(const item of entries){
   const box=tag('div','mgr-entry');
   box.append(tag('b','',item.author),tag('div','mgr-muted',
     new Date(item.created_at).toLocaleString('ru-RU')),
    tag('p','',item.body));node.append(box);
  }
 }catch(e){node.append(tag('p','mgr-muted','Обсуждение пока недоступно'))}
 const form=$('mgr-case-message');
 form.onsubmit=async e=>{
  e.preventDefault();
  const body=String(new FormData(form).get('body')||'').trim();
  try{await api('/manager/workbench/'+employee+'/'+reg+'/discussion','POST',{body});
   form.reset();await renderDiscussion(employee,reg)}
  catch(err){toast(err.message)}
 };
}
$("mgr-finding-load").onclick=()=>loadFindings().catch(e=>toast(e.message));
$("mgr-task-search").oninput=renderTasks;
$("mgr-task-status").onchange=renderTasks;
$("mgr-task-create").onsubmit=async e=>{
 e.preventDefault();const form=e.target,button=form.querySelector("button");button.disabled=true;
 const fd=new FormData(form);
 const data={assignee_id:fd.get("user_id"),reg_number:String(fd.get("reg_number")||"").trim(),
   title:String(fd.get("title")||"").trim(),due_date:fd.get("due_date")||null,
   note:String(fd.get("note")||"")};
 try{await api("/manager/tasks","POST",data);form.reset();toast("Поручение назначено");await reload();show("tasks")}
 catch(error){toast(error.message)}finally{button.disabled=false}
};
async function reload(){
 const paths=[["dashboard","/manager/dashboard"],["tasks","/manager/tasks"],["team","/manager/team"],
  ["memos","/manager/memos"],["alerts","/manager/alerts"],
  ["thematic","/manager/thematic"],["quality","/manager/quality"],["campaigns","/manager/campaigns"],["options","/manager/campaigns/options"]];
 const all=await Promise.allSettled(paths.map(async([key,url])=>[key,await api(url)]));
 for(const a of all){
  if(a.status==="fulfilled")cache[a.value[0]]=a.value[1];
  else toast("Некоторые данные временно недоступны");
 }
 if(ROLE==="admin"){
  try{cache.users=await api("/manager/admin/users")}catch(e){toast(e.message)}
 }
 if(cache.dashboard)metrics(cache.dashboard);
 alerts(cache.alerts||[]);
 populateWorkers();renderTasks();renderMemos();renderTeam();renderAdmin();renderThematic();renderQuality();renderCampaigns();renderCampaignOptions();
 $("mgr-refresh").disabled=false;
}
if(ROLE==="admin"){
 const mailTest=$("mgr-mail-test");
 if(mailTest)mailTest.onclick=async()=>{
  mailTest.disabled=true;
  try{
   const result=await api("/manager/admin/mail/test","POST");
   toast(result.note||"Тестовое письмо поставлено в очередь");
  }catch(error){toast(error.message)}
  finally{await renderMailStatus()}
 };
}
for(const id of ["mgr-admin-search","mgr-admin-status"]){
 const field=$(id);if(field)field.addEventListener(id==="mgr-admin-search"?"input":"change",renderAdmin);
}
const mgrToday=new Date();
$("mgr-report-form").elements.namedItem("to").value=mgrToday.toISOString().slice(0,10);
const mgrPast=new Date(mgrToday.getTime()-29*86400000);
$("mgr-report-form").elements.namedItem("from").value=mgrPast.toISOString().slice(0,10);
$("mgr-refresh").onclick=async()=>{const btn=$("mgr-refresh");btn.disabled=true;try{await reload();toast("Сведения обновлены")}finally{btn.disabled=false}};
$("mgr-history-refresh").onclick=async()=>{
 try{renderHistory(await api("/manager/history"));toast("Журнал актуален")}
 catch(e){toast(e.message)}
};
refreshOwnName();
reload();
})();
</script>
"""


def render_manager(role):
    if role not in ("manager","admin"):
        raise ValueError("Manager role required")
    shell=r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Кабинет руководителя — ЕИС Аудитор</title>
__STYLE__</head><body>
<header class="mgr-top"><div class="mgr-brand"><div class="mgr-logo">223</div>
<div><strong>ЕИС Аудитор</strong><small>Кабинет руководителя · независимая аналитическая система</small>
<small id="mgr-my-name">ФИО: загрузка…</small></div></div>
<div class="mgr-actions"><a href="/account/profile">Моё ФИО</a>
<a href="/">← Главная страница</a></div></header>
<div class="mgr-shell">
<nav class="mgr-side" aria-label="Разделы руководителя">
<span class="mgr-side-heading">УПРАВЛЕНИЕ</span>
<button type="button" class="mgr-tab" data-tab="overview" aria-selected="true"><span class="mgr-tab-icon">▦</span>Обзор</button>
<button type="button" class="mgr-tab" data-tab="tasks" aria-selected="false"><span class="mgr-tab-icon">☷</span>Поручения</button>
<button type="button" class="mgr-tab" data-tab="thematic" aria-selected="false"><span class="mgr-tab-icon">◈</span>Тематические</button>
<button type="button" class="mgr-tab" data-tab="findings" aria-selected="false"><span class="mgr-tab-icon">⚖</span>Нарушения</button>
<button type="button" class="mgr-tab" data-tab="quality" aria-selected="false"><span class="mgr-tab-icon">✓</span>Качество</button>
<button type="button" class="mgr-tab" data-tab="memos" aria-selected="false"><span class="mgr-tab-icon">▤</span>Записки</button>
<button type="button" class="mgr-tab" data-tab="team" aria-selected="false"><span class="mgr-tab-icon">♙</span>Сотрудники</button>
<button type="button" class="mgr-tab" data-tab="reports" aria-selected="false"><span class="mgr-tab-icon">▥</span>Отчёты</button>
<button type="button" class="mgr-tab" data-tab="history" aria-selected="false"><span class="mgr-tab-icon">◷</span>Журнал действий</button>
</nav>
<main class="mgr-work"><div class="mgr-head"><div><h1 id="mgr-title">Панель управления</h1>
<p>Контроль фактических результатов проверок закупок по 223-ФЗ</p></div>
<div class="mgr-row"><span class="mgr-role">__LABEL__</span>
<button type="button" id="mgr-refresh" class="mgr-outline">Обновить данные ↻</button></div></div>
<div class="mgr-banner">Числа получены из сохранённых заданий и проверенных данных ЕИС.
Выявленное программой нарушение и согласованное руководителем нарушение показываются отдельно.
Поручение само по себе не запускает проверку ЕИС.</div>
<section class="mgr-section" data-section="overview">
<div class="mgr-grid">
<div class="mgr-kpi"><small>Сотрудники подразделения</small><strong id="mgr-kpi-staff">—</strong><span>Назначены администратором</span></div>
<div class="mgr-kpi"><small>Поручения</small><strong id="mgr-kpi-tasks">—</strong><span>Сохранённые задания</span></div>
<div class="mgr-kpi"><small>Просроченные поручения</small><strong id="mgr-kpi-overdue">—</strong><span>С действующими сроками</span></div>
<div class="mgr-kpi"><small>Ожидают решения</small><strong id="mgr-kpi-pending">—</strong><span>Сданы сотрудниками</span></div>
<div class="mgr-kpi"><small>Найдено нарушений</small><strong id="mgr-kpi-findings">—</strong><span>Программный юридический анализ</span></div>
<div class="mgr-kpi"><small>Согласовано руководителем</small><strong id="mgr-kpi-accepted">—</strong><span>Самостоятельные решения</span></div>
<div class="mgr-kpi"><small>Прочитано оригиналов</small><strong id="mgr-kpi-documents">—</strong><span>Фактически учтённые документы</span></div>
<div class="mgr-kpi"><small>Записки на согласовании</small><strong id="mgr-kpi-memos">—</strong><span>Ожидают рассмотрения</span></div>
</div>
<div class="mgr-panels">
<div class="mgr-card"><h2>Динамика выдачи поручений</h2><p>Количество реально созданных заданий по датам</p>
<div id="mgr-bars" class="mgr-bars"></div><div id="mgr-bars-labels" class="mgr-bar-labels"></div>
</div>
<div class="mgr-card"><h2>Требуют внимания</h2>
<div id="mgr-alerts"><p>Получаем события...</p></div></div></div>
<p id="mgr-accuracy" class="mgr-footnote"></p>
</section>
<section class="mgr-section mgr-hidden" data-section="tasks">
<div class="mgr-card"><h2>Назначить проверку сотруднику</h2>
<p>Создаёт поручение, не подменяя запуск фактической проверки ЕИС.</p>
<form id="mgr-task-create" class="mgr-form">
<label>Исполнитель<select name="user_id" id="mgr-worker" required><option value="">Загрузка…</option></select></label>
<label>Регистрационный номер ЕИС<input name="reg_number" maxlength="11" pattern="3[0-9]{10}" inputmode="numeric" required placeholder="32616412317"></label>
<label class="wide">Тема поручения<input name="title" minlength="3" maxlength="180" required placeholder="Проверка соблюдения сроков размещения"></label>
<label>Срок исполнения<input name="due_date" type="date"></label>
<label class="wide">Комментарий<textarea name="note" maxlength="2000" placeholder="Что необходимо проверить"></textarea></label>
<button class="mgr-primary" type="submit">Назначить поручение</button>
</form></div>
<div class="mgr-card"><h2>Поручения подразделения</h2>
<div class="mgr-row"><input id="mgr-task-search" type="search" placeholder="Поиск по номеру, теме или сотруднику" aria-label="Поиск поручений">
<select id="mgr-task-status" aria-label="Фильтр статуса">
<option value="">Все статусы</option><option value="assigned">Назначены</option><option value="in_progress">В работе</option><option value="submitted">Сданы</option><option value="returned">Возвращены</option><option value="completed">Завершены</option><option value="cancelled">Отменены</option>
</select></div>
<div class="mgr-table-wrap"><table class="mgr-table"><thead><tr>
<th>Поручение / закупка</th><th>Исполнитель</th><th>Срок</th><th>Статус</th><th>Действия</th>
</tr></thead><tbody id="mgr-task-rows"></tbody></table></div>
<p class="mgr-muted mgr-hidden" id="mgr-task-empty">Поручений пока нет.</p></div>
</section>
<section class="mgr-section mgr-hidden" data-section="thematic">
<div class="mgr-card"><h2>Новое тематическое поручение сотруднику</h2>
<p>Выберите направления проверки, формы заказчиков и целевое число подтверждённых нарушений.
План не является требованием получить нарушения любой ценой. Проверяются только реальные документы ЕИС.</p>
<form id="mgr-goal-form" class="mgr-form">
<label>Исполнитель<select id="mgr-goal-employee" name="assignee_id" required><option value="">Выберите сотрудника</option></select></label>
<label>Цель: подтверждённых нарушений<input type="number" name="target_violations" min="1" max="10000" value="10" required></label>
<label class="wide">Название задания<input name="title" minlength="5" maxlength="170" required placeholder="Проверить сроки и документацию заказчиков ООО и АО"></label>
<div class="wide"><b>Категории нарушений</b><div id="mgr-goal-categories" class="mgr-goal-options"></div></div>
<div class="wide"><b>Организационно-правовые формы заказчиков</b><div id="mgr-goal-forms" class="mgr-goal-options"></div></div>
<label>Срок исполнения<input type="date" name="due_date"></label>
<label class="wide">Пояснение к поручению<textarea name="note" maxlength="1800" placeholder="Укажите область и особенности проверки"></textarea></label>
<button class="mgr-primary" type="submit">Назначить и запустить тематическую проверку</button>
</form><p class="mgr-muted" id="mgr-goal-status" role="status"></p>
</div>
<div class="mgr-card"><h2>План и фактические результаты</h2>
<p>Динамика подтягивается из настоящей тематической очереди. Повторы не увеличивают число нарушений.</p>
<div id="mgr-goal-results"></div>
</div>
<div class="mgr-card"><h2>Тематические проверки сотрудников</h2>
<p>Только реальные запуски ЕИС, закреплённые за сотрудниками подразделения.
Количество нарушений рассчитывается без повторного подсчёта одинаковых эпизодов.</p>
<div class="mgr-table-wrap"><table class="mgr-table"><thead><tr>
<th>Исполнитель</th><th>Направления</th><th>Дата</th><th>Закупок</th><th>Нарушений</th><th>Статус</th>
</tr></thead><tbody id="mgr-thematic-rows"></tbody></table></div>
<p id="mgr-thematic-empty" class="mgr-muted mgr-hidden">Тематических проверок пока нет.</p></div>
</section>
<section class="mgr-section mgr-hidden" data-section="quality">
<div class="mgr-card"><h2>Контроль полноты доказательств</h2>
<p>Проверяем, есть ли правовая норма, документ и фактическое доказательство.
Недостаток данных не превращается в новое нарушение и блокирует утверждение.</p>
<div id="mgr-quality-summary" class="mgr-banner">Сведения загружаются…</div>
<div id="mgr-quality-items"></div></div>
</section>
<section class="mgr-section mgr-hidden" data-section="findings">
<div class="mgr-card"><h2>Рассмотрение нарушений</h2>
<p>Выберите исполнителя и закупку. Отображаются только подтверждённые анализатором нарушения из реальных записей.
Недостаток доказательств блокирует согласование, но не удаляет запись.</p>
<div class="mgr-form">
<label>Сотрудник<select id="mgr-finding-worker"><option value="">Выберите сотрудника</option></select></label>
<label>Номер ЕИС<input id="mgr-finding-reg" maxlength="11" inputmode="numeric" placeholder="Номер закупки"></label>
<button class="mgr-primary" id="mgr-finding-load" type="button">Показать нарушения</button></div>
<p id="mgr-finding-note" class="mgr-muted">Выберите закупку для проверки доказательств.</p>
<div id="mgr-finding-items"></div>
<h2>Нарушения, добавленные сотрудником</h2>
<p>Предложения рассматриваются отдельно и не увеличивают счётчик автоматических нарушений.</p>
<div id="mgr-manual-proposals"></div>
<h2>Обсуждение дела с сотрудником</h2>
<div id="mgr-case-discussion"></div>
<form id="mgr-case-message" class="mgr-form">
<label class="wide">Сообщение или замечание<textarea name="body" minlength="4" maxlength="2000" required></textarea></label>
<button class="mgr-primary" type="submit">Отправить сотруднику</button></form>
</div>
</section>
<section class="mgr-section mgr-hidden" data-section="memos">
<div class="mgr-card"><h2>Служебные записки на согласовании</h2>
<p>Руководитель рассматривает сохранённые оригиналы ODT, может получить PDF,
согласовать документ или вернуть на доработку с замечаниями.</p>
<div class="mgr-table-wrap"><table class="mgr-table"><thead><tr>
<th>Закупка</th><th>Автор</th><th>Дата подачи</th><th>Решение</th><th>Действия</th>
</tr></thead><tbody id="mgr-memo-rows"></tbody></table></div>
<p id="mgr-memo-empty" class="mgr-muted mgr-hidden">Записок на согласовании нет.</p></div>
</section>
<section class="mgr-section mgr-hidden" data-section="team">
<div class="mgr-card"><h2>Сотрудники подразделения</h2>
<p>Руководитель видит только назначенных ему сотрудников. Администратор назначает руководителей
и состав подразделений без ручного ввода идентификаторов.</p>
<div class="mgr-table-wrap"><table class="mgr-table"><thead><tr>
<th>Учётная запись</th><th>Роль</th><th>Руководитель</th><th>Доступ</th><th>Действия</th>
</tr></thead><tbody id="mgr-team-rows"></tbody></table></div>
<p id="mgr-team-empty" class="mgr-muted mgr-hidden">Сотрудники ещё не назначены.</p>
</div>
<div class="mgr-card mgr-hidden" id="mgr-admin-section">
<h2>Администрирование ролей и подразделений</h2>
<p>Изменяйте роли уже зарегистрированных сотрудников и руководителей. Все изменения фиксируются в журнале; назначение администратора требует отдельного подтверждения.</p>
<div class="mgr-row" style="margin-bottom:13px">
<label style="min-width:180px;flex:1">Найти пользователя
<input id="mgr-admin-search" type="search" placeholder="ФИО или адрес регистрации" style="width:100%"></label>
<label style="min-width:150px">Статус
<select id="mgr-admin-status"><option value="all">Все учётные записи</option>
<option value="approved">Подтверждённые</option><option value="pending">Ожидают</option>
<option value="blocked">Заблокированные</option><option value="rejected">Отклонённые</option></select></label>
</div>
<div class="mgr-table-wrap"><table class="mgr-table"><thead><tr>
<th>Учётная запись</th><th>Доступ</th><th>Роль</th><th>Управление</th>
</tr></thead><tbody id="mgr-admin-rows"></tbody></table></div>
<div style="margin-top:14px;background:#f7fafc;border:1px solid #dce6ed;border-radius:10px;padding:16px">
<h3 style="margin:0 0 6px;font-size:15px">Почтовые уведомления</h3>
<p id="mgr-mail-status" class="mgr-muted" role="status" aria-live="polite">Проверяю настройки отправки…</p>
<button id="mgr-mail-test" class="mgr-outline" type="button" disabled>Отправить тестовое письмо на мою почту</button>
<p class="mgr-muted">Используется регистрационный адрес администратора. Счётчик отправки означает принятие письма почтовым сервисом.</p>
</div></div>
</section>
<section class="mgr-section mgr-hidden" data-section="reports">
<div class="mgr-card"><h2>Отчёт подразделения</h2>
<p>Формируется по реальным заданиям и сохранённым результатам ЕИС без искусственного увеличения показателей.</p>
<form id="mgr-report-form" class="mgr-form">
<label>Дата с<input type="date" name="from" required></label>
<label>Дата по<input type="date" name="to" required></label>
<button type="submit" class="mgr-primary" id="mgr-report-submit">Сформировать отчёт</button>
<a id="mgr-report-csv" class="mgr-outline mgr-hidden" href="#" download>Скачать CSV / Excel</a>
</form>
<div id="mgr-report-summary" class="mgr-grid"></div>
<div class="mgr-table-wrap"><table class="mgr-table"><thead><tr>
<th>Сотрудник</th><th>Запуски</th><th>Завершено</th><th>Документы</th><th>Нарушения</th><th>Поручения</th>
</tr></thead><tbody id="mgr-report-rows"></tbody></table></div>
<p class="mgr-muted" id="mgr-report-status">Выберите период для отчёта.</p>
<p class="mgr-muted" id="mgr-report-note"></p></div></section>
<section class="mgr-section mgr-hidden" data-section="history">
<div class="mgr-card"><div class="mgr-row"><h2>Журнал управленческих решений</h2>
<button class="mgr-outline" id="mgr-history-refresh" type="button">Загрузить историю</button></div>
<p>Все назначения, возвраты, согласования и решения сохраняются с исполнителем и датой.
История не изменяет исходные доказательства ЕИС.</p>
<div class="mgr-table-wrap"><table class="mgr-table"><thead><tr>
<th>Дата</th><th>Действие выполнил</th><th>Тип решения</th><th>Примечание</th>
</tr></thead><tbody id="mgr-history-rows"></tbody></table></div></div>
</section>
</main></div>
<div id="mgr-toast" class="mgr-toast mgr-hidden" role="status" aria-live="polite"></div>
__SCRIPT__</body></html>"""
    return (shell.replace("__STYLE__",STYLE)
                 .replace("__SCRIPT__",SCRIPT.replace("__ROLE__",json.dumps(role)))
                 .replace("__LABEL__","Роль: "+("Администратор" if role=="admin" else "Руководитель")))
