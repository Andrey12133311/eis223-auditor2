"""Accessible edit-profile page, no fake surname derived from email."""
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
SCRIPT=r"""
<script>
(()=>{
const form=document.getElementById('profile-edit'),status=document.getElementById('profile-status');
async function read(){
 try{
  const r=await fetch('/me/profile',{credentials:'same-origin',cache:'no-store'});
  if(r.status===401){location.replace('/account/login');return}
  if(!r.ok)throw Error('Профиль недоступен');
  const data=await r.json();
  for(const field of ['last_name','first_name','patronymic'])
   form.elements[field].value=data[field]||'';
  document.getElementById('profile-email').textContent=data.email;
  document.getElementById('profile-name').textContent=data.full_name||
   'Укажите фамилию и имя, чтобы они отображались в кабинете.';
 }catch(e){status.textContent=e.message}
}
form.onsubmit=async event=>{
 event.preventDefault();
 const button=form.querySelector('button[type=submit]');button.disabled=true;
 status.textContent='Сохраняем ФИО…';
 const data=Object.fromEntries(new FormData(form));
 try{
  const r=await fetch('/me/profile',{method:'PUT',credentials:'same-origin',
   headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:'Не удалось сохранить');
  document.getElementById('profile-name').textContent=result.full_name;
  status.textContent='ФИО сохранено. Обновите главную страницу, чтобы увидеть его в личном кабинете.';
 }catch(e){status.textContent=e.message}
 finally{button.disabled=false}
};
read();
})();
</script>"""
PAGE=r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Моё ФИО — ЕИС Аудитор</title>__CSS__</head>
<body><header><strong>ЕИС Аудитор · Мои данные</strong>
<a href="/">← В личный кабинет</a></header><main><article>
<h1>Фамилия, имя, отчество</h1>
<p>Заполните данные один раз. Ваше имя будет отображаться в кабинете сотрудника
или руководителя, а также рядом с назначенными заданиями.
Электронная почта и роль остаются прежними.</p>
<div class="meta"><strong id="profile-name">Загрузка…</strong>
<p>Учётная запись: <span id="profile-email">—</span></p></div>
<form id="profile-edit" autocomplete="on">
<label>Фамилия<input name="last_name" autocomplete="family-name" minlength="2" maxlength="80" required></label>
<label>Имя<input name="first_name" autocomplete="given-name" minlength="2" maxlength="80" required></label>
<label>Отчество (необязательно)<input name="patronymic" autocomplete="additional-name" maxlength="80"></label>
<button type="submit">Сохранить ФИО</button></form>
<p id="profile-status" role="status"></p>
<p>Данные доступны владельцу учётной записи и уполномоченному руководителю подразделения.</p>
</article></main>__SCRIPT__</body></html>"""

def render_profile():
    return PAGE.replace("__CSS__",CSS).replace("__SCRIPT__",SCRIPT)
