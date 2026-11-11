"""Export the real UI builders as one offline, fictional-data demonstration.

Only the downloadable artifact gets a local fetch/navigation adapter. Nothing is
inserted into product HTML builders and no production endpoint is contacted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'), str(ROOT / 'scripts')]
from render_interface_preview import ORIGIN, fixtures, pages


def safe_json(value):
    # Keep serialized HTML and user-facing text inside the outer script element.
    return json.dumps(value, ensure_ascii=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


BOOTSTRAP = r"""
(() => {
 'use strict';
 const origin=__ORIGIN__, documents=__DOCUMENTS__, api=__API__;
 const notify=text=>parent.postMessage({type:'eis-demo-note',text},'*');
 const resolve=input=>new URL(typeof input==='string'?input:input.url,document.baseURI);
 // The real workspace announces an in-page switch. Keep the outer selector in
 // step without rebuilding the iframe and losing its filters or scroll.
 document.addEventListener('eis:viewchange',event=>{
  const view=event.detail?.view;
  if(new URL(document.baseURI).pathname==='/'&&['registry','cabinet'].includes(view))
   parent.postMessage({type:'eis-demo-view',view},'*');
 });
 function response(body,kind,path,status=200){
  const r=new Response(body,{status,headers:{'Content-Type':kind,'Cache-Control':'no-store'}});
  Object.defineProperty(r,'url',{value:origin+path});return r;
 }
 window.fetch=async (input,options={})=>{
  const url=resolve(input),method=String(options.method||input?.method||'GET').toUpperCase();
  if(url.origin!==origin)throw new TypeError('В демонстрации внешние запросы отключены');
  if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
  const page=documents[url.pathname];
  if(page&&method==='GET')return response(page[1],'text/html; charset=utf-8',url.pathname);
  if(url.pathname.startsWith('/me/manual-checks/')&&method==='POST'){
   const reg=url.pathname.slice('/me/manual-checks/'.length);
   if(!/^3[0-9]{10}$/.test(reg))return response(JSON.stringify({detail:'Укажите номер ЕИС из 11 цифр.'}),'application/json',url.pathname,422);
   // A short local wait exposes the real form's pending state without making
   // a network request or inventing a new saved audit result.
   await new Promise(resolve=>setTimeout(resolve,300));
   notify('Демонстрация: запрос на проверку обработан локально. Ниже остаются вымышленные сохранённые результаты.');
   return response(JSON.stringify({ok:true,demo:true,reg_number:reg}),'application/json',url.pathname,202);
  }
  if(url.pathname==='/me/profile'&&method==='PUT'){
   let value={};try{value=JSON.parse(options.body||'{}')}catch{}
   const p=api['/me/profile'];for(const key of ['first_name','last_name','patronymic'])if(key in value)p[key]=String(value[key]);
   p.full_name=[p.last_name,p.first_name,p.patronymic].filter(Boolean).join(' ');
   return response(JSON.stringify(p),'application/json',url.pathname);
  }
  if(!['GET','HEAD'].includes(method)){
   notify('Это предпросмотр: данное действие требует рабочего сервера и здесь не выполняется.');
   return response(JSON.stringify({detail:'В предпросмотре это действие не выполняется. Откройте рабочий сайт после публикации.'}),'application/json',url.pathname,409);
  }
  // Reads use the same static, explicitly fictional data as screenshots.
  if(Object.hasOwn(api,url.pathname))return response(JSON.stringify(api[url.pathname]),'application/json',url.pathname);
  return response(JSON.stringify({detail:'Для этого запроса нет данных в автономном предпросмотре.'}),'application/json',url.pathname,404);
 };
 window.open=()=>{notify('Ссылки на внешние источники и скачивание файлов отключены в демонстрации.');return null};
 window.addEventListener('click',event=>{
  if(event.defaultPrevented)return;
  const anchor=event.target.closest?.('a[href]');if(!anchor)return;
  let url;try{url=resolve(anchor.href)}catch{return}
  event.preventDefault();
  if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey){notify('В демонстрации страницы открываются в этом окне.');return}
  if(url.origin===origin&&url.pathname==='/'&&url.hash==='#cabinet'){
   parent.postMessage({type:'eis-demo-nav',path:'/#cabinet'},'*');return;
  }
  if(url.origin===origin&&url.pathname===new URL(document.baseURI).pathname&&url.hash){
   try{document.querySelector(url.hash)?.scrollIntoView()}catch{}
   return;
  }
  if(url.origin===origin&&documents[url.pathname])parent.postMessage({type:'eis-demo-nav',path:url.pathname},'*');
  else notify('Это демонстрационный набор: оригиналы документов и дополнительные серверные действия здесь недоступны.');
 });
 document.addEventListener('submit',event=>{
  // Let product form listeners run, while every native navigation stays local.
  event.preventDefault();
  if(!['login-form','registration-form','login','registration'].includes(event.target.id))return;
  event.stopImmediatePropagation();
  const status=document.getElementById('auth-status')||document.getElementById('message');
  if(status)status.textContent='Демонстрация: вводить настоящий пароль не требуется. Выберите рабочий экран в верхней панели.';
 },true);
})();
"""

SHELL = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ЕИС Аудитор — интерактивный предпросмотр</title>
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#e9e9ed;color:#1d1d1f;font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.demo-toolbar{min-height:60px;padding:10px 16px;background:#fff;border-bottom:1px solid #dedee3;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.demo-title{font-weight:650;letter-spacing:-.02em}.demo-title small{display:block;color:#636366;font-size:11px;font-weight:400;margin-top:1px}
.demo-controls{display:flex;gap:7px;align-items:center;flex-wrap:wrap}select,button{border:1px solid #d0d0d7;border-radius:7px;background:#fff;color:#1d1d1f;font:inherit;min-height:34px;padding:6px 10px}
button{cursor:pointer}button[aria-pressed=true]{background:#eaf2ff;border-color:#b9d3f4;color:#0055ad}
button:focus-visible,select:focus-visible{outline:3px solid #0066cc;outline-offset:2px}
.demo-stage{height:calc(100dvh - var(--bar-height,60px));display:flex;justify-content:center;align-items:stretch;overflow:hidden;padding:0}
iframe{width:100%;height:100%;border:0;background:#f5f5f7;box-shadow:0 0 20px #1d1d1f0a}
.demo-stage.is-mobile{padding:12px 0}.demo-stage.is-mobile iframe{width:min(390px,100%);height:100%;border:1px solid #c8c8cf;border-radius:16px}
#demo-note{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);max-width:min(600px,calc(100vw - 24px));padding:11px 15px;border-radius:10px;background:#1d1d1f;color:#fff;box-shadow:0 4px 15px #1d1d1f25;font-size:12px;z-index:10}
#demo-note:empty{display:none}
@media(max-width:600px){.demo-toolbar{padding:9px 12px;gap:8px}.demo-controls{width:100%}.demo-controls select{flex:1;min-width:0}.demo-controls button{min-height:36px}.demo-title small{font-size:10px}}
</style></head><body>
<header class="demo-toolbar" id="demo-toolbar">
<div class="demo-title">ЕИС Аудитор · Предпросмотр<small>Данные вымышлены. Рабочий сайт не изменяется.</small></div>
<div class="demo-controls"><select id="demo-route" aria-label="Экран предпросмотра"></select>
<button id="demo-desktop" type="button" aria-pressed="true">Компьютер</button><button id="demo-mobile" type="button" aria-pressed="false">Телефон</button></div></header>
<main class="demo-stage" id="demo-stage"><iframe id="demo-frame" title="Интерактивный макет ЕИС Аудитора" sandbox="allow-scripts allow-same-origin allow-forms"></iframe></main>
<p id="demo-note" role="status" aria-live="polite"></p>
<script>
(()=>{
 const origin=__DEMO_ORIGIN_PAYLOAD__,documents=__DEMO_DOCS_PAYLOAD__,api=__DEMO_API_PAYLOAD__,bootstrap=__DEMO_BOOTSTRAP_PAYLOAD__;
 const frame=document.getElementById('demo-frame'),routes=document.getElementById('demo-route'),stage=document.getElementById('demo-stage');
 const names={registry:'Реестр закупок',login:'Вход',register:'Регистрация',staff:'Задачи и поручения',manager:'Кабинет руководителя',profile:'Профиль',manual:'Мои проверки',thematic:'Тематические проверки',memos:'Служебные записки',card:'Карточка закупки',card2:'Карточка другой закупки','card-pending':'Карточка: проверка выполняется','card-incomplete':'Карточка: неполные доказательства'};
 const safe=value=>JSON.stringify(value).replace(/</g,'\\u003c');
 for(const [path,[name]] of Object.entries(documents)){
  const option=document.createElement('option');option.value=path;option.textContent=names[name]||name;routes.append(option);
  if(path==='/'){const cabinet=document.createElement('option');cabinet.value='/#cabinet';cabinet.textContent='Мой кабинет';routes.append(cabinet)}
 }
 let current='/',timer;
 function note(text){clearTimeout(timer);document.getElementById('demo-note').textContent=text;timer=setTimeout(()=>document.getElementById('demo-note').textContent='',6500)}
 function show(path){
  let url;try{url=new URL(path,origin)}catch{return}
  if(url.origin!==origin||!documents[url.pathname])return;
  const key=url.pathname==='/'&&url.hash==='#cabinet'?'/#cabinet':url.pathname;
  current=key;routes.value=key;
  let html=documents[url.pathname][1];
  if(url.pathname==='/'&&!new DOMParser().parseFromString(html,'text/html').getElementById('eis361-heading'))html=html.replace(/(<main\b[^>]*>)/i,'$1<section class="eis361-heading" id="eis361-heading" aria-label="Общий реестр закупок"><div class="eis361-heading-text"><div><h2>Реестр закупок по 223-ФЗ</h2><p>Проверка документов и подтверждённые нарушения</p></div></div></section>');
  const init=bootstrap.replace('__ORIGIN__',safe(origin)).replace('__DOCUMENTS__',safe(documents)).replace('__API__',safe(api));
  // The adapter is injected before every product script, into the artifact only.
  const prefix='<base href="'+origin+key+'"><script>'+init+'<'+ '/script>';
  html=html.replace(/(<head\b[^>]*>)/i,'$1'+prefix);
  frame.srcdoc=html;
 }
 routes.onchange=()=>show(routes.value);
 function width(mobile){stage.classList.toggle('is-mobile',mobile);document.getElementById('demo-mobile').setAttribute('aria-pressed',String(mobile));document.getElementById('demo-desktop').setAttribute('aria-pressed',String(!mobile))}
 document.getElementById('demo-mobile').onclick=()=>width(true);document.getElementById('demo-desktop').onclick=()=>width(false);
 window.addEventListener('message',event=>{if(event.source!==frame.contentWindow)return;const value=event.data;if(!value||typeof value!=='object')return;
  if(value.type==='eis-demo-nav'&&(documents[value.path]||value.path==='/#cabinet'))show(value.path);
  if(value.type==='eis-demo-view'&&['registry','cabinet'].includes(value.view)){
   current=value.view==='cabinet'?'/#cabinet':'/';routes.value=current;
  }
  if(value.type==='eis-demo-note'&&typeof value.text==='string')note(value.text);
 });
 const bar=document.getElementById('demo-toolbar');new ResizeObserver(()=>document.documentElement.style.setProperty('--bar-height',bar.getBoundingClientRect().height+'px')).observe(bar);
 show(current);
})();
</script></body></html>"""


def export(output: Path):
    documents = pages()
    payload = (SHELL.replace('__DEMO_ORIGIN_PAYLOAD__', safe_json(ORIGIN))
               .replace('__DEMO_DOCS_PAYLOAD__', safe_json(documents))
               .replace('__DEMO_API_PAYLOAD__', safe_json(fixtures()))
               .replace('__DEMO_BOOTSTRAP_PAYLOAD__', safe_json(BOOTSTRAP)))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/workspace/eis223-panels-preview/preview.html'))
    arguments = parser.parse_args()
    print(export(arguments.output))
