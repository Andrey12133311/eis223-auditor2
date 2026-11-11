"""Progressive display preferences over the existing registry/manual controls.

Display choices may persist in this browser. Saved filter values are restricted
to the current tab and authenticated identity; unavailable storage stays in memory.
An explicit filter save/load may read the existing owner profile when identity
has not already been supplied. This module never starts procurement checks.
"""

STYLE = r'''<style id="eis-workspace-preferences-style">
.eis-view-preferences{position:relative;margin:10px 0 16px;color:#1d1d1f;font:13px/1.5 var(--ui-font,system-ui)}
/* The restored registry gives every details/button an important theme rule. */
body.eis-shell-ready details#eis-view-preferences-registry.eis-view-preferences{
 border:0!important;background:transparent!important;padding:0!important;box-shadow:none!important}
.eis-view-preferences>summary{display:inline-flex;align-items:center;gap:7px;cursor:pointer;list-style:none;
 min-height:36px;padding:7px 11px;border:1px solid #dedee3;border-radius:8px;background:#fff;font-weight:550;box-sizing:border-box}
.eis-view-preferences>summary::-webkit-details-marker{display:none}
.eis-view-preferences>summary:after{content:'⌄';color:#636366;font-size:13px}
.eis-view-preferences[open]>summary:after{content:'⌃'}
.eis-view-preferences .eis-preference-content{max-width:660px;margin-top:8px;padding:17px;border:1px solid #dedee3;border-radius:12px;background:#fff;box-shadow:0 3px 12px #1d1d1f05}
.eis-view-preferences fieldset{min-width:0;border:0;margin:0 0 16px;padding:0}
.eis-view-preferences legend{font-size:12px;font-weight:650;padding:0;margin-bottom:8px}
.eis-view-preferences .eis-preference-density{display:inline-flex;gap:3px;padding:3px;background:#f5f5f7;border-radius:8px}
body .eis-view-preferences button,body.eis-shell-ready details.eis-view-preferences button{
 min-height:36px!important;padding:7px 11px!important;border:1px solid #dedee3!important;
 border-radius:7px!important;background:#fff!important;color:#515154!important;font:500 12px/1.4 var(--ui-font,system-ui)!important;box-shadow:none!important;cursor:pointer}
body .eis-view-preferences button:hover:not(:disabled),body.eis-shell-ready details.eis-view-preferences button:hover:not(:disabled){
 background:#ededf1!important;border-color:#c9c9cf!important;color:#1d1d1f!important}
body .eis-view-preferences .eis-preference-density button,body.eis-shell-ready details.eis-view-preferences .eis-preference-density button{
 border-color:transparent!important;background:transparent!important;color:#515154!important}
body .eis-view-preferences .eis-preference-density button[aria-pressed=false]:hover:not(:disabled),
body.eis-shell-ready details.eis-view-preferences .eis-preference-density button[aria-pressed=false]:hover:not(:disabled){
 background:#ededf1!important;border-color:transparent!important;color:#1d1d1f!important}
body .eis-view-preferences .eis-preference-density button[aria-pressed=true],
body.eis-shell-ready details.eis-view-preferences .eis-preference-density button[aria-pressed=true],
body .eis-view-preferences .eis-preference-density button[aria-pressed=true]:hover:not(:disabled),
body.eis-shell-ready details.eis-view-preferences .eis-preference-density button[aria-pressed=true]:hover:not(:disabled){
 background:#fff!important;border-color:#dedee3!important;color:#1d1d1f!important}
body .eis-view-preferences button:disabled,body.eis-shell-ready details.eis-view-preferences button:disabled{
 cursor:default;opacity:.5!important;background:#fff!important;color:#515154!important;border-color:#dedee3!important}
.eis-view-preferences .eis-preference-columns{display:flex;gap:7px 16px;flex-wrap:wrap}
.eis-view-preferences label.eis-preference-column{display:flex!important;flex-direction:row!important;align-items:center;gap:7px!important;color:#515154;font-size:12px;font-weight:400;min-height:28px;margin:0!important;cursor:pointer}
.eis-view-preferences .eis-preference-column input{width:15px!important;height:15px!important;min-height:15px!important;margin:0;padding:0!important;accent-color:#0066cc;flex:none}
.eis-view-preferences .eis-preference-column input:disabled+span{color:#636366}
.eis-view-preferences .eis-preference-note{font-size:11px;color:#636366;margin:7px 0 12px;line-height:1.55}
.eis-view-preferences .eis-preference-save,.eis-view-preferences .eis-preference-use{display:flex;gap:7px;align-items:end;flex-wrap:wrap}
.eis-view-preferences .eis-preference-save{margin-bottom:10px}
.eis-view-preferences .eis-preference-field{display:flex!important;flex-direction:column!important;gap:5px!important;font-size:11px;font-weight:500;min-width:160px;flex:1;margin:0!important}
.eis-view-preferences :is(input[type=text],select){box-sizing:border-box;width:100%;min-width:0;min-height:36px;padding:7px 10px;border:1px solid #d2d2d7;border-radius:7px;background:#fff;color:#1d1d1f;font:13px/1.4 var(--ui-font,system-ui)}
.eis-view-preferences .eis-preference-status{min-height:18px;margin:11px 0 0;color:#636366;font-size:11px;overflow-wrap:anywhere}
.eis-view-preferences :is(summary,button,input,select):focus-visible{outline:3px solid #86b7ed!important;outline-offset:3px}
.eis-display-table [data-eis-column-hidden=true]{display:none!important}
@media(min-width:651px){body .eis-display-table.eis-density-compact :is(th,td){padding-top:8px!important;padding-bottom:8px!important;font-size:12px!important}
 body .eis-display-table.eis-density-comfortable :is(th,td){padding-top:15px!important;padding-bottom:15px!important}}
@media(max-width:650px){.eis-view-preferences .eis-preference-content{padding:14px}.eis-view-preferences .eis-preference-columns{gap:4px 12px}
 .eis-view-preferences .eis-preference-column{min-height:40px}.eis-view-preferences .eis-preference-field{flex-basis:100%}
 .eis-view-preferences .eis-preference-save>button{width:100%}.eis-view-preferences .eis-preference-use>button{flex:1}
 body .eis-display-table.eis-density-compact tbody tr{gap:9px!important}body .eis-display-table.eis-density-comfortable tbody tr{gap:16px!important}}
@media(pointer:coarse){body .eis-view-preferences :is(summary,button,input[type=text],select){min-height:44px!important}.eis-view-preferences label.eis-preference-column{min-height:44px}}
@media(prefers-reduced-motion:reduce){.eis-view-preferences *{transition:none!important;scroll-behavior:auto!important}}
</style>'''


SCRIPT = r'''<script id="eis-workspace-preferences-script">
(()=>{
'use strict';
if(window.__eisWorkspacePreferencesReady)return;window.__eisWorkspacePreferencesReady=true;
const adapters=new Map(),displayPrefix='eis223.display.v1.',filterPrefix='eis223.filters.v1.';
let identity='',ownerKey='',identitySequence=0,identityWork=null,ownerRead=null,scheduled=false;
const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node};
const button=text=>{const node=make('button',text);node.type='button';return node};
function readStore(kind,key){try{const value=window[kind].getItem(key);return value&&value.length<24000?JSON.parse(value):null}catch{return null}}
function writeStore(kind,key,value){try{window[kind].setItem(key,JSON.stringify(value));return true}catch{return false}}
function canStore(kind){try{const store=window[kind],key='eis223.preferences.probe';store.setItem(key,'1');store.removeItem(key);return true}catch{return false}}
let sessionReady=canStore('sessionStorage');
function allowedSets(value){
 if(!Array.isArray(value))return [];
 return value.slice(0,6).filter(x=>x&&typeof x.name==='string'&&x.name.trim()&&x.name.length<=40&&Array.isArray(x.fields)&&x.fields.length<=80)
  .map(x=>({name:x.name,fields:x.fields.filter(f=>f&&typeof f.key==='string'&&f.key.length<=180&&
    ['text','search','number','date','month','checkbox','radio','select-one','select-multiple','textarea'].includes(f.type)&&
    (typeof f.value==='boolean'||typeof f.value==='string'&&f.value.length<=500||Array.isArray(f.value)&&f.value.length<=40&&f.value.every(v=>typeof v==='string'&&v.length<=200)))}));
}
function filterControls(adapter){
 return [...adapter.filters.querySelectorAll('input,select,textarea')].filter(field=>!field.disabled&&
  !['hidden','submit','button','reset','file','password','email','url','range','color'].includes(field.type)&&
  !field.closest('.eis-view-preferences'));
}
function controlKey(field,index){return field.id?'id:'+field.id:field.name?'name:'+field.name+':'+field.type+':'+index:'index:'+index+':'+field.type}
function snapshot(adapter){
 return filterControls(adapter).map((field,index)=>({key:controlKey(field,index),type:field.type||'textarea',
  value:['checkbox','radio'].includes(field.type)?field.checked:field.multiple?[...field.selectedOptions].map(x=>x.value):field.value}));
}
function message(adapter,text){adapter.status.textContent=text}
function persistDisplay(adapter){
 const value={version:1,density:adapter.density,hidden:[...adapter.hidden]};
 if(!writeStore('localStorage',displayPrefix+adapter.kind,value))message(adapter,'Вид изменён на этой странице. Браузер не разрешает сохранить настройку.');
}
function persistSets(adapter){
 if(!ownerKey||!sessionReady)return false;
 return writeStore('sessionStorage',filterPrefix+ownerKey+'.'+adapter.kind,adapter.sets);
}
function storageNote(adapter,persisted){
 adapter.note.textContent=ownerKey&&sessionReady&&persisted!==false?'Наборы доступны вашей учётной записи в этой вкладке до её закрытия.':
  'Наборы доступны только на этой странице; после её закрытия они не сохранятся.';
}
function renderSets(adapter){
 const previous=adapter.select.value;adapter.select.replaceChildren();
 adapter.select.append(new Option(adapter.sets.length?'Выберите набор':'Сохранённых наборов нет',''));
 adapter.sets.forEach((item,index)=>adapter.select.append(new Option(item.name,String(index))));
 adapter.select.value=adapter.sets[Number(previous)]&&previous!==''?previous:'';
 adapter.select.disabled=!adapter.sets.length;if(adapter.load)adapter.load.hidden=!!ownerKey;syncSetActions(adapter);
}
function syncSetActions(adapter){const ready=adapter.select.value!==''&&!!adapter.sets[Number(adapter.select.value)];adapter.apply.disabled=!ready;adapter.remove.disabled=!ready}
function applyDisplay(adapter){
 const table=adapter.table,headers=[...table.querySelectorAll('thead tr:first-child th')];
 const headerSignature=headers.map((th,index)=>th.textContent.trim()+'#'+index).join('|');
 if(!headers.length||headers.some(th=>th.colSpan>1))return;
 table.classList.add('eis-display-table');
 table.classList.toggle('eis-density-compact',adapter.density==='compact');
 table.classList.toggle('eis-density-comfortable',adapter.density==='comfortable');
 const identityColumns=new Set();
 for(const row of table.querySelectorAll('tbody tr'))[...row.children].forEach((cell,index)=>{
  if(cell.querySelector('a[href^="/check-new/"],button,input,select,textarea')||(!headers[index]?.textContent.trim()&&cell.querySelector('a[href]')))identityColumns.add(index);
 });
 const columns=headers.map((th,index)=>({key:th.textContent.trim()+'#'+index,label:th.textContent.trim()||'Столбец '+(index+1),
  locked:index===0||identityColumns.has(index)||/^(номер(?: закупки)?|закупка(?: и дата)?|идентификатор)$/i.test(th.textContent.trim())||/действ|источник|открыть/i.test(th.textContent)}));
 const signature=headerSignature+'|'+columns.map(column=>column.locked?'1':'0').join('');
 if(signature!==adapter.signature){
  adapter.signature=signature;adapter.columns.replaceChildren();
  adapter.hidden=new Set([...adapter.hidden].filter(key=>columns.some(c=>c.key===key&&!c.locked)));
  columns.forEach(column=>{
   const label=make('label'),input=make('input'),name=make('span',column.label);
   label.className='eis-preference-column';input.type='checkbox';input.checked=!adapter.hidden.has(column.key);input.disabled=column.locked;
   if(column.locked)name.title='Всегда показывается';
   input.addEventListener('change',()=>{if(input.checked)adapter.hidden.delete(column.key);else adapter.hidden.add(column.key);applyDisplay(adapter);persistDisplay(adapter)});
   label.append(input,name);adapter.columns.append(label);
  });
 }
 const hidden=columns.map(c=>!c.locked&&adapter.hidden.has(c.key));
 for(const row of table.querySelectorAll('tr')){
  const cells=[...row.children].filter(x=>x.tagName==='TD'||x.tagName==='TH');
  cells.forEach((cell,index)=>{
   const hide=cells.length===columns.length&&cell.colSpan===1&&hidden[index]===true;
   if(hide&&cell.contains(document.activeElement))adapter.details.querySelector('summary').focus({preventScroll:true});
   if(hide)cell.setAttribute('data-eis-column-hidden','true');else cell.removeAttribute('data-eis-column-hidden');
  });
 }
}
function restore(adapter,record){
 let ignored=0;const fields=filterControls(adapter),current=new Map(fields.map((field,index)=>[controlKey(field,index),field]));
 for(const saved of record.fields){
  const field=current.get(saved.key);if(!field||(field.type||'textarea')!==saved.type){ignored++;continue}
  if(['checkbox','radio'].includes(field.type)){if(typeof saved.value==='boolean')field.checked=saved.value;else ignored++;continue}
  if(field.tagName==='SELECT'){
   const values=Array.isArray(saved.value)?saved.value:[saved.value],allowed=[...field.options].filter(x=>!x.disabled).map(x=>x.value);
   if(values.some(value=>!allowed.includes(value))){ignored++;continue}
   if(field.multiple)for(const option of field.options)option.selected=values.includes(option.value);else field.value=values[0];
  }else if(typeof saved.value==='string'){field.value=saved.value;if(field.value!==saved.value)ignored++}else ignored++;
 }
 // Manual filters render locally; registry selects can submit themselves on
 // change, so its whole saved set goes through the existing Apply handler once.
 if(adapter.kind==='manual')for(const field of fields){field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}))}
 if(adapter.kind==='registry'){
  const apply=[...adapter.filters.querySelectorAll('button,input[type=submit]')].find(node=>/^(Применить|Найти|Показать|Поиск)$/i.test((node.textContent||node.value||'').trim()));
  const form=adapter.filters.matches('form')?adapter.filters:adapter.filters.closest('form');
  if(apply&&(!form||form.method.toLowerCase()==='get'))apply.click();
  else if(form&&form.method.toLowerCase()==='get')form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  document.dispatchEvent(new CustomEvent('eis:filters-restored',{detail:{surface:'registry'}}));
 }
 message(adapter,ignored?'Набор применён. Недоступные условия пропущены.':'Набор фильтров применён.');
}
function setup(kind,filters,table){
 const existing=adapters.get(kind);
 if(existing&&existing.filters===filters){existing.table=table;applyDisplay(existing);return}
 if(existing)existing.details.remove();
 const prefs=readStore('localStorage',displayPrefix+kind),details=make('details'),summary=make('summary','Вид'),content=make('div');
 details.className='eis-view-preferences';details.id='eis-view-preferences-'+kind;details.dataset.eisPreferenceSurface=kind;summary.setAttribute('aria-label','Настроить вид списка');content.className='eis-preference-content';
 const adapter={kind,filters,table,details,density:prefs?.version===1&&prefs.density==='compact'?'compact':'comfortable',
  hidden:new Set(prefs?.version===1&&Array.isArray(prefs.hidden)?prefs.hidden.filter(x=>typeof x==='string'&&x.length<180).slice(0,30):[]),signature:'',sets:[],status:make('p')};
 const density=make('fieldset'),densityLegend=make('legend','Плотность строк'),densityButtons=make('div');densityButtons.className='eis-preference-density';
 for(const [key,label] of [['comfortable','Обычная'],['compact','Компактная']]){
  const choice=button(label);choice.dataset.eisDensity=key;choice.setAttribute('aria-pressed',String(adapter.density===key));
  choice.addEventListener('click',()=>{adapter.density=key;for(const b of densityButtons.children)b.setAttribute('aria-pressed',String(b===choice));applyDisplay(adapter);persistDisplay(adapter)});densityButtons.append(choice);
 }
 density.append(densityLegend,densityButtons);
 const cols=make('fieldset'),colsLegend=make('legend','Столбцы');adapter.columns=make('div');adapter.columns.className='eis-preference-columns';cols.append(colsLegend,adapter.columns);
 const sets=make('fieldset'),setsLegend=make('legend','Мои фильтры'),saveLine=make('div'),nameLabel=make('label','Название набора'),name=make('input'),save=button('Сохранить фильтры');
 name.type='text';name.maxLength=40;name.placeholder='Например, «В работе»';name.autocomplete='off';nameLabel.className='eis-preference-field';nameLabel.append(name);saveLine.className='eis-preference-save';saveLine.append(nameLabel,save);
 adapter.note=make('p');adapter.note.className='eis-preference-note';storageNote(adapter);
 const useLine=make('div'),selectLabel=make('label','Сохранённые наборы');adapter.select=make('select');adapter.apply=button('Применить');adapter.remove=button('Удалить набор');adapter.load=button('Загрузить мои наборы');
 useLine.className='eis-preference-use';selectLabel.className='eis-preference-field';selectLabel.append(adapter.select);useLine.append(selectLabel,adapter.apply,adapter.remove,adapter.load);sets.append(setsLegend,adapter.note,saveLine,useLine);
 adapter.status.className='eis-preference-status';adapter.status.setAttribute('role','status');adapter.status.setAttribute('aria-live','polite');
 save.addEventListener('click',async()=>{
  if(save.disabled)return;
  const label=name.value.trim();if(!label){message(adapter,'Укажите название набора фильтров.');name.focus();return}
  const fields=snapshot(adapter);if(!fields.length){message(adapter,'На этой странице нет доступных фильтров.');return}
  const previousIdentity=identity;save.disabled=true;
  try{
   await resolveOwner();
   if(previousIdentity&&identity!==previousIdentity){message(adapter,'Учётная запись изменилась. Сохраните набор повторно.');return}
   const index=adapter.sets.findIndex(x=>x.name.toLocaleLowerCase('ru')===label.toLocaleLowerCase('ru'));
   if(index<0&&adapter.sets.length>=6){message(adapter,'В этой вкладке можно сохранить до шести наборов. Удалите ненужный.');return}
   const record={name:label,fields};if(index>=0)adapter.sets[index]=record;else adapter.sets.push(record);
   const persisted=persistSets(adapter);storageNote(adapter,persisted);renderSets(adapter);adapter.select.value=String(index>=0?index:adapter.sets.length-1);syncSetActions(adapter);
   message(adapter,index>=0?'Набор фильтров обновлён.':'Набор фильтров сохранён.');
  }finally{save.disabled=false}
 });
 adapter.load.addEventListener('click',async()=>{if(adapter.load.disabled)return;adapter.load.disabled=true;try{const ready=await resolveOwner();message(adapter,ready&&sessionReady?'Наборы этой вкладки загружены.':ready?'Браузер не разрешает сохранить наборы. Они доступны на этой странице.':'Не удалось загрузить наборы. Сохранение на этой странице доступно.')}finally{adapter.load.disabled=false}});
 adapter.select.addEventListener('change',()=>syncSetActions(adapter));
 adapter.apply.addEventListener('click',()=>{const record=adapter.sets[Number(adapter.select.value)];if(adapter.select.value!==''&&record)restore(adapter,record)});
 adapter.remove.addEventListener('click',()=>{if(adapter.select.value==='')return;adapter.sets.splice(Number(adapter.select.value),1);const persisted=persistSets(adapter);storageNote(adapter,persisted);renderSets(adapter);message(adapter,'Набор удалён. Текущие фильтры сохранены на экране.');if(adapter.sets.length)adapter.select.focus();else name.focus()});
 details.append(summary,content);content.append(density,cols,sets,adapter.status);filters.insertAdjacentElement('afterend',details);adapters.set(kind,adapter);
 if(ownerKey)adapter.sets=allowedSets(readStore('sessionStorage',filterPrefix+ownerKey+'.'+kind));renderSets(adapter);applyDisplay(adapter);
 details.addEventListener('keydown',event=>{if(event.key==='Escape'){details.open=false;summary.focus();event.stopPropagation()}});
}
function scan(){
 scheduled=false;
 const manual=document.getElementById('manual-table'),manualFilters=document.querySelector('.manual-filter-area');
 if(manual&&manualFilters)setup('manual',manualFilters,manual);
 const filters=document.querySelector('body>main .filters');
 if(filters){const table=[...document.querySelectorAll('body>main table')].find(t=>t.querySelector('thead th')&&
  (t.querySelector('a[href^="/check-new/"]')||/закуп|номер/i.test(t.querySelector('thead').textContent)));
  if(table)setup('registry',filters,table)}
}
function schedule(){if(scheduled)return;scheduled=true;requestAnimationFrame(scan)}
function acceptIdentity(data){
 const email=data?.email;if(typeof email!=='string'||!email.trim())return Promise.resolve(false);
 if(email===identity)return identityWork||Promise.resolve(!!ownerKey);
 const wasKnown=!!identity;identity=email;ownerKey='';const sequence=++identitySequence;
 for(const adapter of adapters.values()){if(wasKnown)adapter.sets=[];renderSets(adapter);storageNote(adapter,false)}
 const task=(async()=>{
  try{
   if(!crypto.subtle)return false;
   const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(email.trim().toLowerCase()));
   if(sequence!==identitySequence)return false;ownerKey=Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join('');sessionReady=canStore('sessionStorage');
   for(const adapter of adapters.values()){
    const stored=allowedSets(readStore('sessionStorage',filterPrefix+ownerKey+'.'+adapter.kind));
    const names=new Set(stored.map(x=>x.name));adapter.sets=[...stored,...adapter.sets.filter(x=>!names.has(x.name))].slice(0,6);
    renderSets(adapter);storageNote(adapter);
   }
   return true;
  }catch{return false}
 })();identityWork=task;task.finally(()=>{if(identityWork===task)identityWork=null});return task;
}
function resolveOwner(){
 if(ownerKey)return Promise.resolve(true);if(identityWork)return identityWork;if(ownerRead)return ownerRead;
 const sequence=identitySequence;
 const task=(async()=>{try{
  const response=await fetch('/me/profile',{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)return false;const data=await response.json();
  if(sequence!==identitySequence)return !!ownerKey;
  return await acceptIdentity(data);
 }catch{return false}})();ownerRead=task;task.finally(()=>{if(ownerRead===task)ownerRead=null});return task;
}
document.addEventListener('eis:workspace-data',event=>{
 if(event.detail?.path==='/me/profile')acceptIdentity(event.detail.data);
});
document.getElementById('eis223-logout')?.addEventListener('click',()=>{identity='';ownerKey='';identitySequence++;for(const adapter of adapters.values()){adapter.sets=[];renderSets(adapter);storageNote(adapter,false)}},true);
new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',scan,{once:true});else scan();
})();
</script>'''


def workspace_preferences():
    """Return presentation-only enhancement for existing registry/manual lists."""
    return STYLE + SCRIPT
