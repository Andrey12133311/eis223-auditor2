"""V257: types of findings, customer INN, manual checks and richer cards.
Only applies at app import, private recovery snapshot is not modified.
"""
from __future__ import annotations
import asyncio
import json
import re
import sqlite3
import time
from dashboard_read_cache import MembershipCache, MembershipUnavailable, ensure_read_indexes
from dashboard_load_ui import inject as dashboard_load_state
from dashboard_async_read import DashboardReadCoordinator, DashboardWarming, SQLiteDataVersion
from starlette.datastructures import QueryParams
from contextlib import contextmanager
from v295_eis_originals import root,allowed_documents,saved_document
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from v319_manual_inputs import MANUAL_SOURCE_SQL,ensure_schema,migrate_legacy_inputs,record_input
from v342_identity import organization_display_name

MANUAL_PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Самостоятельные проверки · 223-ФЗ</title><style>
body{margin:0;font:15px system-ui;background:#f6f7f5;color:#24342a}header{background:#203b32;color:white;padding:22px max(20px,5vw)}main{max-width:1180px;margin:auto;padding:24px}nav{display:flex;gap:10px;flex-wrap:wrap}a{color:#1e6659}header a{color:#e9faf5}button,input{font:inherit;padding:10px;border:1px solid #aebbb1;border-radius:7px}button{cursor:pointer;background:#276f59;color:#fff}.card{padding:16px;background:white;border:1px solid #dce4dd;border-radius:10px;margin:12px 0}small{color:#657169}#notice{min-height:28px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #e5e9e5;text-align:left;padding:12px}td{vertical-align:top}@media(max-width:700px){table,thead,tbody,tr,td{display:block}thead{display:none}td{border:0;padding:5px}tr{padding:10px;border-bottom:1px solid #dde2dd}}
</style></head><body><header><h1>Самостоятельные проверки закупок</h1><nav><a href="/">Автоматические закупки</a><a href="/account/home">Самостоятельные проверки</a><a href="/manual-documents">Проверка собственных документов</a></nav></header><main><section class="card"><h2>Проверить по номеру ЕИС</h2><form id="start"><input id="reg" aria-label="Номер закупки ЕИС" required inputmode="numeric" pattern="[0-9]{10,20}" placeholder="Номер закупки ЕИС"><button>Поставить на проверку</button></form><p id="notice" role="status"></p></section><section class="card"><h2>Введённые вручную закупки</h2><p>Список сохраняется отдельно от общей очереди, в том числе пока документ ожидает источник.</p><table><thead><tr><th>Номер / заказчик</th><th>Проверка</th><th>Документы / результат</th><th>Карточка</th></tr></thead><tbody id="rows"><tr><td>Загрузка…</td></tr></tbody></table><p id="page"></p><button id="more" type="button">Показать ещё</button></section></main><script>
const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let offset=0,total=0;
async function api(url,opt){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json();if(!r.ok)throw Error(d.detail||r.status);return d}
function priceText(p){
 const raw=p.price_num;
 if(raw!==null&&raw!==undefined&&raw!==''){
  const amount=Number(raw);
  if(Number.isFinite(amount)&&amount>=0)
   return new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)+' ₽';
 }
 return p.price&&String(p.price).trim()?String(p.price).trim():'не установлена';
}
async function load(){
 const d=await api('/api/v257/manual?offset='+offset+'&limit=80');total=d.total;
 const labels={queued:'В очереди',leased:'ЕИС получает оригиналы',
 done:'Поиск окончен, проверка не подтверждена',partial:'Загружено частично',
 error:'Ошибка получения',deferred:'Ожидает повтор',pending:'В очереди',
 running:'Юридическая проверка',waiting:'Ожидает оригиналы ЕИС',
 saved:'Оригиналы сохранены',completed:'Проверено',auditing:'Юридическая проверка',
 search_only:'Поиск окончен, документы ещё не проверены',failed:'Ошибка юридической проверки'};
 const rows=d.items.map(p=>'<tr><td><b>'+esc(p.reg_number)+'</b><br>'+
 esc(p.customer||'Заказчик уточняется')+'<br><small>'+
 esc(p.title||'Карточка загружается')+'</small><br><small>Стоимость закупки: '+
 esc(priceText(p))+'</small></td><td>'+
 esc(labels[p.audit_status]||labels[p.manual_status]||p.audit_status||p.manual_status||'В очереди')+
 '<br><small>'+esc(p.requested_at||'')+'</small></td><td>'+
 Number(p.documents_checked||0)+' / '+Number(p.documents_found||0)+
 '<br>Нарушений: '+Number(p.violations||0)+' · рисков: '+Number(p.risks||0)+
 (p.originals_expected>0?'<br><small>Оригиналы ЕИС: '+
 Number(p.originals_received||0)+' / '+Number(p.originals_expected)+'</small>':'')+
 '</td><td><a href="/check-new/'+encodeURIComponent(p.reg_number)+
 '">Карточка проверки</a></td></tr>').join('');
 if(offset===0)$('rows').textContent='';
 $('rows').insertAdjacentHTML('beforeend',rows||'<tr><td>Ручных проверок пока нет</td></tr>');
 $('page').textContent='Показано '+Math.min(offset+d.items.length,total)+' из '+total;
 $('more').hidden=offset+d.items.length>=total
}
$('start').onsubmit=async e=>{e.preventDefault();const reg=$('reg').value.trim();if(!/^\d{10,20}$/.test(reg))return;$('notice').textContent='Проверка запускается…';try{await api('/api/v301/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'reg',value:reg})});$('notice').innerHTML='Закупка '+esc(reg)+' добавлена. <a href="/check-new/'+reg+'">Открыть проверку</a>';offset=0;await load()}catch(err){$('notice').textContent='Ошибка: '+err.message}};
window.addEventListener('eis223:manual-input',()=>{offset=0;load().catch(e=>$('page').textContent=e.message)});$('more').onclick=()=>{offset+=80;load().catch(e=>$('page').textContent=e.message)};load().catch(e=>$('page').textContent='Не удалось загрузить список: '+e.message);setInterval(()=>{if(!document.hidden){offset=0;load().catch(()=>{})}},30000);
</script></body></html>'''

SCRIPT = r'''<script id="v257-ui">(()=>{'use strict';
const id=x=>document.getElementById(x);const regFrom=()=>{const m=(id('detailTitle')?.textContent||'').match(/\d{10,20}/);return m?m[0]:''};
async function json(url,opt={}){const r=await fetch(url,{cache:'no-store',...opt});const d=await r.json();if(!r.ok)throw Error(d.detail||r.status);return d}
function nav(){if(location.pathname!=='/')return;const main=document.querySelector('main')||document.body;if(id('v257-tabs'))return;const block=document.createElement('nav');block.id='v257-tabs';block.setAttribute('aria-label','Вкладки закупок');block.style.cssText='display:flex;gap:9px;flex-wrap:wrap;margin:12px 0 18px';block.innerHTML='<a href="/" aria-current="page">Автоматические закупки</a><a href="/account/home">Самостоятельные проверки</a><a href="/thematic" id="thematic-nav-link">Тематические проверки</a><a href="/manual-documents">Самостоятельная проверка документов</a>';block.querySelectorAll('a').forEach(a=>a.style.cssText='padding:10px 14px;border:1px solid #c9d5cb;background:#fff;border-radius:8px;color:#175840;text-decoration:none;font-weight:600');main.insertBefore(block,main.firstChild)}
function setupFilters(){const f=id('filters'),inn=id('inn');if(!f||!inn)return;
if(!id('v257-inn-search')){const b=document.createElement('button');b.type='button';b.id='v257-inn-search';b.textContent='Искать закупки по ИНН';inn.insertAdjacentElement('afterend',b);b.onclick=async()=>{const v=inn.value.trim();const notice=id('customer-progress-v233')||id('runStatus');if(!/^(\d{10}|\d{12})$/.test(v)){if(notice)notice.textContent='Введите ИНН из 10 или 12 цифр';inn.focus();return}b.disabled=true;try{const r=await json('/api/v233/customer/'+v,{method:'POST'});if(notice)notice.textContent='Поиск всех закупок заказчика запущен: найдено '+(r.found||0)+'. Новые данные будут появляться по мере проверки.';f.requestSubmit()}catch(e){if(notice)notice.textContent='Ошибка поиска по ИНН: '+e.message}finally{b.disabled=false}}}
if(!id('v257-violation-type')){const s=document.createElement('select');s.id='v257-violation-type';s.setAttribute('aria-label','Вид нарушения');s.innerHTML='<option value="">Все виды нарушений</option>';const kind=id('kind');if(kind)kind.insertAdjacentElement('afterend',s);else f.appendChild(s);s.onchange=()=>f.requestSubmit();json('/api/v257/violation-types').then(d=>{for(const x of d.items){const o=document.createElement('option');o.value=x.code;o.textContent=x.title+' ('+x.purchases+')';s.appendChild(o)}}).catch(()=>{});id('reset')?.addEventListener('click',()=>{s.value=''},true)}}
function removeRedundant(){const nav=id('v255-document-nav');if(nav){const home=nav.querySelector('a[href="/"]');if(home)home.remove()}}
let last='';async function card(){if(location.pathname!=='/')return;const detail=id('detail'),reg=regFrom();if(!detail||!reg||!detail.querySelector('.detailmeta')||detail.querySelector('#v257-extra')||last===reg)return;last=reg;try{const x=await json('/api/v257/purchase-extra/'+reg);if(regFrom()!==reg)return;const box=document.createElement('div');box.id='v257-extra';box.style.cssText='margin:10px 0;padding:14px;border:1px solid #d3ded4;border-radius:8px;background:#f8faf7;line-height:1.8';const fields=[['Способ закупки',x.method],['Стоимость закупки',window.eis223PriceText?window.eis223PriceText(x):(x.price||x.price_num||'Не указана')],['Дата размещения',x.published_at],['Заказчик: ИНН',x.customer_inn],['ОГРН',x.customer_ogrn],['Статус карточки ЕИС',x.card_source_status],['Источник данных',x.source],['Последнее обновление',x.last_seen],['Срок подачи заявок',x.submission_start&&x.submission_end?x.submission_start+' — '+x.submission_end:x.submission_end],['Документы',x.documents_checked+' проверено из '+x.documents_found],['Извещения / протоколы / разъяснения',x.protocols+' протоколов · '+x.clarifications+' разъяснений · '+x.cancellations+' отмен']];const h=document.createElement('strong');h.textContent='Подробные сведения о закупке';box.appendChild(h);for(const [k,v]of fields){if(v===null||v===undefined||v==='')continue;const p=document.createElement('div'),b=document.createElement('b');b.textContent=k+': ';p.append(b,document.createTextNode(String(v)));box.appendChild(p)}detail.querySelector('.detailmeta')?.insertAdjacentElement('afterend',box)}catch(e){console.warn('Дополнительные сведения',e)}finally{last=''}}
function run(){nav();setupFilters();removeRedundant();card()}
let queued=false;new MutationObserver(()=>{if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;run()})}}).observe(document.documentElement,{childList:true,subtree:true});if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();})();</script>'''

OLD_FILTER = "$('forms').querySelectorAll('input:checked').forEach(x=>p.append('org_form',x.value));return p}"
NEW_FILTER = "$('forms').querySelectorAll('input:checked').forEach(x=>p.append('org_form',x.value));if($('v257-violation-type')?.value)p.set('violation_type',$('v257-violation-type').value);return p}"
OLD_DASHBOARD = "api('/api/v204/dashboard?'+filters())"
NEW_DASHBOARD = "api('/api/v257/dashboard?'+filters())"

def _html_patch(body: str) -> str:
    body=dashboard_load_state(body)
    # This is the existing procurement total across all pages, not a new metric.
    body=body.replace('<small>Закупок по фильтру</small>','<small>Закупки</small>')
    if OLD_DASHBOARD in body: body=body.replace(OLD_DASHBOARD,NEW_DASHBOARD,1)
    if OLD_FILTER in body: body=body.replace(OLD_FILTER,NEW_FILTER,1)
    if 'id="v257-ui"' not in body:body=body.replace('</body>',SCRIPT+'</body>',1) if '</body>' in body else body+SCRIPT
    return body

def manual_progress(reg,checked,search_status):
    """Check real saved originals and the legal audit, not search-job state."""
    review={}
    try:
        p=root()/'eis223_official_review_jobs'/(reg+'.json')
        loaded=json.loads(p.read_text(encoding='utf-8'))
        if isinstance(loaded,dict) and loaded.get('reg_number')==reg:
            review=loaded
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    status=str(review.get('status') or 'not_queued')
    expected=received=0
    try:
        files=allowed_documents(reg)
        expected=len(files)
        received=sum(saved_document(reg,uid) is not None for uid in files)
        manifest=root()/'eis223_public_browser_intake'/reg/'manifest.json'
        try:
            meta=json.loads(manifest.read_text(encoding='utf-8'))
            if (isinstance(meta,dict) and meta.get('reg_number')==reg and
                    meta.get('source')=='public_eis_223_browser'):
                declared=int(meta.get('declared_attachment_count') or 0)
                if 0<declared<=100:expected=max(expected,declared)
        except (OSError,ValueError,TypeError,AttributeError):
            pass
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    def integer(value):
        try:return max(0,int(value or 0))
        except (ValueError,TypeError,OverflowError):return 0
    complete=(status=='completed' and expected>0 and received==expected
              and integer(review.get('originals_received'))>=expected
              and integer(review.get('checked_documents'))>=expected
              and integer(checked)>0)
    if complete:label='completed'
    elif status in ('queued','running'):label='auditing'
    elif status=='failed':label='failed'
    elif expected>0 and received<expected:label='waiting'
    elif search_status in ('queued','leased','deferred','partial'):label=search_status
    else:label='search_only'
    return {'audit_status':label,'audit_complete':complete,
            'review_state':status,'originals_expected':expected,
            'originals_received':received}


def install(ns:dict)->None:
    app,connection=ns['app'],ns['conn']
    membership=MembershipCache(lambda: ns['_verified_set235']())
    @contextmanager
    def db():
        c=connection();c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def setup():
        with db() as c:
            migrate_legacy_inputs(c)
            c.execute('CREATE TABLE IF NOT EXISTS v257_manual_registry(reg TEXT PRIMARY KEY,requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            c.execute('CREATE INDEX IF NOT EXISTS v257_manual_order ON v257_manual_registry(requested_at DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS v257_purchase_inn ON purchases(customer_inn)')
            c.execute('CREATE INDEX IF NOT EXISTS v257_findings_code ON findings(kind,code,reg_number)')
    # EIS-only mode disables the legacy startup hook. The display table must
    # nevertheless exist before any dashboard or manual-list request.
    with db() as c:
        ensure_schema(c)
    @app.on_event('startup')
    async def on_start():
        await asyncio.to_thread(setup)
        print('V257 INN TYPES MANUAL SEPARATE READY',flush=True)
    @app.on_event('startup')
    async def warm_dashboard_reads():
        # Retained explicitly by EIS-only startup. This starts one read-only
        # scan, not a collector or an audit. Only bounded index creation
        # (up to five seconds) is awaited before HTTP readiness.
        membership.refresh()
        await asyncio.to_thread(ensure_read_indexes,connection)
    @app.get('/api/v257/violation-types',include_in_schema=False)
    def types():
        with db() as c:
            # One legal violation type may have hundreds of instance-specific
            # finding codes. Group by its actual description, not by code.
            rows=c.execute("""SELECT TRIM(title) title,
                   COUNT(DISTINCT reg_number) purchases
              FROM findings
              WHERE kind='violation' AND TRIM(COALESCE(title,''))<>''
              GROUP BY TRIM(title)
              ORDER BY purchases DESC,title LIMIT 150""").fetchall()
        return {'items':[{'code':'title:'+r['title'], 'title':r['title'],
                          'purchases':int(r['purchases'])} for r in rows]}
    dashboard_cache = DashboardReadCoordinator(version_getter=SQLiteDataVersion(connection))
    @app.get('/api/v257/dashboard',include_in_schema=False)
    def dashboard(request:Request):
        params=request.query_params
        inn=(params.get('inn') or '').strip()
        if inn and not re.fullmatch(r'\d{10}|\d{12}',inn):
            raise HTTPException(400,'Введите ИНН из 10 или 12 цифр')
        try:
            return dashboard_cache.get(str(params),lambda: _read_dashboard(params))
        except ValueError:
            raise HTTPException(400,'Некорректные параметры фильтра')
        except DashboardWarming:
            raise HTTPException(503,'Список закупок готовится; сохранённые результаты не удалены.',
                                headers={'Retry-After':'5','Cache-Control':'no-store',
                                         'X-EIS-Dashboard-State':'warming'})
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc).lower() or 'interrupted' in str(exc).lower():
                raise HTTPException(503,'База данных занята, выполняется обновление списка.',
                                    headers={'Retry-After':'5','Cache-Control':'no-store',
                                             'X-EIS-Dashboard-State':'busy'})
            raise

    def _read_dashboard(params):
        inn=(params.get('inn') or '').strip()
        if inn and not re.fullmatch(r'\d{10}|\d{12}',inn):
            raise HTTPException(400,'Введите ИНН из 10 или 12 цифр')
        started=time.monotonic()
        try:
            snapshot,membership_info=membership.snapshot()
        except MembershipUnavailable:
            raise HTTPException(503,'Список закупок обновляется. Повторите загрузку через 10 секунд.',
                                headers={'Retry-After':'10','Cache-Control':'no-store',
                                         'X-EIS-Dashboard-State':'warming'})
        verified=set(snapshot)
        # The current intake snapshot is volatile (it can shrink to a few
        # hundred after the collector rotates pages). Preserve ALL purchases
        # that already entered the verified automatic audit queue.
        with db() as c:
            has_history=c.execute("""SELECT 1 FROM sqlite_master
                                     WHERE type='table' AND name='v332_audit_queue'""").fetchone()
            if has_history:
                from v332_all_procurements_audit import CAMPAIGN
                verified.update(str(r[0]) for r in c.execute(
                    "SELECT q.reg FROM v332_audit_queue q JOIN purchases p "
                    "ON p.reg_number=q.reg WHERE q.campaign=?",(CAMPAIGN,)))
        if inn and re.fullmatch(r'\d{10}|\d{12}',inn):
            # Explicit customer search includes independently checked purchases;
            # do not imply they passed the ordinary automated audit queue.
            with db() as c:
                extra=c.execute(
                    "SELECT DISTINCT p.reg_number FROM purchases p "
                    "LEFT JOIN v301_inn_purchases bp ON bp.reg=p.reg_number "
                    "WHERE p.documents_checked>0 AND (p.customer_inn=? OR bp.inn=?)",
                    (inn,inn)).fetchall()
                verified.update(str(row[0]) for row in extra)
        verified=sorted(verified)
        if not verified:return {'stats':{'purchases':0,'violations':0,'risks':0,'documents_found':0,'documents_checked':0},'purchases':[],'offset':0,'limit':100,**membership_info}
        # Use one reconciler for dashboard counts AND general card findings:
        # raw COUNT(findings)+COUNT(checks) counts the same violation twice.
        from v352_dashboard_truth import dashboard_counts
        conditions=['p.reg_number IN ('+','.join('?' for _ in verified)+')']
        if not inn:
            conditions.append('p.reg_number NOT IN (SELECT reg FROM ('+MANUAL_SOURCE_SQL+'))')
        args=list(verified)
        forms=params.getlist('org_form')
        if forms:
            forms=[v[:80] for v in forms if v and len(v)<81]
            if forms:conditions.append('p.org_form IN ('+','.join('?' for _ in forms)+')');args.extend(forms)
        kind=(params.get('kind') or params.get('result_kind') or '').strip()
        if kind in ('violations','violation'):
            conditions.append("""(EXISTS (SELECT 1 FROM findings f WHERE
                f.reg_number=p.reg_number AND f.kind='violation')
                OR EXISTS (SELECT 1 FROM checks ck WHERE
                ck.reg_number=p.reg_number AND ck.status='violation'))""")
        if kind in ('risks','risk'):conditions.append('p.risks>0')
        code=(params.get('violation_type') or '').strip()
        if code:
            if len(code)>160:raise HTTPException(400,'Недопустимый вид нарушения')
            if code.startswith('title:'):
                # UI groups equal descriptions, retaining ALL original codes.
                conditions.append(
                    "EXISTS (SELECT 1 FROM findings f WHERE f.reg_number=p.reg_number "
                    "AND f.kind='violation' AND TRIM(f.title)=?)")
                args.append(code[6:])
            else:
                conditions.append(
                    "EXISTS (SELECT 1 FROM findings f WHERE f.reg_number=p.reg_number "
                    "AND f.kind='violation' AND f.code=?)")
                args.append(code)
        inn=(params.get('inn') or '').strip()
        if inn:
            if not re.fullmatch(r'\d{10}|\d{12}',inn):raise HTTPException(400,'Введите ИНН из 10 или 12 цифр')
            conditions.append('''(p.customer_inn=? OR EXISTS(SELECT 1 FROM customer_purchases_v233 cp WHERE cp.reg=p.reg_number AND cp.inn=?) OR EXISTS(SELECT 1 FROM v301_inn_purchases bp WHERE bp.reg=p.reg_number AND bp.inn=?))''')
            args.extend((inn,inn,inn))
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
            # One consistent read snapshot; no writer reservation or changes.
            c.execute('PRAGMA query_only=ON')
            c.execute('BEGIN')
            stats,items=dashboard_counts(c,sql,args,offset,limit)
        print('DASHBOARD_READ_READY elapsed_ms='+str(round((time.monotonic()-started)*1000,1))+
              ' page_rows='+str(len(items))+' membership_age_seconds='+
              str(membership_info['membership_age_seconds']),flush=True)
        return {'stats':stats,'purchases':items,'offset':offset,'limit':limit,**membership_info,
                'count_basis':'unique_saved_legal_episodes',
                'list_basis':'verified_intake_and_persistent_automatic_queue',
                'filter_scoped':True}
    @app.on_event('startup')
    async def prewarm_default_dashboard():
        # Shared, read-only, exact saved count; never a completion shortcut.
        dashboard_cache.prime('', lambda: _read_dashboard(QueryParams('')))

    @app.get('/api/v257/manual',include_in_schema=False)
    def manual_list(offset:int=0,limit:int=80):
        offset=min(max(offset,0),1_000_000);limit=min(max(1,limit),200)
        with db() as c:
            total=c.execute('SELECT COUNT(*) FROM ('+MANUAL_SOURCE_SQL+')').fetchone()[0]
            has_jobs=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v301_search_jobs'").fetchone()
            job_join=' LEFT JOIN v301_search_jobs sj ON sj.id=r.job_id ' if has_jobs else ''
            status="COALESCE(sj.status,j.status,'В очереди')" if has_jobs else "COALESCE(j.status,'В очереди')"
            records=c.execute("""SELECT r.reg AS reg_number,r.requested_at,p.customer,p.title,p.price,p.price_num,p.documents_found,
             p.documents_checked,p.violations,p.risks,"""+status+""" manual_status
             FROM ("""+MANUAL_SOURCE_SQL+""") r LEFT JOIN purchases p ON p.reg_number=r.reg
             LEFT JOIN requests_v233 j ON j.reg=r.reg AND j.lane='manual'
             """+job_join+" ORDER BY r.requested_at DESC,r.reg DESC LIMIT ? OFFSET ?",(limit,offset)).fetchall()
        return {'total':total,'items':[{**dict(r),'customer':organization_display_name(r['customer']),**manual_progress(r['reg_number'],r['documents_checked'],r['manual_status'])} for r in records]}
    @app.post('/api/v257/manual/{reg}',include_in_schema=False)
    def register_manual(reg:str):
        if not re.fullmatch(r'3[0-9]{10}',reg):raise HTTPException(400,'Некорректный номер закупки')
        with db() as c:
            row=c.execute("SELECT reg FROM requests_v233 WHERE reg=? AND lane='manual'",(reg,)).fetchone()
            if not row:raise HTTPException(409,'Сначала запустите самостоятельную проверку')
            record_input(c,reg,provenance='legacy_number_field')
        return {'reg':reg,'saved':True}
    @app.get('/api/v257/purchase-extra/{reg}',include_in_schema=False)
    def extra(reg:str):
        if not re.fullmatch(r'\d{10,20}',reg):raise HTTPException(400,'Некорректный номер закупки')
        with db() as c:
            row=c.execute("""SELECT reg_number,method,price,price_num,published_at,customer_inn,card_source_status,source,
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
            if request.method!='GET' or request.url.path not in ('/','/documents','/manual-documents') or 'text/html' not in response.headers.get('content-type',''):
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
