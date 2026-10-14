"""Fill missing monetary fields using existing EIS evidence, without re-auditing.

Cheap repairs run at startup. Verified original-document recovery runs in a
single background worker; readiness, findings and document counts are untouched.
"""
from __future__ import annotations
import asyncio
from functools import wraps
import html
import json
import logging
import math
import re
import sqlite3
from decimal import Decimal,ROUND_HALF_UP

from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from v311_verified_customer import _verified_money, read_verified_price
from v295_eis_originals import uid_from_url

LOG=logging.getLogger(__name__)


def money(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if not math.isfinite(value) or value<0 or value>1e15:return None
        number=Decimal(str(value)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
        return f"{number:.2f}",float(number)
    try:
        price,number=_verified_money(value)
        return f"{number:.2f}",number
    except (ValueError,TypeError,OverflowError):
        return None


def price_changes(p,evidence=None):
    """Repair either missing representation, preserving an existing real amount."""
    raw=money(p.get("price"))
    numeric=money(p.get("price_num"))
    chosen=numeric or raw or money((evidence or {}).get("price"))
    if not chosen:return {}
    # Existing conflicting concrete values need a source comparison.
    if raw and numeric and raw[1]!=numeric[1]:return {}
    updates={}
    if not raw:updates["price"]=chosen[0]
    if not numeric:updates["price_num"]=chosen[1]
    return updates


def sync_price_checks(db,reg,price,evidence):
    """Refresh only the two factual extraction checks, never legal conclusions."""
    if not evidence or money(evidence.get("price"))!=money(price):return
    columns={r[1] for r in db.execute('PRAGMA table_info(checks)')}
    if not {'reg_number','item','status','evidence'}.issubset(columns):return
    maximum=evidence.get('price_kind')=='maximum_contract_value'
    value=format_money(price)
    values={'evidence':('Максимальное значение цены договора: ' if maximum else '')+value,
            'source':'Подтверждённые сведения ЕИС',
            'source_url':evidence.get('price_source_url') or evidence.get('source_url') or evidence.get('evidence_page') or '',
            'document':evidence.get('price_document') or evidence.get('document') or '',
            'note':'Сумма получена из сохранённого официального источника ЕИС.'}
    values={k:v for k,v in values.items() if k in columns}
    if not maximum:values['status']='ok'
    db.execute('UPDATE checks SET '+','.join(k+'=?' for k in values)+
        " WHERE reg_number=? AND item IN ('НМЦД','НМЦД распознана как число') "
        "AND status IN ('check','read','pending','unknown')",(*values.values(),reg))


def format_money(value):
    parsed=money(value)
    if not parsed:return 'Не указана в полученных данных'
    return f'{parsed[1]:,.2f}'.replace(',','\u00a0').replace('.',',')+' ₽'


def update_price(db,reg,evidence=None):
    db.row_factory=sqlite3.Row
    row=db.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
    if row is None:return False
    p=dict(row)
    updates=price_changes(p,evidence)
    if not updates:
        sync_price_checks(db,reg,p.get('price'),evidence)
        return False
    if "card_fields_json" in p:
        try:
            card=json.loads(p.get("card_fields_json") or "{}")
            if not isinstance(card,dict):card={}
        except (ValueError,TypeError):card={}
        summary=card.get("summary")
        if not isinstance(summary,dict):summary={};card["summary"]=summary
        summary.update({k:updates.get(k,p.get(k)) for k in ("price","price_num")})
        if evidence and money(evidence.get("price"))==money(summary.get("price")):
            summary["price_kind"]=evidence.get("price_kind") or "nmcd"
            card["price_evidence"]=dict(evidence)
        updates["card_fields_json"]=json.dumps(card,ensure_ascii=False)
    db.execute("UPDATE purchases SET "+",".join(k+"=?" for k in updates)+
               " WHERE reg_number=?",(*updates.values(),reg))
    sync_price_checks(db,reg,updates.get('price',p.get('price')),evidence)
    return True


def cached_evidence(p,verified):
    if verified:return {**verified,"source":"official_eis_card",
                        "source_url":verified.get("evidence_page","")}
    try:
        card=json.loads(p.get("card_fields_json") or "{}")
        for key in ('price_evidence','metadata_evidence'):
            item=card.get(key) or {}
            if (isinstance(item,dict) and
                (item.get("source")=="official_eis_document" or
                 (key=='price_evidence' and item.get('source') is None)) and
                uid_from_url(item.get("price_source_url") or "")):
                from v324_official_price import prices_in_text
                parsed=money(item.get("price"))
                if parsed and any(number==parsed[1] for _,number,_ in prices_in_text(item.get("price_evidence"))):
                    return item
    except (ValueError,TypeError,AttributeError):pass
    return {}


def repair_cached(ns):
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        try:
            verified={r["reg"]:dict(r) for r in db.execute("SELECT * FROM v322_verified_prices")}
        except sqlite3.OperationalError:verified={}
        rows=[dict(r) for r in db.execute("SELECT * FROM purchases")]
        corrected=0
        for row in rows:
            reg=row["reg_number"]
            if update_price(db,reg,cached_evidence(row,verified.get(reg))):corrected+=1
        db.commit()
        return corrected
    finally:db.close()


def missing_regs(ns):
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        return [r["reg_number"] for r in db.execute(
            "SELECT reg_number,price,price_num FROM purchases ORDER BY last_seen DESC,reg_number")
            if not money(r["price_num"]) or not money(r["price"])]
    finally:db.close()


def recover_one(ns,reg):
    from v298_eis_only import official_notice,verified_xml_nmcd
    from v314_eis_metadata import stored_metadata
    evidence=read_verified_price(ns,reg)
    if evidence:
        evidence={**evidence,"source":"official_eis_card",
                  "source_url":evidence.get("evidence_page","")}
    else:
        price=verified_xml_nmcd(official_notice(reg))
        evidence={"price":price,"source":"official_eis_xml"} if price else stored_metadata(ns,reg)
    db=ns["conn"]()
    try:
        corrected=update_price(db,reg,evidence)
        db.commit()
        return corrected
    finally:db.close()


PRICE_JS=r'''<script id="v335-price-format">
window.eis223PriceText=function(p){
 const parse=value=>{
  if(value===null||value===undefined||value===''||typeof value==='boolean')return null;
  const raw=String(value).replace(/\s+/g,' ').trim();
  const m=raw.match(/^([0-9][0-9 ]{0,18}(?:[.,][0-9]{1,2})?)\s*(?:₽|руб\.?|рублей|рубля|рубль|RUB|Российский рубль)?$/i);
  if(!m)return null;
  const n=Number(m[1].replace(/ /g,'').replace(',','.'));
  return Number.isFinite(n)&&n>=0&&n<=1e15?n:null;
 };
 const value=parse(p?.price_num)??parse(p?.price);
 if(value===null)return 'Не указана в полученных данных';
 let kind=p?.price_kind;
 if(!kind){try{kind=JSON.parse(p?.card_fields_json||'{}').summary?.price_kind}catch(e){}}
 return new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(value)+' ₽'+
  (kind==='maximum_contract_value'?' (максимальная цена договора)':'');
};
</script>'''


def patch_html(body):
    if 'id="v335-price-format"' in body:return body
    for name in ('x','p'):
        body=body.replace('money('+name+'.price_num)','window.eis223PriceText('+name+')')
    body=body.replace("['НМЦД',x.price||x.price_num]","['НМЦД',window.eis223PriceText(x)]")
    body=body.replace('esc(priceText(p))','esc(window.eis223PriceText(p))')
    return body.replace('</head>',PRICE_JS+'</head>',1) if '</head>' in body else PRICE_JS+body


def card_price_html(ns,reg):
    db=ns['conn']();db.row_factory=sqlite3.Row
    try:row=db.execute('SELECT * FROM purchases WHERE reg_number=?',(reg,)).fetchone()
    finally:db.close()
    if row is None:return ''
    p=dict(row)
    try:kind=json.loads(p.get('card_fields_json') or '{}').get('summary',{}).get('price_kind')
    except (ValueError,TypeError,AttributeError):kind=None
    label='Максимальная цена договора' if kind=='maximum_contract_value' else 'НМЦД'
    value=format_money(p.get('price_num') if money(p.get('price_num')) else p.get('price'))
    return '<p id="v335-card-price" class="detailmeta"><b>'+label+':</b> '+html.escape(value)+'</p>'


def install(ns):
    app=ns["app"]
    status={"recovered":0,"reviewed":0,"remaining":None,"running":False}
    original_save=ns.get("save")
    if callable(original_save) and not getattr(original_save,"__v335__",False):
        @wraps(original_save)
        def save_with_price(*args,**kwargs):
            p=args[0] if args else kwargs.get("p")
            if isinstance(p,dict):
                reg=p.get("reg") or p.get("reg_number")
                db=ns["conn"]();db.row_factory=sqlite3.Row
                try:previous=db.execute("SELECT price,price_num,card_fields_json FROM purchases WHERE reg_number=?",(reg,)).fetchone()
                finally:db.close()
                prior=dict(previous) if previous else {}
                evidence=prior if money(prior.get("price")) else read_verified_price(ns,str(reg))
                # A numeric-only historical value is valid evidence too.
                if not money(evidence.get("price")) and money(prior.get("price_num")):
                    evidence={"price":money(prior["price_num"])[0]}
                p.update(price_changes(p,evidence))
                card=args[4] if len(args)>4 else kwargs.get("card_payload")
                if isinstance(card,dict) and isinstance(card.get("summary"),dict):
                    card["summary"].update(price=p.get("price"),price_num=p.get("price_num"))
                    if money(prior.get("price"))==money(p.get("price")):
                        try:
                            kind=json.loads(prior.get("card_fields_json") or "{}").get("summary",{}).get("price_kind")
                            if kind:card["summary"]["price_kind"]=kind
                        except (ValueError,TypeError,AttributeError):pass
            return original_save(*args,**kwargs)
        save_with_price.__v335__=True
        ns["save"]=save_with_price

    @app.on_event("startup")
    async def start_price_recovery():
        status["recovered"]=await asyncio.to_thread(repair_cached,ns)
        async def worker():
            await asyncio.sleep(5)
            while True:
                try:
                    status["running"]=True
                    status["recovered"]+=await asyncio.to_thread(repair_cached,ns)
                    regs=await asyncio.to_thread(missing_regs,ns)
                    for reg in regs:
                        try:
                            if await asyncio.to_thread(recover_one,ns,reg):status["recovered"]+=1
                        except Exception:LOG.exception("Cannot recover EIS price reg=%s",reg)
                        status["reviewed"]+=1
                        # Yield between purchases; never launch concurrent OCR workers.
                        await asyncio.sleep(.1)
                    status["remaining"]=len(await asyncio.to_thread(missing_regs,ns))
                    print("V335 NMCD_RECOVERY "+json.dumps(status),flush=True)
                except Exception:LOG.exception("Price recovery sweep failed")
                finally:status["running"]=False
                await asyncio.sleep(1800)
        ns["_v335_price_worker"]=asyncio.create_task(worker())

    @app.get("/api/v335/price-recovery/status",include_in_schema=False)
    async def price_status():
        return {**status,"remaining":len(await asyncio.to_thread(missing_regs,ns))}

    class PricePresentation(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if (request.method!="GET" or response.status_code!=200 or
                "text/html" not in response.headers.get("content-type","") or
                response.headers.get("content-encoding") or
                (request.url.path not in ('/','/manual-purchases') and
                 not request.url.path.startswith('/check-new/'))):return response
            raw=b"".join([part async for part in response.body_iterator])
            headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding')}
            headers['Cache-Control']='no-store'
            body=patch_html(raw.decode('utf-8'))
            match=re.fullmatch(r'/check-new/(3\d{10})',request.url.path)
            if match and 'id="v335-card-price"' not in body:
                panel=await asyncio.to_thread(card_price_html,ns,match[1])
                body=re.sub(r'(<main\b[^>]*>)',lambda m:m[0]+panel,body,count=1,flags=re.I)
            return HTMLResponse(body,headers=headers)
    app.add_middleware(PricePresentation)
    print("V335_PRICE_READY verified_original_recovery=True empty_overwrite_blocked=True",flush=True)
