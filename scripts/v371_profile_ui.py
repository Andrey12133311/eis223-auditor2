"""Accessible edit-profile page, no fake surname derived from email."""
from scripts.account_visual_polish_v1 import profile_style
from scripts.workspace_discovery_v1 import workspace_discovery

CSS=r"""
<style>
*{box-sizing:border-box}body{font:14px/1.65 Inter,"Segoe UI",Arial,system-ui;color:#25465e;background:#f3f7fb;margin:0}
header{background:#174c76;color:white;padding:16px clamp(12px,4vw,55px);
display:flex;justify-content:space-between;gap:9px;align-items:center;flex-wrap:wrap}
header strong{font-size:18px}header a{border:1px solid #ffffff66;color:#fff;padding:8px 12px;border-radius:7px;text-decoration:none}
main{max-width:750px;margin:clamp(20px,4vw,52px) auto;padding:0 14px}
article{background:#fff;border:1px solid #dce8f0;padding:clamp(17px,4vw,34px);border-radius:14px;box-shadow:0 8px 26px #2047690c}
h1{font-size:25px;letter-spacing:-.04em;color:#1c5279;margin:0 0 12px}
p{font-size:13px;color:#627d90}
form{display:grid;gap:14px;margin:17px 0}
label{display:block;font-weight:750;font-size:12px;color:#41617c}
input{display:block;margin:6px 0 0;width:100%;min-height:43px;padding:10px 13px;
border:1px solid #cbdce9;border-radius:9px;background:#fff;color:#284f69;font:inherit}
button{background:#176397;color:white;border:1px solid #176397;border-radius:8px;
font:750 13px system-ui;padding:11px 16px;cursor:pointer}
button:disabled{opacity:.55}
.meta{padding:12px 15px;background:#f0f7fc;color:#52748d;border-left:3px solid #4488b9;border-radius:6px;margin:15px 0}
#profile-status{font-size:13px;color:#236a99;min-height:20px}
</style>"""
PROFILE_EXPERIENCE_STYLE=r"""
<style id="eis223-profile-experience">
body.eis-profile-page header{gap:14px}
.eis-profile-page .profile-brand{border:0;padding:0;font-size:16px;font-weight:600;letter-spacing:-.025em;min-height:44px;display:inline-flex;align-items:center}
.eis-profile-page header .profile-back{min-height:40px;display:inline-flex;align-items:center}
.eis-profile-page main{max-width:720px;margin:28px auto}
.eis-profile-page .profile-eyebrow{font-size:11px;letter-spacing:.055em;text-transform:uppercase;margin:0 0 6px}
.eis-profile-page h1{margin-bottom:9px}
.eis-profile-page .profile-intro{margin:0;max-width:54ch}
.profile-account{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:22px 0;padding:16px;background:#f5f5f7;border:1px solid var(--ui-line,#dedee3);border-radius:10px}
.profile-account div{min-width:0}.profile-account dt{font-size:11px;color:var(--ui-muted,#636366);margin-bottom:4px}
.profile-account dd{font-size:13px;font-weight:500;margin:0;overflow-wrap:anywhere}
.eis-profile-page form{display:block;margin:0}
.eis-profile-page fieldset{border:0;padding:0;margin:0;min-width:0}
.eis-profile-page legend{padding:0;font-size:15px;font-weight:600}
.profile-form-help{font-size:12px;margin:6px 0 16px}
.profile-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.profile-fields label{min-width:0}.profile-fields label:last-child{grid-column:1/-1}
.profile-fields .optional{font-weight:400;color:var(--ui-muted,#636366)}
.eis-profile-page input{min-height:44px;font-size:14px}
.eis-profile-page input:disabled{background:#f5f5f7;color:var(--ui-muted,#636366);opacity:.7}
.profile-form-footer{margin-top:20px;padding-top:18px;border-top:1px solid var(--ui-line,#dedee3)}
.eis-profile-page button{min-height:44px;padding:10px 16px}
.eis-profile-page #profile-status{margin:12px 0 0;min-height:20px;font-size:12px;overflow-wrap:anywhere}
.eis-profile-page #profile-status[data-state=error]{color:var(--ui-danger,#b42332)}
.eis-profile-page .profile-retry{margin-top:10px;background:#fff;color:var(--ui-ink,#1d1d1f);border-color:var(--ui-line,#dedee3)}
.eis-profile-page .profile-privacy{font-size:11px;margin:18px 0 0;padding-top:16px;border-top:1px solid var(--ui-line,#dedee3)}
.eis-profile-page :focus-visible{outline:3px solid #0066cc;outline-offset:3px}
@media(max-width:500px){
 .eis-profile-page main{margin:18px auto}.profile-account,.profile-fields{grid-template-columns:minmax(0,1fr)}
 .profile-account{gap:12px;margin:18px 0}.profile-fields label:last-child{grid-column:auto}
 .profile-form-footer button,.eis-profile-page .profile-retry{width:100%}
 .eis-profile-page header .profile-back{min-height:44px}
}
</style>"""
SCRIPT=r"""
<script>
(()=>{
const form=document.getElementById('profile-edit'),status=document.getElementById('profile-status'),
 fields=document.getElementById('profile-fields'),button=form.querySelector('button[type=submit]'),
 retry=document.getElementById('profile-retry');
let ready=false,busy=false;
function feedback(message,state=''){status.textContent=message;status.dataset.state=state}
function progress(value,label='Сохранить изменения'){
 busy=value;form.setAttribute('aria-busy',String(value));fields.disabled=value||!ready;
 button.textContent=label;
}
async function read(){
 if(busy)return;
 ready=false;retry.hidden=true;retry.disabled=true;progress(true);
 feedback('Загружаем данные профиля…');
 try{
  const r=await fetch('/me/profile',{credentials:'same-origin',cache:'no-store'});
  if(r.status===401){location.replace('/account/login');return}
  if(!r.ok)throw Error('Профиль недоступен');
  const data=await r.json();
  for(const field of ['last_name','first_name','patronymic'])
   form.elements[field].value=data[field]||'';
  document.getElementById('profile-email').textContent=data.email||'Не указана';
  document.getElementById('profile-name').textContent=data.full_name||
   'Имя не заполнено';
  ready=true;feedback('Данные загружены.');
 }catch(e){feedback('Не удалось загрузить профиль. Повторите попытку.','error');retry.hidden=false}
 finally{progress(false);retry.disabled=false}
}
form.onsubmit=async event=>{
 event.preventDefault();
 if(busy||!ready)return;
 // Read enabled fields before disabling the form during the request.
 const data=Object.fromEntries(new FormData(form));
 const returnFocus=document.activeElement;
 progress(true,'Сохраняем…');feedback('Сохраняем изменения…');
 try{
  const r=await fetch('/me/profile',{method:'PUT',credentials:'same-origin',
   headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.status===401){location.replace('/account/login');return}
  const result=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:'Не удалось сохранить');
  document.getElementById('profile-name').textContent=result.full_name||
   [data.last_name,data.first_name,data.patronymic].filter(Boolean).join(' ');
  feedback('Изменения сохранены. Имя обновится при возвращении в кабинет.','saved');
 }catch(e){feedback(e instanceof TypeError?'Не удалось сохранить изменения. Проверьте соединение и повторите попытку.':
                    (e.message||'Не удалось сохранить изменения.'),'error')}
 finally{
  progress(false);
  if(document.activeElement===document.body&&returnFocus&&form.contains(returnFocus))
   returnFocus.focus({preventScroll:true});
 }
};
retry.onclick=read;
read();
})();
</script>"""
PAGE=r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Профиль — ЕИС Аудитор</title>__CSS__</head>
<body class="eis-profile-page"><header><a class="profile-brand" href="/">ЕИС Аудитор</a>
<a class="profile-back" href="/#cabinet">← В кабинет</a></header><main><article>
<p class="profile-eyebrow">Учётная запись</p><h1>Профиль</h1>
<p class="profile-intro">Ваше имя отображается в кабинете и рядом с назначенными заданиями.</p>
<dl class="profile-account"><div><dt>Отображаемое имя</dt><dd id="profile-name">Загрузка…</dd></div>
<div><dt>Электронная почта</dt><dd id="profile-email">—</dd></div></dl>
<form id="profile-edit" autocomplete="on" aria-busy="true">
<fieldset id="profile-fields" disabled aria-describedby="profile-help"><legend>Личные данные</legend>
<p class="profile-form-help" id="profile-help">Фамилия и имя обязательны. Отчество можно пропустить.</p><div class="profile-fields">
<label>Фамилия<input name="last_name" autocomplete="family-name" minlength="2" maxlength="80" required></label>
<label>Имя<input name="first_name" autocomplete="given-name" minlength="2" maxlength="80" required></label>
<label>Отчество <span class="optional">· необязательно</span><input name="patronymic" autocomplete="additional-name" maxlength="80"></label>
</div><div class="profile-form-footer"><button type="submit">Сохранить изменения</button></div></fieldset></form>
<p id="profile-status" role="status" aria-live="polite" aria-atomic="true">Загружаем данные профиля…</p>
<button class="profile-retry" id="profile-retry" type="button" hidden>Повторить загрузку</button>
<p class="profile-privacy">Данные доступны владельцу учётной записи и уполномоченному руководителю подразделения.</p>
</article></main>__SCRIPT__</body></html>"""

def render_profile():
    return PAGE.replace("__CSS__",CSS+profile_style()+PROFILE_EXPERIENCE_STYLE).replace("__SCRIPT__",SCRIPT+workspace_discovery())
