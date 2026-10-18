"""Reusable, owner-scoped service-memo requisites editor for audit cards."""
import re


def render_memo_requisites_editor(reg: str) -> str:
    if not isinstance(reg, str) or not re.fullmatch(r"3[0-9]{10}", reg):
        raise ValueError("Invalid EIS procurement number")
    return _EDITOR.replace("__REG__", reg)


_EDITOR = r'''<style>
.memo-requisites-widget{margin:12px 0}
.memo-requisites-widget button{font:inherit;cursor:pointer;border:1px solid #a9bed0;border-radius:8px;padding:9px 13px;background:#fff;color:#174e78}
.memo-requisites-widget button:disabled{cursor:wait;opacity:.65}
.memo-requisites-dialog{box-sizing:border-box;width:min(540px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto;border:1px solid #c8d7e4;border-radius:12px;padding:22px;color:#203247;background:#fff;box-shadow:0 18px 60px #102b4b33}
.memo-requisites-dialog::backdrop{background:#102b4b88}
.memo-requisites-dialog h2{font-size:21px;margin:0 0 8px}
.memo-requisites-dialog p{line-height:1.45;margin:8px 0 14px}
.memo-requisites-dialog fieldset{border:0;padding:0;margin:16px 0}
.memo-requisites-dialog label{display:block;margin:12px 0;font-weight:600}
.memo-requisites-dialog input{box-sizing:border-box;display:block;width:100%;font:inherit;font-weight:400;margin-top:6px;padding:10px;border:1px solid #a9bed0;border-radius:7px;background:#fff;color:#203247}
.memo-requisites-dialog :focus-visible{outline:3px solid #2674bd;outline-offset:2px}
.memo-requisites-dialog .memo-requisites-actions{display:flex;gap:9px;flex-wrap:wrap;justify-content:flex-end}
.memo-requisites-dialog button[type=submit]{background:#175e9a;border-color:#175e9a;color:#fff}
.memo-requisites-dialog [data-role=status]{min-height:1.5em;font-size:14px}
.memo-requisites-dialog [hidden]{display:none!important}
@media print{.memo-requisites-widget{display:none!important}}
</style>
<div class="memo-requisites-widget" id="memo-requisites-widget-__REG__">
<button type="button" data-role="open" aria-haspopup="dialog" aria-controls="memo-requisites-dialog-__REG__">Реквизиты служебной записки</button>
<dialog class="memo-requisites-dialog" id="memo-requisites-dialog-__REG__" aria-labelledby="memo-requisites-title-__REG__" aria-describedby="memo-requisites-description-__REG__">
<h2 id="memo-requisites-title-__REG__">Реквизиты служебной записки</h2>
<p id="memo-requisites-description-__REG__">Закупка № __REG__. Изменения сохраняются для этой закупки в вашей учётной записи и используются при следующем формировании записки.</p>
<form data-role="form">
<fieldset data-role="fields" disabled>
<label>Кому направляется служебная записка<input name="recipient" type="text" maxlength="199" autocomplete="off"></label>
<label>Исполнитель по служебной записке<input name="executor" type="text" maxlength="199" autocomplete="off"></label>
<label>Телефон исполнителя<input name="phone" type="text" maxlength="199" autocomplete="off" inputmode="tel"></label>
</fieldset>
<p>Пустое поле удаляет соответствующую строку. Ранее созданные документы не изменяются.</p>
<p data-role="status" role="status" aria-live="polite" aria-atomic="true"></p>
<a data-role="login" href="/account/login" hidden>Войти в учётную запись</a>
<div class="memo-requisites-actions">
<button type="button" data-role="retry" hidden>Повторить загрузку</button>
<button type="button" data-role="close">Закрыть</button>
<button type="submit" data-role="save" disabled>Сохранить реквизиты</button>
</div>
</form>
</dialog>
</div>
<script>
(()=>{
 const root=document.getElementById('memo-requisites-widget-__REG__');
 if(!root||root.dataset.ready)return;root.dataset.ready='true';
 const el=role=>root.querySelector('[data-role="'+role+'"]'),dialog=root.querySelector('dialog'),form=el('form');
 const keys=['recipient','executor','phone'],url='/me/memo-requisites/__REG__';
 let sequence=0,controller=null,loaded=false,saving=false,returnFocus=null;
 const isCurrent=n=>n===sequence&&dialog.open;
 function status(message){el('status').textContent=message}
 function fields(data){
  if(!data||!data.fields||typeof data.fields!=='object'||Array.isArray(data.fields))throw Error('Некорректный ответ сервера. Повторите попытку.');
  const result={};for(const key of keys){const value=data.fields[key];if(value!=null&&typeof value!=='string')throw Error('Некорректный ответ сервера. Повторите попытку.');result[key]=value||''}return result;
 }
 function setFields(values){for(const key of keys)form.elements.namedItem(key).value=values[key]||''}
 function errorMessage(code,operation){
  if(code===401)return 'Сессия закончилась. Войдите снова, затем откройте реквизиты.';
  if(code===403)return 'Нет доступа к реквизитам. Обновите страницу и проверьте вход в учётную запись.';
  if(code===422)return 'Проверьте реквизиты: каждое поле должно содержать не более 199 символов.';
  return 'Не удалось '+operation+' реквизиты. Сервер временно недоступен. Повторите попытку.';
 }
 async function request(options,operation){
  const active=new AbortController();controller=active;let timedOut=false;
  const timer=setTimeout(()=>{timedOut=true;active.abort()},15000);
  try{
   const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...options,signal:active.signal});
   if(!response.ok){const error=Error(errorMessage(response.status,operation));error.status=response.status;throw error}
   return await response.json();
  }catch(error){
   if(timedOut)throw Error('Сервер не ответил вовремя. Повторите попытку.');
   if(error.name==='AbortError'||error.status)throw error;
   throw Error('Не удалось '+operation+' реквизиты. Проверьте соединение и повторите попытку.');
  }finally{clearTimeout(timer);if(controller===active)controller=null}
 }
 async function load(){
  const current=++sequence;if(controller)controller.abort();loaded=false;
  el('fields').disabled=true;el('save').disabled=true;el('retry').hidden=true;el('login').hidden=true;
  setFields({});dialog.setAttribute('aria-busy','true');status('Загружаем реквизиты…');
  try{
   const data=await request({},'загрузить');if(!isCurrent(current))return;
   setFields(fields(data));loaded=true;el('fields').disabled=false;el('save').disabled=false;
   status(data.saved?'Загружены ваши реквизиты для этой закупки.':'Загружены реквизиты образца. Можно изменить их перед формированием записки.');
   form.elements.namedItem('recipient').focus();
  }catch(error){if(isCurrent(current)&&error.name!=='AbortError'){status(error.message);el('retry').hidden=false;el('login').hidden=error.status!==401}}
  finally{if(isCurrent(current))dialog.removeAttribute('aria-busy')}
 }
 function close(){if(saving)return;if(typeof dialog.close==='function')dialog.close();else{dialog.removeAttribute('open');cleanup()}}
 function cleanup(){++sequence;if(controller)controller.abort();loaded=false;el('fields').disabled=true;el('save').disabled=true;dialog.removeAttribute('aria-busy');if(returnFocus&&returnFocus.isConnected)returnFocus.focus()}
 el('open').addEventListener('click',()=>{
  if(dialog.open)return;returnFocus=document.activeElement;
  if(typeof dialog.showModal==='function')dialog.showModal();else{dialog.setAttribute('open','');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true')}
  el('close').focus();load();
 });
 el('close').addEventListener('click',close);el('retry').addEventListener('click',load);
 dialog.addEventListener('close',cleanup);
 dialog.addEventListener('cancel',event=>{event.preventDefault();close()});
 dialog.addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();close();return}
  if(event.key!=='Tab')return;
  const items=[...dialog.querySelectorAll('button,input,a[href]')].filter(node=>!node.hidden&&!node.matches(':disabled'));
  if(!items.length)return;const first=items[0],last=items[items.length-1];
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
 });
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(!loaded||saving||!dialog.open||!form.reportValidity())return;
  const current=sequence,payload={};for(const key of keys)payload[key]=form.elements.namedItem(key).value;
  saving=true;el('fields').disabled=true;el('save').disabled=true;el('close').disabled=true;el('retry').hidden=true;el('login').hidden=true;
  dialog.setAttribute('aria-busy','true');status('Сохраняем реквизиты…');
  try{
   const data=await request({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)},'сохранить');
   if(!isCurrent(current))return;
   if(data.saved!==true)throw Error('Сохранение не подтверждено сервером. Повторите попытку.');
   setFields(fields(data));status('Реквизиты сохранены. Теперь сформируйте служебную записку с обновлёнными данными.');
   root.dispatchEvent(new CustomEvent('eis223:memo-requisites-saved',{bubbles:true,detail:{reg:'__REG__'}}));
  }catch(error){if(isCurrent(current)&&error.name!=='AbortError'){status(error.message);el('login').hidden=error.status!==401}}
  finally{saving=false;el('close').disabled=false;if(isCurrent(current)){el('fields').disabled=!loaded;el('save').disabled=!loaded;dialog.removeAttribute('aria-busy')}}
 });
})();
</script>'''
