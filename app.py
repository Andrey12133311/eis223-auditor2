import os,re,json,sqlite3,asyncio,io,html,zipfile,subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,urljoin,unquote
import httpx
from fastapi import FastAPI,HTTPException,Query
from fastapi.responses import HTMLResponse,FileResponse
try:
 import fitz
except Exception: fitz=None
try:
 from docx import Document
 from docx.enum.text import WD_COLOR_INDEX
except Exception: Document=None;WD_COLOR_INDEX=None
try:
 import openpyxl
except Exception: openpyxl=None
try:
 import pytesseract
 from PIL import Image
except Exception: pytesseract=None;Image=None
try:
 import holidays
except Exception: holidays=None

APP_VERSION='1.1.0'
BASE=os.getenv('GOSPLAN_BASE','https://v2test.gosplan.info').rstrip('/')
API_KEY=os.getenv('GOSPLAN_API_KEY','').strip(); API_HEADER=os.getenv('GOSPLAN_API_HEADER','X-API-Key')
DATA=Path(os.getenv('EIS223_DATA_DIR','/data')); DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'eis223.db'; FILES=DATA/'files'; REPORTS=DATA/'reports'; FILES.mkdir(exist_ok=True); REPORTS.mkdir(exist_ok=True)
SCAN_INTERVAL=max(300,int(os.getenv('SCAN_INTERVAL_SECONDS','300')))
DETAIL_LIMIT=max(1,min(10,int(os.getenv('GOSPLAN_DETAILS_PER_CYCLE','3'))))
PAGE_SIZE=100
LAW32='https://www.consultant.ru/document/cons_doc_LAW_116964/05cd0add21b39d478c8b8af91fb2f2cd80d4a6e8/'
LAW4='https://www.consultant.ru/document/cons_doc_LAW_116964/441d00be62e3224cdc0514cffaf2a26b5b40a1c7/'
LAW3='https://www.consultant.ru/document/cons_doc_LAW_116964/fddec0f5c16a67f6fca41f9e31dfb0dcc72cc49a/'
app=FastAPI(title='ЕИС 223-ФЗ Аудитор',version=APP_VERSION)
state={'running':False,'last_start':None,'last_finish':None,'last_error':None,'last_result':None,'backfill_skip':500}
lock=asyncio.Lock()

ORG=[('ПАО',r'\bПАО\b|публичн\w+\s+акционерн'),('НАО',r'\bНАО\b|непубличн\w+\s+акционерн'),('АО',r'\bАО\b|акционерн\w+\s+обществ'),('ООО',r'\bООО\b|ограниченн\w+\s+ответственност'),('ФГУП',r'\bФГУП\b|федеральн\w+\s+государственн\w+\s+унитарн'),('ГУП',r'\bГУП\b|государственн\w+\s+унитарн'),('МУП',r'\bМУП\b|муниципальн\w+\s+унитарн'),('ФГБУ',r'\bФГБУ\b'),('ГБУ',r'\bГБУ\b'),('МБУ',r'\bМБУ\b'),('ФГАУ',r'\bФГАУ\b'),('ГАУ',r'\bГАУ\b'),('МАУ',r'\bМАУ\b'),('АНО',r'\bАНО\b'),('ГК',r'\bГК\b|государственн\w+\s+корпорац'),('ФКП',r'\bФКП\b')]

def now(): return datetime.now(timezone.utc).isoformat()
def clean(x): return re.sub(r'\s+',' ',str(x or '')).strip()
def org_form(s):
 for n,p in ORG:
  if re.search(p,s or '',re.I): return n
 return 'Другая'
def doc_type(name,desc=''):
 s=(str(name)+' '+str(desc)).lower().replace('ё','е')
 if 'запрос' in s and 'разъяснен' in s:return 'clarification_request'
 if 'разъяснен' in s or 'ответ на запрос' in s:return 'clarification'
 if 'отмен' in s or 'отказ от провед' in s:return 'cancellation'
 if 'протокол' in s:return 'final_protocol' if any(x in s for x in ('итог','подведен','результат')) else 'protocol'
 if 'извещен' in s:return 'notice'
 if 'документац' in s:return 'documentation'
 return 'other'
def conn():
 c=sqlite3.connect(DB,timeout=30,check_same_thread=False);c.row_factory=sqlite3.Row;return c
def init_db():
 c=conn();c.executescript('''
 CREATE TABLE IF NOT EXISTS purchases(reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,org_form TEXT,method TEXT,price TEXT,published_at TEXT,deadline TEXT,url TEXT,source TEXT,last_seen TEXT,clarifications INTEGER DEFAULT 0,protocols INTEGER DEFAULT 0,cancellations INTEGER DEFAULT 0,violations INTEGER DEFAULT 0,risks INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY AUTOINCREMENT,reg_number TEXT,name TEXT,url TEXT,doc_type TEXT,published_at TEXT,local_path TEXT,annotated_path TEXT,status TEXT,error TEXT,metadata_json TEXT);
 CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY AUTOINCREMENT,reg_number TEXT,code TEXT,kind TEXT,title TEXT,law TEXT,law_url TEXT,evidence TEXT,note TEXT,document TEXT,source_url TEXT);
 CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
 CREATE INDEX IF NOT EXISTS idx_org ON purchases(org_form);CREATE INDEX IF NOT EXISTS idx_find_reg ON findings(reg_number);CREATE INDEX IF NOT EXISTS idx_docs_reg ON docs(reg_number);
 ''');c.commit();c.close()
init_db()

def migrate_db():
 c=conn(); cols={r['name'] for r in c.execute('PRAGMA table_info(purchases)')}
 for name in ('submission_start','submission_end'):
  if name not in cols:c.execute(f'ALTER TABLE purchases ADD COLUMN {name} TEXT')
 c.commit();c.close()
migrate_db()

def walk(x):
 if isinstance(x,dict):
  yield x
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
def getv(obj,*keys):
 ks={k.lower() for k in keys}
 for d in walk(obj):
  for k,v in d.items():
   if str(k).lower() in ks and v not in (None,'',[]):return v
 return None
def txt(v):
 if v is None:return ''
 if isinstance(v,(str,int,float)):return clean(v)
 if isinstance(v,dict):
  for k in ('fullName','name','shortName','organizationName','customerName','value','text','title'):
   if v.get(k):return txt(v[k])
 if isinstance(v,list):
  for x in v:
   q=txt(x)
   if q:return q
 return ''
def regnum(obj):
 s=txt(getv(obj,'purchaseNumber','purchase_number','registryNumber','regNumber','noticeNumber','number'))
 m=re.search(r'\d{10,}',s);return m.group(0) if m else s
def eis_url(reg):return f'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber={reg}'
def dt(v):
 if not v:return None
 s=str(v).strip()
 for z in (s,s.replace('Z','+00:00')):
  try:return datetime.fromisoformat(z)
  except:pass
 for f in ('%d.%m.%Y %H:%M','%d.%m.%Y','%Y-%m-%d %H:%M:%S'):
  try:return datetime.strptime(s[:19],f)
  except:pass
 return None
def wdays(a,b):
 if not a or not b or b<=a:return 0
 hs=None
 if holidays:
  try:hs=holidays.RU(years=range(a.year,b.year+1))
  except:pass
 n=0;d=a.date()+timedelta(days=1)
 while d<=b.date():
  if d.weekday()<5 and (hs is None or d not in hs):n+=1
  d+=timedelta(days=1)
 return n

def headers():
 h={'User-Agent':'EIS223-Auditor/1.0','Accept':'application/json'}
 if API_KEY:h[API_HEADER]=API_KEY
 return h
async def jget(client,path,params=None):
 last=None
 for _ in range(3):
  r=await client.get(BASE+path,params=params,headers=headers())
  if r.status_code!=429:r.raise_for_status();return r.json()
  last=r;wait=60
  for k in ('retry-after','ratelimit-reset'):
   try:
    if r.headers.get(k):wait=max(2,min(180,int(float(r.headers[k]))+2));break
   except:pass
  await asyncio.sleep(wait)
 if last:last.raise_for_status()

def extract_docs(detail):
 out=[];seen=set();ext=re.compile(r'\.(pdf|docx?|xlsx?|xlsm|rtf|txt|zip)(?:$|[?#])',re.I)
 for d in walk(detail):
  name=txt(d.get('fileName') or d.get('filename') or d.get('documentName') or d.get('docName') or d.get('name'))
  desc=txt(d.get('description') or d.get('documentType') or d.get('typeName') or d.get('title'))
  url=txt(d.get('url') or d.get('downloadUrl') or d.get('fileUrl') or d.get('href') or d.get('link'))
  label=(name+' '+desc).strip()
  if not (url or name):continue
  if not (ext.search(url) or ext.search(name) or any(x in label.lower() for x in ('протокол','разъяснен','отмен','документац','извещен','запрос'))):continue
  key=(name,url)
  if key in seen:continue
  seen.add(key);meta={}
  for k,v in d.items():
   lk=str(k).lower()
   if any(x in lk for x in ('date','time','publish','sign','decision','request','create')) and not isinstance(v,(dict,list)):meta[str(k)]=v
  out.append({'name':name or desc or 'Документ','desc':desc,'url':url,'type':doc_type(name,desc),'published':txt(d.get('publishDate') or d.get('publicationDate') or d.get('publishedAt') or d.get('createDate')),'meta':meta,'text':'','local':None,'annotated':None,'status':'metadata','error':None})
 return out[:150]

def submission_dates(detail):
 start=txt(getv(detail,'submissionStartDate','applicationStartDate','submissionOpenDate','applicationOpenDate'))
 end=txt(getv(detail,'submissionCloseDate','submissionEndDate','applicationEndDate','submissionDeadline','applicationDeadline'))
 return start,end

def safe_host(url):
 try:
  p=urlparse(url);h=(p.hostname or '').lower()
  return p.scheme in ('http','https') and any(h==x or h.endswith('.'+x) for x in ('zakupki.gov.ru','gosplan.info','roskazna.gov.ru'))
 except:return False
def safe_name(s):return re.sub(r'[^0-9A-Za-zА-Яа-яЁё._-]+','_',s or 'file')[:160]
async def download_doc(client,reg,d):
 if not d['url'] or not safe_host(d['url']):return
 try:
  async with client.stream('GET',d['url'],headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36','Accept':'application/pdf,application/zip,application/octet-stream,application/msword,application/vnd.openxmlformats-officedocument.*,*/*;q=0.8','Accept-Language':'ru-RU,ru;q=0.9,en;q=0.5','Referer':eis_url(reg),'Cache-Control':'no-cache'},follow_redirects=True) as r:
   r.raise_for_status();data=bytearray()
   async for ch in r.aiter_bytes():
    data.extend(ch)
    if len(data)>30*1024*1024:raise ValueError('Файл больше 30 МБ')
  name=safe_name(d['name']);ct=r.headers.get('content-type','').lower()
  if not Path(name).suffix:
   name+= '.pdf' if 'pdf' in ct else '.docx' if 'word' in ct else '.xlsx' if ('sheet' in ct or 'excel' in ct) else '.bin'
  folder=FILES/reg/'original';folder.mkdir(parents=True,exist_ok=True);p=folder/name;p.write_bytes(data);d['local']=str(p);d['text']=extract_text(p);d['status']='checked' if len(clean(d['text']))>=3 else 'downloaded_no_text';d['error']=None if d['status']=='checked' else 'Оригинал скачан, но содержимое извлечь не удалось'
 except Exception as e:d['status']='error';d['error']=str(e)[:400]
def extract_text(p):
 p=Path(p);ext=p.suffix.lower()
 try:
  if ext=='.pdf' and fitz:
   doc=fitz.open(p);parts=[]
   for pg in doc:
    t=pg.get_text('text') or ''
    if len(t.strip())<30 and pytesseract and Image:
     try:
      pix=pg.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False);t=pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes('png'))),lang='rus+eng')
     except:pass
    parts.append(t)
   doc.close();return '\n'.join(parts)
  if ext=='.docx' and Document:
   d=Document(p);return '\n'.join([x.text for x in d.paragraphs]+[' | '.join(c.text for c in row.cells) for t in d.tables for row in t.rows])
  if ext in ('.xlsx','.xlsm') and openpyxl:
   wb=openpyxl.load_workbook(p,read_only=True,data_only=True);out=[]
   for ws in wb.worksheets:
    for row in ws.iter_rows(values_only=True):
     vals=[str(v) for v in row if v is not None]
     if vals:out.append(' | '.join(vals))
   return '\n'.join(out)
  if ext in ('.txt','.rtf','.csv'):return p.read_text(errors='ignore')
 except:return ''
 return ''
def mdate(d,*need):
 for k,v in d.get('meta',{}).items():
  lk=k.lower()
  if all(n in lk for n in need):
   z=dt(v)
   if z:return z
 return dt(d.get('published')) if ('publish' in need or 'publication' in need) else None
def finding(code,kind,title,law,law_url,evidence,note,doc=None):return {'code':code,'kind':kind,'title':title,'law':law,'law_url':law_url,'evidence':clean(evidence)[:1400],'note':note,'document':doc['name'] if doc else None,'source_url':doc['url'] if doc else None}

def analyze(p,docs):
 out=[];deadline=dt(p['deadline']);reqs=[d for d in docs if d['type']=='clarification_request'];answers=[d for d in docs if d['type']=='clarification']
 for rq in reqs:
  rd=mdate(rq,'request') or mdate(rq,'create') or mdate(rq,'publish')
  if not rd or (deadline and wdays(rd,deadline)<3):continue
  later=[]
  for a in answers:
   ad=mdate(a,'publish') or mdate(a,'create')
   if ad and ad>=rd:later.append((ad,a))
  if not later:out.append(finding('CLAR-NO-ANSWER','check','Не найдено разъяснение на своевременный запрос','223-ФЗ, ст. 3.2 ч. 2–3',LAW32,f'Запрос: {rd.isoformat()}; ответа среди доступных документов не найдено.','Нужна проверка полного состава сведений ЕИС.',rq));continue
  ad,a=min(later,key=lambda x:x[0]);wd=wdays(rd,ad)
  if wd>3:out.append(finding('CLAR-LATE','violation','Разъяснение опубликовано позднее трёх рабочих дней','223-ФЗ, ст. 3.2 ч. 3',LAW32,f'Запрос: {rd.isoformat()}; разъяснение: {ad.isoformat()}; рабочих дней: {wd}.','Срок — три рабочих дня для своевременного запроса.',a))
 for a in answers:
  m=re.search(r'(?:измен|замен)[^.\n]{0,120}(?:предмет\w*\s+закупк|существенн\w*\s+услов)',a.get('text',''),re.I)
  if m:out.append(finding('CLAR-CHANGES','risk','Разъяснение может менять предмет или существенные условия','223-ФЗ, ст. 3.2 ч. 4',LAW32,a['text'][max(0,m.start()-120):m.end()+180],'Требуется сверка с исходной редакцией.',a))
 for d in [x for x in docs if x['type']=='cancellation']:
  dec=mdate(d,'decision') or mdate(d,'sign');pub=mdate(d,'publish')
  if dec and pub and dec.date()!=pub.date():out.append(finding('CANCEL-LATE','violation','Решение об отмене размещено не в день принятия','223-ФЗ, ст. 3.2 ч. 6',LAW32,f'Решение: {dec.date()}; размещение: {pub.date()}.','Решение об отмене размещается в ЕИС в день принятия.',d))
  if dec and deadline and dec>deadline:out.append(finding('CANCEL-AFTER-DEADLINE','check','Отмена после окончания подачи заявок требует проверки','223-ФЗ, ст. 3.2 ч. 5–7',LAW32,f'Окончание подачи: {deadline}; решение: {dec}.','После срока до заключения договора требуется наличие обстоятельств непреодолимой силы.',d))
 for d in [x for x in docs if x['type'] in ('protocol','final_protocol')]:
  sign=mdate(d,'sign');pub=mdate(d,'publish')
  if sign and pub and (pub.date()-sign.date()).days>3:out.append(finding('PROTOCOL-LATE','violation','Протокол размещён позднее трёх дней со дня подписания','223-ФЗ, ст. 4 ч. 12',LAW4,f'Подписание: {sign.date()}; размещение: {pub.date()}.','Протокол должен быть размещён не позднее трёх дней со дня подписания.',d))
  t=d.get('text','')
  if len(re.sub(r'\s+','',t))>=250:
   checks=[('дата подписания',r'дата\s+(?:подписан|составлен)|\b\d{2}\.\d{2}\.\d{4}\b'),('количество заявок',r'количеств\w+\s+(?:поданн\w+\s+)?заяв|подано\s+\d+\s+заяв'),('дата/время регистрации',r'регистрац\w+.*(?:дата|время)|дата\s+и\s+время\s+регистрац')]
   if d['type']=='final_protocol':checks += [('ранжирование/победитель',r'порядков\w+\s+номер|победител|место\s+\d'),('ценовые предложения',r'ценов\w+\s+предлож|цена\s+(?:договора|заявки)|руб'),('результаты рассмотрения',r'результат\w+\s+рассмотрен|допущ|отклон')]
   for label,pat in checks:
    if not re.search(pat,t,re.I|re.S):out.append(finding('PROTOCOL-FIELD','violation','В протоколе не найдено обязательное сведение: '+label,'223-ФЗ, ст. 3.2 ч. 13–14',LAW32,f'В файле «{d["name"]}» не найдено: {label}.','Возможна ошибка OCR или нестандартная формулировка; проверьте оригинал.',d))
 # generic competition risks from text
 for d in docs:
  t=d.get('text','')
  for code,pat,title in [('DEALER',r'авторизационн\w*\s+письм|статус\s+(?:официального\s+)?дилера','Требование связи с производителем/дилерского статуса'),('LOCALITY',r'(?:офис|склад|сервисн\w*\s+центр)[^.\n]{0,120}(?:в\s+городе|в\s+регионе|на\s+территории)','Территориальное требование к участнику')]:
   m=re.search(pat,t,re.I)
   if m:out.append(finding(code,'risk',title,'223-ФЗ, ст. 3 ч. 1',LAW3,t[max(0,m.start()-150):m.end()+200],'Индикатор требует юридической оценки в контексте предмета закупки.',d))
 return out

def annotate(reg,docs,finds):
 for d in docs:
  if not d.get('local'):continue
  relevant=[f for f in finds if f.get('document')==d['name'] and f.get('evidence')]
  if not relevant:continue
  p=Path(d['local']);folder=FILES/reg/'annotated';folder.mkdir(parents=True,exist_ok=True);dst=folder/(p.stem+'__ПРОВЕРЕНО'+p.suffix)
  try:
   if p.suffix.lower()=='.pdf' and fitz:
    doc=fitz.open(p);hits=0
    for f in relevant:
     seed=clean(f['evidence'])[:70]
     if len(seed)<8:continue
     for pg in doc:
      rs=pg.search_for(seed)
      for r in rs[:3]:pg.add_highlight_annot(r);hits+=1
      if rs:break
    if hits:doc.save(dst);d['annotated']=str(dst)
    doc.close()
   elif p.suffix.lower()=='.docx' and Document:
    doc=Document(p);hits=0
    for par in doc.paragraphs:
     norm=clean(par.text)
     if any(clean(f['evidence'])[:50].lower() in norm.lower() for f in relevant if len(clean(f['evidence']))>=10):
      for r in par.runs:r.font.highlight_color=WD_COLOR_INDEX.YELLOW
      hits+=1
    if hits:doc.save(dst);d['annotated']=str(dst)
  except:pass

def save(p,docs,finds):
 c=conn();c.execute('''INSERT INTO purchases(reg_number,title,customer,org_form,method,price,published_at,deadline,url,source,last_seen,clarifications,protocols,cancellations,violations,risks) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(reg_number) DO UPDATE SET title=excluded.title,customer=excluded.customer,org_form=excluded.org_form,method=excluded.method,price=excluded.price,published_at=excluded.published_at,deadline=excluded.deadline,url=excluded.url,source=excluded.source,last_seen=excluded.last_seen,clarifications=excluded.clarifications,protocols=excluded.protocols,cancellations=excluded.cancellations,violations=excluded.violations,risks=excluded.risks''',(p['reg'],p['title'],p['customer'],org_form(p['customer']),p['method'],p['price'],p['published'],p['deadline'],p['url'],'gosplan-eis',now(),sum(d['type'] in ('clarification','clarification_request') for d in docs),sum(d['type'] in ('protocol','final_protocol') for d in docs),sum(d['type']=='cancellation' for d in docs),sum(f['kind']=='violation' for f in finds),sum(f['kind']=='risk' for f in finds)))
 c.execute('DELETE FROM docs WHERE reg_number=?',(p['reg'],));c.execute('DELETE FROM findings WHERE reg_number=?',(p['reg'],))
 for d in docs:c.execute('INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,annotated_path,status,error,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(p['reg'],d['name'],d['url'],d['type'],d['published'],d['local'],d['annotated'],d['status'],d['error'],json.dumps(d['meta'],ensure_ascii=False,default=str)))
 for f in finds:c.execute('INSERT INTO findings(reg_number,code,kind,title,law,law_url,evidence,note,document,source_url) VALUES(?,?,?,?,?,?,?,?,?,?)',(p['reg'],f['code'],f['kind'],f['title'],f['law'],f['law_url'],f['evidence'],f['note'],f['document'],f['source_url']))
 c.execute('UPDATE purchases SET submission_start=?,submission_end=?,deadline=? WHERE reg_number=?',(p.get('submission_start'),p.get('submission_end'),p.get('submission_end') or p.get('deadline'),p['reg']))
 c.commit();c.close();render_report(p,docs,finds)
def render_report(p,docs,finds):
 rows=''.join(f"<tr><td>{html.escape(d['type'])}</td><td>{html.escape(d['name'])}</td><td>{html.escape(d['status'])}</td><td>{'<a href='+repr(d['url'])+'>оригинал</a>' if d['url'] else '—'}</td></tr>" for d in docs)
 blocks=''.join(f"<article><h3>{html.escape(f['title'])}</h3><p><a href='{html.escape(f['law_url'])}'>{html.escape(f['law'])}</a> · {html.escape(f['kind'])}</p><pre>{html.escape(f['evidence'])}</pre><p>{html.escape(f['note'])}</p></article>" for f in finds)
 (REPORTS/(p['reg']+'.html')).write_text(f"<!doctype html><meta charset=utf-8><style>body{{font-family:Arial;max-width:1100px;margin:30px}}article{{border-left:5px solid #b33;padding:12px;margin:12px 0}}pre{{white-space:pre-wrap;background:#f5f5f5;padding:10px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd}}</style><h1>Закупка {p['reg']}</h1><p>{html.escape(p['customer'])}</p><a href='{p['url']}'>ЕИС</a><h2>Нарушения и риски</h2>{blocks or '<p>Не выявлены</p>'}<h2>Вложения</h2><table>{rows}</table>",encoding='utf-8')

async def cycle():
 if lock.locked():return
 async with lock:
  state.update(running=True,last_start=now(),last_error=None)
  try:
   async with httpx.AsyncClient(timeout=httpx.Timeout(25,connect=10),follow_redirects=True) as cl:
    fresh=await jget(cl,'/fz223/purchases',{'limit':100,'skip':0});fresh=fresh if isinstance(fresh,list) else fresh.get('items',fresh.get('data',[]))
    back=await jget(cl,'/fz223/purchases',{'limit':100,'skip':state['backfill_skip']});back=back if isinstance(back,list) else back.get('items',back.get('data',[]));state['backfill_skip']+=100
    todo=[];seen=set()
    for x in fresh+back:
     r=regnum(x)
     if r and r not in seen:seen.add(r);todo.append((r,x))
    checked=0;docn=0
    for r,basic in todo[:DETAIL_LIMIT]:
     try:detail=await jget(cl,f'/fz223/purchases/{r}')
     except Exception:detail=basic
     submission_start,submission_end=submission_dates(detail)
     p={'reg':r,'title':txt(getv(detail,'purchaseName','purchaseObjectInfo','name','title','subject')),'customer':txt(getv(detail,'customer','customerInfo','customerName','organization','placer')),'method':txt(getv(detail,'purchaseMethodName','purchaseMethod','method','placingWayName')),'price':txt(getv(detail,'initialSum','initialPrice','maxPrice','price','lotPrice')),'published':txt(getv(detail,'publicationDate','publishDate','createDate','publishedAt')),'submission_start':submission_start,'submission_end':submission_end,'deadline':submission_end,'url':eis_url(r)}
     docs=extract_docs(detail)
     for d in docs[:10]:await download_doc(cl,r,d)
     finds=analyze(p,docs);annotate(r,docs,finds);save(p,docs,finds);checked+=1;docn+=len(docs)
    state['last_result']={'latest':len(fresh),'backfill':len(back),'checked':checked,'documents':docn};state['last_finish']=now()
  except Exception as e:state['last_error']=repr(e);state['last_finish']=now()
  finally:state['running']=False
async def loop():
 await asyncio.sleep(2)
 while True:await cycle();await asyncio.sleep(SCAN_INTERVAL)
@app.on_event('startup')
async def start():asyncio.create_task(loop())
@app.get('/health')
async def health():return {'status':'ok','version':APP_VERSION,'features':['auto-scan','attachments','clarifications','protocols','cancellations','org-form-filter','marked-documents']}
@app.get('/api/status')
async def status():return state
@app.post('/api/scan-now')
async def scan_now():
 if lock.locked():return {'started':False,'message':'Уже выполняется'}
 asyncio.create_task(cycle());return {'started':True}
@app.get('/api/org-forms')
async def forms():
 c=conn();x=[dict(r) for r in c.execute("SELECT org_form,COUNT(*) count FROM purchases GROUP BY org_form ORDER BY count DESC")];c.close();return {'items':x}
@app.get('/api/procurements')
@app.get('/api/dashboard')
async def procurements(org_form_:str|None=Query(None,alias='org_form'),result_kind:str|None=None,q:str|None=None,limit:int=200):
 c=conn();where=[];args=[]
 if org_form_:where.append('org_form=?');args.append(org_form_)
 if result_kind=='violations':where.append('violations>0')
 elif result_kind=='risks':where.append('risks>0')
 elif result_kind=='clarifications':where.append('clarifications>0')
 elif result_kind=='protocols':where.append('protocols>0')
 elif result_kind=='cancellations':where.append('cancellations>0')
 if q:where.append('(reg_number LIKE ? OR customer LIKE ? OR title LIKE ?)');z='%'+q+'%';args += [z,z,z]
 sql='SELECT * FROM purchases'+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY last_seen DESC LIMIT ?';args.append(min(500,max(1,limit)))
 rows=[dict(r) for r in c.execute(sql,args)];c.close();stats={'purchases':len(rows),'violations':sum(r['violations'] or 0 for r in rows),'risks':sum(r['risks'] or 0 for r in rows),'protocols':sum(r['protocols'] or 0 for r in rows),'clarifications':sum(r['clarifications'] or 0 for r in rows),'cancellations':sum(r['cancellations'] or 0 for r in rows)};return {'stats':stats,'purchases':rows}
@app.get('/api/purchases/{reg}')
async def detail(reg:str):
 if not re.fullmatch(r'[0-9A-Za-z._-]{1,120}',reg):raise HTTPException(400,'Некорректный номер')
 c=conn();p=c.execute('SELECT * FROM purchases WHERE reg_number=?',(reg,)).fetchone()
 if not p:c.close();raise HTTPException(404,'Не найдено')
 docs=[dict(r) for r in c.execute('SELECT * FROM docs WHERE reg_number=?',(reg,))];finds=[dict(r) for r in c.execute('SELECT * FROM findings WHERE reg_number=? ORDER BY CASE kind WHEN "violation" THEN 1 WHEN "risk" THEN 2 ELSE 3 END,id',(reg,))];c.close();return {'purchase':dict(p),'documents':docs,'findings':finds,'report_url':f'/reports/{reg}.html' if (REPORTS/(reg+'.html')).exists() else None}
@app.get('/reports/{name}')
async def report(name:str):
 if not re.fullmatch(r'[0-9A-Za-z._-]+\.html',name):raise HTTPException(400)
 p=(REPORTS/name).resolve()
 if p.parent!=REPORTS.resolve() or not p.exists():raise HTTPException(404)
 return FileResponse(p)
@app.get('/files/{reg}/{folder}/{name}')
async def file(reg:str,folder:str,name:str):
 if folder not in ('original','annotated') or Path(name).name!=name:raise HTTPException(400)
 root=(FILES/reg/folder).resolve();p=(root/name).resolve()
 if p.parent!=root or not p.exists():raise HTTPException(404)
 return FileResponse(p)

HTML='''<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>ЕИС 223-ФЗ Аудитор</title><style>body{font-family:system-ui;margin:0;background:#f5f3ed;color:#18201b}header{padding:22px 5%;background:#19211c;color:white}main{padding:32px 5%;max-width:1400px;margin:auto}.hero{display:flex;justify-content:space-between;gap:30px;align-items:end}.hero h1{font:52px Georgia;margin:0}.status{background:white;padding:18px;border:1px solid #ddd}.stats,.filters{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}.card{background:white;padding:14px 18px;border:1px solid #ddd;min-width:130px}.card b{font-size:28px;display:block}input,select,button{padding:10px;border:1px solid #bbb;background:white}table{width:100%;border-collapse:collapse;background:white}th,td{padding:12px;border-bottom:1px solid #ddd;text-align:left;font-size:13px}.bad{color:#a22;font-weight:700}.modal{position:fixed;inset:0;background:#0008;display:none;padding:4%;overflow:auto}.panel{background:white;max-width:1000px;margin:auto;padding:25px}.finding{border-left:5px solid #b36;padding:10px;margin:10px 0;background:#fafafa}pre{white-space:pre-wrap;background:#f4f4f4;padding:10px}a{color:#155b42}</style><header><b>223 / ЕИС Аудитор</b></header><main><div class=hero><div><h1>Автоматическая проверка 223‑ФЗ</h1><p>Разъяснения · протоколы · отмены · вложения · конкретные фрагменты</p></div><div class=status id=status>Статус…</div></div><div class=stats><div class=card>Закупок<b id=s1>0</b></div><div class=card>Нарушений<b id=s2>0</b></div><div class=card>Рисков<b id=s3>0</b></div><div class=card>Протоколов<b id=s4>0</b></div></div><div class=filters><input id=q placeholder="Поиск"><select id=org><option value="">Все формы</option></select><select id=kind><option value="">Все</option><option value=violations>Нарушения</option><option value=risks>Риски</option><option value=clarifications>Разъяснения</option><option value=protocols>Протоколы</option><option value=cancellations>Отмены</option></select><button onclick="scan()">Сканировать сейчас</button></div><table><thead><tr><th>№</th><th>Заказчик</th><th>Форма</th><th>Документы</th><th>Результат</th><th></th></tr></thead><tbody id=rows></tbody></table></main><div class=modal id=modal onclick="if(event.target===this)this.style.display='none'"><div class=panel id=panel></div></div><script>
const E=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function A(u,o){let r=await fetch(u,o),d=await r.json();if(!r.ok)throw Error(d.detail||r.status);return d}async function loadForms(){let d=await A('/api/org-forms');E('#org').innerHTML='<option value="">Все формы</option>'+d.items.map(x=>`<option>${esc(x.org_form)}</option>`).join('')}async function load(){let p=new URLSearchParams();if(E('#org').value)p.set('org_form',E('#org').value);if(E('#kind').value)p.set('result_kind',E('#kind').value);if(E('#q').value)p.set('q',E('#q').value);let d=await A('/api/dashboard?'+p);E('#s1').textContent=d.stats.purchases;E('#s2').textContent=d.stats.violations;E('#s3').textContent=d.stats.risks;E('#s4').textContent=d.stats.protocols;E('#rows').innerHTML=d.purchases.map(x=>`<tr><td><b>${esc(x.reg_number)}</b></td><td>${esc(x.customer)}<br><small>${esc(x.title)}</small></td><td>${esc(x.org_form)}</td><td>разъясн. ${x.clarifications||0}<br>проток. ${x.protocols||0}<br>отмена ${x.cancellations||0}</td><td class=${x.violations?'bad':''}>наруш. ${x.violations||0}<br>риски ${x.risks||0}</td><td><button onclick="openP('${esc(x.reg_number)}')">Открыть</button></td></tr>`).join('')}async function openP(r){let d=await A('/api/purchases/'+r),p=d.purchase;E('#panel').innerHTML=`<button onclick="E('#modal').style.display='none'">Закрыть</button><h2>${esc(p.reg_number)}</h2><p>${esc(p.customer)}</p><p><a target=_blank href="${esc(p.url)}">Карточка ЕИС</a> ${d.report_url?`· <a target=_blank href="${d.report_url}">Отчёт</a>`:''}</p><h3>Вложения</h3><ul>${d.documents.map(x=>`<li>${esc(x.doc_type)} — ${esc(x.name)} — ${esc(x.status)} ${x.url?`<a target=_blank href="${esc(x.url)}">оригинал</a>`:''} ${x.annotated_path?`<b>отмеченная копия создана</b>`:''}</li>`).join('')}</ul><h3>Нарушения и риски</h3>${d.findings.map(f=>`<div class=finding><b>${esc(f.title)}</b><p>${esc(f.kind)} · <a target=_blank href="${esc(f.law_url)}">${esc(f.law)}</a></p><pre>${esc(f.evidence)}</pre><p>${esc(f.note)}</p>${f.source_url?`<a target=_blank href="${esc(f.source_url)}">Источник</a>`:''}</div>`).join('')}`;E('#modal').style.display='block'}async function status(){let d=await A('/api/status');E('#status').textContent=d.running?'Идёт сканирование':d.last_error?'Ошибка источника: '+d.last_error:'Автосканирование включено'}async function scan(){await A('/api/scan-now',{method:'POST'});status()}E('#org').onchange=load;E('#kind').onchange=load;let t;E('#q').oninput=()=>{clearTimeout(t);t=setTimeout(load,300)};loadForms().then(load);status();setInterval(()=>{status();load()},30000);
</script></html>'''
@app.get('/',response_class=HTMLResponse)
async def home():return HTML
