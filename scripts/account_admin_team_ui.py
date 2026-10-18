"""Shared, explicit administrator team-assignment controls (no implicit saves)."""
TEAM_SCRIPT = r'''<script>
window.eis223TeamControl=function(person,users,status,onSaved){
 const root=document.createElement('div');root.style.cssText='flex-basis:100%;padding:8px 0';
 if(person.role!=='user')return root;
 const managers=users.filter(u=>u.role==='manager'&&u.status==='approved');
 let current=person.manager_id||'';
 const label=document.createElement('label');label.textContent='Руководитель: ';
 const select=document.createElement('select');select.setAttribute('aria-label','Руководитель сотрудника '+person.email);
 select.style.maxWidth='100%';
 const option=(value,text,disabled=false)=>{const o=document.createElement('option');o.value=value;o.textContent=text;o.disabled=disabled;select.append(o)};
 option('','Без руководителя');
 for(const m of managers)option(m.id,(m.full_name?m.full_name+' · ':'')+m.email);
 if(current&&!managers.some(m=>m.id===current))option(current,(person.manager_name||person.manager_email||current)+' (недоступен)',true);
 select.value=current;
 const save=document.createElement('button');save.type='button';save.textContent='Сохранить руководителя';save.disabled=true;
 const summary=document.createElement('p');
 const describe=()=>{summary.textContent='Сейчас: '+(select.selectedOptions[0]?.textContent||'Без руководителя')};describe();
 const note=document.createElement('p');note.textContent='При переназначении новый руководитель получает доступ к нарушениям сотрудника, включая ранее отправленные и согласованные; прежний теряет доступ. Авторы и решения в истории сохраняются. Без руководителя отправка нарушений недоступна.';
 note.style.cssText='font-size:12px;max-width:760px';
 let busy=false;
 select.onchange=()=>{save.disabled=busy||select.value===current};
 if(person.status!=='approved'){
  for(const o of select.options)if(o.value)o.disabled=true;
  select.disabled=!current;
  const warning=document.createElement('p');warning.textContent='Учётная запись не активна: можно только снять текущее назначение.';root.append(warning);
 }
 save.onclick=async()=>{
  if(busy||select.value===current)return;
  const next=select.value;
  if(!window.confirm('Сохранить руководителя сотрудника '+person.email+'? Доступ к существующим нарушениям будет определяться новым назначением.'))return;
  busy=true;save.disabled=true;select.disabled=true;status.textContent='Сохранение руководителя…';
  try{
   const response=await fetch('/manager/admin/team/'+encodeURIComponent(person.id),{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({manager_id:next||null})});
   const data=await response.json().catch(()=>({}));
   if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'HTTP '+response.status);
   if((data.manager||'')!==next)throw Error('Сервер не подтвердил выбранного руководителя. Обновите список.');
   current=next;person.manager_id=next||null;describe();status.textContent=next?'Руководитель сохранён':'Назначение руководителя снято';
   if(onSaved)await onSaved();
  }catch(e){status.textContent='Не удалось сохранить руководителя: '+e.message}
  finally{busy=false;select.disabled=person.status!=='approved'&&!current;save.disabled=select.value===current}
 };
 label.append(select);root.append(summary,label,save,note);return root;
};
</script>'''
