"""Isolated, evidence-only thematic queue over EIS procurements.
No fabricated findings; reuses verified-original legal engine and filters results.
"""
import asyncio,json,re,secrets,sqlite3,time
from datetime import datetime,timezone
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
import v360_thematic_ownership as ownership
from v381_audit_scheduling import audit_signals
from v342_identity import organization_display_name
from thematic_period import normalize_period, period_fields, index_campaign

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
 from legal_applicability import competitive_scope
 state=competitive_scope({"method":method})
 if state is False:return {"classification":"noncompetitive","reason":"В способе закупки явно указана неконкурентная процедура"}
 if state is True:return {"classification":"competitive","reason":"Указан конкурентный способ закупки; применимость сверяется с положением о закупке"}
 return {"classification":"unknown","reason":"Недостаточно сведений для достоверной квалификации"}

def db(ns):
 c=ns["conn"]();c.row_factory=sqlite3.Row;return c
def sqlite_busy(exc):
 code=getattr(exc,"sqlite_errorcode",None)
 if code is not None:return code&255 in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED)
 return "locked" in str(exc).lower() or "busy" in str(exc).lower()
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
  batch_cols={x[1] for x in c.execute("PRAGMA table_info(thematic_batches)")}
  for col in ("date_from","date_to"):
   if col not in batch_cols:
    c.execute("ALTER TABLE thematic_batches ADD COLUMN "+col+" TEXT NOT NULL DEFAULT ''")
  evidence_cols={x[1] for x in c.execute("PRAGMA table_info(thematic_findings)")}
  for col in ("code","document","source_url","law_url"):
   if col not in evidence_cols:
    c.execute("ALTER TABLE thematic_findings ADD COLUMN "+col+" TEXT NOT NULL DEFAULT ''")
  c.execute("CREATE INDEX IF NOT EXISTS idx_thematic_evidence_by_purchase ON thematic_findings(batch_id,reg)")
  # Early Docker selftests expose scripts/ alone; account routes use the package.
  try:
   from scripts.v385_msp_reassessment import initialize as initialize_reassessments
  except ModuleNotFoundError as exc:
   if exc.name != "scripts":raise
   from v385_msp_reassessment import initialize as initialize_reassessments
  initialize_reassessments(c)
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
   index_campaign(c,b,customer_form,now())
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
 from v332_all_procurements_audit import existing,as_procurement,collect_saved_evidence,is_official
 purchase,rows=await asyncio.to_thread(existing,ns,reg)
 p=as_procurement(purchase)
 if not business_company(p.get("customer")):return "excluded_company",[]
 source=await collect_saved_evidence(ns,reg,rows)
 docs,unreadable=source["docs"],source["unreadable"]
 incomplete=bool(unreadable or source["source_incomplete"])
 for key,value in source["p"].items():
    if value and (not p.get(key) or key in ("method","submission_end","submission_start")):
       p[key]=value
 verified=[d for d in docs if is_official(d) and str(d.get("text") or "").strip()]
 if not verified:
    # A missing original needs the EIS intake; an existing but temporarily
    # unreadable file needs a later extraction attempt, with bounded backoff.
    from pathlib import Path
    available=bool(source["original_received"])
    if not available and unreadable:
       available=await asyncio.to_thread(lambda:any(Path(r["local_path"]).is_file()
           for r in rows if r.get("local_path")))
    return ("retry" if available else "waiting_originals"),[]
 # Both halves of the general engine can establish violations. Running
 # analyze alone silently omitted the confirmed checklist rules here.
 analyzer=ns.get("analyze")
 checker=ns.get("build_checks")
 if not callable(analyzer) or not callable(checker):
    raise RuntimeError("Main legal analyzer or checklist is unavailable")
 try:card=json.loads(purchase.get("card_fields_json") or "{}")
 except (ValueError,TypeError):card={}
 if not isinstance(card,dict):card={}
 card.update(source["card"])
 card.setdefault("status","partial")
 card.setdefault("pages",{})
 detail={"registrationNumber":reg,"purchaseName":p.get("title") or "",
         "purchaseMethodName":p.get("method") or "",
         "publicationDate":p.get("published") or "",
         "submissionStart":p.get("submission_start") or "",
         "submissionEnd":p.get("submission_end") or ""}
 detail.update(source["detail"])
 findings=await asyncio.to_thread(analyzer,p,verified)
 checks=await asyncio.to_thread(checker,p,detail,card,verified)
 findings=list(findings) if isinstance(findings,(list,tuple)) else []
 for item in checks if isinstance(checks,(list,tuple)) else []:
    if not isinstance(item,dict) or item.get("status")!="violation":continue
    if not str(item.get("evidence") or "").strip():continue
    findings.append({**item,"kind":"violation",
                     "title":item.get("item") or item.get("title") or "",
                     "code":item.get("field_path") or item.get("code") or ""})
 # Match document provenance robustly: verified originals can be stored
 # under a local filename while the analyzer cites the EIS display name.
 import os
 def norm_name(value):
    return os.path.basename(str(value or "").replace("\\","/")).strip().casefold()
 names={norm_name(d.get("name")) for d in verified}
 names.update(norm_name(d.get("local")) for d in verified)
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
    if not str(f.get("evidence") or "").strip():continue
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
 from v352_dashboard_truth import collapse_legal_events
 return ("retry" if incomplete else "completed"),collapse_legal_events(selected)

def read_db(ns):
 c=db(ns)
 try:
  c.execute("PRAGMA busy_timeout=1000")
  c.execute("PRAGMA query_only=ON")
  return c
 except Exception:
  c.close()
  raise

def saved_read(operation,*args):
 """Bound contention on saved views; never turn a failed read into no results."""
 from audit_db_retry import is_busy
 for attempt in range(3):
  try:return operation(*args)
  except sqlite3.OperationalError as exc:
   if not is_busy(exc):raise
   if attempt==2:
    raise HTTPException(503,"Сохранённая проверка временно недоступна: база занята. Повторите открытие через несколько секунд.",headers={"Retry-After":"2"}) from exc
   time.sleep(.15*(attempt+1))

def overview(ns,batch):
 c=read_db(ns)
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
  from v352_dashboard_truth import collapse_legal_events
  from v365_consultant_refs import enrich
  try:
   from scripts.v384_thematic_findings import read_thematic_items
  except ModuleNotFoundError as exc:
   if exc.name != "scripts":raise
   from v384_thematic_findings import read_thematic_items
  canonical=read_thematic_items(c,batch)
  unique=[r for r in canonical if r["effective_status"]=="confirmed"]
  revised=[r for r in canonical if r["effective_status"]!="confirmed"]
  matches=[enrich({"reg":r["reg_number"],"title":r["title"],
                   "categories":[r["category"]],"law":r["law"],
                   "law_url":r.get("law_url"),"code":r.get("code"),
                   "evidence":r["evidence"] or ""}) for r in unique[:500]]
  items=[{"reg":r["reg"],"status":r["status"],"detail":r["detail"],"updated_at":r["updated_at"]} for r in c.execute("SELECT reg,status,detail,updated_at FROM thematic_items WHERE batch_id=? ORDER BY CASE status WHEN 'queued' THEN 0 WHEN 'retry' THEN 1 WHEN 'waiting_originals' THEN 2 ELSE 3 END,updated_at DESC LIMIT 200",(batch,)).fetchall()]
  try:
   from v301_search_jobs import _bridge
   bridge=_bridge({**ns,"conn":lambda:read_db(ns)})
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
  return {"worker":worker,"bridge":bridge,"storage":storage,"id":batch,"categories":cats,"legal_forms":json.loads(b["legal_forms"]),**period_fields(b),"status":b["status"],"counts":counts,"confirmed_matching_findings":matches,"reassessed_findings":revised,"items":items,"note":"Тематические результаты сохраняются отдельно. Проверяются подтверждённые оригиналы ЕИС; доступные правила не охватывают все категории."}
 finally:c.close()
_HISTORY_COUNT_CACHE={}

def thematic_history_records(ns, permitted_ids=None):
 """One bounded retry boundary for every caller, including owner-filtered views."""
 return saved_read(_thematic_history_records,ns,permitted_ids)

def _thematic_history_records(ns, permitted_ids=None):
 """Show unique legal incidents, not repeated saved excerpts, in history."""
 from v352_dashboard_truth import collapse_legal_events
 if permitted_ids is not None and not permitted_ids:
  return {"batches":[]}
 c=read_db(ns)
 try:
  placeholders=",".join("?" for _ in permitted_ids) if permitted_ids is not None else ""
  owner_filter=(" WHERE b.id IN ("+placeholders+")") if permitted_ids is not None else ""
  rows=c.execute("""SELECT b.id,b.categories,b.created_at,b.status,b.date_from,b.date_to,
         COUNT(DISTINCT i.reg) AS purchases,
         (SELECT COUNT(*) FROM thematic_findings f WHERE f.batch_id=b.id) AS stored_rows
         FROM thematic_batches b
         LEFT JOIN thematic_items i ON i.batch_id=b.id
         """+owner_filter+""" GROUP BY b.id ORDER BY b.created_at DESC LIMIT 100""",
         permitted_ids or ()).fetchall()
  out=[]
  has_purchases=bool(c.execute(
      "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchases'"
  ).fetchone())
  customer_col=""
  if has_purchases:
   cols={r[1] for r in c.execute("PRAGMA table_info(purchases)")}
   customer_col="customer" if "customer" in cols else ("customer_name" if "customer_name" in cols else "")
  for row in rows:
   item=dict(row)
   try:item["categories"]=json.loads(item["categories"] or "[]")
   except (ValueError,TypeError):item["categories"]=[]
   batch=item["id"]
   rev=c.execute("SELECT COALESCE(MAX(rowid),0) FROM thematic_findings WHERE batch_id=?",(batch,)).fetchone()[0]
   try:
    from scripts.v385_msp_reassessment import revision as reassessment_revision
   except ModuleNotFoundError as exc:
    if exc.name != "scripts":raise
    from v385_msp_reassessment import revision as reassessment_revision
   stamp=(int(item["stored_rows"]),int(rev),reassessment_revision(c,batch))
   cached=_HISTORY_COUNT_CACHE.get(batch)
   if cached and cached[0]==stamp:
    unique,titles,revised_count=cached[1:]
   else:
    try:
     from scripts.v384_thematic_findings import read_thematic_items
    except ModuleNotFoundError as exc:
     if exc.name != "scripts":raise
     from v384_thematic_findings import read_thematic_items
    classified=read_thematic_items(c,batch)
    unique=sum(f["effective_status"]=="confirmed" for f in classified)
    titles=list(dict.fromkeys(f["title"] for f in classified
       if f["effective_status"]=="confirmed" and f["title"]))[:35]
    revised_count=sum(f["effective_status"]!="confirmed" for f in classified)
    _HISTORY_COUNT_CACHE[batch]=(stamp,unique,titles,revised_count)
   item["violations"]=unique
   item["violation_types"]=titles
   item["reassessed_findings"]=revised_count
   item["customers"]=[]
   if customer_col:
    item["customers"]=[organization_display_name(r[0]) for r in c.execute(
      "SELECT DISTINCT p."+customer_col+" FROM thematic_items i "
      "JOIN purchases p ON p.reg_number=i.reg WHERE i.batch_id=? "
      "AND p."+customer_col+" IS NOT NULL AND p."+customer_col+"<>'' LIMIT 35",
      (batch,))]
   out.append(item)
  # Discard counts for deleted campaigns; never change their stored evidence.
  active={x["id"] for x in out}
  for key in list(_HISTORY_COUNT_CACHE):
   if key not in active:_HISTORY_COUNT_CACHE.pop(key,None)
  return {"batches":out}
 finally:c.close()

PAGE="""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Тематическая проверка ЕИС</title><style>
:root{font-family:Inter,"Segoe UI",system-ui,sans-serif;--tone:#17618f;--ink:#203e34;--muted:#687b71}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(ellipse at 95% 0%,#eaf3f9,transparent 32%),#f4f7fa;
 color:var(--ink);font:14px/1.6 Inter,"Segoe UI",system-ui,sans-serif}
main{width:min(100% - 24px,1100px);margin:clamp(16px,3vw,38px) auto 60px;
 padding:clamp(18px,3.5vw,36px);border:1px solid #dbe6ee;border-radius:19px;
 background:white;box-shadow:0 15px 40px #17466a0d}
h1{font-size:clamp(25px,3.6vw,34px);letter-spacing:-.045em;line-height:1.2;margin:17px 0 8px;color:#1c4b70}
h2{font-size:20px;margin:25px 0 12px;letter-spacing:-.025em;color:#285878}
h3{font-size:16px;color:#355c76}
p{line-height:1.7;color:#61796d}
#cats,#forms{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:10px;margin:14px 0}
#cats label,#forms label{display:flex;align-items:center;gap:10px;cursor:pointer;
 border:1px solid #dce7f0;border-radius:12px;background:#f9fbfe;padding:13px 14px;
 font-size:13px;min-height:56px}
#cats label:hover,#forms label:hover{border-color:#bed9eb;background:#f1f7fb}
#thematic-period{margin:24px 0 14px;padding:16px;border:1px solid #dce7f0;
 border-radius:12px;background:#f9fbfe;min-width:0}
#thematic-period legend{padding:0 6px;font-size:18px;font-weight:700;color:#285878}
.thematic-period-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr)) auto;
 align-items:end;gap:12px}
.thematic-period-fields label{display:grid;gap:6px;font-weight:650;color:#355c76}
.thematic-period-fields input{width:100%;min-width:0;min-height:44px;padding:9px 11px;
 font:16px system-ui;color:#203e34;background:#fff;border:1px solid #c8dce9;border-radius:8px}
#thematic-period-clear{min-height:44px;background:#fff;color:#17618f;border-color:#bed2e1}
#thematic-period-clear:hover{background:#edf5fb}
#thematic-period-hint{margin:10px 0 0;font-size:12px;color:#536b7d}
#thematic-start-error{margin:8px 0;color:#a33434;font-weight:600}
.thematic-saved-period{display:block;color:#536b7d;font-size:12px}
input[type=checkbox]{height:17px;width:17px;accent-color:#17618f;flex:none}
button{min-height:40px;background:#17618f;color:#fff;padding:10px 16px;
 font:700 13px system-ui;border:1px solid #17618f;border-radius:10px;cursor:pointer}
button:hover:not(:disabled){background:#114b74}
button:disabled{opacity:.55;cursor:wait}
a{color:#176394;text-decoration:none;font-weight:650}
a:hover{text-decoration:underline}
.thematic-registration{display:block;margin:1em 0;line-height:1.7;font-weight:700}
#history{display:grid;gap:9px}
#history p{margin:0!important;display:flex;flex-wrap:wrap;align-items:center;gap:10px;
 background:#f9fbfe;border:1px solid #dde8f0;border-radius:12px;padding:11px 14px}
#history button{margin:0!important}
#out{white-space:pre-wrap;overflow-wrap:anywhere}
#out table{width:100%;border-spacing:0;border-collapse:separate;
 border:1px solid #dce7df;border-radius:13px;overflow:hidden}
#out td{padding:10px 12px!important;border-bottom:1px solid #ebf0ec!important;
 vertical-align:top}
#out article{border-radius:12px!important;border-left-color:#ba6954!important;
 background:#fffaf5!important;padding:15px!important}
:focus-visible{outline:3px solid #99dfce;outline-offset:2px}
@media(max-width:700px){
 main{width:calc(100% - 20px);border-radius:16px;padding:17px 15px}
 #cats,#forms{grid-template-columns:minmax(0,1fr)}
 .thematic-period-fields{grid-template-columns:repeat(2,minmax(0,1fr))}
 #thematic-period-clear{grid-column:1/-1;justify-self:start}
 #out table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
 #history a{overflow-wrap:anywhere}
}
</style><main><a href="/">← Главная</a><h1>Тематическая проверка</h1><p>Выберите категории. Закупки из ЕИС поступают в отдельную очередь. Нарушения подтверждаются только оригиналами и юридическими правилами.</p><div id="cats"></div><h2>Организационно-правовые формы заказчиков</h2><p>Выберите одну или несколько форм. По умолчанию выбраны все хозяйственные общества.</p><div id="forms"></div>
<fieldset id="thematic-period" aria-describedby="thematic-period-hint"><legend>Дата размещения в ЕИС</legend>
<div class="thematic-period-fields">
<label for="thematic-date-from">С<input type="date" id="thematic-date-from" name="date_from" min="0001-01-01" max="9999-12-31" aria-label="Дата размещения в ЕИС: с"></label>
<label for="thematic-date-to">По<input type="date" id="thematic-date-to" name="date_to" min="0001-01-01" max="9999-12-31" aria-label="Дата размещения в ЕИС: по"></label>
<button type="button" id="thematic-period-clear">За всё время</button></div>
<p id="thematic-period-hint">Закупки отбираются по дате размещения в ЕИС, включая обе даты. Можно указать только одну дату. Без дат — за всё время.</p></fieldset>
<p id="thematic-start-error" role="alert" aria-live="polite" hidden></p>
<p><button type="button" id="start">Начать проверку</button></p><h2>Сохранённые тематические проверки</h2><div id="history"></div><h2>Очередь и нарушения</h2><div id="thematic-load-status" role="status" aria-live="polite"></div><div id="out">Выберите категории</div></main><script>
(()=>{
const categories=__CATEGORIES__,legalForms=__LEGAL_FORMS__;const formsBox=document.querySelector("#forms");for(const [id,title] of Object.entries(legalForms)){const label=document.createElement("label"),check=document.createElement("input");check.type="checkbox";check.value=id;check.checked=true;label.append(check,document.createTextNode(" "+id+" — "+title));formsBox.append(label)}const box=document.querySelector('#cats');
for(const [id,title] of Object.entries(categories)){let l=document.createElement('label'),i=document.createElement('input');i.type='checkbox';i.value=id;l.append(i,document.createTextNode(' '+title));box.append(l)}
function thematicPeriod(check){
 const day=value=>String(value||'').slice(0,10).split('-').reverse().join('.');
 const from=day(check.date_from),to=day(check.date_to);
 return from&&to?'С '+from+' по '+to+' включительно':from?'С '+from:to?'По '+to+' включительно':'За всё время';
}
const dateFrom=document.querySelector('#thematic-date-from'),dateTo=document.querySelector('#thematic-date-to');
const startError=document.querySelector('#thematic-start-error');
function showStartError(message){startError.textContent=message;startError.hidden=!message}
for(const field of [dateFrom,dateTo])field.addEventListener('input',()=>showStartError(''));
document.querySelector('#thematic-period-clear').onclick=()=>{dateFrom.value='';dateTo.value='';showStartError('');dateFrom.focus()};
document.querySelector('#start').onclick=async()=>{
 const button=document.querySelector('#start');if(button.disabled)return;showStartError('');
 const cats=[...box.querySelectorAll('input:checked')].map(x=>x.value);
 if(!cats.length){showStartError('Выберите хотя бы одну категорию проверки.');return}
 const forms=[...formsBox.querySelectorAll('input:checked')].map(x=>x.value);
 if(!forms.length){showStartError('Выберите хотя бы одну организационно-правовую форму заказчика.');return}
 for(const field of [dateFrom,dateTo])if(!field.checkValidity()){
  showStartError('Укажите корректную дату размещения в ЕИС.');field.reportValidity();return;
 }
 const date_from=dateFrom.value,date_to=dateTo.value;
 if(date_from&&date_to&&date_from>date_to){
  showStartError('Дата «с» не может быть позже даты «по». Измените период проверки.');dateTo.focus();return;
 }
 button.disabled=true;button.textContent='Запускаем проверку…';
 try{
  const r=await fetch('/api/thematic/start',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({categories:cats,legal_forms:forms,date_from,date_to})});
  const j=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(typeof j.detail==='string'?j.detail:'Не удалось запустить проверку. Попробуйте ещё раз.');
  openSaved(j.id);loadHistory();
 }catch(error){showStartError(error instanceof TypeError?'Не удалось соединиться с сервером. Повторите запуск проверки.':error.message)}
 finally{button.disabled=false;button.textContent='Начать проверку'}
};
let historyLoading=false;
async function loadHistory(){
 if(historyLoading)return;historyLoading=true;
 try{const j=await savedJSON('/api/thematic/history');const box=document.querySelector('#history');box.replaceChildren();for(const b of j.batches||[]){const p=document.createElement('p'),a=document.createElement('a');a.href='#'+b.id;a.textContent=new Date(b.created_at).toLocaleString('ru-RU')+' — закупок: '+b.purchases+'; нарушений: '+b.violations+'; '+(b.status==='active'?'в работе':'остановлена');a.onclick=(ev)=>{ev.preventDefault();openSaved(b.id)};p.append(a);const period=document.createElement('span');period.className='thematic-saved-period';period.textContent='Дата размещения в ЕИС: '+thematicPeriod(b);p.append(period);const del=document.createElement('button');del.textContent='Удалить очередь';del.style.cssText='margin-left:12px;background:#a52a2a;padding:7px 10px';del.onclick=async()=>{if(!confirm('Удалить эту тематическую очередь и все её сохранённые результаты? Это действие нельзя отменить.'))return;const resp=await fetch('/api/thematic/'+encodeURIComponent(b.id),{method:'DELETE'});if(!resp.ok){alert('Не удалось удалить очередь');return}if(location.hash==='#'+b.id){window.history.replaceState(null,'',location.pathname);document.querySelector('#out').textContent='Выберите очередь из истории'}await loadHistory()};p.append(del);box.append(p)}if(!(j.batches||[]).length)box.textContent='История пока пуста'
 }catch(error){const box=document.querySelector('#history');if(!box.children.length){box.textContent=error.message;const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить загрузку';retry.onclick=loadHistory;box.append(retry)}}
 finally{historyLoading=false}
}
async function savedJSON(url,signal){
 const controller=new AbortController();
 const cancel=()=>controller.abort();signal?.addEventListener('abort',cancel,{once:true});
 const timer=setTimeout(cancel,15000);
 try{
  const r=await fetch(url,{credentials:'same-origin',signal:controller.signal,headers:{'Accept':'application/json'}});
  const j=await r.json().catch(()=>null);
  if(!r.ok||!j)throw Error(r.status===401?'Войдите в личный кабинет повторно.':typeof j?.detail==='string'?j.detail:'Не удалось загрузить сохранённую проверку. Повторите открытие.');
  return j;
 }catch(error){if(error.name==='AbortError')throw Error('Сервер не ответил вовремя. Повторите открытие проверки.');throw error}
 finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel)}
}
let pending=null,shownBatch='';
function openSaved(id){
 if(location.hash.slice(1)!==id){location.hash=id}
 refresh();
 document.querySelector('#out').scrollIntoView?.({behavior:'smooth',block:'start'});
}
async function refresh(){
 const batch=location.hash.slice(1),out=document.querySelector('#out'),status=document.querySelector('#thematic-load-status');
 if(!batch){pending?.controller.abort();pending=null;shownBatch='';out.textContent='Выберите очередь из истории';status.textContent='';return}
 if(pending?.batch===batch)return;
 pending?.controller.abort();
 const request={batch,controller:new AbortController()};pending=request;
 if(shownBatch!==batch){out.replaceChildren();out.textContent='Загрузка сохранённой проверки…'}
 status.replaceChildren();status.textContent='Загрузка…';
 try{
 const j=await savedJSON('/api/thematic/'+encodeURIComponent(batch),request.controller.signal);
 if(pending!==request||location.hash.slice(1)!==batch)return;
 out.replaceChildren();shownBatch=batch;status.textContent='';
 const names={queued:'В очереди',submitted:'Передано на проверку',completed:'Проверено',waiting_originals:'Ожидаются оригиналы ЕИС',waiting_engine:'Ожидается обработчик',retry:'Повторная попытка',outside_period:'Вне выбранного периода'};const heading=document.createElement('p');heading.textContent='Статус: '+(j.status==='active'?'Работает':'Остановлена');out.append(heading);const period=document.createElement('p');period.className='thematic-saved-period';period.textContent='Дата размещения в ЕИС: '+thematicPeriod(j);out.append(period);const bridge=document.createElement('p');bridge.textContent='Проверка сохранённых оригиналов: '+(j.worker?.active?'серверный обработчик работает':'серверный обработчик недоступен')+'. Загрузка новых документов ЕИС: '+(j.bridge?.active?'подключена':'не подключена')+'; ожидают загрузки: '+(j.bridge?.queued??'неизвестно');out.append(bridge);if(j.storage?.blocked){const warning=document.createElement('p');warning.style.cssText='font-weight:600;color:#9a3412';warning.textContent='Недостаточно места в хранилище Railway (свободно '+j.storage.free_mb+' МБ). Новые запросы и результаты не смогут сохраняться, пока место не освободится. Сохранённые оригиналы не удаляются.';out.append(warning)}for(const [key,num] of Object.entries(j.counts||{})){const line=document.createElement('p');line.textContent=(names[key]||key)+': '+num;out.append(line)}const stop=document.createElement('button');stop.textContent='Остановить проверку';stop.disabled=j.status!=='active';stop.onclick=async()=>{if(!confirm('Остановить тематическую проверку?'))return;await fetch('/api/thematic/'+encodeURIComponent(j.id)+'/stop',{method:'POST'});refresh()};out.append(stop);const qh=document.createElement('h3');qh.textContent='Закупки в тематической проверке';out.append(qh);const table=document.createElement('table');table.style.cssText='width:100%;border-collapse:collapse';for(const p of j.items||[]){const tr=document.createElement('tr');for(const value of [p.reg,names[p.status]||p.status,p.detail||'']){const td=document.createElement('td');td.style.cssText='border-bottom:1px solid #ddd;padding:7px';td.textContent=value;tr.append(td)}const a=document.createElement('a');a.href='/thematic/card/'+encodeURIComponent(j.id)+'/'+encodeURIComponent(p.reg);a.textContent='Карточка проверки';const td=document.createElement('td');td.append(a);tr.append(td);table.append(tr)}out.append(table);const h=document.createElement('h3');h.textContent='Подтверждённые нарушения по выбранным категориям';out.append(h);const findings=j.confirmed_matching_findings||[];if(!findings.length){const p=document.createElement('p');p.textContent='Подтверждённых нарушений пока нет';out.append(p)}for(const f of findings){const item=document.createElement('article');item.style.cssText='border-left:4px solid #b91c1c;background:#fff5f5;margin:12px 0;padding:12px';const strong=document.createElement('strong');const cardUrl='/thematic/card/'+encodeURIComponent(j.id)+'/'+encodeURIComponent(f.reg);const registration=document.createElement('a');registration.className='thematic-registration';registration.href=cardUrl;registration.textContent='Номер закупки ЕИС: '+f.reg;item.append(registration);strong.textContent=f.title;item.append(strong);const detail=document.createElement('p');detail.textContent=f.evidence||'';item.append(detail);if(f.law){const law=document.createElement('p');law.textContent='Правовое основание: '+f.law;item.append(law)}if(f.consultant_url){const norm=document.createElement('a');norm.href=f.consultant_url;norm.target='_blank';norm.rel='noopener noreferrer';norm.textContent='КонсультантПлюс · '+(f.consultant_citation||'Текст нормы');item.append(norm)}const card=document.createElement('a');card.href=cardUrl;card.textContent='Карточка проверки и служебная записка';item.append(card);out.append(item)}
 const revised=j.reassessed_findings||[];
 if(revised.length){
  const title=document.createElement('h3');title.textContent='Пересмотренные выводы / требуют проверки: '+revised.length;out.append(title);
  for(const f of revised){
   const article=document.createElement('article'),name=document.createElement('strong'),reason=document.createElement('p'),link=document.createElement('a');
   name.textContent=f.title;reason.textContent=f.reassessment?.reason||'Вывод требует повторной проверки.';
   link.href='/thematic/card/'+encodeURIComponent(j.id)+'/'+encodeURIComponent(f.reg_number);
   link.textContent='Первоначальное доказательство и результат перепроверки · '+f.reg_number;
   article.append(name,reason,link);out.append(article);
  }
 }
 }catch(error){
  if(pending!==request||location.hash.slice(1)!==batch)return;
  if(shownBatch!==batch)out.textContent='Не удалось открыть сохранённую проверку.';
  status.textContent=error.message;const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить открытие';retry.onclick=()=>refresh();status.append(retry);
 }finally{if(pending===request)pending=null}
}
window.addEventListener('hashchange',()=>refresh());setInterval(()=>{if(!document.hidden){refresh();loadHistory()}},12000);loadHistory();refresh();
})();</script></html>""".replace("__CATEGORIES__",json.dumps(CATEGORIES,ensure_ascii=False)).replace("__LEGAL_FORMS__",json.dumps(LEGAL_FORMS,ensure_ascii=False))
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
 def classify(reg:str):
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
  if not isinstance(cats,list) or not cats or len(cats)>len(CATEGORIES) or any(not isinstance(x,str) or x not in CATEGORIES for x in cats):raise HTTPException(422,"Некорректные категории")
  if not isinstance(forms,list) or not forms or len(forms)>len(LEGAL_FORMS) or any(not isinstance(x,str) or x not in LEGAL_FORMS for x in forms):raise HTTPException(422,"Некорректные организационно-правовые формы")
  try:
   date_from,date_to=normalize_period(payload.get("date_from"),payload.get("date_to"))
  except ValueError as exc:
   raise HTTPException(422,str(exc)) from exc
  identifier=secrets.token_hex(12)
  def persist_start():
   # Keep the connection and complete transaction in the same worker thread.
   # PostgreSQL ownership and SQLite lock waits must not stop HTTP serving.
   c=db(ns);linked=False
   try:
    # Read this owner's active queues after taking the shared creation lock.
    # Other users and historical unassigned queues do not consume their slots.
    c.execute("BEGIN IMMEDIATE")
    ownership.require_start_capacity(c,ownership.account_id(request))
    # Assign the real campaign to its authenticated creator before activation.
    ownership.link_started(request,identifier)
    linked=True
    c.execute("INSERT INTO thematic_batches(id,categories,created_at,legal_forms,date_from,date_to) VALUES(?,?,?,?,?,?)",
              (identifier,json.dumps(sorted(set(cats))),now(),
               json.dumps(sorted(set(forms)),ensure_ascii=False),date_from,date_to))
    c.commit()
   except Exception:
    c.rollback()
    if linked:
     try:ownership.unlink(request,identifier)
     except HTTPException:pass
    raise
   finally:c.close()
  await asyncio.to_thread(persist_start)
  # New campaigns should not inherit another campaign's 90-second index wait.
  WORKER_STATE["index_at"]=0
  audit_signals(ns).notify("thematic")
  return {"id":identifier,"status":"active","date_from":date_from,"date_to":date_to}
 @app.post("/api/thematic/{batch}/stop")
 def stop(batch:str,request:Request):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(400,"Некорректный номер")
  ownership.protect_write(request)
  ownership.require_owned(request,batch)
  c=db(ns)
  try:
   c.execute("UPDATE thematic_batches SET status=? WHERE id=?",("stopped",batch));c.commit()
  finally:c.close()
  return {"id":batch,"status":"stopped"}
 @app.delete("/api/thematic/{batch}")
 def delete_thematic(batch:str,request:Request):
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
 def thematic_history(request:Request):
  # FastAPI runs this whole sync handler in its bounded worker pool, including
  # PostgreSQL ownership lookup as well as the potentially large evidence scan.
  return thematic_history_records(ns,ownership.linked_ids(request))
 @app.get("/api/thematic/unassigned")
 def unassigned_history(request:Request):
  # Administrator-assisted migration for real older checks with no recorded
  # owner. Never silently attribute legacy campaigns to the current user.
  claimed=ownership.already_assigned(request)
  result=thematic_history_records(ns)
  return {"batches":[x for x in result["batches"] if x["id"] not in claimed]}

 @app.post("/api/thematic/adopt/{batch}")
 def adopt_real_batch(batch:str,request:Request):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(422,"Некорректная проверка")
  ownership.protect_write(request)
  ownership.already_assigned(request)  # administrator gate and identity DB health
  c=db(ns)
  try:
   exists=c.execute("SELECT 1 FROM thematic_batches WHERE id=?",(batch,)).fetchone()
   if not exists:raise HTTPException(404,"Тематическая проверка не найдена")
  finally:c.close()
  ownership.claim_historical(request,batch)
  return {"linked":True}

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
   item=c.execute("SELECT status,detail FROM thematic_items WHERE batch_id=? AND reg=?",(batch,reg)).fetchone()
   if not item:raise HTTPException(404,"Закупка не найдена в тематической проверке")
   p=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
   customer=escape(organization_display_name(p["customer"])) if p and "customer" in p.keys() else ""
  finally:c.close()
  from v384_thematic_findings import thematic_items
  findings=thematic_items(ns,batch,reg)
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
           "findings_canonical":True,
           "findings":[dict(f) for f in findings]}
   return HTMLResponse(render_card(reg,audit,thematic=themed,
       account_review=bool(request.scope.get("eis223_account_id"))),
       headers={"Cache-Control":"no-store","X-Audit-Card":"shared-thematic-v346"})
  from v352_dashboard_truth import collapse_legal_events
  from v343_fast_cards import legal_links
  unique_print=findings
  confirmed_count=sum(f.get("effective_status","confirmed")=="confirmed" for f in unique_print)
  rows="<p><b>Подтверждённых нарушений: "+str(confirmed_count)+"</b></p>"
  try:
   from scripts.v385_msp_reassessment import scope_display
  except ModuleNotFoundError as exc:
   if exc.name != "scripts":raise
   from v385_msp_reassessment import scope_display
  current_msp=scope_display(unique_print)
  if current_msp:rows+="<p><b>Закупка только для МСП:</b> "+escape(current_msp)+"</p>"
  for f in unique_print:
   revised=f.get("effective_status","confirmed")!="confirmed"
   reassessment=f.get("reassessment") or {}
   banner=("<p><b>Пересмотренный вывод / требуется проверка.</b> "+escape(str(reassessment.get("reason") or ""))+"</p>") if revised else ""
   rows+="<section style='border:1px solid #ccd7e2;padding:16px;margin:16px 0;border-radius:8px'>"+banner+"<h3>"+escape(str(f["title"] or ""))+"</h3><p><b>Категория:</b> "+escape(str(CATEGORIES.get(f["category"],f["category"])))+"</p><p><b>Правовое основание первоначального вывода:</b> "+escape(str(f["law"] or "Не указано"))+"</p>"+legal_links(f,f.get("law"),f.get("law_url"))+"<p style='white-space:pre-wrap'><b>Сохранённые обстоятельства и доказательства:</b> "+escape(str(f["evidence"] or "Не указаны"))+"</p></section>"
  owner=request.scope.get("eis223_account_id")
  requisites={}
  if owner:
   from scripts.v367_private_features import _memo_defaults, _memo_requisites
   requisites={**_memo_defaults(),**_memo_requisites(owner,reg)}
  recipient=str(requisites.get("recipient") or "").strip()
  executor=re.sub(r"^Исполнитель\s*:?\s*","",str(requisites.get("executor") or ""),flags=re.I).strip()
  phone=re.sub(r"^Тел(?:ефон)?[.:\s]*","",str(requisites.get("phone") or ""),flags=re.I).strip()
  recipient_html='<p class="memo-recipient">'+escape(recipient)+'</p>' if recipient else ''
  footer='<footer class="memo-executor">'+('<p>Исполнитель: '+escape(executor)+'</p>' if executor else '')+('<p>Тел.: '+escape(phone)+'</p>' if phone else '')+'</footer>'
  title="Служебная записка — проект" if printable else "Карточка тематической проверки"
  notice="<p><b>Проект для юридической проверки:</b> сведения необходимо сверить с оригиналами ЕИС до подписания.</p>" if printable else ""
  actions="" if printable else "<p><a href='/thematic#"+batch+"'>← К очереди</a>　<a href='/thematic/card/"+batch+"/"+reg+"?print=1' target='_blank'>Сформировать проект служебной записки / печать в PDF</a></p>"
  script="<script>window.print()</script>" if printable else ""
  return HTMLResponse("<!doctype html><html lang='ru'><meta charset='utf-8'><title>"+title+"</title><style>body{font:16px Georgia,serif;max-width:850px;margin:35px auto;padding:20px;color:#203247}h1{font-size:25px}section{break-inside:avoid}a{color:#175e9a}.memo-recipient{text-align:right;white-space:pre-wrap;color:#000}.memo-executor{margin-top:35px;color:#000}.memo-executor p{margin:3px 0;color:#000}@media print{body{margin:0;max-width:none}a{display:none}}</style>"+recipient_html+"<h1>"+title+"</h1>"+actions+"<p><b>Номер закупки ЕИС:</b> "+escape(reg)+"</p><p><b>Заказчик:</b> "+customer+"</p><p><b>Статус:</b> "+escape(str(item["status"]))+"</p>"+notice+(rows or "<p>Подтверждённых нарушений по выбранным категориям пока нет.</p>")+footer+script+"</html>")
 @app.get("/api/thematic/{batch}")
 def status(batch:str,request:Request):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(400,"Некорректный номер")
  ownership.require_owned(request,batch)
  return saved_read(overview,ns,batch)
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
  signals=audit_signals(ns)
  while True:
   version=signals.version("thematic")
   processed=0
   WORKER_STATE["last_cycle_at"]=now()
   WORKER_STATE["last_error"]=None
   # This scan runs many SQLite operations: keep it off the HTTP event loop.
   try:processed=await tick(ns)
   except Exception as e:
    WORKER_STATE["last_error"]=type(e).__name__+": "+str(e)[:120]
    print("THEMATIC_TICK_ERROR",type(e).__name__,str(e)[:120],flush=True)
   finally:WORKER_STATE["last_cycle_finished_at"]=now()
   # Drain work already on disk without an unconditional 20-second gap.
   # Capture the version before the scan so arrivals during a cycle are kept.
   await signals.wait("thematic",version,.05 if processed else 20)
 @app.on_event("startup")
 async def startup_thematic():
  global WORKER_TASK
  if WORKER_TASK is None or WORKER_TASK.done():
   WORKER_TASK=asyncio.create_task(worker(),name="thematic-audit-worker")
 print("THEMATIC_QUEUE_READY",flush=True)
