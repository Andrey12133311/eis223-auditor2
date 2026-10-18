"""Responsive electronic case for a genuine account-owned 223-FZ check."""
from __future__ import annotations
import json

STYLE=r"""
<style>
:root{font-family:Inter,"Segoe UI",Arial,sans-serif;color:#244258;
 --navy:#153f64;--blue:#176092;--muted:#657d8e;--edge:#dce8f0;--paper:#fff;--bg:#f3f7fa}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:#294253}
a{color:var(--blue);text-decoration:none;font-weight:680}a:hover{text-decoration:underline}
.case-top{background:#153f64;padding:17px clamp(13px,4vw,55px);
 color:#fff;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.case-top strong{font-size:17px;letter-spacing:-.02em}
.case-top a{color:#fff;border:1px solid #779aba;padding:10px 13px;border-radius:8px;font-size:12px}
.case-main{max-width:1340px;margin:0 auto;padding:22px clamp(12px,3vw,34px) 45px}
.case-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;margin-bottom:17px}
.case-head h1{font-size:clamp(22px,3.4vw,31px);letter-spacing:-.045em;margin:0 0 8px;color:#1d4e73}
.case-head p{margin:0;font-size:12px;color:var(--muted);line-height:1.6}
.case-label{font-size:12px;border-radius:8px;background:#e7f2fa;color:#2b688f;padding:9px 12px;align-self:flex-start;font-weight:750}
.case-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:15px 0}
.case-kpis>div{border:1px solid var(--edge);background:#fff;border-radius:10px;padding:15px}
.case-kpis strong{display:block;color:#245e87;font-size:24px;letter-spacing:-.025em;line-height:1.4;font-variant-numeric:tabular-nums}
.case-kpis small{display:block;color:#6c8291;font-size:11px;line-height:1.45}
.case-tabs{display:flex;overflow-x:auto;gap:7px;padding:3px 0 13px;white-space:nowrap}
.case-tab{border:1px solid #d5e3ed;border-radius:8px;padding:11px 15px;background:#fff;color:#2f6486;font:720 12px system-ui;cursor:pointer}
.case-tab[aria-selected=true]{background:#1d5a85;color:#fff;border-color:#1d5a85}
.case-panel{background:#fff;border:1px solid var(--edge);border-radius:12px;padding:clamp(14px,2.5vw,25px);min-width:0;box-shadow:0 5px 18px #174d6d07}
.case-panel[hidden]{display:none!important}
.case-panel h2{font-size:18px;letter-spacing:-.02em;color:#245478;margin:0 0 11px}
.case-muted{font-size:12px;color:var(--muted);line-height:1.65}
.case-alert{border-left:3px solid #4484b1;background:#edf6fb;padding:10px 12px;border-radius:6px;color:#3b6885;font-size:12px;line-height:1.6}
.case-entry{padding:16px;margin:12px 0;border:1px solid #dee9f1;border-radius:10px;background:#fff}
.case-entry h3{margin:0 0 11px;font-size:15px;color:#1e5075}
.case-proof{font-size:12px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f9fc;border:1px solid #e6eef4;border-radius:8px;padding:12px;margin:11px 0}
.case-entry small{font-size:11px;color:#6b8190}
.case-problem{font-size:12px;color:#9c5f26;background:#fff7ea;padding:9px;margin-top:10px}
.case-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.case-links a{font-size:12px;padding:7px 10px;border:1px solid #dce9f1;background:#f3f8fc;border-radius:7px}
.case-meta-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
.case-meta-grid>div{border:1px solid #e5edf3;padding:13px;border-radius:8px;min-width:0}
.case-meta-grid small{display:block;color:#788d9c;font-size:11px;margin-bottom:4px}
.case-meta-grid strong{font-size:13px;overflow-wrap:anywhere;font-weight:700}
.case-meta-grid a{font-size:12px}
.case-list{margin:0;padding:0;list-style:none}
.case-list li{padding:12px 5px;border-bottom:1px solid #e6edf2;font-size:12px;overflow-wrap:anywhere}
.case-list li:last-child{border-bottom:0}
.case-list b{display:block;color:#284b65;margin-bottom:4px}
.case-table-wrap{overflow-x:auto;border:1px solid #e0e9f0;border-radius:9px}
.case-table{width:100%;border-collapse:collapse;font-size:12px;min-width:600px}
.case-table th{background:#eaf3f9;color:#3b6481;text-align:left;font-size:11px;font-weight:750}
.case-table :is(th,td){padding:11px;border-bottom:1px solid #e7eef3;vertical-align:top}
.case-table tr:last-child td{border-bottom:0}
.case-note textarea{width:100%;min-height:105px;padding:12px;border-radius:9px;border:1px solid #ccdbe6;background:#fff;color:#254456;font:13px/1.5 inherit;resize:vertical}
.case-note button{border:1px solid #155e90;background:#155e90;color:#fff;padding:10px 14px;border-radius:8px;font:730 12px system-ui;margin-top:10px;cursor:pointer}
.case-note button:disabled{opacity:.55}
.case-note label{display:block;font-size:12px;color:#526e83;font-weight:720;margin-bottom:7px}
.case-note select{width:100%;padding:11px;border-radius:8px;border:1px solid #cddfeb;margin:0 0 10px;background:#fff;color:#294b61}
.case-timeline{list-style:none;padding:0 0 0 18px;margin:0;border-left:2px solid #d5e6f1}
.case-timeline li{position:relative;margin:0 0 17px;padding:3px 0 7px 15px;font-size:12px}
.case-timeline li::before{content:"";position:absolute;width:10px;height:10px;border-radius:50%;background:#3d86b8;top:8px;left:-24px;border:2px solid #fff}
.case-timeline strong{display:block;font-size:13px;color:#255276}
.case-timeline time{font-weight:750;color:#35799e}
.case-empty{font-size:12px;color:#7e8e99;margin:12px 0}
.case-chip{background:#eaf5ed;color:#29724f;padding:5px 9px;border-radius:10px;font-size:11px}
:focus-visible{outline:3px solid #8fc2e0;outline-offset:2px}
@media(max-width:700px){.case-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.case-meta-grid{grid-template-columns:1fr}.case-main{padding-top:15px}.case-entry{padding:13px}.case-top{padding:12px}}
</style>
"""

SCRIPT=r"""
<script>
(()=>{
const REG=__REG__;
const $=id=>document.getElementById(id),status=$("case-status");
function report(text){status.textContent=text}
function text(tag,value,className){const n=document.createElement(tag);n.textContent=String(value??"");if(className)n.className=className;return n}
function sourceLink(url,label){
 try{const u=new URL(url);const host=u.hostname.toLowerCase();
 if(u.protocol!=="https:"||!(host==="zakupki.gov.ru"||host.endsWith(".zakupki.gov.ru")))return null;
 const a=text("a",label);a.href=u.href;a.target="_blank";a.rel="noopener noreferrer";return a;
 }catch{return null}
}
function lawLink(row){
 const links=document.createElement("div");links.className="case-links";
 for(const item of row.consultant_references||[]){
  if(!item.url?.startsWith("https://www.consultant.ru/document/"))continue;
  const a=text("a",item.label+" · КонсультантПлюс ↗");
  a.href=item.url;a.target="_blank";a.rel="noopener noreferrer";links.append(a);
 }
 return links;
}
function drawEntry(item,index,type){
 const box=document.createElement("article");box.className="case-entry";
 box.append(text("h3",type==="findings"?"Карточка нарушения · "+(index+1)+". "+item.title:item.title));
 if(item.law)box.append(text("div","Основание: "+item.law,"case-muted"));
 const evidence=text("div",item.evidence||"Текст доказательства пока отсутствует","case-proof");
 box.append(evidence);
 if(item.document)box.append(text("small","Оригинал: "+item.document));
 const links=lawLink(item);
 const src=sourceLink(item.source_url,"Открыть источник ЕИС ↗");if(src)links.append(src);
 for(const other of (item.supporting_sources||[]).slice(0,4)){
  const more=sourceLink(other.url,"Также: "+(other.document||"Оригинал ЕИС"));
  if(more)links.append(more);
 }
 if(links.childElementCount)box.append(links);
 if(item.warnings?.length)box.append(text("p","Требует уточнения: "+item.warnings.join("; "),"case-problem"));
 if(item.source_representations>1)box.append(text("small","Повторные записи источников объединены: "+item.source_representations));
 if(type==="findings"){
  if(item.finding_key&&window.EISFindingReview)box.append(window.EISFindingReview.control(REG,item.finding_key));
  const b=text("button","Оставить замечание к нарушению");b.type="button";
  b.style.cssText="margin-top:12px;padding:9px 12px;background:#eaf4fb;border:1px solid #d4e5ef;border-radius:8px;color:#1c5b8b;font-weight:700;cursor:pointer";
  b.onclick=()=>{document.querySelector('[data-case-tab="notes"]').click();
   $("case-note-finding").value=item.finding_key||"";$("case-note-body").focus()};
  box.append(b);
 }
 return box;
}
function fillEntries(id,items,kind){
 const root=$(id);root.replaceChildren();
 if(!items?.length){root.append(text("p","Записей пока нет.","case-empty"));return}
 items.forEach((item,i)=>root.append(drawEntry(item,i,kind)));
}
function counter(id,val){$(id).textContent=Number(val??0).toLocaleString("ru-RU")}
function meta(id,value){$(id).textContent=value||"Не указано"}
function render(data){
 const p=data.purchase||{},audit=data.audit||{};
 meta("case-title",p.title||"Закупка № "+REG);meta("case-customer",p.customer);
 meta("case-inn",p.customer_inn);meta("case-method",p.method);meta("case-price",p.price);
 meta("case-audit",audit.state==="completed"?"Завершена юридическая проверка":
  audit.state==="unknown"?"Проверка ещё не завершена":audit.state);
 counter("case-violations",data.findings_count);counter("case-duplicates",data.duplicates_grouped);
 counter("case-docs",audit.documents_read);counter("case-questions",
  (data.comparisons||[]).length+(data.review_items||[]).length);
 $("case-note").textContent=data.note||"";
 const src=sourceLink(p.url,"Открыть карточку ЕИС ↗");
 if(src)$("case-source").append(src);
 fillEntries("case-finding-entries",data.findings||[],"findings");
 if(window.EISFindingReview)window.EISFindingReview.load(REG,true);
 fillEntries("case-comparison-entries",data.comparisons||[],"comparisons");
 fillEntries("case-review-entries",data.review_items||[],"review");
 const docs=$("case-doc-rows");docs.replaceChildren();
 for(const doc of data.documents||[]){
  const tr=document.createElement("tr");
  for(const value of [doc.name,doc.type,doc.status,
                       doc.text_chars?doc.text_chars+" знаков":"Текст не извлечён"]){
   tr.append(text("td",value||"—"));
  }
  const td=document.createElement("td"),a=sourceLink(doc.url,"Источник ↗");
  if(a)td.append(a);else td.textContent="—";
  tr.append(td);docs.append(tr);
 }
 if(!(data.documents||[]).length){
  const tr=document.createElement("tr"),td=text("td","Оригиналы пока недоступны");
  td.colSpan=5;tr.append(td);docs.append(tr);
 }
 const timeline=$("case-timeline");timeline.replaceChildren();
 for(const event of data.timeline||[]){
  const li=document.createElement("li");
  const day=text("time",event.day);
  li.append(day,text("strong",event.title),text("div",event.source,"case-muted"),
    text("div",event.qualification,"case-muted"));
  const a=sourceLink(event.url,"Проверить в ЕИС ↗");if(a)li.append(a);
  timeline.append(li);
 }
 if(!(data.timeline||[]).length)timeline.append(text("li","Подтверждённых дат пока нет.","case-empty"));
 const select=$("case-note-finding");select.replaceChildren();
 const empty=document.createElement("option");empty.value="";empty.textContent="Общее замечание к делу";select.append(empty);
 for(const item of data.findings||[]){
  if(!item.finding_key)continue;
  const opt=document.createElement("option");opt.value=item.finding_key;
  opt.textContent=item.title.slice(0,130);select.append(opt);
 }
 const notes=$("case-notes");notes.replaceChildren();
 for(const n of data.notes||[]){
  const li=document.createElement("li");
  li.append(text("b",new Date(n.created_at).toLocaleString("ru-RU")),text("p",n.body));
  notes.append(li);
 }
 if(!(data.notes||[]).length)notes.append(text("li","Замечаний пока нет.","case-empty"));
 report("Сведения получены из сохранённых результатов ЕИС. Исходные документы не изменялись.");
}
function tab(target){
 document.querySelectorAll("[data-case-tab]").forEach(b=>{
  const selected=b.dataset.caseTab===target;b.setAttribute("aria-selected",String(selected));
  $("case-panel-"+b.dataset.caseTab).hidden=!selected
 });
}
document.querySelectorAll("[data-case-tab]").forEach(button=>button.onclick=()=>tab(button.dataset.caseTab));
async function load(){
 try{
  const r=await fetch("/me/cases/"+REG+"/data",{credentials:"same-origin",cache:"no-store"});
  if(r.status===401){location.replace("/account/login");return}
  if(!r.ok){const o=await r.json().catch(()=>({}));throw Error(o.detail||"Не удалось получить дело")}
  render(await r.json());
 }catch(err){report("Дело временно недоступно: "+err.message)}
}
$("case-note-form").onsubmit=async event=>{
 event.preventDefault();const btn=$("case-note-submit");btn.disabled=true;
 try{
  const body=$("case-note-body").value.trim(),finding_key=$("case-note-finding").value;
  if(body.length<4)throw Error("Замечание слишком короткое");
  const r=await fetch("/me/cases/"+REG+"/notes",{method:"POST",credentials:"same-origin",
    headers:{"Content-Type":"application/json"},body:JSON.stringify({body,finding_key})});
  if(!r.ok){const o=await r.json().catch(()=>({}));throw Error(o.detail||"Ошибка сохранения")}
  $("case-note-body").value="";await load();tab("notes");
 }catch(err){report(err.message)}finally{btn.disabled=false}
};
load();
})();
</script>
"""


def render_case(reg):
    from scripts.v382_finding_review_ui import STYLE as review_style, SCRIPT as review_script
    if not (isinstance(reg,str) and reg.isdigit() and len(reg)==11 and reg[0]=="3"):
        raise ValueError("Invalid EIS registry number")
    markup=r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Электронное дело закупки № __REG__ — ЕИС Аудитор</title>__STYLE__</head>
<body><header class="case-top"><strong>ЕИС Аудитор · Электронное дело</strong>
<a href="/account/home">← Мои самостоятельные проверки</a></header>
<main class="case-main"><div class="case-head"><div>
<h1>Дело закупки № __REG__</h1>
<p id="case-title">Загрузка сведений о закупке…</p></div>
<span class="case-label">Личное дело · 223-ФЗ</span></div>
<section class="case-kpis" aria-label="Результаты">
<div><strong id="case-violations">—</strong><small>Нарушений</small></div>
<div><strong id="case-questions">—</strong><small>Вопросов для проверки</small></div>
<div><strong id="case-docs">—</strong><small>Прочитано документов ЕИС</small></div>
<div><strong id="case-duplicates">—</strong><small>Повторных записей объединено</small></div>
</section>
<div class="case-panel" style="margin-bottom:12px">
<h2>Сведения о закупке</h2><div class="case-meta-grid">
<div><small>Заказчик</small><strong id="case-customer">—</strong></div>
<div><small>ИНН заказчика</small><strong id="case-inn">—</strong></div>
<div><small>Способ закупки</small><strong id="case-method">—</strong></div>
<div><small>Стоимость</small><strong id="case-price">—</strong></div>
<div><small>Состояние проверки</small><strong id="case-audit">—</strong></div>
<div id="case-source"><small>Первоисточник</small></div></div></div>
<nav class="case-tabs" aria-label="Разделы дела">
<button class="case-tab" type="button" data-case-tab="findings" aria-selected="true">Нарушения</button>
<button class="case-tab" type="button" data-case-tab="compare" aria-selected="false">Сопоставление</button>
<button class="case-tab" type="button" data-case-tab="review" aria-selected="false">Требует проверки</button>
<button class="case-tab" type="button" data-case-tab="dates" aria-selected="false">Сроки и изменения</button>
<button class="case-tab" type="button" data-case-tab="docs" aria-selected="false">Оригиналы ЕИС</button>
<button class="case-tab" type="button" data-case-tab="notes" aria-selected="false">Замечания</button>
</nav>
<div class="case-panel" id="case-panel-findings">
<h2>Нарушения</h2><p class="case-alert" id="case-note">Загружаем сведения…</p>
<div id="case-finding-entries"></div></div>
<div class="case-panel" id="case-panel-compare" hidden>
<h2>Сопоставление карточки ЕИС и документов</h2>
<p class="case-alert">Несовпадения цены, дат, способа закупки и ИНН требуют проверки версии,
лота и первоисточника. Они не считаются новыми нарушениями автоматически.</p>
<div id="case-comparison-entries"></div></div>
<div class="case-panel" id="case-panel-review" hidden>
<h2>Требует проверки</h2>
<p class="case-muted">Здесь находятся замечания к срокам, документам и обстоятельствам,
для которых пока недостаточно доказательств.</p><div id="case-review-entries"></div></div>
<div class="case-panel" id="case-panel-dates" hidden>
<h2>История сроков и изменений</h2>
<p class="case-alert">Сроки сопоставляются по календарным датам, без времени суток.
События истории ЕИС не признаются нарушениями без юридической оценки и проверки исключений.</p>
<ul class="case-timeline" id="case-timeline"></ul></div>
<div class="case-panel" id="case-panel-docs" hidden>
<h2>Оригиналы документов ЕИС</h2>
<p class="case-muted">Перечень реально сохранённых записей с состоянием извлечения текста.</p>
<div class="case-table-wrap"><table class="case-table"><thead><tr>
<th>Документ</th><th>Вид</th><th>Состояние</th><th>Текст</th><th>Источник</th>
</tr></thead><tbody id="case-doc-rows"></tbody></table></div></div>
<div class="case-panel" id="case-panel-notes" hidden>
<h2>Замечания сотрудника</h2>
<p class="case-muted">Замечания сохраняются с датой и видны вашему руководителю,
если он назначен администратором. Исходное доказательство не редактируется.</p>
<form class="case-note" id="case-note-form">
<label for="case-note-finding">К чему относится замечание</label>
<select id="case-note-finding"></select>
<label for="case-note-body">Текст замечания</label>
<textarea id="case-note-body" minlength="4" maxlength="3000" required
 placeholder="Укажите документ, обстоятельство и что нужно проверить или исправить"></textarea>
<button id="case-note-submit" type="submit">Сохранить замечание</button></form>
<h2 style="margin-top:25px">История замечаний</h2><ul id="case-notes" class="case-list"></ul></div>
<div class="case-muted" role="status" id="case-status" style="padding:15px 0"></div>
<p class="case-muted"><a href="/account/memos">Служебные записки ODT/PDF →</a></p>
</main>__SCRIPT__</body></html>"""
    return (markup.replace("__REG__",reg).replace("__STYLE__",STYLE+review_style)
                  .replace("__SCRIPT__",review_script+SCRIPT.replace("__REG__",json.dumps(reg))))
