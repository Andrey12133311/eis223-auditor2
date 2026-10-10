"""V257: types of findings, customer INN, manual checks and richer cards.
Only applies at app import, private recovery snapshot is not modified.
"""
from __future__ import annotations
import asyncio
import re
import sqlite3
from contextlib import contextmanager
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

MANUAL_PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Самостоятельные проверки · 223-ФЗ</title><style>
body{margin:0;font:15px system-ui;background:#f6f7f5;color:#24342a}header{background:#203b32;color:white;padding:22px max(20px,5vw)}main{max-width:1180px;margin:auto;padding:24px}nav{display:flex;gap:10px;flex-wrap:wrap}a{color:#1e6659}header a{color:#e9faf5}button,input{font:inherit;padding:10px;border:1px solid #aebbb1;border-radius:7px}button{cursor:pointer;background:#276f59;color:#fff}.card{padding:16px;background:white;border:1px solid #dce4dd;border-radius:10px;margin:12px 0}small{color:#657169}#notice{min-height:28px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #e5e9e5;text-align:left;padding:12px}td{vertical-align:top}@media(max-width:700px){table,thead,tbody,tr,td{display:block}thead{display:none}td{border:0;padding:5px}tr{padding:10px;border-bottom:1px solid #dde2dd}}
</style></head><body><header><h1>Самостоятельные проверки закупок</h1><nav><a href="/">Автоматические закупки</a><a href="/manual-purchases">Самостоятельные проверки</a><a href="/manual-documents">Проверка собственных документов</a></nav></header><main><section class="card"><h2>Проверить по номеру ЕИС</h2><form id="start"><input id="reg" aria-label="Номер закупки ЕИС" required inputmode="numeric" pattern="[0-9]{10,20}" placeholder="Номер закупки ЕИС"><button>Поставить на проверку</button></form><p id="notice" role="status"></p></section><section class="card"><h2>Введённые вручную закупки</h2><p>Список сохраняется отдельно от общей очереди, в том числе пока документ ожидает источник.</p><table><thead><tr><th>Номер / заказчик</th><th>Проверка</th><th>Документы / результат</th><th>Карточка</th></tr></thead><tbody id="rows"><tr><td>Загрузка…</td></tr></tbody></table><p id="page"></p><button id="more" type="button">Показать ещё</button></section></main><script>
const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let offset=0,total=0;
async function api(url,opt){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json();if(!r.ok)throw Error(d.detail||r.status);return d}
async function load(){const d=await api('/api/v257/manual?offset='+offset+'&limit=80');total=d.total;const labels={pending:'В очереди',running:'Идёт проверка',waiting:'Ожидает источник',saved:'Проверено',completed:'Завершено',failed:'Ошибка'};const rows=d.items.map(p=>'<tr><td><b>'+esc(p.reg_number)+'</b><br>'+esc(p.customer||'Заказчик уточняется')+'<br><small>'+esc(p.title||'Карточка загружается')+'</small></td><td>'+esc(labels[p.manual_status]||p.manual_status||'В очереди')+'<br><small>'+esc(p.requested_at||'')+'</small></td><td>'+Number(p.documents_checked||0)+' / '+Number(p.documents_found||0)+'<br>Нарушений: '+Number(p.violations||0)+' · рисков: '+Number(p.risks||0)+'</td><td><a href="/check-new/'+encodeURIComponent(p.reg_number)+'">Карточка проверки</a></td></tr>').join('');if(offset===0)$('rows').textContent='';$('rows').insertAdjacentHTML('beforeend',rows||'<tr><td>Ручных проверок пока нет</td></tr>');$('page').textContent='Показано '+Math.min(offset+d.items.length,total)+' из '+total;$('more').hidden=offset+d.items.length>=total}
$('start').onsubmit=async e=>{e.preventDefault();const reg=$('reg').value.trim();if(!/^\d{10,20}$/.test(reg))return;$('notice').textContent='Проверка запускается…';try{await api('/api/v204/retry/'+reg,{method:'POST'});await api('/api/v257/manual/'+reg,{method:'POST'});$('notice').innerHTML='Закупка '+esc(reg)+' добавлена. <a href="/check-new/'+reg+'">Открыть проверку</a>';offset=0;await load()}catch(err){$('notice').textContent='Ошибка: '+err.message}};
$('more').onclick=()=>{offset+=80;load().catch(e=>$('page').textContent=e.message)};load().catch(e=>$('page').textContent='Не удалось загрузить список: '+e.message);setInterval(()=>{if(!document.hidden){offset=0;load().catch(()=>{})}},30000);
</script></body></html>'''

SCRIPT = r'''<script id="v257-ui">(()=>{'use strict';
const id=x=>document.getElementById(x);const regFrom=()=>{const m=(id('detailTitle')?.textContent||'').match(/\d{10,20}/);return m?m[0]:''};
async function json(url,opt={}){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json();if(!r.ok)throw Error(d.detail||r.status);return d}
function nav(){if(location.pathname!=='/')return;const main=document.querySelector('main')||document.body;if(id('v257-tabs'))return;const block=document.createElement('nav');block.id='v257-tabs';block.setAttribute('aria-label','Вкладки закупок');block.style.cssText='display:flex;gap:9px;flex-wrap:wrap;margin:12px 0 18px';block.innerHTML='<a href="/" aria-current="page">Автоматические закупки</a><a href="/manual-purchases">Самостоятельные проверки</a><a href="/manual-documents">Самостоятельная проверка документов</a>';block.querySelectorAll('a').forEach(a=>a.style.cssText='padding:10px 14px;border:1px solid #c9d5cb;background:#fff;border-radius:8px;color:#175840;text-decoration:none;font-weight:600');main.insertBefore(block,main.firstChild)}
function setupFilters(){const f=id('filters'),inn=id('inn');if(!f||!inn)return;
if(!id('v257-inn-search')){const b=document.createElement('button');b.type='button';b.id='v257-inn-search';b.textContent='Искать закупки по ИНН';inn.insertAdjacentElement('afterend',b);b.onclick=async()=>{const v=inn.value.trim();const notice=id('customer-progress-v233')||id('runStatus');if(!/^(\d{10}|\d{12})$/.test(v)){if(notice)notice.textContent='Введите ИНН из 10 или 12 цифр';inn.focus();return}b.disabled=true;try{const r=await json('/api/v233/customer/'+v,{method:'POST'});if(notice)notice.textContent='Поиск всех закупок заказчика запущен: найдено '+(r.found||0)+'. Новые данные будут появляться по мере проверки.';f.requestSubmit()}catch(e){if(notice)notice.textContent='Ошибка поиска по ИНН: '+e.message}finally{b.disabled=false}}}
if(!id('v257-violation-type')){const s=document.createElement('select');s.id='v257-violation-type';s.setAttribute('aria-label','Вид нарушения');s.innerHTML='<option value="">Все виды нарушений</option>';const kind=id('kind');if(kind)kind.insertAdjacentElement('afterend',s);else f.appendChild(s);s.onchange=()=>f.requestSubmit();json('/api/v257/violation-types').then(d=>{for(const x of d.items){const o=document.createElement('option');o.value=x.code;o.textContent=x.title+' ('+x.purchases+')';s.appendChild(o)}}).catch(()=>{});id('reset')?.addEventListener('click',()=>{s.value=''},true)}}
function removeRedundant(){const nav=id('v255-document-nav');if(nav){const home=nav.querySelector('a[href="/"]');if(home)home.remove()}}
let last='';async function card(){if(location.pathname!=='/')return;const detail=id('detail'),reg=regFrom();if(!detail||!reg||!detail.querySelector('.detailmeta')||detail.querySelector('#v257-extra')||last===reg)return;last=reg;try{const x=await json('/api/v257/purchase-extra/'+reg);if(regFrom()!==reg)return;const box=document.createElement('div');box.id='v257-extra';box.style.cssText='margin:10px 0;padding:14px;border:1px solid #d3ded4;border-radius:8px;background:#f8faf7;line-height:1.8';const fields=[['Способ закупки',x.method],['Дата размещения',x.published_at],['Заказчик: ИНН',x.customer_inn],['ОГРН',x.customer_ogrn],['Статус карточки ЕИС',x.card_source_status],['Источник данных',x.source],['Последнее обновление',x.last_seen],['Срок подачи заявок',x.submission_start&&x.submission_end?x.submission_start+' — '+x.submission_end:x.submission_end],['Документы',x.documents_checked+' проверено из '+x.documents_found],['Извещения / протоколы / разъяснения',x.protocols+' протоколов · '+x.clarifications+' разъяснений · '+x.cancellations+' отмен']];const h=document.createElement('strong');h.textContent='Подробные сведения о закупке';box.appendChild(h);for(const [k,v]of fields){if(v===null||v===undefined||v==='')continue;const p=document.createElement('div'),b=document.createElement('b');b.textContent=k+': ';p.append(b,document.createTextNode(String(v)));box.appendChild(p)}detail.querySelector('.detailmeta')?.insertAdjacentElement('afterend',box)}catch(e){console.warn('Дополнительные сведения',e)}finally{last=''}}
function run(){nav();setupFilters();removeRedundant();card()}
let queued=false;new MutationObserver(()=>{if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;run()})}}).observe(document.documentElement,{childList:true,subtree:true});if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();})();</script>'''

OLD_FILTER = "$('forms').querySelectorAll('input:checked').forEach(x=>p.append('org_form',x.value));return p}"
NEW_FILTER = "$('forms').querySelectorAll('input:checked').forEach(x=>p.append('org_form',x.value));if($('v257-violation-type')?.value)p.set('violation_type',$('v257-violation-type').value);return p}"
OLD_DASHBOARD = "api('/api/v204/dashboard?'+filters())"
NEW_DASHBOARD = "api('/api/v257/dashboard?'+filters())"

def _html_patch(body: str) -> str:
    if OLD_DASHBOARD in body: body=body.replace(OLD_DASHBOARD,NEW_DASHBOARD,1)
    if OLD_FILTER in body: body=body.replace(OLD_FILTER,NEW_FILTER,1)
    if 'id="v257-ui"' not in body:body=body.replace('</body>',SCRIPT+'</body>',1) if '</body>' in body else body+SCRIPT
    return body

def install(ns:dict)->None:
    app,connection=ns['app'],ns['conn']
    @contextmanager
    def db():
        c=connection();c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def setup():
        with db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS v257_manual_registry(reg TEXT PRIMARY KEY,requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            c.execute('CREATE INDEX IF NOT EXISTS v257_manual_order ON v257_manual_registry(requested_at DESC)')
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'requests_v233' in tables:
                c.execute("INSERT OR IGNORE INTO v257_manual_registry(reg,requested_at) SELECT reg,COALESCE(requested_at,CURRENT_TIMESTAMP) FROM requests_v233 WHERE lane='manual'")
            if 'document_priorities_v233' in tables:
                c.execute("INSERT OR IGNORE INTO v257_manual_registry(reg,requested_at) SELECT reg,COALESCE(requested_at,CURRENT_TIMESTAMP) FROM document_priorities_v233 WHERE lane='manual'")
            c.execute("INSERT OR IGNORE INTO v257_manual_registry(reg) SELECT reg_number FROM purchases WHERE source='manual'")
            c.execute('CREATE INDEX IF NOT EXISTS v257_purchase_inn ON purchases(customer_inn)')
            c.execute('CREATE INDEX IF NOT EXISTS v257_findings_code ON findings(kind,code,reg_number)')
    @app.on_event('startup')
    async def on_start():
        await asyncio.to_thread(setup)
        print('V257 INN TYPES MANUAL SEPARATE READY',flush=True)
    @app.get('/api/v257/violation-types',include_in_schema=False)
    def types():
        with db() as c:
            rows=c.execute("""SELECT COALESCE(code,'') code,MAX(title) title,COUNT(DISTINCT reg_number) purchases
             FROM findings WHERE kind='violation' AND COALESCE(code,'')<>''
             GROUP BY code ORDER BY purchases DESC,code LIMIT 150""").fetchall()
        return {'items':[dict(r) for r in rows]}
    @app.get('/api/v257/dashboard',include_in_schema=False)
    def dashboard(request:Request):
        params=request.query_params
        verified=ns['_verified_set235']()
        if not verified:return {'stats':{'purchases':0,'violations':0,'risks':0,'documents_found':0,'documents_checked':0},'purchases':[],'offset':0,'limit':100}
        conditions=['p.reg_number IN ('+','.join('?' for _ in verified)+')',
                    'p.reg_number NOT IN (SELECT reg FROM v257_manual_registry)']
        args=list(verified)
        forms=params.getlist('org_form')
        if forms:
            forms=[v[:80] for v in forms if v and len(v)<81]
            if forms:conditions.append('p.org_form IN ('+','.join('?' for _ in forms)+')');args.extend(forms)
        kind=(params.get('kind') or params.get('result_kind') or '').strip()
        if kind in ('violations','violation'):conditions.append('p.violations>0')
        if kind in ('risks','risk'):conditions.append('p.risks>0')
        code=(params.get('violation_type') or '').strip()
        if code:
            if len(code)>160:raise HTTPException(400,'Недопустимый вид нарушения')
            conditions.append("EXISTS (SELECT 1 FROM findings f WHERE f.reg_number=p.reg_number AND f.kind='violation' AND f.code=?)")
            args.append(code)
        inn=(params.get('inn') or '').strip()
        if inn:
            if not re.fullmatch(r'\d{10}|\d{12}',inn):raise HTTPException(400,'Введите ИНН из 10 или 12 цифр')
            conditions.append('''(p.customer_inn=? OR EXISTS(SELECT 1 FROM customer_purchases_v233 cp WHERE cp.reg=p.reg_number AND cp.inn=?))''')
            args.extend((inn,inn))
        q=(params.get('q') or '').strip()
        if q:
            conditions.append('(p.reg_number LIKE ? OR p.title LIKE ? OR p.customer LIKE ?)')
            args.extend(['%'+q[:120]+'%']*3)
        for label,op in [('min','>='),('max','<=')]:
            raw=params.get(label)
            if raw:
                try:amount=float(raw)
                except ValueError:raise HTTPException(400,'Некорректная НМЦД')
                if amount<0 or amount>1e16:raise HTTPException(400,'Некорректная НМЦД')
                conditions.append('p.price_num'+op+'?');args.append(amount)
        try:offset=min(max(0,int(params.get('offset') or 0)),1_000_000);limit=min(max(1,int(params.get('limit') or 100)),200)
        except ValueError:raise HTTPException(400,'Некорректная страница')
        sql=' FROM purchases p WHERE '+' AND '.join(conditions)
        with db() as c:
            result=c.execute('SELECT COUNT(*) purchases,COALESCE(SUM(violations),0) violations,COALESCE(SUM(risks),0) risks,COALESCE(SUM(documents_found),0) documents_found,COALESCE(SUM(documents_checked),0) documents_checked'+sql,args).fetchone()
            records=c.execute('SELECT p.*'+sql+' ORDER BY p.last_seen DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        return {'stats':dict(result),'purchases':[dict(r) for r in records],'offset':offset,'limit':limit}
    @app.get('/api/v257/manual',include_in_schema=False)
    def manual_list(offset:int=0,limit:int=80):
        offset=min(max(offset,0),1_000_000);limit=min(max(1,limit),200)
        with db() as c:
            total=c.execute('SELECT COUNT(*) FROM v257_manual_registry').fetchone()[0]
            records=c.execute("""SELECT r.reg AS reg_number,r.requested_at,p.customer,p.title,p.documents_found,
             p.documents_checked,p.violations,p.risks,COALESCE(j.status,'В очереди') manual_status
             FROM v257_manual_registry r LEFT JOIN purchases p ON p.reg_number=r.reg
             LEFT JOIN requests_v233 j ON j.reg=r.reg AND j.lane='manual'
             ORDER BY r.requested_at DESC,r.reg DESC LIMIT ? OFFSET ?""",(limit,offset)).fetchall()
        return {'total':total,'items':[dict(r) for r in records]}
    @app.post('/api/v257/manual/{reg}',include_in_schema=False)
    def register_manual(reg:str):
        if not re.fullmatch(r'\d{10,20}',reg):raise HTTPException(400,'Некорректный номер закупки')
        with db() as c:
            row=c.execute("SELECT reg FROM requests_v233 WHERE reg=? AND lane='manual'",(reg,)).fetchone()
            if not row:raise HTTPException(409,'Сначала запустите самостоятельную проверку')
            c.execute('INSERT OR IGNORE INTO v257_manual_registry(reg) VALUES(?)',(reg,))
        return {'reg':reg,'saved':True}
    @app.get('/api/v257/purchase-extra/{reg}',include_in_schema=False)
    def extra(reg:str):
        if not re.fullmatch(r'\d{10,20}',reg):raise HTTPException(400,'Некорректный номер закупки')
        with db() as c:
            row=c.execute("""SELECT reg_number,method,published_at,customer_inn,card_source_status,source,
             last_seen,submission_start,submission_end,documents_found,documents_checked,
             clarifications,protocols,cancellations FROM purchases WHERE reg_number=?""",(reg,)).fetchone()
        if not row:raise HTTPException(404,'Закупка ещё загружается')
        d=dict(row);d['customer_ogrn']=''
        return d
    @app.get('/manual-purchases',response_class=HTMLResponse,include_in_schema=False)
    def manual_page():return HTMLResponse(MANUAL_PAGE,headers={'Cache-Control':'no-store'})
    class UI(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!='GET' or request.url.path not in ('/','/documents') or 'text/html' not in response.headers.get('content-type',''):
                return response
            try:
                raw=b''.join([x async for x in response.body_iterator])
                new=_html_patch(raw.decode('utf-8'))
                headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')}
                headers['Cache-Control']='no-store'
                return HTMLResponse(new,status_code=response.status_code,headers=headers)
            except Exception as e:
                print('V257 UI_ERROR '+repr(e)[:250],flush=True)
                return response
    app.add_middleware(UI)
