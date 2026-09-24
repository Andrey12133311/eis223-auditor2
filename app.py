import os,re,json,sqlite3,asyncio,io,html,zipfile,subprocess,tempfile
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
 import xlrd
except Exception: xlrd=None
try:
 import pytesseract
 from PIL import Image
except Exception: pytesseract=None;Image=None
try:
 import holidays
except Exception: holidays=None
try:
 from bs4 import BeautifulSoup
except Exception: BeautifulSoup=None
try:
 from striprtf.striprtf import rtf_to_text
except Exception: rtf_to_text=None

APP_VERSION='1.3.0'
BASE=os.getenv('GOSPLAN_BASE','https://v2test.gosplan.info').rstrip('/')
API_KEY=os.getenv('GOSPLAN_API_KEY','').strip(); API_HEADER=os.getenv('GOSPLAN_API_HEADER','X-API-Key')
DATA=Path(os.getenv('EIS223_DATA_DIR','/data')); DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'eis223.db'; FILES=DATA/'files'; REPORTS=DATA/'reports'; FILES.mkdir(exist_ok=True); REPORTS.mkdir(exist_ok=True)
SCAN_INTERVAL=max(300,int(os.getenv('SCAN_INTERVAL_SECONDS','300')))
DETAIL_LIMIT=max(1,min(25,int(os.getenv('GOSPLAN_DETAILS_PER_CYCLE','3'))))
MAX_DOCS=max(1,min(200,int(os.getenv('MAX_DOCS_PER_DETAIL','150'))))
MAX_DOWNLOADS=max(1,min(100,int(os.getenv('MAX_ATTACHMENT_DOWNLOADS','40'))))
MAX_FILE_MB=max(1,min(100,int(os.getenv('MAX_FILE_MB','30'))))
PAGE_SIZE=max(20,min(500,int(os.getenv('GOSPLAN_PAGE_SIZE','100'))))
LAW32='https://www.consultant.ru/document/cons_doc_LAW_116964/05cd0add21b39d478c8b8af91fb2f2cd80d4a6e8/'
LAW4='https://www.consultant.ru/document/cons_doc_LAW_116964/441d00be62e3224cdc0514cffaf2a26b5b40a1c7/'
LAW3='https://www.consultant.ru/document/cons_doc_LAW_116964/fddec0f5c16a67f6fca41f9e31dfb0dcc72cc49a/'
app=FastAPI(title='ЕИС 223-ФЗ Аудитор',version=APP_VERSION)
state={'running':False,'last_start':None,'last_finish':None,'last_error':None,'last_result':None,'backfill_skip':500,'current_reg':None}
lock=asyncio.Lock()

# Наиболее распространённые организационно-правовые формы заказчиков/учреждений.
# Фильтр в UI дополнительно показывает любые новые формы, уже встреченные в БД.
ORG=[
 ('ПАО',r'\bПАО\b|публичн\w+\s+акционерн'),('НАО',r'\bНАО\b|непубличн\w+\s+акционерн'),('АО',r'\bАО\b|акционерн\w+\s+обществ'),('ООО',r'\bООО\b|ограниченн\w+\s+ответственност'),
 ('ФГУП',r'\bФГУП\b|федеральн\w+\s+государственн\w+\s+унитарн'),('ГУП',r'\bГУП\b|государственн\w+\s+унитарн'),('МУП',r'\bМУП\b|муниципальн\w+\s+унитарн'),('ФКП',r'\bФКП\b|федеральн\w+\s+казенн\w+\s+предприят'),
 ('ФГБУ',r'\bФГБУ\b|федеральн\w+\s+государственн\w+\s+бюджетн\w+\s+учрежден'),('ГБУ',r'\bГБУ\b|государственн\w+\s+бюджетн\w+\s+учрежден'),('МБУ',r'\bМБУ\b|муниципальн\w+\s+бюджетн\w+\s+учрежден'),('ФБУ',r'\bФБУ\b|федеральн\w+\s+бюджетн\w+\s+учрежден'),
 ('ФГАУ',r'\bФГАУ\b|федеральн\w+\s+государственн\w+\s+автономн\w+\s+учрежден'),('ГАУ',r'\bГАУ\b|государственн\w+\s+автономн\w+\s+учрежден'),('МАУ',r'\bМАУ\b|муниципальн\w+\s+автономн\w+\s+учрежден'),('ФАУ',r'\bФАУ\b|федеральн\w+\s+автономн\w+\s+учрежден'),
 ('ФКУ',r'\bФКУ\b|федеральн\w+\s+казенн\w+\s+учрежден'),('ГКУ',r'\bГКУ\b|государственн\w+\s+казенн\w+\s+учрежден'),('МКУ',r'\bМКУ\b|муниципальн\w+\s+казенн\w+\s+учрежден'),
 ('АНО',r'\bАНО\b|автономн\w+\s+некоммерческ'),('НКО',r'\bНКО\b|некоммерческ\w+\s+организац'),('ГК',r'\bГК\b|государственн\w+\s+корпорац'),('Фонд',r'\bфонд\b'),('Учреждение',r'\bучреждени[ея]\b')]

EXT_RE=re.compile(r'\.(pdf|docx?|xlsx?|xlsm|rtf|txt|csv|xml|zip|png|jpe?g)(?:$|[?#])',re.I)

def now(): return datetime.now(timezone.utc).isoformat()
def clean(x): return re.sub(r'\s+',' ',str(x or '')).strip()
def org_form(s):
 for n,p in ORG:
  if re.search(p,s or '',re.I): return n
 return 'Не определена'
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
 CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY AUTOINCREMENT,reg_number TEXT,scope TEXT,item TEXT,status TEXT,evidence TEXT,source TEXT,source_url TEXT,document TEXT,field_path TEXT,note TEXT);
 CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
 CREATE INDEX IF NOT EXISTS idx_org ON purchases(org_form);CREATE INDEX IF NOT EXISTS idx_find_reg ON findings(reg_number);CREATE INDEX IF NOT EXISTS idx_docs_reg ON docs(reg_number);CREATE INDEX IF NOT EXISTS idx_checks_reg ON checks(reg_number);
 ''')
 cols={r['name'] for r in c.execute('PRAGMA table_info(purchases)')}
 for name,typ in [('submission_start','TEXT'),('submission_end','TEXT'),('price_num','REAL'),('card_fields_json','TEXT'),('card_source_status','TEXT')]:
  if name not in cols:c.execute(f'ALTER TABLE purchases ADD COLUMN {name} {typ}')
 c.execute('CREATE INDEX IF NOT EXISTS idx_price_num ON purchases(price_num)')
 dcols={r['name'] for r in c.execute('PRAGMA table_info(docs)')}
 for name,typ in [('text_chars','INTEGER DEFAULT 0'),('final_url','TEXT'),('checked_at','TEXT')]:
  if name not in dcols:c.execute(f'ALTER TABLE docs ADD COLUMN {name} {typ}')
 fcols={r['name'] for r in c.execute('PRAGMA table_info(findings)')}
 for name,typ in [('location','TEXT'),('match_text','TEXT')]:
  if name not in fcols:c.execute(f'ALTER TABLE findings ADD COLUMN {name} {typ}')
 ccols={r['name'] for r in c.execute('PRAGMA table_info(checks)')}
 for name,typ in [('law','TEXT'),('law_url','TEXT'),('location','TEXT')]:
  if name not in ccols:c.execute(f'ALTER TABLE checks ADD COLUMN {name} {typ}')
 pcols={r['name'] for r in c.execute('PRAGMA table_info(purchases)')}
 for name,typ in [('documents_found','INTEGER DEFAULT 0'),('documents_checked','INTEGER DEFAULT 0'),('checks_ok','INTEGER DEFAULT 0'),('checks_attention','INTEGER DEFAULT 0')]:
  if name not in pcols:c.execute(f'ALTER TABLE purchases ADD COLUMN {name} {typ}')
 c.commit();c.close()
init_db()

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
def eis_docs_url(reg):return f'https://zakupki.gov.ru/223/purchase/public/purchase/info/documents.html?regNumber={reg}'
def eis_page_urls(reg):
 base='https://zakupki.gov.ru/223/purchase/public/purchase/info/'
 return {
  'common':base+f'common-info.html?regNumber={reg}',
  'lots':base+f'lot-list.html?regNumber={reg}',
  'documents':base+f'documents.html?regNumber={reg}',
  'changes':base+f'change-history.html?regNumber={reg}',
 }

def dt(v):
 if not v:return None
 s=str(v).strip()
 for z in (s,s.replace('Z','+00:00')):
  try:return datetime.fromisoformat(z)
  except:pass
 for f in ('%d.%m.%Y %H:%M:%S','%d.%m.%Y %H:%M','%d.%m.%Y','%Y-%m-%d %H:%M:%S','%Y-%m-%d'):
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

def price_num(v):
 if v is None:return None
 if isinstance(v,(int,float)):return float(v)
 s=str(v).replace('\xa0',' ').replace('₽','').replace('руб.','').replace('руб','')
 s=re.sub(r'[^0-9,.-]','',s.replace(' ',''))
 if not s:return None
 if ',' in s and '.' not in s:s=s.replace(',','.')
 elif ',' in s and '.' in s:s=s.replace(',','')
 try:return float(s)
 except:return None

def headers(json_mode=True,referer=None):
 h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36','Accept-Language':'ru-RU,ru;q=0.9,en;q=0.5','Cache-Control':'no-cache'}
 h['Accept']='application/json' if json_mode else 'text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,application/octet-stream,*/*;q=0.8'
 if referer:h['Referer']=referer
 if API_KEY and json_mode:h[API_HEADER]=API_KEY
 return h
async def jget(client,path,params=None):
 last=None
 for _ in range(3):
  r=await client.get(BASE+path,params=params,headers=headers(True))
  if r.status_code!=429:r.raise_for_status();return r.json()
  last=r;wait=60
  for k in ('retry-after','ratelimit-reset'):
   try:
    if r.headers.get(k):wait=max(2,min(180,int(float(r.headers[k]))+2));break
   except:pass
  await asyncio.sleep(wait)
 if last:last.raise_for_status()

def flatten_scalars(obj,path='',out=None,limit=2500):
 if out is None:out=[]
 if len(out)>=limit:return out
 if isinstance(obj,dict):
  for k,v in obj.items():
   p=f'{path}.{k}' if path else str(k);flatten_scalars(v,p,out,limit)
 elif isinstance(obj,list):
  for i,v in enumerate(obj[:200]):flatten_scalars(v,f'{path}[{i}]',out,limit)
 elif obj not in (None,''):
  s=clean(obj)
  if s and len(s)<=4000:out.append((path,s))
 return out

def submission_dates(detail):
 start=txt(getv(detail,'submissionStartDate','submissionStartDateTime','applicationStartDate','applicationStartDateTime','submissionOpenDate','applicationOpenDate','startSubmissionDate','applicationSubmissionStartDate'))
 end=txt(getv(detail,'submissionCloseDate','submissionEndDate','submissionEndDateTime','applicationEndDate','applicationEndDateTime','submissionDeadline','applicationDeadline','endSubmissionDate','applicationSubmissionEndDate'))
 return start,end

def extract_docs(detail):
 out=[];seen=set()
 for d in walk(detail):
  name=txt(d.get('fileName') or d.get('filename') or d.get('documentName') or d.get('docName') or d.get('name'))
  desc=txt(d.get('description') or d.get('documentType') or d.get('typeName') or d.get('title'))
  urls=[]
  for k in ('url','downloadUrl','fileUrl','href','link','sourceUrl','attachmentUrl','downloadLink'):
   u=txt(d.get(k))
   if u and u.startswith(('http://','https://')) and u not in urls:urls.append(u)
  url=urls[0] if urls else ''
  label=(name+' '+desc).strip(); low=label.lower()
  if not (url or name):continue
  if not (EXT_RE.search(url) or EXT_RE.search(name) or any(x in low for x in ('протокол','разъяснен','отмен','документац','извещен','запрос','техническ','обоснован','форма заявки'))):continue
  key=(name,url)
  if key in seen:continue
  seen.add(key);meta={}
  for k,v in d.items():
   lk=str(k).lower()
   if any(x in lk for x in ('date','time','publish','sign','decision','request','create','id','guid')) and not isinstance(v,(dict,list)):meta[str(k)]=v
  if len(urls)>1:meta['alt_urls']=urls[1:]
  out.append({'name':name or desc or 'Документ','desc':desc,'url':url,'type':doc_type(name,desc),'published':txt(d.get('publishDate') or d.get('publicationDate') or d.get('publishedAt') or d.get('createDate')),'meta':meta,'text':'','local':None,'annotated':None,'status':'metadata','error':None,'final_url':None})
 return out[:150]

def safe_host(url):
 try:
  p=urlparse(url);h=(p.hostname or '').lower()
  return p.scheme in ('http','https') and any(h==x or h.endswith('.'+x) for x in ('zakupki.gov.ru','gosplan.info','roskazna.gov.ru'))
 except:return False
def safe_name(s):return re.sub(r'[^0-9A-Za-zА-Яа-яЁё._-]+','_',unquote(s or 'file'))[:180]

def parse_card_html(raw):
 pairs=[];visible=''
 try:
  if BeautifulSoup:
   soup=BeautifulSoup(raw,'html.parser')
   for x in soup(['script','style','noscript']):x.decompose()
   visible='\n'.join(clean(x) for x in soup.stripped_strings if clean(x))
   for tr in soup.find_all('tr'):
    cells=[clean(c.get_text(' ',strip=True)) for c in tr.find_all(['th','td'])]
    if len(cells)>=2 and cells[0] and cells[1]:pairs.append((cells[0], ' | '.join(cells[1:])))
   for dtel in soup.find_all('dt'):
    dd=dtel.find_next_sibling('dd')
    if dd:pairs.append((clean(dtel.get_text(' ',strip=True)),clean(dd.get_text(' ',strip=True))))
   # Часто в ЕИС подпись и значение находятся соседними блоками.
   for el in soup.find_all(['div','span','p','label']):
    cls=' '.join(el.get('class') or []).lower()
    if 'label' in cls or 'title' in cls or 'name' in cls:
     label=clean(el.get_text(' ',strip=True))
     sib=el.find_next_sibling()
     if label and sib:
      val=clean(sib.get_text(' ',strip=True))
      if val and val!=label:pairs.append((label,val))
  else:
   visible=clean(re.sub(r'<[^>]+>',' ',raw))
 except Exception:pass
 seen=set();uniq=[]
 for k,v in pairs:
  k,v=clean(k)[:300],clean(v)[:1500]
  if not k or not v:continue
  key=(k.lower(),v.lower())
  if key in seen:continue
  seen.add(key);uniq.append((k,v))
 return uniq[:1200],visible[:120000]

def eis_blocked(raw,status_code=200):
 s=(raw or '').lower()
 markers=('access denied','request rejected','captcha','robot check','доступ ограничен','проверка браузера','cloudflare')
 return status_code in (401,403,429) or any(x in s for x in markers)

async def fetch_eis_card(client,reg):
 result={'status':'error','pairs':[],'visible_text':'','html':'','error':None,'pages':{}}
 all_pairs=[];all_visible=[];errors=[];success=0
 # Первый запрос создаёт сессию/cookies ЕИС. Далее тот же httpx.AsyncClient используется для вкладок карточки.
 referer='https://zakupki.gov.ru/'
 for key,url in eis_page_urls(reg).items():
  page={'url':url,'status':'error','http_status':None,'final_url':None,'pairs':[],'visible_text':'','html':'','error':None}
  try:
   r=await client.get(url,headers=headers(False,referer),follow_redirects=True)
   page['http_status']=r.status_code;page['final_url']=str(r.url)
   raw=r.text if r.content else ''
   if eis_blocked(raw,r.status_code):raise ValueError(f'ЕИС ограничил доступ, HTTP {r.status_code}')
   r.raise_for_status()
   pairs,visible=parse_card_html(raw)
   page.update(status='ok',pairs=pairs,visible_text=visible,html=raw[:400000])
   success+=1;all_pairs.extend((f'{key}: {a}',b) for a,b in pairs);all_visible.append(f'[{key}]\n{visible}')
   referer=str(r.url)
  except Exception as e:
   page['error']=str(e)[:600];errors.append(f'{key}: {e}')
  result['pages'][key]=page
 # Убираем дубли полей между вкладками.
 seen=set();pairs=[]
 for k,v in all_pairs:
  sig=(clean(k).lower(),clean(v).lower())
  if sig in seen:continue
  seen.add(sig);pairs.append((k,v))
 result['pairs']=pairs[:2500];result['visible_text']='\n'.join(all_visible)[:250000]
 result['html']='\n'.join(p.get('html','') for p in result['pages'].values())[:600000]
 common_ok=result['pages'].get('common',{}).get('status')=='ok'
 docs_ok=result['pages'].get('documents',{}).get('status')=='ok'
 result['status']='ok' if common_ok and docs_ok else ('partial' if success else 'error')
 result['error']='; '.join(errors)[:1200] if errors else None
 return result

def _candidate_urls_from_html(raw,base_url):
 found=[]
 def add(u):
  if not u:return
  u=html.unescape(str(u)).replace('\\/','/')
  # Из onclick/data-* извлекаем первую URL/relative path в кавычках.
  mm=re.search(r"(https?://[^\s<>]+|/[^\s<>]+)",u)
  if mm and not u.startswith(('http://','https://','/')):u=mm.group(1).strip(chr(34)+chr(39))
  full=urljoin(base_url,u)
  low=full.lower()
  if safe_host(full) and (EXT_RE.search(low) or any(x in low for x in ('download','attachment','file','document','getfile','filestore','upload'))):
   if full not in found:found.append(full)
 if BeautifulSoup:
  soup=BeautifulSoup(raw or '','html.parser')
  for tag in soup.find_all(True):
   for attr in ('href','data-href','data-url','data-download-url','data-file-url','onclick'):
    if tag.get(attr):add(tag.get(attr))
 else:
  for u in re.findall(r"https?://[^\s\"<>]+",raw or '',re.I):add(u)
 return found[:500]

async def discover_eis_documents(client,reg,card=None):
 out=[]
 page=(card or {}).get('pages',{}).get('documents',{})
 raw=page.get('html') or ''
 base=page.get('final_url') or eis_docs_url(reg)
 if not raw:
  try:
   r=await client.get(eis_docs_url(reg),headers=headers(False,eis_url(reg)),follow_redirects=True);r.raise_for_status();raw=r.text;base=str(r.url)
  except Exception:return []
 urls=_candidate_urls_from_html(raw,base)
 # Текст ссылки используем как имя, когда это возможно.
 anchor_names={}
 if BeautifulSoup:
  try:
   soup=BeautifulSoup(raw,'html.parser')
   for a in soup.find_all('a',href=True):anchor_names[urljoin(base,a.get('href'))]=clean(a.get_text(' ',strip=True))
  except Exception:pass
 for u in urls:
  name=anchor_names.get(u) or Path(unquote(urlparse(u).path)).name or 'Документ ЕИС'
  out.append({'name':name,'desc':'Оригинал со страницы «Документы» ЕИС','url':u,'type':doc_type(name,'Документ ЕИС'),'published':'','meta':{'source':'eis-documents-page'},'text':'','local':None,'annotated':None,'status':'metadata','error':None,'final_url':None})
 seen=set();res=[]
 for d in out:
  key=(clean(d['name']).lower(),clean(d['url']).lower())
  if key not in seen:seen.add(key);res.append(d)
 return res[:MAX_DOCS]

def merge_docs(a,b):
 out=[];seen=set()
 for d in a+b:
  key=(clean(d.get('name')).lower(),clean(d.get('url')).lower())
  if key in seen:continue
  seen.add(key);out.append(d)
 return out[:MAX_DOCS]

def filename_from_headers(resp,fallback):
 cd=resp.headers.get('content-disposition','')
 m=re.search(r"filename\*=UTF-8''([^;]+)",cd,re.I) or re.search(r'filename="?([^";]+)',cd,re.I)
 return safe_name(m.group(1) if m else fallback)

def looks_html(data,ct):
 head=bytes(data[:1000]).lstrip().lower()
 return 'text/html' in ct or head.startswith(b'<!doctype html') or head.startswith(b'<html')

async def fetch_binary(client,url,reg):
 h=headers(False,eis_url(reg));r=await client.get(url,headers=h,follow_redirects=True)
 r.raise_for_status()
 if not safe_host(str(r.url)):raise ValueError('Переадресация на недопустимый домен')
 data=r.content
 if len(data)>MAX_FILE_MB*1024*1024:raise ValueError(f'Файл больше {MAX_FILE_MB} МБ')
 ct=r.headers.get('content-type','').lower()
 if looks_html(data,ct):
  raw=r.text;links=[]
  if BeautifulSoup:
   soup=BeautifulSoup(raw,'html.parser')
   links=[urljoin(str(r.url),a.get('href')) for a in soup.find_all('a',href=True)]
  else:links=[urljoin(str(r.url),x) for x in re.findall(r'href=["\']([^"\']+)',raw,re.I)]
  links=[u for u in links if safe_host(u) and (EXT_RE.search(u) or any(x in u.lower() for x in ('download','attachment','file')))]
  if links:
   r2=await client.get(links[0],headers=headers(False,str(r.url)),follow_redirects=True);r2.raise_for_status()
   if not safe_host(str(r2.url)):raise ValueError('Переадресация на недопустимый домен')
   data=r2.content;ct=r2.headers.get('content-type','').lower();r=r2
   if len(data)>MAX_FILE_MB*1024*1024:raise ValueError(f'Файл больше {MAX_FILE_MB} МБ')
 if looks_html(data,ct):raise ValueError('Ссылка вернула HTML-страницу вместо файла')
 return r,data,ct

async def download_doc(client,reg,d):
 urls=[d.get('url')]+list(d.get('meta',{}).get('alt_urls') or [])
 urls=[u for u in urls if u and safe_host(u)]
 if not urls:
  d['status']='error';d['error']='Нет допустимой ссылки на оригинал';return
 last=None
 for u in urls:
  try:
   r,data,ct=await fetch_binary(client,u,reg)
   fallback=d.get('name') or Path(urlparse(str(r.url)).path).name or 'file'
   name=filename_from_headers(r,fallback)
   suffix=Path(name).suffix.lower()
   if not suffix:
    if data[:4]==b'%PDF':name+='.pdf'
    elif data[:2]==b'PK':name+='.zip'
    elif 'word' in ct:name+='.docx'
    elif 'sheet' in ct or 'excel' in ct:name+='.xlsx'
    elif 'xml' in ct:name+='.xml'
    else:name+='.bin'
   folder=FILES/reg/'original';folder.mkdir(parents=True,exist_ok=True);p=folder/name;p.write_bytes(data)
   d['local']=str(p);d['final_url']=str(r.url);d['text']=extract_text(p);chars=len(clean(d['text']))
   d['status']='checked' if chars>=3 else 'downloaded_no_text';d['error']=None if d['status']=='checked' else 'Оригинал скачан, но содержимое извлечь не удалось'
   return
  except Exception as e:last=e
 d['status']='error';d['error']=str(last)[:500] if last else 'Не удалось скачать оригинал'

def extract_text(p,depth=0):
 p=Path(p);ext=p.suffix.lower()
 if depth>2:return ''
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
  if ext=='.doc':
   try:return subprocess.run(['antiword',str(p)],capture_output=True,text=True,timeout=20,errors='ignore').stdout
   except:return ''
  if ext in ('.xlsx','.xlsm') and openpyxl:
   wb=openpyxl.load_workbook(p,read_only=True,data_only=True);out=[]
   for ws in wb.worksheets:
    for row in ws.iter_rows(values_only=True):
     vals=[str(v) for v in row if v is not None]
     if vals:out.append(' | '.join(vals))
   return '\n'.join(out)
  if ext=='.xls' and xlrd:
   wb=xlrd.open_workbook(str(p));out=[]
   for ws in wb.sheets():
    for i in range(ws.nrows):
     vals=[str(x) for x in ws.row_values(i) if str(x).strip()]
     if vals:out.append(' | '.join(vals))
   return '\n'.join(out)
  if ext=='.rtf':
   raw=p.read_text(errors='ignore');return rtf_to_text(raw) if rtf_to_text else raw
  if ext in ('.txt','.csv','.xml'):
   raw=p.read_text(errors='ignore')
   return clean(re.sub(r'<[^>]+>',' ',raw)) if ext=='.xml' else raw
  if ext in ('.png','.jpg','.jpeg') and pytesseract and Image:return pytesseract.image_to_string(Image.open(p),lang='rus+eng')
  if ext=='.zip':
   parts=[];total=0
   with zipfile.ZipFile(p) as z:
    for info in z.infolist()[:50]:
     if info.is_dir() or info.file_size>MAX_FILE_MB*1024*1024:continue
     total+=info.file_size
     if total>100*1024*1024:break
     member=Path(info.filename)
     if member.suffix.lower() not in ('.pdf','.doc','.docx','.xls','.xlsx','.xlsm','.rtf','.txt','.csv','.xml','.png','.jpg','.jpeg','.zip'):continue
     data=z.read(info)
     with tempfile.NamedTemporaryFile(suffix=member.suffix,delete=False) as tf:tf.write(data);tmp=tf.name
     try:
      t=extract_text(tmp,depth+1)
      if t:parts.append(f'--- {member.name} ---\n{t}')
     finally:
      try:os.unlink(tmp)
      except:pass
   return '\n'.join(parts)
 except:return ''
 return ''

def mdate(d,*need):
 for k,v in d.get('meta',{}).items():
  lk=k.lower()
  if all(n in lk for n in need):
   z=dt(v)
   if z:return z
 return dt(d.get('published')) if ('publish' in need or 'publication' in need) else None

def compact_seed(s):
 s=clean(s)
 if not s:return ''
 # Сначала берём фрагмент без переносов; слишком длинные контексты плохо ищутся в PDF/DOCX.
 return s[:120]

def locate_in_file(path,needle):
 if not path or not needle:return ''
 p=Path(path);ext=p.suffix.lower();needle=clean(needle)
 try:
  if ext=='.pdf' and fitz:
   doc=fitz.open(p)
   for i,pg in enumerate(doc):
    text=pg.get_text('text') or ''
    if needle.lower() in clean(text).lower() or (len(needle)>25 and needle[:50].lower() in clean(text).lower()):doc.close();return f'страница {i+1}'
   doc.close()
  elif ext=='.docx' and Document:
   d=Document(p)
   for i,par in enumerate(d.paragraphs,1):
    if needle.lower() in clean(par.text).lower() or (len(needle)>25 and needle[:50].lower() in clean(par.text).lower()):return f'абзац {i}'
   for ti,t in enumerate(d.tables,1):
    for ri,row in enumerate(t.rows,1):
     for ci,cell in enumerate(row.cells,1):
      if needle.lower() in clean(cell.text).lower() or (len(needle)>25 and needle[:50].lower() in clean(cell.text).lower()):return f'таблица {ti}, строка {ri}, ячейка {ci}'
  elif ext in ('.xlsx','.xlsm') and openpyxl:
   wb=openpyxl.load_workbook(p,read_only=True,data_only=True)
   for ws in wb.worksheets:
    for row in ws.iter_rows():
     for cell in row:
      val=clean(cell.value)
      if needle.lower() in val.lower() or (len(needle)>25 and needle[:50].lower() in val.lower()):return f'лист «{ws.title}», ячейка {cell.coordinate}'
  else:
   t=extract_text(p);pos=t.lower().find(needle.lower())
   if pos>=0:return f'текст документа, позиция ~{pos+1}'
 except Exception:pass
 return ''

def finding(code,kind,title,law,law_url,evidence,note,doc=None,match_text='',location=''):
 if doc and not location and match_text:location=locate_in_file(doc.get('local'),match_text)
 return {'code':code,'kind':kind,'title':title,'law':law,'law_url':law_url,'evidence':clean(evidence)[:1800],'note':note,'document':doc['name'] if doc else None,'source_url':doc['url'] if doc else None,'location':location,'match_text':clean(match_text)[:500]}
def check(scope,item,status,evidence='',source='',source_url='',document=None,field_path='',note='',law='',law_url='',location=''):
 return {'scope':scope,'item':item,'status':status,'evidence':clean(evidence)[:3500],'source':source,'source_url':source_url,'document':document,'field_path':field_path,'note':note,'law':law,'law_url':law_url,'location':location}

def analyze(p,docs):
 out=[];deadline=dt(p.get('submission_end') or p.get('deadline'));reqs=[d for d in docs if d['type']=='clarification_request'];answers=[d for d in docs if d['type']=='clarification']
 for rq in reqs:
  rd=mdate(rq,'request') or mdate(rq,'create') or mdate(rq,'publish')
  if not rd or (deadline and wdays(rd,deadline)<3):continue
  later=[]
  for a in answers:
   ad=mdate(a,'publish') or mdate(a,'create')
   if ad and ad>=rd:later.append((ad,a))
  if not later:out.append(finding('CLAR-NO-ANSWER','check','Не найдено разъяснение на своевременный запрос','223-ФЗ, ст. 3.2 ч. 2–3',LAW32,f'Запрос: {rd.isoformat()}; ответа среди доступных документов не найдено.','Контрольный индикатор: требуется убедиться, что в ЕИС действительно отсутствует ответ и запрос был своевременным.',rq));continue
  ad,a=min(later,key=lambda x:x[0]);wd=wdays(rd,ad)
  if wd>3:out.append(finding('CLAR-LATE','violation','Разъяснение опубликовано позднее трёх рабочих дней','223-ФЗ, ст. 3.2 ч. 3',LAW32,f'Запрос: {rd.isoformat()}; разъяснение: {ad.isoformat()}; рабочих дней: {wd}.','Факт срока сформирован по метаданным ЕИС.',a))
 for a in answers:
  m=re.search(r'(?:измен|замен)[^.\n]{0,140}(?:предмет\w*\s+закупк|существенн\w*\s+услов)',a.get('text',''),re.I)
  if m:
   mt=m.group(0);ctx=a['text'][max(0,m.start()-160):m.end()+220]
   out.append(finding('CLAR-CHANGES','risk','Разъяснение может изменять предмет или существенные условия','223-ФЗ, ст. 3.2 ч. 4',LAW32,ctx,'Риск требует сопоставления исходной и изменённой редакций.',a,mt))
 for d in [x for x in docs if x['type']=='cancellation']:
  dec=mdate(d,'decision') or mdate(d,'sign');pub=mdate(d,'publish')
  if dec and pub and dec.date()!=pub.date():out.append(finding('CANCEL-LATE','violation','Решение об отмене размещено не в день принятия','223-ФЗ, ст. 3.2 ч. 6',LAW32,f'Решение: {dec.date()}; размещение: {pub.date()}.','Срок рассчитан по метаданным документа.',d))
  if dec and deadline and dec>deadline:out.append(finding('CANCEL-AFTER-DEADLINE','check','Отмена после окончания подачи заявок требует отдельной правовой проверки','223-ФЗ, ст. 3.2 ч. 5–7',LAW32,f'Окончание подачи: {deadline}; решение: {dec}.','Нужно проверить наличие предусмотренных законом обстоятельств и положение о закупке.',d))
 for d in [x for x in docs if x['type'] in ('protocol','final_protocol')]:
  sign=mdate(d,'sign');pub=mdate(d,'publish')
  if sign and pub and (pub.date()-sign.date()).days>3:out.append(finding('PROTOCOL-LATE','violation','Протокол размещён позднее трёх дней со дня подписания','223-ФЗ, ст. 4 ч. 12',LAW4,f'Подписание: {sign.date()}; размещение: {pub.date()}.','Срок рассчитан по метаданным ЕИС.',d))
  t=d.get('text','')
  if len(re.sub(r'\s+','',t))>=250:
   mandatory=[('дата подписания',r'дата\s+(?:подписан|составлен)|\b\d{2}\.\d{2}\.\d{4}\b'),('количество заявок',r'количеств\w+\s+(?:поданн\w+\s+)?заяв|подано\s+\d+\s+заяв'),('дата/время регистрации',r'регистрац\w+.*(?:дата|время)|дата\s+и\s+время\s+регистрац')]
   if d['type']=='final_protocol':mandatory += [('ранжирование/победитель',r'порядков\w+\s+номер|победител|место\s+\d'),('ценовые предложения',r'ценов\w+\s+предлож|цена\s+(?:договора|заявки)|руб'),('результаты рассмотрения',r'результат\w+\s+рассмотрен|допущ|отклон')]
   for label,pp in mandatory:
    if not re.search(pp,t,re.I|re.S):out.append(finding('PROTOCOL-FIELD','check','В протоколе автоматически не найдено обязательное сведение: '+label,'223-ФЗ, ст. 3.2 ч. 13–14',LAW32,f'Документ «{d["name"]}»: не найдено — {label}.','Отсутствие текста может быть связано с форматом/OCR; до ручного подтверждения отмечается как «требует проверки».',d,location='документ целиком: обязательное сведение автоматически не найдено'))
 # Индикаторы ограничения конкуренции: формулировка -> контекст -> норма. Они помечаются как риск, а не автоматически как установленное нарушение.
 risk_rules=[
  ('DEALER',r'авторизационн\w*\s+письм|статус\s+(?:официального\s+)?дилера','Требование авторизационного письма/дилерского статуса'),
  ('LOCALITY',r'(?:офис|склад|сервисн\w*\s+центр)[^.\n]{0,150}(?:в\s+городе|в\s+регионе|на\s+территории)','Территориальное требование к участнику'),
  ('NO-EQUIV',r'(?:только|исключительно)[^.\n]{0,100}(?:марки|бренда|производител)|без\s+(?:возможности\s+)?(?:поставки\s+)?эквивалент','Указание на конкретный бренд/производителя без явного эквивалента'),
  ('EXPERIENCE',r'опыт\w*\s+(?:исполнения|выполнения|поставки)[^.\n]{0,180}(?:не\s+менее|за\s+последн)','Требование к опыту исполнения аналогичных договоров'),
  ('STAFF',r'наличи\w+\s+в\s+штате[^.\n]{0,160}(?:специалист|работник|персонал)','Требование наличия персонала именно в штате'),
 ]
 for d in docs:
  t=d.get('text','')
  if not t:continue
  for code,pp,title in risk_rules:
   m=re.search(pp,t,re.I|re.S)
   if m:
    mt=m.group(0);ctx=t[max(0,m.start()-180):m.end()+260]
    out.append(finding(code,'risk',title,'223-ФЗ, ст. 3 ч. 1',LAW3,ctx,'Контрольный индикатор ограничения конкуренции. Итоговый вывод зависит от предмета закупки, положения о закупке и объективной необходимости требования.',d,mt))
 return out

def norm_search(s):return re.sub(r'[^0-9a-zа-я]+','',str(s or '').lower().replace('ё','е'))
def _contains_any(text,patterns):
 t=(text or '').lower().replace('ё','е')
 return any(re.search(p,t,re.I|re.S) for p in patterns)

def build_checks(p,detail,card,docs):
 out=[]
 # Реальная проверка вкладок сайта ЕИС: фиксируем HTTP/доступность отдельно от API-источника.
 page_names={'common':'Основная информация','lots':'Лоты','documents':'Документы','changes':'Изменения'}
 for key,label in page_names.items():
  pg=(card.get('pages') or {}).get(key,{})
  ok=pg.get('status')=='ok'
  ev=f"HTTP {pg.get('http_status') or '—'}; конечный URL: {pg.get('final_url') or pg.get('url') or '—'}"
  if pg.get('error'):ev+='; '+pg['error']
  out.append(check('eis-site','Вкладка ЕИС: '+label,'ok' if ok else 'check',ev,'zakupki.gov.ru',pg.get('final_url') or pg.get('url') or p['url'],note='Страница ЕИС реально запрошена сервером и разобрана.' if ok else 'Страница ЕИС не подтверждена; это техническая неполнота проверки.'))
 # Все скалярные поля источника фиксируем, чтобы пользователь видел не «чёрный ящик», а прочитанные значения.
 for path,val in flatten_scalars(detail):out.append(check('card','Поле карточки/API','read',val,'Структурированные данные закупки',p['url'],field_path=path,note='Поле получено и зафиксировано.'))
 if card.get('status')=='ok':
  for label,val in card.get('pairs',[]):out.append(check('card','Поле на странице ЕИС','read',val,'zakupki.gov.ru',p['url'],field_path=label,note='Видимое поле страницы ЕИС прочитано.'))
 else:out.append(check('card','Страница карточки ЕИС','check',card.get('error') or 'Страница недоступна','ЕИС',p['url'],note='Без прямого чтения сайта проверка считается неполной.'))
 key=[('Номер закупки',p.get('reg')),('Наименование закупки',p.get('title')),('Заказчик',p.get('customer')),('Способ закупки',p.get('method')),('НМЦД',p.get('price')),('Дата публикации',p.get('published')),('Начало подачи заявок',p.get('submission_start')),('Окончание подачи заявок',p.get('submission_end'))]
 for label,val in key:out.append(check('card',label,'ok' if clean(val) else 'check',val or 'Не найдено','Карточка закупки',p['url'],note='Ключевое поле найдено.' if clean(val) else 'Ключевое поле не найдено в доступных данных.'))
 pn=p.get('price_num');out.append(check('card','НМЦД распознана как число','ok' if pn is not None and pn>=0 else 'check',str(pn) if pn is not None else p.get('price',''),'Карточка закупки',p['url']))
 st,en=dt(p.get('submission_start')),dt(p.get('submission_end'))
 if st and en:out.append(check('card','Хронология подачи заявок','ok' if en>=st else 'violation',f'{p.get("submission_start")} → {p.get("submission_end")}','Карточка закупки',p['url'],note='Окончание не раньше начала.' if en>=st else 'Окончание подачи раньше начала.',law='223-ФЗ, ст. 4 ч. 9 п. 7 и ч. 10 п. 8',law_url=LAW4))
 else:out.append(check('card','Хронология подачи заявок','check',f'начало: {p.get("submission_start") or "—"}; окончание: {p.get("submission_end") or "—"}','Карточка закупки',p['url'],note='Не удалось проверить обе даты.',law='223-ФЗ, ст. 4 ч. 9 п. 7 и ч. 10 п. 8',law_url=LAW4))

 checked=[d for d in docs if d.get('status')=='checked' and clean(d.get('text'))]
 for d in docs:
  chars=len(clean(d.get('text','')));stt='ok' if d.get('status')=='checked' else 'check';ev=f'Статус: {d.get("status")}; извлечено символов: {chars}; конечный URL: {d.get("final_url") or "—"}'
  if d.get('error'):ev+='; ошибка: '+d['error']
  out.append(check('document','Оригинал документа скачан и прочитан',stt,ev,'Документ закупки',d.get('url') or '',document=d.get('name'),note='Содержимое использовано в правовой проверке.' if stt=='ok' else 'Документ не считается проверенным, пока содержимое не извлечено.'))

 # Контрольный чек-лист по ст. 4 223-ФЗ. Статус «check» = автоматикой не подтверждено, а не готовый вывод о нарушении.
 corpus='\n'.join(d.get('text','') for d in checked)
 site_text=card.get('visible_text','') or ''
 full=(site_text+'\n'+corpus).lower().replace('ё','е')
 rules=[
  ('223-4-9-1','Извещение: способ закупки',[r'способ\w*\s+закуп',r'аукцион|конкурс|запрос\s+(?:котиров|предлож)'],'223-ФЗ, ст. 4 ч. 9 п. 1'),
  ('223-4-9-2','Извещение: сведения о заказчике и контакты',[r'почтов\w+\s+адрес',r'электронн\w+\s+почт|e-?mail',r'телефон'],'223-ФЗ, ст. 4 ч. 9 п. 2'),
  ('223-4-9-3','Извещение: предмет и объём/количество',[r'предмет\w+\s+(?:договора|закупк)',r'количеств|объем'],'223-ФЗ, ст. 4 ч. 9 п. 3'),
  ('223-4-9-4','Извещение: место поставки/выполнения',[r'место\s+(?:поставк|выполнен|оказан)'],'223-ФЗ, ст. 4 ч. 9 п. 4'),
  ('223-4-9-5','Извещение: НМЦД',[r'начальн\w+\s*\(?максимальн|нмцд|начальн\w+\s+цен'],'223-ФЗ, ст. 4 ч. 9 п. 5'),
  ('223-4-9-7','Извещение: начало и окончание подачи заявок',[r'начал\w+\s+подач\w+\s+заяв',r'окончан\w+\s+(?:срока\s+)?подач\w+\s+заяв'],'223-ФЗ, ст. 4 ч. 9 п. 7'),
  ('223-4-10-1','Документация: требования к товару/работе/услуге',[r'техническ\w+\s+характерист',r'требован\w+\s+к\s+(?:качеств|безопасност|товар|работ|услуг)'],'223-ФЗ, ст. 4 ч. 10 п. 1'),
  ('223-4-10-2','Документация: содержание и форма заявки',[r'требован\w+\s+к\s+(?:содержан|форм|оформлен).*заяв',r'форма\s+заявк'],'223-ФЗ, ст. 4 ч. 10 п. 2'),
  ('223-4-10-4','Документация: место, условия и сроки поставки',[r'срок\w+\s+(?:поставк|выполнен|оказан)',r'услови\w+\s+(?:поставк|выполнен|оказан)'],'223-ФЗ, ст. 4 ч. 10 п. 4'),
  ('223-4-10-6','Документация: форма, сроки и порядок оплаты',[r'порядок\s+оплат',r'услови\w+\s+оплат',r'срок\w+\s+оплат'],'223-ФЗ, ст. 4 ч. 10 п. 6'),
  ('223-4-10-7','Документация: обоснование НМЦД',[r'обоснован\w+\s+(?:начальн|нмцд|цен)'],'223-ФЗ, ст. 4 ч. 10 п. 7'),
  ('223-4-10-8','Документация: порядок и сроки подачи заявок',[r'порядок\s+подач\w+\s+заяв',r'дата\s+и\s+время\s+окончан\w+.*подач'],'223-ФЗ, ст. 4 ч. 10 п. 8'),
 ]
 for code,item,patterns,law in rules:
  found=[]
  for pp in patterns:
   m=re.search(pp,full,re.I|re.S)
   if m:found.append(m.group(0))
  ok=len(found)>=min(1,len(patterns))
  out.append(check('223-ФЗ',item,'ok' if ok else 'check','; '.join(found[:4]) if found else 'Автоматически не найдено','Карточка ЕИС + документы',p['url'],field_path=code,note='Требование подтверждено по доступным данным.' if ok else 'Нужно проверить оригинал и положение о закупке; отсутствие совпадения автоматикой само по себе не признаётся нарушением.',law=law,law_url=LAW4))

 # Сверка извещения/карточки и документации (ч. 8 ст. 4).
 if corpus:
  for label,val in [('Номер закупки',p.get('reg')),('Заказчик',p.get('customer')),('Наименование закупки',p.get('title'))]:
   needle=norm_search(val); needle=needle[:80] if len(needle)>80 else needle; found=bool(needle and needle in norm_search(corpus))
   out.append(check('223-ФЗ','Соответствие извещения и документации: '+label,'ok' if found else 'check',val or '—','Карточка ↔ документы',p['url'],note='Совпадение найдено.' if found else 'Автоматически не подтверждено; требуется сопоставление редакций.',law='223-ФЗ, ст. 4 ч. 8',law_url=LAW4))
  if p.get('price_num') is not None:
   variants=[str(int(p['price_num'])),f'{p["price_num"]:.2f}'.replace('.',','),f'{p["price_num"]:,.2f}'.replace(',',' ').replace('.',',')];found=any(norm_search(v) in norm_search(corpus) for v in variants)
   out.append(check('223-ФЗ','Соответствие извещения и документации: НМЦД','ok' if found else 'check',p.get('price'),'Карточка ↔ документы',p['url'],note='НМЦД найдена в документах.' if found else 'НМЦД в таком представлении автоматически не найдена.',law='223-ФЗ, ст. 4 ч. 8',law_url=LAW4))
 else:out.append(check('223-ФЗ','Соответствие извещения и документации','check','Нет документов с извлечённым текстом','Карточка ↔ документы',p['url'],note='Невозможно выполнить ч. 8 ст. 4 без чтения оригиналов.',law='223-ФЗ, ст. 4 ч. 8',law_url=LAW4))
 return out

def annotate(reg,docs,finds,checks):
 for d in docs:
  if not d.get('local'):continue
  relevant=[]
  for f in finds:
   if f.get('document')==d['name'] and f.get('match_text'):relevant.append({'text':f.get('match_text'),'label':f.get('title'),'location':f.get('location')})
  for x in checks:
   if x.get('document')==d['name'] and x.get('status') in ('violation','risk') and x.get('evidence'):relevant.append({'text':compact_seed(x.get('evidence')),'label':x.get('item'),'location':x.get('location')})
  if not relevant:continue
  p=Path(d['local']);folder=FILES/reg/'annotated';folder.mkdir(parents=True,exist_ok=True);dst=folder/(p.stem+'__ПРОВЕРЕНО'+p.suffix)
  try:
   if p.suffix.lower()=='.pdf' and fitz:
    doc=fitz.open(p);hits=0
    for item in relevant:
     seed=compact_seed(item['text'])
     if len(seed)<5:continue
     for pi,pg in enumerate(doc):
      rs=pg.search_for(seed)
      if not rs and len(seed)>45:rs=pg.search_for(seed[:45])
      for rr in rs[:5]:
       pg.add_highlight_annot(rr);hits+=1
       try:pg.add_text_annot(rr.tl,f"Проверка 223-ФЗ: {item['label']}")
       except:pass
      if rs:break
    if hits:doc.save(dst);d['annotated']=str(dst)
    doc.close()
   elif p.suffix.lower()=='.docx' and Document:
    doc=Document(p);hits=0
    for par in doc.paragraphs:
     norm=clean(par.text).lower()
     for item in relevant:
      seed=compact_seed(item['text']).lower()
      if seed and (seed in norm or (len(seed)>45 and seed[:45] in norm)):
       for rr in par.runs:rr.font.highlight_color=WD_COLOR_INDEX.YELLOW
       hits+=1;break
    for table in doc.tables:
     for row in table.rows:
      for cell in row.cells:
       norm=clean(cell.text).lower()
       for item in relevant:
        seed=compact_seed(item['text']).lower()
        if seed and (seed in norm or (len(seed)>45 and seed[:45] in norm)):
         for par in cell.paragraphs:
          for rr in par.runs:rr.font.highlight_color=WD_COLOR_INDEX.YELLOW
         hits+=1;break
    if hits:doc.save(dst);d['annotated']=str(dst)
   elif p.suffix.lower() in ('.xlsx','.xlsm') and openpyxl:
    from openpyxl.comments import Comment
    from openpyxl.styles import PatternFill
    wb=openpyxl.load_workbook(p);hits=0
    for ws in wb.worksheets:
     for row in ws.iter_rows():
      for cell in row:
       val=clean(cell.value).lower()
       for item in relevant:
        seed=compact_seed(item['text']).lower()
        if seed and (seed in val or (len(seed)>45 and seed[:45] in val)):
         cell.fill=PatternFill('solid',fgColor='FFF59D');cell.comment=Comment('Проверка 223-ФЗ: '+item['label'],'ЕИС 223-ФЗ Аудитор');hits+=1;break
    if hits:wb.save(dst);d['annotated']=str(dst)
  except Exception:pass

def save(p,docs,finds,checks,card_payload):
 c=conn();c.execute('''INSERT INTO purchases(reg_number,title,customer,org_form,method,price,published_at,deadline,url,source,last_seen,clarifications,protocols,cancellations,violations,risks,submission_start,submission_end,price_num,card_fields_json,card_source_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(reg_number) DO UPDATE SET title=excluded.title,customer=excluded.customer,org_form=excluded.org_form,method=excluded.method,price=excluded.price,published_at=excluded.published_at,deadline=excluded.deadline,url=excluded.url,source=excluded.source,last_seen=excluded.last_seen,clarifications=excluded.clarifications,protocols=excluded.protocols,cancellations=excluded.cancellations,violations=excluded.violations,risks=excluded.risks,submission_start=excluded.submission_start,submission_end=excluded.submission_end,price_num=excluded.price_num,card_fields_json=excluded.card_fields_json,card_source_status=excluded.card_source_status''',(p['reg'],p['title'],p['customer'],org_form(p['customer']),p['method'],p['price'],p['published'],p.get('submission_end') or p.get('deadline'),p['url'],'eis+gosplan',now(),sum(d['type'] in ('clarification','clarification_request') for d in docs),sum(d['type'] in ('protocol','final_protocol') for d in docs),sum(d['type']=='cancellation' for d in docs),sum(f['kind']=='violation' for f in finds)+sum(x['status']=='violation' for x in checks),sum(f['kind']=='risk' for f in finds),p.get('submission_start'),p.get('submission_end'),p.get('price_num'),json.dumps(card_payload,ensure_ascii=False,default=str)[:1000000],card_payload.get('status')))
 c.execute('DELETE FROM docs WHERE reg_number=?',(p['reg'],));c.execute('DELETE FROM findings WHERE reg_number=?',(p['reg'],));c.execute('DELETE FROM checks WHERE reg_number=?',(p['reg'],))
 for d in docs:c.execute('INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,annotated_path,status,error,metadata_json,text_chars,final_url,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(p['reg'],d['name'],d['url'],d['type'],d['published'],d['local'],d['annotated'],d['status'],d['error'],json.dumps(d['meta'],ensure_ascii=False,default=str),len(clean(d.get('text',''))),d.get('final_url'),now()))
 for f in finds:c.execute('INSERT INTO findings(reg_number,code,kind,title,law,law_url,evidence,note,document,source_url,location,match_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(p['reg'],f['code'],f['kind'],f['title'],f['law'],f['law_url'],f['evidence'],f['note'],f['document'],f['source_url'],f.get('location'),f.get('match_text')))
 for x in checks:c.execute('INSERT INTO checks(reg_number,scope,item,status,evidence,source,source_url,document,field_path,note,law,law_url,location) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(p['reg'],x['scope'],x['item'],x['status'],x['evidence'],x['source'],x['source_url'],x['document'],x['field_path'],x['note'],x.get('law'),x.get('law_url'),x.get('location')))
 c.execute('UPDATE purchases SET documents_found=?,documents_checked=?,checks_ok=?,checks_attention=? WHERE reg_number=?',(len(docs),sum(d.get('status')=='checked' for d in docs),sum(x.get('status')=='ok' for x in checks),sum(x.get('status') in ('check','violation','risk') for x in checks),p['reg']))
 c.commit();c.close();render_report(p,docs,finds,checks)

def render_report(p,docs,finds,checks):
 rows=''.join(f"<tr><td>{html.escape(d['type'])}</td><td>{html.escape(d['name'])}</td><td>{html.escape(d['status'])}</td><td>{html.escape(d.get('error') or '')}</td><td>{'<a href='+repr(d['url'])+'>источник</a>' if d['url'] else '—'}</td></tr>" for d in docs)
 blocks=''.join(f"<article><h3>{html.escape(f['title'])}</h3><p><a href='{html.escape(f['law_url'])}'>{html.escape(f['law'])}</a> · {html.escape(f['kind'])}</p><pre>{html.escape(f['evidence'])}</pre><p>{html.escape(f['note'])}</p></article>" for f in finds)
 ck=''.join(f"<tr><td>{html.escape(x['scope'])}</td><td>{html.escape(x['item'])}</td><td>{html.escape(x['status'])}</td><td>{html.escape(x['evidence'])}</td></tr>" for x in checks[:1500])
 (REPORTS/(p['reg']+'.html')).write_text(f"<!doctype html><meta charset=utf-8><style>body{{font-family:Arial;max-width:1200px;margin:30px}}article{{border-left:5px solid #b33;padding:12px;margin:12px 0}}pre{{white-space:pre-wrap;background:#f5f5f5;padding:10px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd;vertical-align:top}}</style><h1>Закупка {p['reg']}</h1><p>{html.escape(p['customer'])}</p><p>НМЦД: {html.escape(p.get('price') or '—')} · начало: {html.escape(p.get('submission_start') or '—')} · окончание: {html.escape(p.get('submission_end') or '—')}</p><a href='{p['url']}'>Карточка ЕИС</a><h2>Нарушения и риски</h2>{blocks or '<p>По выполненным автоматическим правилам нарушений не выявлено.</p>'}<h2>Документы</h2><table>{rows}</table><h2>Что проверено</h2><table>{ck}</table>",encoding='utf-8')

async def process_purchase(client,r,basic=None):
 try:detail=await jget(client,f'/fz223/purchases/{r}')
 except Exception:detail=basic or {}
 card=await fetch_eis_card(client,r)
 st,en=submission_dates(detail)
 price=txt(getv(detail,'initialSum','initialPrice','maxPrice','price','lotPrice','initialContractPrice','nmcd'))
 p={'reg':r,'title':txt(getv(detail,'purchaseName','purchaseObjectInfo','name','title','subject')),'customer':txt(getv(detail,'customer','customerInfo','customerName','organization','placer')),'method':txt(getv(detail,'purchaseMethodName','purchaseMethod','method','placingWayName')),'price':price,'price_num':price_num(price),'published':txt(getv(detail,'publicationDate','publishDate','createDate','publishedAt')),'submission_start':st,'submission_end':en,'deadline':en,'url':eis_url(r)}
 docs=merge_docs(extract_docs(detail),await discover_eis_documents(client,r,card))
 for d in docs:await download_doc(client,r,d)
 finds=analyze(p,docs);checks=build_checks(p,detail,card,docs);annotate(r,docs,finds,checks)
 payload={'status':card.get('status'),'error':card.get('error'),'pairs':card.get('pairs',[]),'visible_text':card.get('visible_text',''),'api_fields':flatten_scalars(detail)}
 save(p,docs,finds,checks,payload)
 return {'reg':r,'documents':len(docs),'checked_documents':sum(d.get('status')=='checked' for d in docs),'findings':len(finds),'checks':len(checks)}

async def cycle():
 if lock.locked():return
 async with lock:
  state.update(running=True,last_start=now(),last_error=None,current_reg=None)
  try:
   async with httpx.AsyncClient(timeout=httpx.Timeout(35,connect=12),follow_redirects=True) as cl:
    fresh=await jget(cl,'/fz223/purchases',{'limit':PAGE_SIZE,'skip':0});fresh=fresh if isinstance(fresh,list) else fresh.get('items',fresh.get('data',[]))
    back=await jget(cl,'/fz223/purchases',{'limit':PAGE_SIZE,'skip':state['backfill_skip']});back=back if isinstance(back,list) else back.get('items',back.get('data',[]));state['backfill_skip']+=PAGE_SIZE
    todo=[];seen=set()
    for x in fresh+back:
     r=regnum(x)
     if r and r not in seen:seen.add(r);todo.append((r,x))
    results=[]
    for r,basic in todo[:DETAIL_LIMIT]:state['current_reg']=r;results.append(await process_purchase(cl,r,basic))
    state['last_result']={'latest':len(fresh),'backfill':len(back),'checked':len(results),'documents':sum(x['documents'] for x in results),'documents_read':sum(x['checked_documents'] for x in results),'checks':sum(x['checks'] for x in results)};state['last_finish']=now()
  except Exception as e:state['last_error']=repr(e);state['last_finish']=now()
  finally:state['running']=False;state['current_reg']=None
async def loop():
 await asyncio.sleep(2)
 while True:await cycle();await asyncio.sleep(SCAN_INTERVAL)
@app.on_event('startup')
async def start():asyncio.create_task(loop())
@app.get('/health')
async def health():return {'status':'ok','version':APP_VERSION,'features':['auto-scan','eis-card-audit','eis-document-download','document-text-analysis','cross-checks','org-form-filter','nmcd-filter','checked-items','marked-documents']}
@app.get('/api/status')
async def status():return state
@app.post('/api/scan-now')
async def scan_now():
 if lock.locked():return {'started':False,'message':'Уже выполняется'}
 asyncio.create_task(cycle());return {'started':True}
@app.post('/api/purchases/{reg}/recheck')
async def recheck(reg:str):
 if not re.fullmatch(r'\d{10,}',reg):raise HTTPException(400,'Некорректный номер')
 if lock.locked():raise HTTPException(409,'Сейчас выполняется другая проверка')
 async with lock:
  state.update(running=True,last_start=now(),last_error=None,current_reg=reg)
  try:
   async with httpx.AsyncClient(timeout=httpx.Timeout(35,connect=12),follow_redirects=True) as cl:res=await process_purchase(cl,reg,None)
   state['last_result']=res;state['last_finish']=now();return res
  except Exception as e:state['last_error']=repr(e);state['last_finish']=now();raise HTTPException(500,str(e))
  finally:state['running']=False;state['current_reg']=None

@app.get('/api/org-forms')
async def forms():
 c=conn();counts={r['org_form']:r['count'] for r in c.execute("SELECT org_form,COUNT(*) count FROM purchases GROUP BY org_form")};c.close()
 names=[x[0] for x in ORG]
 for x in counts:
  if x not in names and x not in ('Другая','Не определена',''):names.append(x)
 return {'items':[{'org_form':x,'count':counts.get(x,0)} for x in names if x not in ('Другая','Не определена','')]}
@app.get('/api/procurements')
@app.get('/api/dashboard')
async def procurements(org_form_:list[str]|None=Query(None,alias='org_form'),result_kind:str|None=None,q:str|None=None,price_band:str|None=None,price_min:float|None=None,price_max:float|None=None,limit:int=200):
 c=conn();where=[];args=[]
 forms=[]
 for v in (org_form_ or []):forms.extend([x.strip() for x in str(v).split(',') if x.strip()])
 if forms:
  where.append('org_form IN ('+','.join('?' for _ in forms)+')');args.extend(forms)
 if result_kind=='violations':where.append('violations>0')
 elif result_kind=='risks':where.append('risks>0')
 elif result_kind=='clarifications':where.append('clarifications>0')
 elif result_kind=='protocols':where.append('protocols>0')
 elif result_kind=='cancellations':where.append('cancellations>0')
 if q:where.append('(reg_number LIKE ? OR customer LIKE ? OR title LIKE ?)');z='%'+q+'%';args += [z,z,z]
 bands={'lt500':(None,500000),'gte500':(500000,None),'500to1000':(500000,1000000),'1to5m':(1000000,5000000),'5to10m':(5000000,10000000),'10to50m':(10000000,50000000),'gte50m':(50000000,None)}
 if price_band in bands:
  lo,hi=bands[price_band]
  if lo is not None:where.append('price_num>=?');args.append(lo)
  if hi is not None:where.append('price_num<?' if price_band=='lt500' else 'price_num<=?');args.append(hi)
 if price_min is not None:where.append('price_num>=?');args.append(price_min)
 if price_max is not None:where.append('price_num<=?');args.append(price_max)
 sql='SELECT * FROM purchases'+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY last_seen DESC LIMIT ?';args.append(min(1000,max(1,limit)))
 rows=[dict(r) for r in c.execute(sql,args)];c.close();stats={'purchases':len(rows),'violations':sum(r['violations'] or 0 for r in rows),'risks':sum(r['risks'] or 0 for r in rows),'protocols':sum(r['protocols'] or 0 for r in rows),'clarifications':sum(r['clarifications'] or 0 for r in rows),'cancellations':sum(r['cancellations'] or 0 for r in rows)};return {'stats':stats,'purchases':rows}
@app.get('/api/purchases/{reg}')
async def detail_api(reg:str):
 if not re.fullmatch(r'[0-9A-Za-z._-]{1,120}',reg):raise HTTPException(400,'Некорректный номер')
 c=conn();p=c.execute('SELECT * FROM purchases WHERE reg_number=?',(reg,)).fetchone()
 if not p:c.close();raise HTTPException(404,'Не найдено')
 docs=[dict(r) for r in c.execute('SELECT * FROM docs WHERE reg_number=? ORDER BY id',(reg,))]
 for d in docs:
  if d.get('local_path'):d['local_url']=f"/files/{reg}/original/{Path(d['local_path']).name}"
  if d.get('annotated_path'):d['annotated_url']=f"/files/{reg}/annotated/{Path(d['annotated_path']).name}"
 finds=[dict(r) for r in c.execute('SELECT * FROM findings WHERE reg_number=? ORDER BY CASE kind WHEN "violation" THEN 1 WHEN "risk" THEN 2 ELSE 3 END,id',(reg,))]
 checks=[dict(r) for r in c.execute('SELECT * FROM checks WHERE reg_number=? ORDER BY CASE status WHEN "violation" THEN 1 WHEN "risk" THEN 2 WHEN "check" THEN 3 WHEN "ok" THEN 4 ELSE 5 END,id',(reg,))]
 c.close();summary={'documents_total':len(docs),'documents_checked':sum(d['status']=='checked' for d in docs),'documents_failed':sum(d['status']!='checked' for d in docs),'checks_total':len(checks),'checks_ok':sum(x['status']=='ok' for x in checks),'checks_attention':sum(x['status'] in ('check','violation','risk') for x in checks)}
 law_checks=[x for x in checks if (x.get('law') or '').startswith('223-ФЗ') or x.get('scope')=='223-ФЗ']
 return {'purchase':dict(p),'documents':docs,'findings':finds,'checks':checks,'law_checks':law_checks,'summary':summary,'report_url':f'/reports/{reg}.html' if (REPORTS/(reg+'.html')).exists() else None}
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

HTML=r'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ЕИС 223-ФЗ Аудитор</title><style>
:root{--bg:#f5f3ed;--ink:#18201b;--green:#155b42;--red:#a22;--amber:#9a6500;--line:#ddd;--muted:#66736c}*{box-sizing:border-box}body{font-family:system-ui;margin:0;background:var(--bg);color:var(--ink)}header{padding:18px 5%;background:#19211c;color:white}main{padding:26px 5%;max-width:1500px;margin:auto}.hero{display:flex;justify-content:space-between;gap:24px;align-items:end}.hero h1{font:46px Georgia;margin:0}.status{background:white;padding:14px;border:1px solid var(--line);max-width:460px}.stats,.filters{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.card{background:white;padding:12px 16px;border:1px solid var(--line);min-width:125px}.card b{font-size:26px;display:block}input,select,button,summary{padding:10px;border:1px solid #bbb;background:white;border-radius:5px}button,summary{cursor:pointer}table{width:100%;border-collapse:collapse;background:white}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:top}.bad{color:var(--red);font-weight:700}.modal{position:fixed;inset:0;background:#0008;display:none;padding:3%;overflow:auto}.panel{background:white;max-width:1260px;margin:auto;padding:22px;border-radius:8px}.finding{border-left:5px solid #b36;padding:10px;margin:10px 0;background:#fafafa}.okbox{border-left:5px solid #286b49;padding:10px;background:#f4faf6}.tabs{display:flex;gap:8px;margin:18px 0;flex-wrap:wrap}.tabbtn.active{background:#19211c;color:white}.tab{display:none}.tab.active{display:block}.badge{padding:3px 7px;border-radius:10px;background:#eee;font-size:12px;white-space:nowrap}.s-violation{background:#fee;color:#900}.s-risk,.s-check{background:#fff3cd;color:#765400}.s-ok{background:#e6f5ec;color:#175d3b}.s-read{background:#eef2f4;color:#46555d}pre{white-space:pre-wrap;background:#f4f4f4;padding:10px}a{color:var(--green)}small{color:var(--muted)}details.multi{position:relative;min-width:210px}details.multi[open] .choices{display:grid}.choices{position:absolute;z-index:20;display:none;grid-template-columns:1fr;background:white;border:1px solid var(--line);padding:8px;max-height:320px;overflow:auto;min-width:300px;box-shadow:0 8px 20px #0002}.choices label{padding:6px;display:flex;gap:8px;align-items:center}.law-note{padding:10px;background:#f7f7f2;border-left:4px solid #5c6f64;margin:10px 0}@media(max-width:720px){.hero{display:block}.hero h1{font-size:34px}table{display:block;overflow:auto}.panel{padding:14px}.modal{padding:1%}.choices{min-width:260px;right:0}}
</style><header><b>223 / ЕИС Аудитор · v1.3</b></header><main><div class="hero"><div><h1>Автоматическая проверка 223-ФЗ</h1><p>Прямое чтение карточки ЕИС · оригиналы документов · контрольный чек-лист 223-ФЗ · точное место спорной формулировки</p></div><div class="status" id="status">Статус…</div></div><div class="stats"><div class="card">Закупок<b id="s1">0</b></div><div class="card">Нарушений<b id="s2">0</b></div><div class="card">Рисков<b id="s3">0</b></div></div><div class="filters"><input id="q" placeholder="Поиск"><details class="multi" id="orgmulti"><summary id="orgsummary">Все формы учреждений</summary><div class="choices" id="orgchecks"></div></details><select id="price"><option value="">Любая НМЦД</option><option value="lt500">До 500 тыс.</option><option value="gte500">От 500 тыс.</option><option value="500to1000">500 тыс. – 1 млн</option><option value="1to5m">1–5 млн</option><option value="5to10m">5–10 млн</option><option value="10to50m">10–50 млн</option><option value="gte50m">От 50 млн</option></select><select id="kind"><option value="">Все результаты</option><option value="violations">Нарушения</option><option value="risks">Риски</option><option value="cancellations">Отмены</option></select><button onclick="scan()">Сканировать сейчас</button></div><table><thead><tr><th>№ закупки</th><th>Заказчик / предмет</th><th>НМЦД</th><th>Результат проверки</th><th></th></tr></thead><tbody id="rows"></tbody></table></main><div class="modal" id="modal" onclick="if(event.target===this)this.style.display='none'"><div class="panel" id="panel"></div></div><script>
const E=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function A(u,o){let r=await fetch(u,o),d=await r.json();if(!r.ok)throw Error(d.detail||r.status);return d}function money(x){let n=Number(x);return Number.isFinite(n)?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(n)+' ₽':'—'}function selectedForms(){return [...document.querySelectorAll('#orgchecks input:checked')].map(x=>x.value)}function updateFormSummary(){let a=selectedForms();E('#orgsummary').textContent=a.length?`Формы: ${a.length}`:'Все формы учреждений'}async function loadForms(){let d=await A('/api/org-forms');E('#orgchecks').innerHTML=d.items.filter(x=>x.org_form&&x.org_form!=='Другая'&&x.org_form!=='Не определена').map(x=>`<label><input type="checkbox" value="${esc(x.org_form)}"> <span>${esc(x.org_form)} <small>(${x.count})</small></span></label>`).join('');document.querySelectorAll('#orgchecks input').forEach(x=>x.onchange=()=>{updateFormSummary();load()})}async function load(){let p=new URLSearchParams();selectedForms().forEach(v=>p.append('org_form',v));if(E('#kind').value)p.set('result_kind',E('#kind').value);if(E('#price').value)p.set('price_band',E('#price').value);if(E('#q').value)p.set('q',E('#q').value);let d=await A('/api/dashboard?'+p);E('#s1').textContent=d.stats.purchases;E('#s2').textContent=d.stats.violations;E('#s3').textContent=d.stats.risks;E('#rows').innerHTML=d.purchases.map(x=>`<tr><td><b>${esc(x.reg_number)}</b></td><td><b>${esc(x.customer)}</b><br><small>${esc(x.title)}</small></td><td><b>${money(x.price_num)}</b></td><td class="${x.violations?'bad':''}">нарушения: ${x.violations||0}<br>риски: ${x.risks||0}<br><small>ЕИС: ${esc(x.card_source_status||'не проверено')}</small></td><td><button onclick="openP('${esc(x.reg_number)}')">Открыть проверку</button></td></tr>`).join('')}function tab(n){document.querySelectorAll('.tab,.tabbtn').forEach(x=>x.classList.remove('active'));E('#t'+n).classList.add('active');E('#b'+n).classList.add('active')}function badge(s){let m={violation:'Нарушение',risk:'Риск',check:'Требует проверки',ok:'ОК',read:'Прочитано'};return `<span class="badge s-${esc(s)}">${m[s]||esc(s)}</span>`}async function recheck(r){let b=E('#recheck');b.disabled=true;b.textContent='Проверка…';try{await A('/api/purchases/'+r+'/recheck',{method:'POST'});await openP(r);await load()}catch(e){alert(e.message)}finally{if(E('#recheck')){E('#recheck').disabled=false;E('#recheck').textContent='Перепроверить ЕИС и оригиналы'}}}function whereLine(x){let z=[];if(x.location)z.push('<b>'+esc(x.location)+'</b>');if(x.document)z.push(esc(x.document));if(x.source&&!x.document)z.push(esc(x.source));if(x.source_url)z.push(`<a target="_blank" href="${esc(x.source_url)}">открыть источник</a>`);return z.join('<br>')||'—'}async function openP(r){let d=await A('/api/purchases/'+r),p=d.purchase,s=d.summary;let res=d.findings.length?d.findings.map(f=>`<div class="finding"><b>${esc(f.title)}</b><p>${badge(f.kind)} · ${f.law_url?`<a target="_blank" href="${esc(f.law_url)}">${esc(f.law)}</a>`:esc(f.law||'')}</p>${f.location?`<p><b>Где в документе:</b> ${esc(f.location)}</p>`:''}<pre>${esc(f.evidence)}</pre><p>${esc(f.note)}</p>${f.source_url?`<a target="_blank" href="${esc(f.source_url)}">Исходный документ</a>`:''}</div>`).join(''):`<div class="okbox"><b>По выполненным автоматическим правилам подтверждённых нарушений не выявлено.</b><p>Отдельно просмотрите пункты со статусом «Требует проверки» во вкладке 223-ФЗ.</p></div>`;let docs=d.documents.map(x=>`<tr><td><b>${esc(x.name)}</b></td><td>${badge(x.status==='checked'?'ok':'check')}<br><small>${esc(x.error||'')}</small></td><td>${x.text_chars||0}</td><td>${x.url?`<a target="_blank" href="${esc(x.url)}">ссылка ЕИС/источника</a>`:'—'} ${x.local_url?`<br><a target="_blank" href="${esc(x.local_url)}">скачанный оригинал</a>`:''} ${x.annotated_url?`<br><a target="_blank" href="${esc(x.annotated_url)}"><b>отмеченная копия</b></a>`:''}</td></tr>`).join('');let checks=d.checks.map(x=>`<tr><td>${badge(x.status)}</td><td>${esc(x.scope)}</td><td><b>${esc(x.item)}</b>${x.field_path?`<br><small>${esc(x.field_path)}</small>`:''}</td><td>${esc(x.evidence)}</td><td>${whereLine(x)}</td><td>${esc(x.note||'')}</td></tr>`).join('');let law=(d.law_checks||[]).map(x=>`<tr><td>${badge(x.status)}</td><td><b>${esc(x.item)}</b>${x.law?`<br><small>${x.law_url?`<a target="_blank" href="${esc(x.law_url)}">${esc(x.law)}</a>`:esc(x.law)}</small>`:''}</td><td>${esc(x.evidence)}</td><td>${whereLine(x)}</td><td>${esc(x.note||'')}</td></tr>`).join('');E('#panel').innerHTML=`<button onclick="E('#modal').style.display='none'">Закрыть</button> <button id="recheck" onclick="recheck('${esc(r)}')">Перепроверить ЕИС и оригиналы</button><h2>${esc(p.reg_number)}</h2><p><b>${esc(p.customer)}</b><br>${esc(p.title)}</p><p>НМЦД: <b>${money(p.price_num)}</b> · начало подачи: <b>${esc(p.submission_start||'—')}</b> · окончание: <b>${esc(p.submission_end||'—')}</b></p><p><a target="_blank" href="${esc(p.url)}">Открыть карточку ЕИС</a> ${d.report_url?`· <a target="_blank" href="${d.report_url}">Отчёт</a>`:''}</p><div class="law-note"><b>Методика контрольного анализа:</b> факт → норма 223-ФЗ → доказательство → место в документе → вывод. Статус «Требует проверки» не считается установленным нарушением без проверки контекста и положения о закупке.</div><div class="tabs"><button id="b1" class="tabbtn active" onclick="tab(1)">Нарушения и риски</button><button id="b2" class="tabbtn" onclick="tab(2)">Оригиналы документов</button><button id="b3" class="tabbtn" onclick="tab(3)">Что прочитано в ЕИС</button><button id="b4" class="tabbtn" onclick="tab(4)">Что проверено по 223-ФЗ</button></div><div id="t1" class="tab active">${res}</div><div id="t2" class="tab"><table><thead><tr><th>Документ</th><th>Статус чтения</th><th>Символов</th><th>Источник / отмеченная копия</th></tr></thead><tbody>${docs}</tbody></table></div><div id="t3" class="tab"><table><thead><tr><th>Статус</th><th>Область</th><th>Что прочитано/проверено</th><th>Данные</th><th>Источник</th><th>Комментарий</th></tr></thead><tbody>${checks}</tbody></table></div><div id="t4" class="tab"><table><thead><tr><th>Статус</th><th>Пункт контроля</th><th>Доказательство</th><th>Где проверено</th><th>Вывод</th></tr></thead><tbody>${law}</tbody></table></div>`;E('#modal').style.display='block'}async function status(){let d=await A('/api/status');E('#status').textContent=d.running?('Идёт проверка ЕИС и документов'+(d.current_reg?' · '+d.current_reg:'')):d.last_error?'Ошибка: '+d.last_error:'Автопроверка включена · версия 1.3'}async function scan(){await A('/api/scan-now',{method:'POST'});status()}E('#kind').onchange=load;E('#price').onchange=load;let t;E('#q').oninput=()=>{clearTimeout(t);t=setTimeout(load,300)};loadForms().then(load);status();setInterval(()=>{status();load()},30000);
</script></html>
'''
@app.get('/',response_class=HTMLResponse)
async def home():return HTML
