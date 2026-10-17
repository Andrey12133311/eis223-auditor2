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
@media(max-width:760px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}form{grid-template-columns:minmax(0,1fr)}.tabs{padding-top:13px}}
:focus-visible{outline:3px solid #92bddb;outline-offset:3px}
</style>
"""
JS=r"""
<script>
(()=>{
const el=id=>document.getElementById(id),msg=text=>{el('staff-status').textContent=text};
const regOK=x=>/^3[0-9]{10}$/.test(x);
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
function section(tab){
 document.querySelectorAll('[data-staff-section]').forEach(x=>x.hidden=x.dataset.staffSection!==tab);
 document.querySelectorAll('[data-staff-tab]').forEach(x=>x.setAttribute("aria-selected",String(x.dataset.staffTab===tab)));
}
document.querySelectorAll('[data-staff-tab]').forEach(x=>x.onclick=()=>section(x.dataset.staffTab));
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
async function campaigns(){
 try{
  const data=await api('/me/campaign-goals');
  list('staff-campaign-list',data,item=>{
   const box=make("article");box.className="entry";
   box.append(make("h3",item.title),make("p","Категории: "+item.category_names.join(", ")),
    make("p","Формы заказчиков: "+item.legal_forms.join(", ")),
    make("p","Руководитель установил план "+item.target_violations+
     " нарушений. Фактически найдено: "+item.actual_violations+
     ". Закупок в очереди: "+item.verified_procurements));
   const pr=make("progress");pr.className="progress";pr.max=100;pr.value=item.progress_percent;
   box.append(pr,make("small","Срок: "+(item.due_date||"не установлен")+
    " · задание: "+item.status+" · обработчик ЕИС: "+item.engine_status));
   const row=make("div");row.className="action-row";
   row.append(link('/account/thematic','Мои тематические проверки'),
    link('/thematic#'+encodeURIComponent(item.batch_id),'Открыть настоящую очередь ЕИС'));
   if(item.status==="active"){
    const submit=make("button","Передать руководителю");submit.className="outline";
    submit.onclick=async()=>{if(!confirm("Передать фактические результаты, даже если план не выполнен?"))return;
      try{await api('/me/campaign-goals/'+item.id+'/submit','POST');await campaigns()}
      catch(e){msg(e.message)}};
    row.append(submit);
   }
   box.append(row);return box;
  });
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
 for(const cb of [loadChecklist,loadManual,loadRelated,loadDiscussion,loadPreferences,loadVersions])await cb(reg);
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
  el("staff-reminder-days").value=pref.notification_days;
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
   notification_days:Number(el("staff-reminder-days").value)});
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
async function loadCalendar(){
 try{
  const result=await api('/me/workbench/calendar');
  list('staff-calendar-list',result.events||[],x=>{
   const box=make('article');box.className='entry';
   box.append(make('h3',x.title),
    make('p','Срок: '+x.due_date+' · '+(x.overdue?'Просрочено':
     x.days_left+' дней осталось')+' · '+x.status));return box;
  });
 }catch(e){msg(e.message)}
}
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
loadCalendar();loadHistory();loadLibrary();
options().catch(e=>msg(e.message));
reloadOverview();campaigns();templates();
})();
</script>
"""
PAGE=r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Рабочее место сотрудника — ЕИС Аудитор</title>__CSS__</head>
<body><header><strong>ЕИС Аудитор · Рабочее место сотрудника</strong>
<a href="/">← В личный кабинет</a></header>
<main>
<h1>Моё рабочее пространство</h1>
<p>Здесь находятся только реальные проверки, поручения и материалы вашей учётной записи.</p>
<div class="grid">
<div class="metric"><small>Самостоятельные проверки</small><strong id="staff-checks">—</strong></div>
<div class="metric"><small>Завершён анализ</small><strong id="staff-complete">—</strong></div>
<div class="metric"><small>Нарушения</small><strong id="staff-found">—</strong></div>
<div class="metric"><small>Прочитано документов</small><strong id="staff-documents">—</strong></div>
<div class="metric"><small>Ожидают документы</small><strong id="staff-waiting">—</strong></div>
<div class="metric"><small>Служебные записки</small><strong id="staff-memos">—</strong></div>
</div>
<nav class="tabs" aria-label="Разделы рабочего места">
<button type="button" data-staff-tab="goals" aria-selected="true">Поручения руководителя</button>
<button type="button" data-staff-tab="bulk" aria-selected="false">Массовая проверка</button>
<button type="button" data-staff-tab="templates" aria-selected="false">Мои шаблоны</button>
<button type="button" data-staff-tab="case" aria-selected="false">Контроль закупки</button>
<button type="button" data-staff-tab="activity" aria-selected="false">Календарь и история</button>
<button type="button" data-staff-tab="library" aria-selected="false">База нарушений</button>
</nav>
<section class="panel" data-staff-section="goals">
<h2>Тематические задания от руководителя</h2>
<p class="notice">Вы видите выбранные категории и формы заказчиков, план и реальные нарушения.
Недостижение целевого показателя не считается нарушением сотрудника и не создаёт ложных результатов.</p>
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
<div class="action-row">
<a id="staff-case-link" href="#">Электронное дело и оригиналы ЕИС</a>
<a id="staff-case-download" href="#">Выгрузить материалы ZIP</a>
<button class="outline" id="staff-recheck" type="button">Перепроверить оригиналы ЕИС</button>
<a href="/account/memos">Архив служебных записок</a></div>
<fieldset><legend>Персональные настройки</legend>
<label class="check-options"><input id="staff-priority" type="checkbox"> Приоритетная закупка</label>
<label>Напоминать за количество дней<input id="staff-reminder-days" type="number" min="0" max="30" value="3"></label>
<button class="outline" type="button" id="staff-save-pref">Сохранить настройки</button></fieldset>
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
<h2>Календарь сроков и поручений</h2>
<p>Сроки из реальных поручений. Приближение срока определяется по вашим настройкам.</p>
<div id="staff-calendar-list"></div>
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
<div id="staff-status" role="status" aria-live="polite"></div>
</main>__JS__</body></html>"""

def render_workbench():
    return PAGE.replace("__CSS__",CSS).replace("__JS__",JS)
