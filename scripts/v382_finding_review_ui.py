"""Shared account-only controls for submitting one canonical legal finding."""
from hashlib import sha256
from html import escape
import json

STYLE = r"""<style>
.eis-finding-review{margin-top:14px;padding:12px;border:1px solid #cfdfeb;border-radius:9px;background:#f2f8fc;color:#284b65;font:13px/1.55 system-ui;overflow-wrap:anywhere}
.eis-finding-review strong{display:block;margin-bottom:6px}
.eis-finding-review p{margin:5px 0;font-size:12px;white-space:pre-wrap}
.eis-finding-review button{min-height:40px;padding:9px 12px;margin:7px 8px 0 0;border:1px solid #176397;border-radius:8px;background:#176397;color:white;font:700 12px system-ui;cursor:pointer}
.eis-finding-review button.secondary{background:white;color:#245e87;border-color:#c8dce9}
.eis-finding-review button:disabled{opacity:.6;cursor:default}
.eis-finding-review a{display:inline-block;margin-top:7px;font-size:12px}
.eis-finding-review :focus-visible{outline:3px solid #8fc2e0;outline-offset:2px}
</style>"""

SCRIPT = r"""<script>
(()=>{
if(window.EISFindingReview)return;
const states=new Map();
const labels={submitted:'Отправлено на согласование',approved:'Согласовано руководителем',
 rejected:'Отклонено руководителем',returned:'Возвращено на доработку'};
const node=(tag,value)=>{const el=document.createElement(tag);el.textContent=value;return el};
function state(reg,batch=''){
 const id=reg+'|'+batch;
 if(!states.has(id))states.set(id,{items:null,manager:false,error:'',request:null,queued:false,
  version:0,busy:new Set(),rechecking:new Set(),messages:new Map()});
 return states.get(id);
}
async function api(url,method='GET'){
 const r=await fetch(url,{method,credentials:'same-origin',cache:'no-store'});
 const data=await r.json().catch(()=>({}));
 if(r.status===401){location.replace('/account/login');throw Error('Войдите в учётную запись')}
 if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Ошибка запроса '+r.status);
 return data;
}
function draw(box,reg,s,batch=''){
 const key=box.dataset.findingReview,item=s.items?.find(x=>x.finding_key===key);
 box.replaceChildren();
 if(!s.items){
  box.append(node('p',s.error||'Загружаем состояние согласования…'));
 }else if(!item){
  box.append(node('p','Версия доказательства изменилась или пока недоступна для отправки. Откройте электронное дело и проверьте актуальную карточку.'));
  const link=node('a',batch?'Обновить тематическую карточку →':'Открыть электронное дело →');
  link.href=batch?'/thematic/card/'+encodeURIComponent(batch)+'/'+encodeURIComponent(reg):'/me/cases/'+encodeURIComponent(reg);box.append(link);
 }else{
  const status=item.submission?.status||item.review?.decision||'',busy=s.busy.has(key);
  box.append(node('strong',labels[status]||'Ещё не отправлено на согласование'));
  if(item.submission?.submitted_at){
   box.append(node('p','Отправлено: '+new Date(item.submission.submitted_at).toLocaleString('ru-RU')));
  }
  if(item.review?.comment){
   box.append(node('p',(status==='submitted'&&item.review.decision==='returned'?
    'Предыдущее замечание руководителя: ':'Комментарий руководителя: ')+item.review.comment));
  }
  const invalidated=item.effective_status&&item.effective_status!=='confirmed';
  if(item.reassessment)box.append(node('p',item.reassessment.reason||'Применимость правил МСП перепроверена.'));
  const submit=node('button',busy?'Отправляем…':'Направить на согласование руководителю');
  submit.type='button';submit.disabled=busy||invalidated||!s.manager||['submitted','approved','rejected','returned'].includes(status);
  submit.onclick=()=>send(reg,key,batch);
  box.append(submit);
  if(batch&&item.code==='223-SME-BID-SECURITY-OVER-TWO-PERCENT'){
   const recheck=node('button',s.rechecking.has(key)?'Перепроверяем МСП…':'Перепроверить признак МСП');
   recheck.type='button';recheck.className='secondary';recheck.disabled=busy;
   recheck.onclick=()=>recheckMsp(reg,key,batch);box.append(recheck);
   box.append(node('p','Проверяются сохранённые официальные источники только для этого вывода о пределе 2%. Исходное доказательство и согласования сохраняются.'));
  }
  if(!s.manager)box.append(node('p','Руководитель не назначен. Обратитесь к администратору, чтобы отправить нарушение на согласование.'));
  if(s.messages.has(key))box.append(node('p',s.messages.get(key)));
 }
 if(s.error&&s.items)box.append(node('p','Не удалось обновить состояние: '+s.error));
 const refresh=node('button',s.request?'Обновляем…':'Обновить статус');
 refresh.type='button';refresh.className='secondary';refresh.disabled=!!s.request||s.busy.size>0;
 refresh.onclick=()=>load(reg,true,batch);box.append(refresh);
}
function paint(reg,batch=''){
 const s=state(reg,batch);
 document.querySelectorAll('[data-finding-review]').forEach(box=>{
  if(box.dataset.reviewReg===reg&&(box.dataset.reviewBatch||'')===batch)draw(box,reg,s,batch);
 });
}
function control(reg,key,batch=''){
 const box=node('div','');box.className='eis-finding-review';
 box.dataset.findingReview=key;box.dataset.reviewReg=reg;box.dataset.reviewBatch=batch;
 box.setAttribute('role','status');box.setAttribute('aria-live','polite');
 draw(box,reg,state(reg,batch),batch);return box;
}
function load(reg,fresh=false,batch=''){
 const s=state(reg,batch);
 if(s.request){if(fresh)s.queued=true;return s.request}
 s.request=(async()=>{
  do{
   s.queued=false;const version=s.version;
   try{
    const data=await api('/me/finding-submissions/'+encodeURIComponent(reg)+(batch?'?batch='+encodeURIComponent(batch):''));
    if(version===s.version){s.items=Array.isArray(data.findings)?data.findings:[];s.manager=data.manager_assigned===true;s.error=''}
   }catch(e){if(version===s.version)s.error=e.message}
  }while(s.queued);
 })().finally(()=>{s.request=null;paint(reg,batch)});
 paint(reg,batch);return s.request;
}
async function send(reg,key,batch=''){
 const s=state(reg,batch),item=s.items?.find(x=>x.finding_key===key);
 const current=item?.submission?.status||item?.review?.decision||'';
 if(!item||!s.manager||s.busy.has(key)||(item.effective_status&&item.effective_status!=='confirmed')||['submitted','approved','rejected','returned'].includes(current))return;
 s.busy.add(key);s.version++;s.messages.delete(key);paint(reg,batch);
 try{
  const result=await api('/me/finding-submissions/'+encodeURIComponent(reg)+'/'+encodeURIComponent(key)+'/submit'+(batch?'?batch='+encodeURIComponent(batch):''),'POST');
  item.submission={...(item.submission||{}),status:result.status};
  s.messages.set(key,result.already_submitted?'Это нарушение уже отправлено. Дубликат не создан.':'Нарушение отправлено руководителю.');
 }catch(e){s.messages.set(key,'Не удалось отправить: '+e.message)}
 finally{
  // A GET begun before this POST cannot replace the mutation result.
  s.version++;await load(reg,true,batch);s.busy.delete(key);paint(reg,batch);
 }
}
async function recheckMsp(reg,key,batch){
 const s=state(reg,batch),item=s.items?.find(x=>x.finding_key===key);
 if(!batch||!item||item.code!=='223-SME-BID-SECURITY-OVER-TWO-PERCENT'||s.busy.has(key))return;
 s.busy.add(key);s.rechecking.add(key);s.version++;s.messages.delete(key);paint(reg,batch);
 let refreshed=false;
 try{
  await api('/me/thematic/'+encodeURIComponent(batch)+'/'+encodeURIComponent(reg)+'/findings/'+encodeURIComponent(key)+'/msp-recheck','POST');
  refreshed=true;location.reload();
 }catch(e){s.messages.set(key,'Перепроверку не удалось завершить: '+e.message)}
 finally{
  s.version++;s.busy.delete(key);s.rechecking.delete(key);
  if(!refreshed){await load(reg,true,batch);paint(reg,batch)}
 }
}
window.EISFindingReview={control,load};
const contexts=new Map();document.querySelectorAll('[data-finding-review]').forEach(box=>{
 const reg=box.dataset.reviewReg,batch=box.dataset.reviewBatch||'';contexts.set(reg+'|'+batch,[reg,batch]);
});
contexts.forEach(([reg,batch])=>{if(/^3[0-9]{10}$/.test(reg))load(reg,false,batch)});
})();
</script>"""


def finding_control(reg, item, batch=""):
    """Exact identity only; the private API verifies this key before enabling submit."""
    values = [str(reg)] + [str(item.get(key) or "").strip()
                           for key in ("title", "law", "evidence", "document", "source_url")]
    key = item.get("finding_key") if batch else None
    if not isinstance(key, str) or len(key)!=64 or any(c not in "0123456789abcdef" for c in key):
        key = sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return ('<div class="eis-finding-review" role="status" aria-live="polite" '
            'data-finding-review="' + key + '" data-review-reg="' + escape(str(reg), quote=True) +
            '" data-review-batch="' + escape(str(batch), quote=True) + '">Загружаем состояние согласования…</div>')
