"""Deduplicated legal findings and minimal 223-FZ user-interface cleanup.

The source data and audit evidence remain intact. New audit results are
deduplicated by legal issue; old database rows are canonicalized on display.
ODT inspection-sheet 'Комментарий' cells are kept blank.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from functools import wraps
from xml.etree import ElementTree as ET
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

ODF_TABLE="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
ODF_TEXT="urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def issue_key(title):
    value=str(title or "").casefold().replace("ё","е")
    value=re.sub(r"\s+"," ",value).strip()
    # These two legacy rules describe the same request-deadline condition.
    if ("разъяснен" in value and "срок" in value and
        any(x in value for x in ("раньше допустимого","соответствие срока","ограничен раньше"))):
        return "срок запросов разъяснений ранее допустимого"
    return re.sub(r"[^a-z0-9а-я]+","",value)


def collapse(items,field="title",violation_only=False):
    answer=[]
    positions={}
    for item in items:
        if not isinstance(item,dict):
            answer.append(item)
            continue
        if violation_only and item.get("status") not in ("violation",):
            answer.append(item)
            continue
        key=issue_key(item.get(field) or item.get("item") or item.get("code"))
        if not key:
            answer.append(item)
            continue
        if key in positions:
            # Prefer useful documentary evidence, but never fabricate a new
            # finding by concatenating incompatible snippets.
            old=answer[positions[key]]
            if len(str(item.get("evidence") or ""))>len(str(old.get("evidence") or "")):
                answer[positions[key]]=item
        else:
            positions[key]=len(answer)
            answer.append(item)
    return answer


def blank_inspection_comments(data):
    """Clear only the body cells beneath an ODT 'Комментарий' column."""
    if len(data)>18*1024*1024 or not zipfile.is_zipfile(io.BytesIO(data)):
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names=archive.namelist()
        if "content.xml" not in names or len(names)>250:return data
        original=archive.read("content.xml")
        if len(original)>12*1024*1024 or b"<!DOCTYPE" in original.upper():
            return data
        if b"<!ENTITY" in original.upper():return data
        document=ET.fromstring(original)
        changed=False
        for table in document.iter("{"+ODF_TABLE+"}table"):
            rows=list(table.iter("{"+ODF_TABLE+"}table-row"))
            for idx,row in enumerate(rows[:12]):
                cells=list(row.findall("{"+ODF_TABLE+"}table-cell"))
                headers=[re.sub(r"\s+"," "," ".join(
                    "".join(p.itertext()) for p in c.iter("{"+ODF_TEXT+"}p"))).strip().casefold()
                    for c in cells]
                indices=[j for j,s in enumerate(headers) if s in ("комментарий","комментарии")]
                if not indices:continue
                for later in rows[idx+1:]:
                    columns=list(later.findall("{"+ODF_TABLE+"}table-cell"))
                    for position in indices:
                        if position>=len(columns):continue
                        cell=columns[position]
                        for para in cell.iter("{"+ODF_TEXT+"}p"):
                            para.text=None
                            for child in list(para):para.remove(child)
                        changed=True
                break
        if not changed:return data
        output=io.BytesIO()
        with zipfile.ZipFile(output,"w") as dst:
            for info in archive.infolist():
                dst.writestr(info,ET.tostring(document,encoding="utf-8",xml_declaration=True)
                             if info.filename=="content.xml" else archive.read(info.filename))
        return output.getvalue()


CSS=r'''<style id="v333-clean">
/* Keep legacy input nodes for old JS consumers, but remove their visual UI. */
body:has(#filters) #filters #inn,
body:has(#filters) #filters #query,
body:has(#filters) #v257-inn-search,
body:has(#filters) #v302-inn-form,
#v269-all-findings,#v268-findings-panel{display:none!important}
#v302-search-panel .v302-title small{display:none!important}
body:has(#v302-reg-form) main section.card:has(>form#start){display:none!important}
#v267-canonical{overflow-wrap:anywhere;word-break:normal}
#v267-canonical p{overflow-wrap:anywhere;font-weight:400}
</style>'''

SCRIPT=r'''<script id="v333-clean-script">
(function(){
'use strict';
if(window.__v333Cleanup)return;window.__v333Cleanup=true;
const norm=s=>String(s||'').toLocaleLowerCase('ru-RU').replace(/ё/g,'е')
  .replace(/[^a-z0-9а-я]+/g,'').trim();
function cleanup(){
 const home=location.pathname==='/',manual=location.pathname==='/manual-purchases';
 const card=/^\/check-new\/\d{10,20}/.test(location.pathname);
 document.querySelectorAll('button,input[type=button],a').forEach(b=>{
   const title=String(b.textContent||b.value||'').replace(/\s+/g,' ').trim();
   if(/приостановить\s+загрузку\s+документов/i.test(title))b.remove();
 });
 document.querySelectorAll('#v269-all-findings,#v268-findings-panel').forEach(x=>x.remove());
 if(home){
   // These two inputs remain in the DOM so earlier dashboard JS can still
   // build query parameters without throwing an exception.
   document.querySelectorAll('#v257-inn-search').forEach(x=>x.remove());
   const inn=document.getElementById('inn');
   if(inn)inn.setAttribute('aria-hidden','true');
   const query=document.getElementById('query');
   if(query)query.setAttribute('aria-hidden','true');
 }
 if(manual){
   const label=document.querySelector('label[for="v302-reg"]');
   if(label)label.textContent='Проверить по номеру ЕИС';
 }
 if(!card)return;
 const panel=document.getElementById('v267-canonical');
 const heading=panel?.querySelector('h2');
 if(heading && !/^Нарушения(?:\s*:\s*\d+)?$/i.test((heading.textContent||'').trim())){
    const count=(heading.textContent||'').match(/\d+/)?.[0];
    heading.textContent='Нарушения'+(count?': '+count:'');
 }
 // Result cards are sometimes duplicated independently of the canonical API.
 const results=[...document.querySelectorAll('h2,h3')].find(h=>
    /^Результаты проверки$/i.test((h.textContent||'').trim()));
 if(!results)return;
 const parent=results.closest('section,.card,article')||results.parentElement;
 if(!parent)return;
 const candidates=[...parent.querySelectorAll('article,.finding,.result-item,[style*="border-left"]')];
 const unique=new Set();
 candidates.forEach(node=>{
    const lead=node.querySelector('h3,h4,h5,strong,b');
    const name=lead?.textContent?.trim()||'';
    if(!name||norm(name)==='нарушение'||name.length<9)return;
    const key=norm(name);
    if(unique.has(key))node.remove();else unique.add(key);
 });
}
let scheduled=false;
new MutationObserver(()=>{
 if(scheduled)return;
 scheduled=true;
 requestAnimationFrame(()=>{scheduled=false;cleanup()});
}).observe(document.documentElement,{subtree:true,childList:true});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',cleanup);
else cleanup();
})();
</script>'''


def install(ns):
    """Install after all prior plugins so the final response is canonical."""
    analyze=ns.get("analyze")
    if callable(analyze) and not getattr(analyze,"__v333__",False):
        @wraps(analyze)
        def analyze_unique(*args,**kwargs):
            result=analyze(*args,**kwargs)
            return collapse(result) if isinstance(result,list) else result
        analyze_unique.__v333__=True
        ns["analyze"]=analyze_unique
    checks=ns.get("build_checks")
    if callable(checks) and not getattr(checks,"__v333__",False):
        @wraps(checks)
        def checks_unique(*args,**kwargs):
            result=checks(*args,**kwargs)
            return collapse(result,field="item",violation_only=True) if isinstance(result,list) else result
        checks_unique.__v333__=True
        ns["build_checks"]=checks_unique

    app=ns["app"]
    class FinalQualityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!="GET" or response.status_code!=200:
                return response
            path=request.url.path
            ctype=response.headers.get("content-type","").lower()
            if re.fullmatch(r"/api/v267/findings/\d{10,20}",path) and "json" in ctype:
                body=b"".join([part async for part in response.body_iterator])
                try:
                    obj=json.loads(body)
                    if isinstance(obj,dict) and isinstance(obj.get("items"),list):
                        obj["items"]=collapse(obj["items"])
                        obj["total"]=len(obj["items"])
                    return JSONResponse(obj,headers={"Cache-Control":"no-store"})
                except (ValueError,TypeError):
                    return Response(body,status_code=200,media_type="application/json")
            dispo=response.headers.get("content-disposition","").lower()
            is_odt=("opendocument" in ctype and "text" in ctype) or (
                ".odt" in dispo or path.lower().endswith(".odt"))
            if is_odt and "memo/" not in path:
                binary=b"".join([part async for part in response.body_iterator])
                try:filtered=blank_inspection_comments(binary)
                except (ValueError,KeyError,zipfile.BadZipFile,ET.ParseError):
                    filtered=binary
                hdr={k:v for k,v in response.headers.items() if k.lower() not in
                     ("content-length","content-encoding","transfer-encoding")}
                return Response(filtered,media_type=ctype.split(";")[0],
                                status_code=200,headers=hdr)
            if path not in ("/","/manual-purchases") and not path.startswith("/check-new/"):
                return response
            if "text/html" not in ctype or response.headers.get("content-encoding"):
                return response
            body=b"".join([part async for part in response.body_iterator])
            try:html=body.decode("utf-8")
            except UnicodeError:return Response(body,status_code=200,media_type="text/html")
            # Remove entire legacy scripts that independently inject the same
            # violations into the DOM; keep the canonical v267 panel.
            html=re.sub(r'<script\s+id=["\x27](?:v269-inline-findings|v268-home-findings)["\x27][^>]*>.*?</script>',
                        "",html,flags=re.I|re.S)
            html=html.replace("Полный перечень нарушений: ","Нарушения: ")
            html=html.replace("Нарушения в базе не обнаружены.","Нарушения не выявлены.")
            html=html.replace("По номеру закупки ЕИС</label>","Проверить по номеру ЕИС</label>")
            if 'id="v333-clean"' not in html:
                html=html.replace("</head>",CSS+"</head>",1) if "</head>" in html else CSS+html
            if 'id="v333-clean-script"' not in html:
                html=html.replace("</body>",SCRIPT+"</body>",1) if "</body>" in html else html+SCRIPT
            hdr={k:v for k,v in response.headers.items() if k.lower() not in
                 ("content-length","content-encoding","transfer-encoding")}
            hdr["Cache-Control"]="no-store"
            return HTMLResponse(html,status_code=200,headers=hdr)
    app.add_middleware(FinalQualityMiddleware)
    print("V333 QUALITY_READY canonical_findings=True memo_justify=True clean_ui=True",flush=True)
