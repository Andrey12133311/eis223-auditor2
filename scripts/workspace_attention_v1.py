"""Actionable cabinet observations over existing owner-scoped read results.

The widget never fetches or changes data. The account runtime publishes the
responses it already reads; missing or failed sources never become an all-clear.
"""

STYLE = r'''<style id="eis-attention-style">
#eis-attention[hidden],#eis-attention [hidden]{display:none!important}
.eis223-personal #eis-attention{margin:20px 0;padding:18px 20px;background:#fff;border:1px solid var(--ui-line,#dedee3);border-radius:12px;color:var(--ui-ink,#1d1d1f)}
#eis-attention .eis-attention-head{display:flex;align-items:start;justify-content:space-between;gap:12px}
#eis-attention h3{font-size:16px;line-height:1.4;margin:0;letter-spacing:-.02em}
#eis-attention .eis-attention-intro{margin:4px 0 0;color:var(--ui-muted,#636366);font-size:12px;line-height:1.5}
#eis-attention-count{display:inline-flex;align-items:center;justify-content:center;min-width:28px;min-height:26px;padding:2px 8px;background:#f5f5f7;border:1px solid #e5e5ea;border-radius:8px;font-size:12px;font-variant-numeric:tabular-nums;font-weight:600;box-sizing:border-box}
#eis-attention-list{list-style:none;margin:14px 0 0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px}
#eis-attention-list>li{margin:0;padding:13px 0;border-top:1px solid #ebebef;min-width:0;display:flex;gap:11px;align-items:start;background:transparent;box-shadow:none;border-radius:0}
#eis-attention .eis-attention-mark{flex:none;width:7px;height:7px;border-radius:50%;margin-top:7px;background:#7c7c80}
#eis-attention .eis-attention-mark[data-tone=error]{background:#b42332}
#eis-attention .eis-attention-mark[data-tone=warning]{background:#865b00}
#eis-attention .eis-attention-copy{min-width:0;flex:1}
#eis-attention .eis-attention-copy strong{display:block;font-size:12px;line-height:1.5;font-weight:600;overflow-wrap:anywhere}
#eis-attention .eis-attention-copy p{font-size:12px;line-height:1.55;margin:3px 0 5px;color:var(--ui-muted,#636366);overflow-wrap:anywhere}
#eis-attention .eis-attention-actions{display:flex;flex-wrap:wrap;gap:4px 12px}
#eis-attention :is(a,button){display:inline-flex;align-items:center;min-height:32px;font-size:12px;line-height:1.45;box-sizing:border-box;touch-action:manipulation}
#eis-attention a{color:#0055ad;text-decoration:none;border-radius:5px}
#eis-attention a:hover{text-decoration:underline}
#eis-attention-status{font-size:12px;line-height:1.55;color:var(--ui-muted,#636366);margin:12px 0 0;overflow-wrap:anywhere}
#eis-attention-status[data-state=error]{color:#865b00}
#eis-attention .eis-attention-footer{margin-top:12px;display:flex;flex-wrap:wrap;align-items:center;gap:4px 16px}
.eis223-workspace #eis-attention button{background:#fff!important;color:#1d1d1f!important;border:1px solid #dedee3!important;border-radius:8px;box-shadow:none!important;padding:7px 10px;font-weight:500;min-height:36px}
#eis-attention :is(a,button):focus-visible{outline:3px solid #0066cc;outline-offset:3px}
@media(max-width:650px){.eis223-personal #eis-attention{padding:16px;margin:18px 0}#eis-attention-list{grid-template-columns:minmax(0,1fr)}#eis-attention :is(a,button){min-height:44px}}
@media(pointer:coarse){#eis-attention :is(a,button){min-height:44px!important}}
@media(prefers-reduced-motion:reduce){#eis-attention *{transition:none!important}}
</style>'''

MARKUP = r'''<section id="eis-attention" aria-labelledby="eis-attention-title" aria-busy="true">
<div class="eis-attention-head"><div><h3 id="eis-attention-title">Требует внимания</h3>
<p class="eis-attention-intro">Проверки и поручения, к которым стоит вернуться.</p></div>
<span id="eis-attention-count" aria-label="Количество закупок пока неизвестно">—</span></div>
<ul id="eis-attention-list" aria-label="Закупки, требующие внимания" hidden></ul>
<p id="eis-attention-status" role="status" aria-live="polite">Загружаем проверки и поручения…</p>
<div class="eis-attention-footer"><a href="/account/home">Все мои проверки</a>
<a href="/account/workbench">Открыть поручения</a>
<button id="eis-attention-retry" type="button" hidden>Повторить загрузку</button></div>
</section>'''

SCRIPT = r'''<script id="eis-attention-script">
(()=>{
'use strict';
const root=document.getElementById('eis-attention');if(!root||root.dataset.ready)return;root.dataset.ready='1';
const el=id=>document.getElementById(id),list=el('eis-attention-list'),status=el('eis-attention-status'),count=el('eis-attention-count'),retry=el('eis-attention-retry');
const paths=['/me/manual-checks','/me/assignments'];
const sources=new Map(paths.map(path=>[path,{state:'waiting',items:[]}])) ;
const regOK=value=>typeof value==='string'&&/^3[0-9]{10}$/.test(value);
const number=value=>{if(typeof value==='string'){if(!/^[0-9]+$/.test(value.trim()))return null;value=Number(value.trim())}return typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?value:null};
const text=(value,fallback='')=>typeof value==='string'&&value.trim()?value.trim().slice(0,220):fallback;
const make=(tag,value)=>{const node=document.createElement(tag);if(value!==undefined)node.textContent=value;return node};
function dayDistance(value){
 // These are calendar deadlines. Compare saved date parts with the browser's
 // current local calendar day; do not shift the saved date through a timezone.
 if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return null;
 const [year,month,day]=value.split('-').map(Number),stamp=Date.UTC(year,month-1,day),saved=new Date(stamp);
 if(saved.getUTCFullYear()!==year||saved.getUTCMonth()!==month-1||saved.getUTCDate()!==day)return null;
 const now=new Date(),today=Date.UTC(now.getFullYear(),now.getMonth(),now.getDate());
 return Math.round((stamp-today)/86400000);
}
function observations(){
 const rows=new Map();let incomplete=false;
 function add(reg,reason,priority,tone,href,label,taskTitle=''){
  let row=rows.get(reg);if(!row){row={reg,reasons:[],links:new Map(),priority,tone,taskTitle};rows.set(reg,row)}
  if(!row.reasons.includes(reason))row.reasons.push(reason);
  if(priority<row.priority){row.priority=priority;row.tone=tone}
  if(taskTitle&&!row.taskTitle)row.taskTitle=taskTitle;
  row.links.set(href,label);
 }
 for(const item of sources.get(paths[0]).items){
  if(!item||typeof item!=='object'||!regOK(item.reg_number)){incomplete=true;continue}
  const reg=item.reg_number,job=item.job_status,legal=item.legal_review_state;
  const href=item.card_available===true?'/check-new/'+reg:'/account/home';
  const label=item.card_available===true?'Открыть проверку':'Посмотреть состояние';
  const note=(reason,priority=60,tone='waiting')=>add(reg,reason,priority,tone,href,label);
  if(['error','failed'].includes(job))note('Ошибка получения документов',10,'error');
  else if(job==='partial')note('Документы получены частично',30,'warning');
  else if(job==='deferred')note('Ожидается источник документов');
  else if(['leased','running','processing'].includes(job))note('Документы загружаются');
  else if(['queued','pending'].includes(job))note('Получение документов в очереди',70);
  else if(!['done','completed'].includes(job))note('Состояние получения документов уточняется',80);
  if(['failed','error'].includes(legal))note('Ошибка юридической проверки',10,'error');
  else if(legal==='no_readable_documents')note('Нет читаемых документов для юридической проверки',20,'warning');
  else if(legal==='completed'&&!(number(item.legal_review_documents)>0))note('Юридическая проверка не завершена: нет прочитанных документов',20,'warning');
  else if(['running','processing'].includes(legal))note('Идёт юридическая проверка');
  else if(['pending','queued'].includes(legal))note('Юридическая проверка в очереди',70);
  else if(legal!=='completed')note('Состояние юридической проверки уточняется',80);
 }
 for(const item of sources.get(paths[1]).items){
  if(!item||typeof item!=='object'||!regOK(item.reg_number)){incomplete=true;continue}
  const state=item.status;
  if(['completed','cancelled'].includes(state))continue;
  const note=(reason,priority,tone)=>add(item.reg_number,reason,priority,tone,'/account/workbench','Открыть поручения',text(item.title));
  if(item.overdue===true)note('Срок поручения истёк',10,'error');
  if(state==='returned')note('Поручение возвращено на доработку',20,'warning');
  if(['assigned','in_progress','returned'].includes(state)){
   const days=dayDistance(item.due_date);
   if(days===0)note('Срок поручения сегодня',30,'warning');
   else if(days===1)note('Срок поручения завтра',40,'warning');
  }else if(state!=='submitted')incomplete=true;
 }
 return {rows:[...rows.values()].sort((a,b)=>a.priority-b.priority||a.reg.localeCompare(b.reg)),incomplete};
}
function render(){
 const active=document.activeElement;
 const focused=list.contains(active)&&active.matches('a[href]')?
  {reg:active.closest('li')?.dataset.reg,href:active.getAttribute('href')}:null;
 const waiting=[...sources.values()].some(source=>source.state==='waiting'),failed=[...sources.values()].some(source=>source.state==='error');
 const {rows,incomplete}=observations(),partial=failed||incomplete;
 root.setAttribute('aria-busy',String(waiting));
 count.textContent=(waiting||partial)&&!rows.length?'—':String(rows.length);
 count.setAttribute('aria-label',rows.length?'Закупок среди доступных данных: '+rows.length:waiting||partial?'Количество закупок пока неизвестно':'Среди загруженных данных нет закупок, требующих внимания');
 list.replaceChildren();list.hidden=!rows.length;
 for(const row of rows.slice(0,5)){
  const li=make('li');li.dataset.reg=row.reg;
  const mark=make('span');mark.className='eis-attention-mark';mark.dataset.tone=row.tone;mark.setAttribute('aria-hidden','true');
  const body=make('div');body.className='eis-attention-copy';
  body.append(make('strong','№ '+row.reg),make('p',row.reasons.join(' · ')));
  if(row.taskTitle){const task=make('p',row.taskTitle);task.className='eis-attention-task';body.append(task)}
  const actions=make('div');actions.className='eis-attention-actions';
  for(const [href,label] of row.links){const link=make('a',label);link.href=href;actions.append(link)}
  body.append(actions);li.append(mark,body);list.append(li);
 }
 retry.hidden=!failed;
 status.dataset.state=partial?'error':'';
 if(waiting)status.textContent=rows.length?'Загружаем остальные данные…':'Загружаем проверки и поручения…';
 else if(failed)status.textContent='Часть данных не обновлена. Показаны доступные сведения; повторите загрузку.';
 else if(incomplete)status.textContent='Часть состояний не удалось уточнить. Откройте полный список проверок или поручений.';
 else if(rows.length>5)status.textContent='Показаны 5 из '+rows.length+' закупок. Остальные — в полных списках.';
 else if(rows.length)status.textContent='По последним загруженным проверкам и поручениям. Ожидание проверки не означает нарушение.';
 else status.textContent='В загруженных проверках и поручениях сейчас нет действий, требующих внимания.';
 if(focused&&!root.closest('[hidden],[inert]')&&
    (document.activeElement===document.body||document.activeElement===active)){
  const row=[...list.children].find(item=>item.dataset.reg===focused.reg);
  const links=row?[...row.querySelectorAll('a[href]')]:[];
  const same=links.find(link=>link.getAttribute('href')===focused.href)||links[0];
  const fullHref=focused.href==='/account/workbench'?'/account/workbench':'/account/home';
  const full=[...root.querySelectorAll('.eis-attention-footer a[href]')]
   .find(link=>link.getAttribute('href')===fullHref);
  const target=same||(!retry.hidden?retry:full)||root;
  if(target===root)root.tabIndex=-1;
  target.focus({preventScroll:true});
 }
}
document.addEventListener('eis:workspace-data',event=>{
 const path=event.detail?.path;if(!sources.has(path))return;
 const data=event.detail.data,source=sources.get(path);
 if(Array.isArray(data)){source.items=data;source.state='ready'}else source.state='error';
 render();
});
document.addEventListener('eis:workspace-error',event=>{
 const source=sources.get(event.detail?.path);if(!source)return;source.state='error';render();
});
retry.addEventListener('click',()=>{
 for(const source of sources.values())if(source.state==='error')source.state='waiting';
 render();document.dispatchEvent(new CustomEvent('eis:workspace-refresh'));
});
render();
})();
</script>'''


def workspace_attention():
    """Return a self-contained cabinet widget; all data arrives through events."""
    return STYLE + MARKUP + SCRIPT
