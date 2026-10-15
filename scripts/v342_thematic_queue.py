"""Isolated, evidence-only thematic queue over EIS procurements.
No fabricated findings; reuses verified-original legal engine and filters results.
"""
import asyncio,json,re,secrets,sqlite3,time
from datetime import datetime,timezone
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

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
COMPANY_FORM=re.compile(r"^\s*(?:ООО|АО|ПАО|ОАО|ЗАО|ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ|(?:ПУБЛИЧНОЕ\s+)?АКЦИОНЕРНОЕ\s+ОБЩЕСТВО|ЗАКРЫТОЕ\s+АКЦИОНЕРНОЕ\s+ОБЩЕСТВО)\b",re.I)
def business_company(name):
 return bool(COMPANY_FORM.search(str(name or "").strip().strip('"«»')))
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
  PRIMARY KEY(batch_id,reg));""")
  c.commit()
 finally:c.close()
def now():return datetime.now(timezone.utc).isoformat()
async def tick(ns):
 c=db(ns)
 try:
  batches=c.execute("SELECT * FROM thematic_batches WHERE status='active'").fetchall()
  for b in batches:
   # Existing and newly ingested EIS purchases are automatically added.
   rows=c.execute("SELECT reg_number,customer FROM purchases WHERE reg_number GLOB '3??????????' LIMIT 50000").fetchall()
   for r in rows:
    if business_company(r["customer"]):
     c.execute("INSERT OR IGNORE INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?)",(b["id"],r["reg_number"],now()))
   pending=c.execute("SELECT reg FROM thematic_items WHERE batch_id=? AND status='queued' LIMIT 20",(b["id"],)).fetchall()
   for p in pending:
    reg=p["reg"]
    try:
     from v295_eis_originals import allowed_documents,saved_document
     originals=allowed_documents(reg)
     if not originals or not all(saved_document(reg,uid) for uid in originals):
      state="waiting_originals";detail="Ожидаются подтверждённые оригиналы ЕИС"
     else:
      enqueue=ns.get("_v295_enqueue_review")
      if not callable(enqueue):
       state="waiting_engine";detail="Обработчик юридической проверки недоступен"
      else:
       result=enqueue(reg)
       state="submitted";detail=str(result.get("status","queued"))[:150]
    except Exception as e:
     state="retry";detail=type(e).__name__
    c.execute("UPDATE thematic_items SET status=?,detail=?,updated_at=? WHERE batch_id=? AND reg=?",(state,detail,now(),b["id"],reg))
   # Mark complete only after the EIS original-review worker confirms completion.
   from v295_eis_originals import root
   submitted=c.execute("SELECT reg FROM thematic_items WHERE batch_id=? AND status=? LIMIT 500",(b["id"],"submitted")).fetchall()
   for item in submitted:
    reg=item["reg"]
    try:
     receipt=root()/"eis223_official_review_jobs"/(reg+".json")
     review=json.loads(receipt.read_text(encoding="utf-8"))
     if review.get("reg_number")==reg and review.get("status")=="completed":
      c.execute("UPDATE thematic_items SET status=?,detail=?,updated_at=? WHERE batch_id=? AND reg=?",("completed","Проверка оригиналов завершена",now(),b["id"],reg))
    except (OSError,ValueError,TypeError):
     pass
   # Revisit items after EIS originals arrive, without counting them as reviewed.
   c.execute("UPDATE thematic_items SET status='queued' WHERE batch_id=? AND status IN ('waiting_originals','waiting_engine','retry') AND updated_at<?",(b["id"],datetime.fromtimestamp(time.time()-600,timezone.utc).isoformat()))
  c.commit()
 finally:c.close()
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
  matches=[]
  try:
   columns={x[1] for x in c.execute("PRAGMA table_info(findings)")}
   if not {"reg_number","title","kind"}.issubset(columns):
    raise sqlite3.OperationalError("findings schema missing")
   law="f.law" if "law" in columns else "'' AS law"
   evidence="f.evidence" if "evidence" in columns else "'' AS evidence"
   query="SELECT f.reg_number,f.title,"+law+","+evidence+" FROM findings f JOIN thematic_items t ON t.reg=f.reg_number WHERE t.batch_id=? AND t.status='completed' AND f.kind='violation' LIMIT 2000"
   rows=c.execute(query,(batch,)).fetchall()
   for f in rows:
    text=" ".join(str(f[k] or "") for k in ("title","law"))
    groups=[k for k in cats if re.search(patterns[k],text,re.I)]
    if groups:matches.append({"reg":f["reg_number"],"title":f["title"],"categories":groups,"evidence":str(f["evidence"] or "")[:350]})
  except sqlite3.OperationalError:
   pass
  try:
   from v301_search_jobs import _bridge
   bridge=_bridge(ns)
  except Exception:
   bridge={"active":False,"queued":None}
  return {"bridge":bridge,"id":batch,"categories":cats,"status":b["status"],"counts":counts,"confirmed_matching_findings":matches[:100],"note":"Результаты отобраны по категориям из подтверждённых нарушений. Полный юридический анализ выполняет основной движок."}
 finally:c.close()
PAGE="""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Тематическая проверка ЕИС</title><style>body{font:16px system-ui;background:#f2f6fa;color:#193d5c;margin:0}main{max-width:850px;margin:30px auto;background:white;padding:25px;border-radius:16px}label{display:block;padding:10px;border-bottom:1px solid #eee}button{background:#2466a3;color:white;padding:13px;border:0;border-radius:8px;cursor:pointer}a{color:#2466a3}#out{white-space:pre-wrap}</style><main><a href="/">← Главная</a><h1>Тематическая проверка</h1><p>Выберите категории. Закупки из ЕИС поступают в отдельную очередь. Нарушения подтверждаются только оригиналами и юридическими правилами.</p><div id="cats"></div><p><button id="start">Начать проверку</button></p><h2>Очередь</h2><div id="out">Выберите категории</div></main><script>
const categories=__CATEGORIES__;const box=document.querySelector('#cats');
for(const [id,title] of Object.entries(categories)){let l=document.createElement('label'),i=document.createElement('input');i.type='checkbox';i.value=id;l.append(i,document.createTextNode(' '+title));box.append(l)}
document.querySelector('#start').onclick=async()=>{const cats=[...box.querySelectorAll('input:checked')].map(x=>x.value);if(!cats.length)return alert('Выберите категорию');const r=await fetch('/api/thematic/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({categories:cats})});const j=await r.json();if(!r.ok)return alert(j.detail||r.status);location.hash=j.id;refresh()};
async function refresh(){if(!location.hash)return;let r=await fetch('/api/thematic/'+encodeURIComponent(location.hash.slice(1)));let j=await r.json();const out=document.querySelector('#out');out.replaceChildren();if(!r.ok){out.textContent=j.detail||'Ошибка получения очереди';return}const names={queued:'В очереди',submitted:'Передано на проверку',completed:'Проверено',waiting_originals:'Ожидаются оригиналы ЕИС',waiting_engine:'Ожидается обработчик',retry:'Повторная попытка'};const heading=document.createElement('p');heading.textContent='Статус: '+(j.status==='active'?'Работает':'Остановлена');out.append(heading);const bridge=document.createElement('p');bridge.textContent='Мост ЕИС: '+(j.bridge?.active?'подключён':'нет активного загрузчика')+'; очередь запросов: '+(j.bridge?.queued??'неизвестно');out.append(bridge);for(const [key,num] of Object.entries(j.counts||{})){const line=document.createElement('p');line.textContent=(names[key]||key)+': '+num;out.append(line)}const stop=document.createElement('button');stop.textContent='Остановить проверку';stop.disabled=j.status!=='active';stop.onclick=async()=>{if(!confirm('Остановить тематическую проверку?'))return;await fetch('/api/thematic/'+encodeURIComponent(j.id)+'/stop',{method:'POST'});refresh()};out.append(stop);const h=document.createElement('h3');h.textContent='Подтверждённые нарушения по выбранным категориям';out.append(h);const findings=j.confirmed_matching_findings||[];if(!findings.length){const p=document.createElement('p');p.textContent='Подтверждённых нарушений пока нет';out.append(p)}for(const f of findings){const item=document.createElement('article');item.style.cssText='border-left:4px solid #b91c1c;background:#fff5f5;margin:12px 0;padding:12px';const strong=document.createElement('strong');strong.textContent=f.reg+' — '+f.title;item.append(strong);const detail=document.createElement('p');detail.textContent=f.evidence||'';item.append(detail);out.append(item)}}
setInterval(refresh,12000);refresh();</script></html>""".replace("__CATEGORIES__",json.dumps(CATEGORIES,ensure_ascii=False))
def install(ns):
 init(ns);app=ns["app"]
 @app.get("/thematic")
 async def page():return HTMLResponse(PAGE)
 @app.post("/api/thematic/start")
 async def start(request:Request):
  origin=request.headers.get("origin")
  if origin and not origin.startswith(("https://web-production-1c2ea.up.railway.app","http://localhost:")):raise HTTPException(403,"Неверный источник")
  raw=await request.body()
  if len(raw)>2000:raise HTTPException(413,"Слишком большой запрос")
  try:cats=json.loads(raw)["categories"]
  except (ValueError,KeyError,TypeError):raise HTTPException(422,"Некорректный запрос")
  if not isinstance(cats,list) or not cats or len(cats)>len(CATEGORIES) or any(x not in CATEGORIES for x in cats):raise HTTPException(422,"Некорректные категории")
  identifier=secrets.token_hex(12);c=db(ns)
  try:
   if c.execute("SELECT COUNT(*) FROM thematic_batches WHERE status=?",("active",)).fetchone()[0]>=3:
    raise HTTPException(429,"Одновременно допускается не более трёх тематических очередей")
   c.execute("INSERT INTO thematic_batches(id,categories,created_at) VALUES(?,?,?)",(identifier,json.dumps(sorted(set(cats))),now()));c.commit()
  finally:c.close()
  return {"id":identifier,"status":"active"}
 @app.post("/api/thematic/{batch}/stop")
 async def stop(batch:str):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(400,"Некорректный номер")
  c=db(ns)
  try:
   c.execute("UPDATE thematic_batches SET status=? WHERE id=?",("stopped",batch));c.commit()
  finally:c.close()
  return {"id":batch,"status":"stopped"}
 @app.get("/api/thematic/{batch}")
 async def status(batch:str):
  if not re.fullmatch("[0-9a-f]{24}",batch):raise HTTPException(400,"Некорректный номер")
  return await asyncio.to_thread(overview,ns,batch)
 class ThemeNav(BaseHTTPMiddleware):
  async def dispatch(self,request,call_next):
   response=await call_next(request)
   if request.url.path!="/" or response.status_code!=200 or "text/html" not in response.headers.get("content-type",""):return response
   if response.headers.get("content-encoding"):return response
   raw=b"".join([chunk async for chunk in response.body_iterator])
   html=raw.decode("utf-8",errors="replace")
   html=html.replace("</head>","<style id=\"thematic-row-colors\">table{background:#fff!important}table thead,table thead tr,table thead th,table tbody,table tbody tr,table tbody td,table tbody tr:nth-child(odd),table tbody tr:nth-child(even),table tbody tr:hover,table tbody tr:has(td .violation),table tbody tr:has(td [class*=\"violation\"]),table tbody tr:has(td .check),table tbody tr:has(td [class*=\"review\"]){background:#fff!important;background-color:#fff!important;border-left:0!important}table tbody tr td:first-child{border-left:0!important}table{border-collapse:collapse!important;border-spacing:0!important}table tbody td{border-bottom:1px solid #dce5ed!important}</style></head>",1)
   html=html.replace("</body>",'<a href="/thematic" style="position:fixed;bottom:16px;right:16px;background:#2466a3;color:white;padding:14px;border-radius:10px;z-index:9999">Тематическая проверка</a><script>document.addEventListener("DOMContentLoaded",()=>{const t=document.querySelector("table");if(t){const hs=[...t.querySelectorAll("thead th")];const idxs=hs.map((h,i)=>/^(?:Стоимость закупки|НМЦД|Результат)$/i.test(h.textContent.trim())?i:-1).filter(i=>i>=0).reverse();for(const idx of idxs){hs[idx].remove();t.querySelectorAll("tbody tr").forEach(tr=>tr.children[idx]?.remove())}}document.querySelectorAll("#min,#max,#price,[name=price_band]").forEach(e=>{e.style.display="none";e.disabled=true});})</script></body>',1)
   return HTMLResponse(html,headers={k:v for k,v in response.headers.items() if k.lower() not in ("content-length","content-encoding")})
 app.add_middleware(ThemeNav)
 async def worker():
  while True:
   try:await tick(ns)
   except Exception as e:print("THEMATIC_TICK_ERROR",type(e).__name__,str(e)[:120],flush=True)
   await asyncio.sleep(120)
 @app.on_event("startup")
 async def startup_thematic():asyncio.create_task(worker())
 print("THEMATIC_QUEUE_READY",flush=True)
