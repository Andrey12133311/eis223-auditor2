"""V340: show a real procurement amount on every dashboard/card where a
verified amount is present. Keep NMCD, maximum contract price and subject cost
distinct. No fabricated prices, destructive updates, or legal qualification.
"""
from __future__ import annotations
import json
import re
import sqlite3
import asyncio
from fastapi.responses import Response,HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from v335_price_recovery import money

TYPES={"nmcd":"НМЦД",
       "maximum_contract_value":"Максимальная цена договора",
       "subject_cost":"Стоимость предмета закупки"}
PRICE_PATHS={"/api/dashboard","/api/procurements",
             "/api/v204/dashboard","/api/v257/dashboard","/api/v257/manual"}

def _item(reg,row,official,verified):
    result=dict(row)
    existing=money(result.get("price_num")) or money(result.get("price"))
    chosen=existing
    card_json={}
    try:card_json=json.loads(result.get("card_fields_json") or "{}")
    except (TypeError,ValueError):pass
    if not isinstance(card_json,dict):card_json={}
    summary=card_json.get("summary") or {}
    if not isinstance(summary,dict):summary={}
    kind=summary.get("price_kind")
    source=""
    # v338 is sourced from a labelled field on the verified 223-EIS card.
    # Its independent record must be displayable even when the legacy
    # purchases.price fields are empty or stale.
    if official and not bool(official.get("conflict")):
        direct=money(official.get("price"))
        if direct:
            chosen=direct
            kind=official.get("price_kind") if official.get("price_kind") in TYPES else None
            source=official.get("source_page") or ""
    if not chosen and verified:
        direct=money(verified.get("price"))
        if direct:
            chosen=direct
            source=verified.get("evidence_page") or ""
            # Never label an untyped numeric value as subject cost.
            kind=verified.get("price_kind") if verified.get("price_kind") in TYPES else kind
    if chosen:
        result["price_num"]=chosen[1]
        result["price"]=chosen[0]
    else:
        result["price_num"]=None
        result["price"]=""
    result["price_kind"]=kind if kind in TYPES else None
    result["price_label"]=TYPES.get(result["price_kind"],"Стоимость закупки")
    result["price_source_url"]=source
    result["price_conflict"]=bool(official and official.get("conflict"))
    if result["price_conflict"] and official:
        # Show the actual official card amount separately, not as a silent
        # replacement for a conflicting previously stored amount.
        result["official_price"]=official.get("price")
        result["official_price_kind"]=official.get("price_kind")
        result["price_label"]="Стоимость закупки (требуется сверка)"
    return result

def decorate(ns,data):
    """A single fast DB snapshot is used for a page; existing rows are untouched."""
    if not isinstance(data,dict):return data
    field=None
    for key in ("purchases","items"):
        if isinstance(data.get(key),list):
            field=key;break
    if field:
        records=data[field]
        rows=[r for r in records if isinstance(r,dict)]
    elif isinstance(data.get("purchase"),dict):
        field="purchase";rows=[data["purchase"]]
    elif data.get("reg_number"):
        field="single";rows=[data]
    else:return data
    regs=list(dict.fromkeys(str(r.get("reg_number") or r.get("reg") or "")
                           for r in rows if re.fullmatch(r"3\d{10}",
                           str(r.get("reg_number") or r.get("reg") or ""))))
    if not regs:return data
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        placeholders=",".join("?" for _ in regs)
        official={}
        verified={}
        try:
            for r in db.execute("SELECT * FROM v338_card_price WHERE reg IN ("+placeholders+")",regs):
                official[r["reg"]]=dict(r)
        except sqlite3.OperationalError:pass
        try:
            for r in db.execute("SELECT * FROM v322_verified_prices WHERE reg IN ("+placeholders+")",regs):
                verified[r["reg"]]=dict(r)
        except sqlite3.OperationalError:pass
        output=[]
        for row in rows:
            reg=str(row.get("reg_number") or row.get("reg") or "")
            output.append(_item(reg,row,official.get(reg),verified.get(reg)))
        if field=="purchase":data["purchase"]=output[0]
        elif field=="single":data=output[0]
        else:
            lookup={str(r.get("reg_number") or r.get("reg") or ""):r for r in output}
            data[field]=[lookup.get(str(r.get("reg_number") or r.get("reg") or ""),r)
                         if isinstance(r,dict) else r for r in records]
        return data
    finally:db.close()

SCRIPT=r'''<script id="v340-price">
(function(){
'use strict';
function parse(v){
 if(v===null||v===undefined||v===''||typeof v==='boolean')return null;
 const text=String(v).replace(/[\u00a0\u202f]/g,' ').trim()
   .replace(/(?:российских?\s+рубл(?:ей|ь)|рублей|рубля|рубль|руб\.?|₽|RUB)\s*$/i,'').trim();
 if(!/^\d[\d ]{0,18}(?:[,.]\d{1,2})?$/.test(text))return null;
 const n=Number(text.replace(/ /g,'').replace(',','.'));
 return Number.isFinite(n)&&n>=0&&n<=1e15?n:null;
}
window.eis223PriceText=function(p){
 const num=parse(p?.price_num)??parse(p?.price);
 if(num===null)return 'Стоимость не указана в полученных данных ЕИС';
 const kind=p?.price_kind||'';
 const text=new Intl.NumberFormat('ru-RU',{
   minimumFractionDigits:2,maximumFractionDigits:2
 }).format(num)+' ₽';
 const label=kind==='subject_cost'?' (стоимость предмета закупки)':
   kind==='maximum_contract_value'?' (максимальная цена договора)':'';
 return text+label+(p?.price_conflict?' · требуется сверка с ЕИС':'');
};
// Keep the dynamically added purchase-detail panel consistent with the
// actual provenance, rather than calling all price kinds "НМЦД".
function refresh(){
 document.querySelectorAll('#v257-extra').forEach(panel=>{
   const rows=[...panel.querySelectorAll('div')];
   for(const row of rows){
     const title=row.querySelector('b');
     if(title&&(title.textContent||'').trim()==='НМЦД:'){
       title.textContent='Стоимость закупки: ';
     }
   }
 });
}
let scheduled=false;
new MutationObserver(()=>{if(!scheduled){scheduled=true;
 requestAnimationFrame(()=>{scheduled=false;refresh()});}}).observe(
 document.documentElement,{subtree:true,childList:true});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',refresh);
else refresh();
})();
</script>'''


# Legacy card templates are sometimes re-rendered and contain three identical
# violation blocks. Match only a verified identical title AND substantive
# quotation; preserve distinct amounts, dates, and file identities.
DUP_SCRIPT=r'''<script id="v340-result-dedup">
(function(){
'use strict';
const basic=s=>String(s||'').toLowerCase().replace(/ё/g,'е')
  .replace(/\s+/g,' ').trim();
const cleaned=s=>basic(s).replace(/файл\s*\/\s*источник\s*:[^;]{0,240};\s*фрагмент\s*:\s*/gi,'');
const normalize=s=>cleaned(s).replace(/[^a-z0-9а-я]+/g,'');
function dedup(){
 if(!/^\/check-new\/3\d{10}$/.test(location.pathname)&&
    location.pathname!=='/')return;
 const area=document.querySelector('main')||document.body;
 const nodes=[...area.querySelectorAll(
   '.finding,.result-item,article[style*="border-left"],div[style*="border-left"],#v267-canonical li')];
 const shown=[];
 for(const node of nodes){
   if(!node.isConnected)continue;
   if(node.closest('#v269-all-findings,#v268-findings-panel'))continue;
   const titleNode=node.querySelector('h3,h4,h5,strong,b');
   const title=basic(titleNode?.textContent||'');
   if(title.length<10)continue;
   const text=cleaned(node.textContent||'');
   const pos=text.search(/(?:расчет\s+производится|оплата\s+(?:производится|осуществляется)|срок\s+подачи|дата\s+публикации)/);
   const proof=normalize(pos>=0?text.slice(pos,pos+410):text);
   if(proof.length<95)continue;
   const document=basic(node.querySelector('small')?.textContent||'');
   const similarity=(a,b)=>{
     if(a===b)return true;
     const short=a.length<b.length?a:b,long=a.length<b.length?b:a;
     return short.length>=110&&long.includes(short);
   };
   const match=shown.find(x=>x.title===title&&
     (!x.document||!document||x.document===document)&&similarity(x.proof,proof));
   if(match){
     // Keep the most informative card when duplicate HTML differs only
     // by the presence of a file/source wrapper.
     if(text.length>match.text.length*1.3){
       match.node.remove();shown.splice(shown.indexOf(match),1);
       shown.push({node,title,proof,document,text});
     }else node.remove();
   }else shown.push({node,title,proof,document,text});
 }
}
let scheduled=false;
new MutationObserver(()=>{if(!scheduled){scheduled=true;requestAnimationFrame(()=>{
 scheduled=false;dedup();});}}).observe(document.documentElement,
 {subtree:true,childList:true});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',dedup);
else dedup();
})();
</script>'''

def install(ns):
    app=ns["app"]
    class PriceDisplay(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!="GET" or response.status_code!=200:return response
            path=request.url.path
            typ=response.headers.get("content-type","").lower()
            js_api=(path in PRICE_PATHS or
                    bool(re.fullmatch(r"/api/purchases/3\d{10}",path)) or
                    bool(re.fullmatch(r"/api/v257/purchase-extra/3\d{10}",path)))
            if js_api and "json" in typ:
                buf=b"".join([part async for part in response.body_iterator])
                try:
                    data=json.loads(buf)
                    data=await asyncio.to_thread(decorate,ns,data)
                    if isinstance(data,dict):
                        from v333_dedup_ui import collapse
                        if isinstance(data.get("findings"),list):
                            data["findings"]=collapse(data["findings"])
                        if isinstance(data.get("checks"),list):
                            data["checks"]=collapse(data["checks"],field="item")
                        if isinstance(data.get("law_checks"),list):
                            data["law_checks"]=collapse(data["law_checks"],field="item")
                    body=json.dumps(data,ensure_ascii=False,default=str).encode("utf-8")
                except (ValueError,TypeError,sqlite3.DatabaseError) as err:
                    print("V340 PRICE_UI_ERROR",str(err)[:240],flush=True)
                    body=buf
                headers={k:v for k,v in response.headers.items() if k.lower() not in
                         ("content-length","content-encoding","transfer-encoding")}
                headers["Cache-Control"]="no-store"
                return Response(body,status_code=200,media_type="application/json",headers=headers)
            if path in ("/","/manual-purchases") or path.startswith("/check-new/"):
                if "text/html" not in typ or response.headers.get("content-encoding"):return response
                buf=b"".join([part async for part in response.body_iterator])
                html=buf.decode("utf-8",errors="replace")
                if 'id="v340-price"' not in html:
                    html=html.replace("</body>",SCRIPT+"</body>",1) if "</body>" in html else html+SCRIPT
                if 'id="v340-result-dedup"' not in html:
                    html=html.replace("</body>",DUP_SCRIPT+"</body>",1) if "</body>" in html else html+DUP_SCRIPT
                # The column contains NMCD OR maximum price OR subject
                # cost; a static NMCD header incorrectly renames either.
                html=html.replace("<th>НМЦД</th>","<th>Стоимость закупки</th>")
                headers={k:v for k,v in response.headers.items() if k.lower() not in
                         ("content-length","content-encoding","transfer-encoding")}
                headers["Cache-Control"]="no-store"
                return HTMLResponse(html,status_code=200,headers=headers)
            return response
    app.add_middleware(PriceDisplay)
    print("V340 PRICE_UI_READY official_card_preferred=True old_price_fallback=True "
          "typed_labels=True no_destructive_db_updates=True",flush=True)
