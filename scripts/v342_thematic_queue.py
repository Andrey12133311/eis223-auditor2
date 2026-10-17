"""Isolated, evidence-only thematic queue over EIS procurements.
No fabricated findings; reuses verified-original legal engine and filters results.
"""
import asyncio,json,re,secrets,sqlite3,time
from datetime import datetime,timezone
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from scripts import v360_thematic_ownership as ownership

WORKER_TASK=None
WORKER_STATE={"last_cycle_at":None,"last_cycle_finished_at":None,"last_error":None}

def storage_health():
 """Read-only storage pressure check. Never count an unavailable path as free."""
 try:
  import shutil
  from v295_eis_originals import root
  free=shutil.disk_usage(root()).free
  return {"blocked":free<64*1024*1024,"low":free<256*1024*1024,
          "free_mb":round(free/1048576,1),"reserve_mb":64}
 except (OSError,ValueError):
  return {"blocked":False,"low":False,"free_mb":None,
          "reserve_mb":64,"unknown":True}

CATEGORIES={
"dates":"Сроки подачи заявок, изменений и разъяснений",
"docs":"Извещение и документация",
"national":"Национальный режим",
"security":"Обеспечение заявки и договора",
"sme":"Закупки среди МСП",
"access":"Требования к участникам",
"protocols":"Протоколы и отмена закупки",
"contract":"Заключение и исполнение договора",
"notices":"Сведения извещения",
"evaluation":"Оценка заявок"}
LEGAL_FORMS={"ООО":"Общество с ограниченной ответственностью","АО":"Акционерное общество","ПАО":"Публичное акционерное общество","ОАО":"Открытое акционерное общество","ЗАО":"Закрытое акционерное общество"}
def customer_form(name):
 value=str(name or "").strip().strip('"«»').upper()
 for code,pattern in (("ПАО",r"^(?:ПАО\b|ПУБЛИЧНОЕ\s+АКЦИОНЕРНОЕ\s+ОБЩЕСТВО\b)"),("ОАО",r"^(?:ОАО\b|ОТКРЫТОЕ\s+АКЦИОНЕРНОЕ\s+ОБЩЕСТВО\b)"),("ЗАО",r"^(?:ЗАО\b|ЗАКРЫТОЕ\s+АКЦИОНЕРНОЕ\s+ОБЩЕСТВО\b)"),("ООО",r"^(?:ООО\b|ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ\b)"),("АО",r"^(?:АО\b|АКЦИОНЕРНОЕ\s+ОБЩЕСТВО\b)")):
  if re.search(pattern,value):return code
 return None
COMPANY_FORM=re.compile(r"^\s*(?:ООО|АО|ПАО|ОАО|ЗАО|ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ|(?:ПУБЛИЧНОЕ\s+)?АКЦИОНЕРНОЕ\s+ОБЩЕСТВО|ЗАКРЫТОЕ\s+АКЦИОНЕРНОЕ\s+ОБЩЕСТВО)\b",re.I)
def business_company(name):
 return customer_form(name) is not None
COMPETITIVE=re.compile(r"конкурс|аукцион|запрос\\s+(?:котировок|предложений)|торги",re.I)
NONCOMPETITIVE=re.compile(r"единственн(?:ого|ый|ому)\\s+(?:поставщик|подрядчик|исполнитель)|прямая\\s+закупка|неконкурентн",re.I)
def competitive_kind(method):
 value=str(method or "").strip()
 if not value:return {"classification":"unknown","reason":"Способ закупки не указан в подтверждённых данных"}
 if NONCOMPETITIVE.search(value):return {"classification":"noncompetitive","reason":"В способе закупки явно указана неконкурентная процедура"}
 if COMPETITIVE.search(value):return {"classification":"competitive","reason":"Указан конкурентный способ закупки; правовую применимость следует сверить с положением о закупке"}
 return {"classification":"unknown","reason":"Недостаточно сведений для достоверной квалификации"}
def db(ns):
 c=ns["conn"]();c.row_factory=sqlite3.Row;return c
def init(ns):
 c=db(ns)
 try:
  c.executescript("""CREATE TABLE IF NOT EXISTS thematic_batches(
  id TEXT PRIMARY KEY,categories TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active');
  CREATE TABLE IF NOT EXISTS thematic_items(
  batch_id TEXT NOT NULL,reg TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',
  detail TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
  PRIMARY KEY(batch_id,reg));
  CREATE TABLE IF NOT EXISTS thematic_findings(batch_id TEXT,reg TEXT,category TEXT,title TEXT,law TEXT,evidence TEXT, UNIQUE(batch_id,reg,category,title,evidence));""")
  if "legal_forms" not in {x[1] for x in c.execute("PRAGMA table_info(thematic_batches)")}:
   c.execute("ALTER TABLE thematic_batches ADD COLUMN legal_forms TEXT NOT NULL DEFAULT '[\"ООО\",\"АО\",\"ПАО\",\"ОАО\",\"ЗАО\"]'")
  evidence_cols={x[1] for x in c.execute("PRAGMA table_info(thematic_findings)")}
  for col in ("code","document","source_url","law_url"):
   if col not in evidence_cols:
    c.execute("ALTER TABLE thematic_findings ADD COLUMN "+col+" TEXT NOT NULL DEFAULT ''")
  c.execute("CREATE INDEX IF NOT EXISTS idx_thematic_evidence_by_purchase ON thematic_findings(batch_id,reg)")
  c.commit()
 finally:c.close()
def now():return datetime.now(timezone.utc).isoformat()
async def tick(ns):
 c=db(ns)
 try:
  batches=c.execute("SELECT * FROM thematic_batches WHERE status='active'").fetchall()
  for b in batches:
   chosen=set(json.loads(b["legal_forms"]))
   # Existing and newly ingested EIS purchases are automatically added.
   rows=c.execute("SELECT reg_number,customer FROM purchases WHERE reg_number GLOB '3??????????' LIMIT 50000").fetchall()
   for r in rows:
    if customer_form(r["customer"]) in chosen:
     c.execute("INSERT OR IGNORE INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?)",(b["id"],r["reg_number"],now()))
   # Remove previously queued non-company purchases from active thematic campaigns.
   c.execute("DELETE FROM thematic_items WHERE batch_id=? AND reg IN (SELECT reg_number FROM purchases WHERE COALESCE(customer,'')='')",(b["id"],))
   for row in c.execute("SELECT t.reg,p.customer FROM thematic_items t JOIN purchases p ON p.reg_number=t.reg WHERE t.batch_id=?",(b["id"],)).fetchall():
    if customer_form(row["customer"]) not in chosen:
     c.execute("DELETE FROM thematic_items WHERE batch_id=? AND reg=?",(b["id"],row["reg"]))
   pending=c.execute("SELECT reg FROM thematic_items WHERE batch_id=? AND status='queued' LIMIT 20",(b["id"],)).fetchall()
   for p in pending:
    reg=p["reg"]
    try:
     # The server reviews verified original documents even when browser EIS intake is offline.
     state,findings=await independent_review(ns,reg,json.loads(b["categories"]))
     detail={"completed":"Проверено по сохранённым оригиналам ЕИС",
             "waiting_originals":"Ожидаются доступные оригиналы ЕИС",
             "excluded_company":"Форма заказчика не соответствует выборке"}.get(state,"Тематическая проверка")
     if state=="completed":
      for f in findings:
       c.execute("INSERT OR IGNORE INTO thematic_findings(batch_id,reg,category,title,law,evidence) VALUES(?,?,?,?,?,?)",(b["id"],reg,f["category"],f["title"],f.get("law"),f.get("evidence")))
    except Exception as e:
     state="retry";detail=type(e).__name__
    c.execute("UPDATE thematic_items SET status=?,detail=?,updated_at=? WHERE batch_id=? AND reg=?",(state,detail,now(),b["id"],reg))
   # Revisit items after EIS originals arrive, without counting them as reviewed.
   c.execute("UPDATE thematic_items SET status='queued' WHERE batch_id=? AND status IN ('waiting_originals','waiting_engine','retry') AND updated_at<?",(b["id"],datetime.fromtimestamp(time.time()-600,timezone.utc).isoformat()))
  c.commit()
 finally:c.close()
def match_legal_category(f,categories):
 """Classify a documented offence, not an unrelated word in the source."""
 title=str(f.get("title") or "").casefold().replace("ё","е")
 code=str(f.get("code") or "").casefold()
 law=str(f.get("law") or "").casefold()
 subject=title+" "+code+" "+law
 patterns={
   "sme":r"мсп|малого и среднего|1352|sme",
   "national":r"национальн|режим|запрет|1875|импортозамещ",
   "security":r"обеспечени|гаранти|security",
   "protocols":r"протокол|отмен|cancellation|protocol",
   "dates":r"разъяснен|срок подач|срок размещени|продлен|срок публикац|clarification|amendment",
   "docs":r"извещени|документац|техническ|предмет закупки|notice",
   "access":r"участник|допуск|квалификац|дискриминац|access",
   "evaluation":r"оценк|критери|балл|evaluation",
   "contract":r"оплат|договор|контракт|payment|contract",
   "notices":r"публикац|размещени|извещени|notice"
 }
 # SME obligations (including the SME payment deadline) belong to the SME
 # review if requested; a general payment term belongs to contract review.
 for cat in ("sme","national","security","protocols","dates",
             "access","evaluation","contract","docs","notices"):
    if cat in categories and re.search(patterns[cat],subject,re.I):
       return cat
 return None

async def independent_review(ns,reg,categories):
 from v332_all_procurements_audit import existing,as_procurement,stored_docs,is_official
 from v333_dedup_ui import collapse
 purchase,rows=await asyncio.to_thread(existing,ns,reg)
 p=as_procurement(purchase)
 if not business_company(p.get("customer")):return "excluded_company",[]
 docs,unreadable=await stored_docs(ns,reg,rows)
 verified=[d for d in docs if is_official(d)]
 if not verified:return "waiting_originals",[]
 # Run the same evidence-based analyzer as the general queue, including
 # cross-document clarifications, amendments and the existing legal matrix.
 analyzer=ns.get("analyze")
 if not callable(analyzer):raise RuntimeError("Main legal analyzer is unavailable")
 findings=await asyncio.to_thread(analyzer,p,verified)
 # Match document provenance robustly: verified originals can be stored
 # under a local filename while the analyzer cites the EIS display name.
 import os
 def norm_name(value):
    return os.path.basename(str(value or "").replace("\\","/")).strip().casefold()
 names={norm_name(d.get("name")) for d in verified}
 names.update(norm_name(d.get("local_path")) for d in verified)
 names.update(norm_name((d.get("meta") or {}).get("filename")) for d in verified)
 names.discard("")
 urls={str((d.get("meta") or {}).get("source_url") or "").strip()
       for d in verified}
 urls.discard("")
 selected=[]
 for f in findings if isinstance(findings,list) else []:
    if not isinstance(f,dict) or f.get("kind")!="violation":continue
    if not str(f.get("law") or "").strip():continue
    # Provenance requirements are exactly as strict as the main engine:
    # neither a plausible title nor a local unverified file is enough.
    if not ((f.get("document") and norm_name(f["document"]) in names) or
            (f.get("source_url") and str(f["source_url"]).strip() in urls)):
       continue
    category=match_legal_category(f,categories)
    if category is None:continue
    selected.append({
      "title":str(f.get("title") or "")[:500],
      "law":str(f.get("law") or "")[:500],
      "evidence":str(f.get("evidence") or "")[:3000],
      "category":category,
      "code":str(f.get("code") or "")[:150],
      "document":str(f.get("document") or "")[:255],
      "source_url":str(f.get("source_url") or "")[:2000],
      "law_url":str(f.get("law_url") or "")[:1000]
    })
 # This only changes new presentation/persistence, never deletes historical
 # thematic results. Different dated acts and documentary sources stay distinct.
 return "completed",collapse(selected)

def overview(ns,batch):
 c=db(ns)
 try:
  b=c.execute("SELECT * FROM thematic_batches WHERE id=?",(batch,)).fetchone()
  if not b:raise HTTPException(404,"Очередь не найдена")
  counts={r[0]:r[1] for r in c.execute("SELECT status,COUNT(*) FROM thematic_items WHERE batch_id=? GROUP BY status",(batch,))}
  cats=json.loads(b["categories"])
  patterns={
   "dates":r"срок|дат|разъяснен|продлен|изменен",
   "docs":r"документац|извещени|техническ|предмет",
   "national":r"национальн|режим|запрет|1875",
   "security":r"обеспечени|гаранти",
   "sme":r"мсп|малого и среднего|1352",
   "access":r"участник|допуск|квалификац|дискриминац",
   "protocols":r"протокол|отмен",
   "contract":r"договор|контракт|оплат",
   "notices":r"извещени|публикац",
   "evaluation":r"оценк|критери|балл"}
  from v333_dedup_ui import collapse
  raw=[dict(r) for r in c.execute("SELECT reg,title,category,law,evidence,code,document,source_url,law_url FROM thematic_findings WHERE batch_id=? ORDER BY rowid DESC LIMIT 750",(batch,)).fetchall()]
  unique=[]
  for reg in dict.fromkeys(f["reg"] for f in raw):
   unique.extend(collapse([f for f in raw if f["reg"]==reg]))
  matches=[{"reg":r["reg"],"title":r["title"],"categories":[r["category"]],
            "law":r["law"],"evidence":r["evidence"] or ""} for r in unique[:500]]
  items=[{"reg":r["reg"],"status":r["status"],"detail":r["detail"],"updated_at":r["updated_at"]} for r in c.execute("SELECT reg,status,detail,updated_at FROM thematic_items WHERE batch_id=? ORDER BY CASE status WHEN 'queued' THEN 0 WHEN 'retry' THEN 1 WHEN 'waiting_originals' THEN 2 ELSE 3 END,updated_at DESC LIMIT 200",(batch,)).fetchall()]
  try:
   from v301_search_jobs import _bridge
   bridge=_bridge(ns)
  except Exception:
   bridge={"active":False,"queued":None}
  # Browser discovery sends its heartbeat to v299; the v301 heartbeat only tracks manual jobs.
  # Check both instead of reporting an offline EIS collector when no manual job was claimed.
  try:
   from v299_browser_intake import _scan_status
   collector=_scan_status()
   bridge["collector_active"]=bool(collector.get("browser_collector_active"))
   bridge["collector_version"]=collector.get("collector_version")
   bridge["active"]=bool(bridge.get("active") or bridge["collector_active"])
  except Exception:
   bridge["collector_active"]=False
  # Surface real EIS transfer progress. Saved documents alone can be
  # audited offline; missing originals require an active official browser.
  has_jobs=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v301_search_jobs'").fetchone()
  storage=storage_health()
  bridge["storage_blocked"]=storage["blocked"]
  bridge["storage_free_mb"]=storage["free_mb"]
  for itm in items:
   if itm["status"]!="waiting_originals":continue
   job=c.execute("""SELECT status FROM v301_search_jobs WHERE kind='reg'
      AND target=? ORDER BY created_at DESC LIMIT 1""",
      (itm["reg"],)).fetchone() if has_jobs else None
   if storage["blocked"]:
    itm["detail"]="Хранилище Railway заполнено: запрос ЕИС не может быть сохранён. Требуется освободить место или увеличить том без удаления оригиналов."
   elif job and job["status"]=="leased":
    itm["detail"]="Мост ЕИС получает оригиналы закупки"
   elif job and job["status"] in ("queued","deferred"):
    itm["detail"]="Задание на оригиналы ЕИС уже находится в очереди загрузчика"
   elif job and job["status"] in ("done","partial"):
    itm["detail"]="Предыдущая загрузка закончилась без подтверждённых оригиналов; повторный запрос запланирован"
   elif job and job["status"]=="error":
    itm["detail"]="Ошибка предыдущей загрузки ЕИС; повторная попытка будет назначена"
   elif bridge.get("active"):
    itm["detail"]="Автоматическая постановка оригиналов в очередь ЕИС выполняется"
   else:
    itm["detail"]="Запрос оригиналов ожидает подключения загрузчика ЕИС"
  worker={"active":bool(WORKER_TASK is not None and not WORKER_TASK.done()),"last_cycle_at":WORKER_STATE["last_cycle_at"],"last_cycle_finished_at":WORKER_STATE["last_cycle_finished_at"],"last_error":WORKER_STATE["last_error"],"mode":"saved_eis_originals"}
  return {"worker":worker,"bridge":bridge,"storage":storage,"id":batch,"categories":cats,"legal_forms":json.loads(b["legal_forms"]),"status":b["status"],"counts":counts,"confirmed_matching_findings":matches,"items":items,"note":"Тематические результаты сохраняются отдельно. Проверяются подтверждённые оригиналы ЕИС; доступные правила не охватывают все категории."}
 finally:c.close()
_HISTORY_COUNT_CACHE={}

def thematic_history_records(ns, permitted_ids=None):
 """Show unique legal incidents, not repeated saved excerpts, in history."""
 from v333_dedup_ui import collapse
 if permitted_ids is not None and not permitted_ids:
  return {"batches":[]}
 c=db(ns)
 try:
  c.execute("PRAGMA busy_timeout=1500")
  placeholders=",".join("?" for _ in permitted_ids) if permitted_ids is not None else ""
  owner_filter=(" WHERE b.id IN ("+placeholders+")") if permitted_ids is not None else ""
  rows=c.execute("""SELECT b.id,b.categories,b.created_at,b.status,
         COUNT(DISTINCT i.reg) AS purchases,
         (SELECT COUNT(*) FROM thematic_findings f WHERE f.batch_id=b.id) AS stored_rows
         FROM thematic_batches b
         LEFT JOIN thematic_items i ON i.batch_id=b.id
         """+owner_filter+""" GROUP BY b.id ORDER BY b.created_at DESC LIMIT 100""",
         permitted_ids or ()).fetchall()
  out=[]
  for row in rows:
   item=dict(row)
   batch=item["id"]
   rev=c.execute("SELECT COALESCE(MAX(rowid),0) FROM thematic_findings WHERE batch_id=?",(batch,)).fetchone()[0]
   stamp=(int(item["stored_rows"]),int(rev))
   cached=_HISTORY_COUNT_CACHE.get(batch)
   if cached and cached[0]==stamp:
    unique=cached[1]
   else:
    grouped={}
    for f in c.execute("""SELECT reg,category,title,law,evidence,
                           document,source_url,code,law_url
                           FROM thematic_findings WHERE batch_id=?""",(batch,)):
     grouped.setdefault(f["reg"],[]).append(dict(f))
    unique=sum(len(collapse(group)) for group in grouped.values())
    _HISTORY_COUNT_CACHE[batch]=(stamp,unique)
   item["violations"]=unique
   out.append(item)
  # Discard counts for deleted campaigns; never change their stored evidence.
  active={x["id"] for x in out}
  for key in list(_HISTORY_COUNT_CACHE):
   if key not in active:_HISTORY_COUNT_CACHE.pop(key,None)
  return {"batches":out}
 finally:c.close()

PAGE="""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Тематическая проверка ЕИС</title><style>body{font:16px system-ui;background:#f2f6fa;color:#193d5c;margin:0}main{max-width:850px;margin:30px auto;background:white;padding:25px;border-radius:16px}label{display:block;padding:10px;border-bottom:1px solid #eee}button{background:#2466a3;color:white;padding:13px;border:0;border-radius:8px;cursor:pointer}a{color:#2466a3}#out{white-space:pre-wrap}</style><main><a href="/">← Главная</a><h1>Тематическая проверка</h1><p>Выберите категории. Закупки из ЕИС поступают в отдельную очередь. Нарушения подтверждаются только оригиналами и юридическими правилами.</p><div id="cats"></div><h2>Организационно-правовые формы заказчиков</h2><p>Выберите одну или несколько форм. По умолчанию выбраны все хозяйственные общества.</p><div id="forms"></div><p><button id="start">Начать проверку</button></p><h2>Сохранённые тематические проверки</h2><div id="history"></div><h2>Очередь и нарушения</h2><div id="out">Выберите категории</div></main><script>
const categories=__CATEGORIES__,legalForms=__LEGAL_FORMS__;const formsBox=document.querySelector("#forms");for(const [id,title] of Object.entries(legalForms)){const label=document.createElement("label"),check=document.createElement("input");check.type="checkbox";check.value=id;check.checked=true;label.append(check,document.createTextNode(" "+id+" — "+title));formsBox.append(label)}const box=document.querySelector('#cats');
for(const [id,title] of Object.entries(categories)){let l=document.createElement('label'),i=document.createElement('input');i.type='checkbox';i.value=id;l.append(i,document.createTextNode(' '+title));box.append(l)}
document.querySelector('#start').onclick=async()=>{const cats=[...box.querySelectorAll('input:checked')].map(x=>x.value);if(!cats.length)return alert('Выберите категорию');const forms=[...formsBox.querySelectorAll('input:checked')].map(x=>x.value);if(!forms.length)return alert('Выберите организационно-правовую форму');const r=await fetch('/api/thematic/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({categories:cats,legal_forms:forms})});const j=await r.json();if(!r.ok)return alert(j.detail||r.status);location.hash=j.id;refresh();history()};
async function history(){const r=await fetch('/api/thematic/history');if(!r.ok)return;const j=await r.json(),box=document.querySelector('#history');box.replaceChildren();for(const b of j.batches||[]){const p=document.createElement('p'),a=document.createElement('a');a.href='#'+b.id;a.textContent=new Date(b.created_at).toLocaleString('ru-RU')+' — закупок: '+b.purchases+'; нарушений: '+b.violations+'; '+(b.status==='active'?'в работе':'остановлена');a.onclick=(ev)=>{ev.preventDefault();location.hash=b.id;refresh()};p.append(a);const del=document.createElement('button');del.textContent='Удалить очередь';del.style.cssText='margin-left:12px;background:#a52a2a;padding:7px 10px';del.onclick=async()=>{if(!confirm('Удалить эту тематическую очередь и все её сохранённые результаты? Это действие нельзя отменить.'))return;const resp=await fetch('/api/thematic/'+encodeURIComponent(b.id),{method:'DELETE'});if(!resp.ok){alert('Не удалось удалить очередь');return}if(location.hash==='#'+b.id){window.history.replaceState(null,'',location.pathname);document.querySelector('#out').textContent='Выберите очередь из истории'}await history()};p.append(del);box.append(p)}if(!(j.batches||[]).length)box.textContent='История пока пуста'}async function refresh(){if(!location.hash)return;let r=await fetch('/api/thematic/'+encodeURIComponent(location.hash.slice(1)));let j=await r.json();const out=document.querySelector('#out');out.replaceChildren();if(!r.ok){out.textContent=j.detail||'Ошибка получения очереди';return}const names={queued:'В очереди',submitted:'Передано на проверку',completed:'Проверено',waiting_originals:'Ожидаются оригиналы ЕИС',waiting_engine:'Ожидается обработчик',retry:'Повторная попытка'};const heading=document.createElement('p');heading.textContent='Статус: '+(j.status==='active'?'Работает':'Остановлена');out.append(heading);const bridge=document.createElement('p');bridge.textContent='Проверка сохранённых оригиналов: '+(j.worker?.active?'серверный обработчик работает':'серверный обработчик недоступен')+'. Загрузка новых документов ЕИС: '+(j.bridge?.active?'подключена':'не подключена')+'; ожидают загрузки: '+(j.bridge?.queued??'неизвестно');out.append(bridge);if(j.storage?.blocked){const warning=document.createElement('p');warning.style.cssText='font-weight:600;color:#9a3412';warning.textContent='Недостаточно места в хранилище Railway (свободно '+j.storage.free_mb+' МБ). Новые запросы и результаты не смогут сохраняться, пока место не освободится. Сохранённые оригиналы не удаляются.';out.append(warning)}for(const [key,num] of Object.entries(j.counts||{})){const line=document.createElement('p');line.textContent=(names[key]||key)+': '+num;out.append(line)}const stop=document.createElement('button');stop.textContent='Остановить проверку';stop.disabled=j.status!=='active';stop.onclick=async()=>{if(!confirm('Остановить тематическую проверку?'))return;await fetch('/api/thematic/'+encodeURIComponent(j.id)+'/stop',{method:'POST'});refresh()};out.append(stop);const qh=document.createElement('h3');qh.textContent='Закупки в тематической проверке';out.append(qh);const table=document.createElement('table');table.style.cssText='width:100%;border-collapse:collapse';for(const p of j.items||[]){const tr=document.createElement('tr');for(const value of [p.reg,names[p.status]||p.status,p.detail||'']){const td=document.createElement('td');td.style.cssText='border-bottom:1px solid #ddd;padding:7px';td.textContent=value;tr.append(td)}const a=document.createElement('a');a.href='/thematic/card/'+encodeURIComponent(j.id)+'/'+encodeURIComponent(p.reg);a.textContent='Карточка проверки';const td=document.createElement('td');td.append(a);tr.append(td);table.append(tr)}out.append(table);const h=document.createElement('h3');h.textContent='Подтверждённые нарушения по выбранным категориям';out.append(h);const findings=j.confirmed_matching_findings||[];if(!findings.length){const p=document.createElement('p');p.textContent='Подтверждённых нарушений пока нет';out.append(p)}for(const f of findings){const item=document.createElement('article');item.style.cssText='border-left:4px solid #b91c1c;background:#fff5f5;margin:12px 0;padding:12px';const strong=document.createElement('strong');strong.textContent=f.reg+' — '+f.title;item.append(strong);const detail=document.createElement('p');detail.textContent=f.evidence||'';item.append(detail);if(f.law){const law=document.createElement('p');law.textContent='Правовое основание: '+f.law;item.append(law)}const card=document.createElement('a');card.href='/thematic/card/'+encodeURIComponent(j.id)+'/'+encodeURIComponent(f.reg);card.textContent='Карточка проверки и служебная записка';item.append(card);out.append(item)}}
window.addEventListener('hashchange',refresh);setInterval(()=>{refresh();history()},12000);history();refresh();</script></html>""".replace("__CATEGORIES__",json.dumps(CATEGORIES,ensure_ascii=False)).replace("__LEGAL_FORMS__",json.dumps(LEGAL_FORMS,ensure_ascii=False))
DASHBOARD_CLEANUP = r'''<script id="thematic-dashboard-cleanup">
(function(){
 const t=document.querySelector('table');
 if(t){
   const hs=[...t.querySelectorAll('thead th')];
   const idxs=hs.map((h,i)=>/^(?:Стоимость закупки|НМЦД|Результат)$/i.test(h.textContent.trim())?i:-1).filter(i=>i>=0).reverse();
   for(const idx of idxs){
     hs[idx].remove();
     t.querySelectorAll('tbody tr').forEach(tr=>tr.children[idx]?.remove());
   }
 }
 document.querySelectorAll('#min,#max,#price,[name=price_band]').forEach(e=>{e.style.display='none';e.disabled=true});
})();
</script>'''

def install(ns):
 init(ns);app=ns["app"]
 @app.get("/api/thematic/classify/{reg}")
 async def classify(reg:str):
  if not re.fullmatch(r"3[0-9]{10}",reg):raise HTTPException(422,"Некорректный номер ЕИС")
  c=db(ns)
  try:
   p=c.execute("SELECT method,customer FROM purchases WHERE reg_number=?",(reg,)).fetchone()
   if not p:raise HTTPException(404,"Закупка не найдена")
   result=competitive_kind(p["method"])
   return {"reg_number":reg,"method":p["method"],"business_company":business_company(p["customer"]),**result}
  finally:c.close()
 @app.get("/thematic")
 async def page():return HTMLResponse(PAGE,headers={"Cache-Control":"no-store"})
 @app.post("/api/thematic/start")
 async def start(request:Request):
  ownership.protect_write(request)
  origin=request.headers.get("origin")
  if not ownership.enabled() and origin and not origin.startswith(("https://web-production-1c2ea.up.railway.app","http://localhost:")):raise HTTPException(403,"Неверный источник")
  raw=await request.body()
  if len(raw)>2000:raise HTTPException(413,"Слишком большой запрос")
  try:
   payload=json.loads(raw)
   cats=payload["categories"]
   forms=payload.get("legal_forms",list(LEGAL_FORMS))
  except (ValueError,KeyError,TypeError):raise HTTPException(422,"Некорректный запрос")
  if not isinstance(cats,list) or not cats or len(cats)>len(CATEGORIES) or any(x not in CATEGORIES for x in cats):raise HTTPException(422,"Некорректные категории")
  if not isinstance(forms,list) or not forms or len(forms)>len(LEGAL_FORMS) or any(not isinstance(x,str) or x not in LEGAL_FORMS for x in forms):raise HTTPException(422,"Некорректные организационно-правовые формы")
  identifier=secrets.token_hex(12);c=db(ns)
  linked=False
  try:
   if c.execute("SELECT COUNT(*) FROM thematic_batches WHERE status=?",("active",)).fetchone()[0]>=3:
    raise HTTPException(429,"Одновременно допускается не более трёх тематических очередей")
   # Assign the real campaign to its authenticated creator before activation.
   ownership.link_started(request,identifier)
   linked=True
   c.execute("INSERT INTO thematic_batches(id,categories,created_at,legal_forms) VALUES(?,?,?,?)",
             (identifier,json.dumps(sorted(set(cats))),now(),
              json.dumps(sorted(set(forms)),ensure_ascii=False)))
   c.commit()
  except Exception:
   c.rollback()
   if linked:
    try:ownership.unlink(request,identifier)
    except HTTPException:pass
   raise
  finally:c.close()
  return {"id":identifier,"status":"active"}
 @app.post("/api/thematic/{batch}/stop")
 async def stop(batch:str,request:Request):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(400,"Некорректный номер")
  ownership.protect_write(request)
  ownership.require_owned(request,batch)
  c=db(ns)
  try:
   c.execute("UPDATE thematic_batches SET status=? WHERE id=?",("stopped",batch));c.commit()
  finally:c.close()
  return {"id":batch,"status":"stopped"}
 @app.delete("/api/thematic/{batch}")
 async def delete_thematic(batch:str,request:Request):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(400,"Некорректный номер")
  ownership.protect_write(request)
  ownership.require_owned(request,batch)
  c=db(ns)
  try:
   c.execute("BEGIN IMMEDIATE")
   row=c.execute("SELECT id FROM thematic_batches WHERE id=?",(batch,)).fetchone()
   if not row:raise HTTPException(404,"Очередь не найдена")
   c.execute("UPDATE thematic_batches SET status='stopped' WHERE id=?",(batch,))
   c.execute("DELETE FROM thematic_findings WHERE batch_id=?",(batch,))
   c.execute("DELETE FROM thematic_items WHERE batch_id=?",(batch,))
   c.execute("DELETE FROM thematic_batches WHERE id=?",(batch,))
   c.commit()
  except Exception:
   c.rollback()
   raise
  finally:c.close()
  ownership.unlink(request,batch)
  return {"id":batch,"deleted":True}
 @app.get("/api/thematic/history")
 async def thematic_history(request:Request):
  # Potentially large older evidence sets are scanned outside the HTTP loop,
  # with cached counts reused on the frequent 12-second dashboard refresh.
  return await asyncio.to_thread(thematic_history_records,ns,ownership.linked_ids(request))
 @app.get("/thematic/card/{batch}/{reg}")
 def thematic_card(batch:str,reg:str,request:Request):
  from html import escape
  ownership.require_owned(request,batch)
  if not re.fullmatch("[0-9a-f]{24}",batch) or not re.fullmatch("3[0-9]{10}",reg):raise HTTPException(400,"Некорректный номер")
  c=db(ns)
  try:
   c.execute("PRAGMA busy_timeout=1500")
   b=c.execute("SELECT id,categories FROM thematic_batches WHERE id=?",(batch,)).fetchone()
   if not b:raise HTTPException(404,"Проверка не найдена")
   findings=c.execute("SELECT category,title,law,evidence,code,document,source_url,law_url FROM thematic_findings WHERE batch_id=? AND reg=? ORDER BY rowid",(batch,reg)).fetchall()
   item=c.execute("SELECT status,detail FROM thematic_items WHERE batch_id=? AND reg=?",(batch,reg)).fetchone()
   if not item:raise HTTPException(404,"Закупка не найдена в тематической проверке")
   p=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
   customer=escape(str(p["customer"] or "")) if p and "customer" in p.keys() else ""
  finally:c.close()
  printable=request.query_params.get("print")=="1"
  if not printable:
   # Use the exact same reusable read-only card renderer, layout and controls
   # as /check-new/{reg}. Thematic results stay in a separate durable panel.
   from v343_fast_cards import read as read_card,render as render_card
   audit=read_card(ns,reg)
   themed={"batch":batch,"state":str(item["status"]),
           "detail":str(item["detail"] or ""),
           "categories":json.loads(b["categories"]),
           "category_labels":CATEGORIES,
           "findings":[dict(f) for f in findings]}
   return HTMLResponse(render_card(reg,audit,thematic=themed),
       headers={"Cache-Control":"no-store","X-Audit-Card":"shared-thematic-v346"})
  rows="".join("<section style='border:1px solid #ccd7e2;padding:16px;margin:16px 0;border-radius:8px'><h3>"+escape(str(f["title"] or ""))+"</h3><p><b>Категория:</b> "+escape(str(CATEGORIES.get(f["category"],f["category"])))+"</p><p><b>Правовое основание:</b> "+escape(str(f["law"] or "Не указано"))+"</p><p style='white-space:pre-wrap'><b>Установленные обстоятельства и доказательства:</b> "+escape(str(f["evidence"] or "Не указаны"))+"</p></section>" for f in findings)
  title="Служебная записка — проект" if printable else "Карточка тематической проверки"
  notice="<p><b>Проект для юридической проверки:</b> сведения необходимо сверить с оригиналами ЕИС до подписания.</p>" if printable else ""
  actions="" if printable else "<p><a href='/thematic#"+batch+"'>← К очереди</a>　<a href='/thematic/card/"+batch+"/"+reg+"?print=1' target='_blank'>Сформировать проект служебной записки / печать в PDF</a></p>"
  script="<script>window.print()</script>" if printable else ""
  return HTMLResponse("<!doctype html><html lang='ru'><meta charset='utf-8'><title>"+title+"</title><style>body{font:16px Georgia,serif;max-width:850px;margin:35px auto;padding:20px;color:#203247}h1{font-size:25px}section{break-inside:avoid}a{color:#175e9a}@media print{body{margin:0;max-width:none}a{display:none}}</style><h1>"+title+"</h1>"+actions+"<p><b>Номер закупки ЕИС:</b> "+escape(reg)+"</p><p><b>Заказчик:</b> "+customer+"</p><p><b>Статус:</b> "+escape(str(item["status"]))+"</p>"+notice+(rows or "<p>Подтверждённых нарушений по выбранным категориям пока нет.</p>")+script+"</html>")
 @app.get("/api/thematic/{batch}")
 async def status(batch:str,request:Request):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(400,"Некорректный номер")
  ownership.require_owned(request,batch)
  return await asyncio.to_thread(overview,ns,batch)
 class ThemeNav(BaseHTTPMiddleware):
  async def dispatch(self,request,call_next):
   response=await call_next(request)
   if request.url.path!="/" or response.status_code!=200 or "text/html" not in response.headers.get("content-type",""):return response
   if response.headers.get("content-encoding"):return response
   raw=b"".join([chunk async for chunk in response.body_iterator])
   html=raw.decode("utf-8",errors="replace")
   html=html.replace("</head>","<style id=\"thematic-row-colors\">table{background:#fff!important}table thead,table thead tr,table thead th,table tbody,table tbody tr,table tbody td,table tbody tr:nth-child(odd),table tbody tr:nth-child(even),table tbody tr:hover,table tbody tr:has(td .violation),table tbody tr:has(td [class*=\"violation\"]),table tbody tr:has(td .check),table tbody tr:has(td [class*=\"review\"]){background:#fff!important;background-color:#fff!important;border-left:0!important}table tbody tr td:first-child{border-left:0!important}table{border-collapse:collapse!important;border-spacing:0!important}table tbody td{border-bottom:1px solid #dce5ed!important}</style></head>",1)
   html=html.replace("</body>",DASHBOARD_CLEANUP+"</body>",1)
   return HTMLResponse(html,headers={k:v for k,v in response.headers.items() if k.lower() not in ("content-length","content-encoding")})
 app.add_middleware(ThemeNav)
 async def worker():
  while True:
   WORKER_STATE["last_cycle_at"]=now()
   WORKER_STATE["last_error"]=None
   # This scan runs many SQLite operations: keep it off the HTTP event loop.
   try:await tick(ns)
   except Exception as e:
    WORKER_STATE["last_error"]=type(e).__name__+": "+str(e)[:120]
    print("THEMATIC_TICK_ERROR",type(e).__name__,str(e)[:120],flush=True)
   finally:WORKER_STATE["last_cycle_finished_at"]=now()
   await asyncio.sleep(20)
 @app.on_event("startup")
 async def startup_thematic():
  global WORKER_TASK
  if WORKER_TASK is None or WORKER_TASK.done():
   WORKER_TASK=asyncio.create_task(worker(),name="thematic-audit-worker")
 print("THEMATIC_QUEUE_READY",flush=True)
