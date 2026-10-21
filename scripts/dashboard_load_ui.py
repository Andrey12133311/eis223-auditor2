"""Small dashboard-only load state, without rewriting requests or results.

The original caller retains its Response, credentials, AbortSignal and errors.
No automatic polling/retry storm; the user can retry after source Retry-After.
"""
SCRIPT = r'''<script id="eis-dashboard-load-state">(()=>{'use strict';
if(location.pathname!=='/'||window.__eisDashboardLoadState||typeof window.fetch!=='function')return;
window.__eisDashboardLoadState=true;
const original=window.fetch.bind(window);let serial=0,notice='',retryAt=0,timer=null,autoTimer=null,attempts=0,lastUrl='';
function render(){
 if(!document.body)return;
 let box=document.getElementById('eis-dashboard-load-notice');
 if(!box){box=document.createElement('section');box.id='eis-dashboard-load-notice';box.setAttribute('role','status');box.setAttribute('aria-live','polite');box.style.cssText='margin:12px 0;padding:12px;border:1px solid #c9d5cb;border-radius:8px;background:#fff;color:#24342a';
 const text=document.createElement('span');text.id='eis-dashboard-load-text';box.appendChild(text);
 const button=document.createElement('button');button.type='button';button.id='eis-dashboard-load-retry';button.textContent='Повторить загрузку';button.style.marginLeft='12px';button.onclick=()=>{
  if(Date.now()<retryAt)return;const form=document.getElementById('filters');
  if(form)form.dispatchEvent(new Event('submit',{bubbles:false,cancelable:true}));else location.reload();
 };box.appendChild(button);const parent=document.querySelector('main')||document.body;parent.insertBefore(box,parent.firstChild);}
 box.hidden=!notice;document.getElementById('eis-dashboard-load-text').textContent=notice;
 const button=document.getElementById('eis-dashboard-load-retry');button.disabled=Date.now()<retryAt;
 if(timer!==null)clearTimeout(timer);timer=null;
 if(button.disabled)timer=setTimeout(()=>{timer=null;render()},Math.min(2147483647,Math.max(1,retryAt-Date.now())));
}
function show(ticket,text,delay=0){if(ticket!==serial)return;notice=text;retryAt=Date.now()+delay;render()}
function delay(response){const raw=response.headers.get('Retry-After');if(!raw)return 10000;
 const seconds=Number(raw);if(Number.isFinite(seconds)&&seconds>=0)return Math.ceil(seconds*1000);
 const when=Date.parse(raw);return Number.isFinite(when)?Math.max(0,when-Date.now()):10000;}
window.fetch=async function(input,options){
 let target=false;try{const url=new URL(typeof input==='string'||input instanceof URL?input:input.url,location.href);
 const method=String(options?.method||input?.method||'GET').toUpperCase();
 target=method==='GET'&&url.origin===location.origin&&url.pathname==='/api/v257/dashboard';}catch(_){}
 if(!target)return original(input,options);
 const ticket=++serial;
 const key=String(typeof input==='string'||input instanceof URL?input:input.url);
 if(key!==lastUrl){attempts=0;lastUrl=key}
 if(autoTimer!==null){clearTimeout(autoTimer);autoTimer=null}
 try{const response=await original(input,options);
  if(response.status===503&&['warming','busy'].includes(response.headers.get('X-EIS-Dashboard-State'))){
   const pause=Math.max(5000,delay(response));
   show(ticket,'Идёт загрузка списка закупок. Повторная попытка выполняется автоматически; сохранённые результаты не удалены.',pause);
   if(attempts<15){attempts+=1;autoTimer=setTimeout(()=>{
     autoTimer=null;if(ticket!==serial||document.hidden)return;
     const form=document.getElementById('filters');
     if(form){if(typeof form.requestSubmit==='function')form.requestSubmit();
      else form.dispatchEvent(new Event('submit',{bubbles:false,cancelable:true}));}
   },Math.min(15000,pause+attempts*500));}
  }else if(response.ok){
   attempts=0;
   response.clone().json().then(data=>{
    if(data.dashboard_stale){const date=new Date(data.dashboard_snapshot_as_of);
     const stamp=Number.isFinite(date.getTime())?date.toLocaleTimeString('ru-RU'):'';
     show(ticket,'Показаны ранее рассчитанные результаты'+(stamp?' на '+stamp:'')+'. Проверка новых данных продолжается.');
     return;}
    if(data.membership_stale){const date=new Date(data.membership_refreshed_at);const stamp=Number.isFinite(date.getTime())?date.toLocaleTimeString('ru-RU'):'';
     show(ticket,'Показан последний полученный список'+(stamp?' на '+stamp:'')+'. Обновление списка ожидается; это не отметка о завершении проверки.');
    }else show(ticket,'');
   }).catch(()=>{});
  }else if(response.status===401||response.status===403){show(ticket,'');}
  else show(ticket,'Не удалось загрузить список закупок. Повторите запрос; прежние результаты сохранены.');
  return response;
 }catch(error){if(!options?.signal?.aborted&&!input?.signal?.aborted)show(ticket,'Сервер не ответил на загрузку списка. Повторите запрос; прежние результаты сохранены.');throw error;}
};
document.addEventListener('DOMContentLoaded',render,{once:true});
})();</script>'''


def inject(body):
    if 'id="eis-dashboard-load-state"' in body:
        return body
    # Install before the restored page's first script, not after its first fetch.
    import re
    match = re.search(r'<head\b[^>]*>', body, re.I)
    if match:
        return body[:match.end()] + SCRIPT + body[match.end():]
    return SCRIPT + body
