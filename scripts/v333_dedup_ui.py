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


def _proof(value):
    """Normalize printed wrappers without dropping the underlying source text."""
    s=str(value or "").casefold().replace("ё","е").replace("\xa0"," ")
    s=re.sub(r"файл\s*/\s*источник\s*:[^;\n]{0,220};\s*фрагмент\s*:\s*","",s,flags=re.I)
    # A payment provision is an episode: keep the full numeric deadline and
    # starting event, but not unrelated headings around the excerpt.
    clause=re.search(
        r"(?:расчет\s+производится|оплат[аыуе]\s+производится|"
        r"оплата\s+осуществляется)[\s\S]{0,500}?"
        r"(?:\d{1,3}\s+(?:рабочих|календарных)\s+дн\w*)"
        r"[\s\S]{0,220}",s,re.I)
    if clause:s=clause.group(0).split("|",1)[0]
    return re.sub(r"\s+"," ",s).strip(" \t\n.;,")


def _different_fact(a,b):
    """Don't collapse independent deadlines, amounts, or dated occurrences."""
    date=re.compile(r"\b(?:\d{2}[.]\d{2}[.]20\d{2}|20\d{2}-\d{2}-\d{2})\b")
    days=re.compile(r"\b\d+\s+(?:рабочих|календарных)\s+дн\w*")
    percent=re.compile(r"\b\d+(?:[,.]\d+)?\s*%")
    for pat in (date,days,percent):
        aa=set(pat.findall(a));bb=set(pat.findall(b))
        if aa and bb and aa!=bb:return True
    return False


def _same_finding(a,b,field):
    a_status=a.get("status") or a.get("kind")
    b_status=b.get("status") or b.get("kind")
    if a_status and b_status and a_status!=b_status:return False
    ta=issue_key(a.get(field) or a.get("item") or a.get("code"))
    tb=issue_key(b.get(field) or b.get("item") or b.get("code"))
    if not ta or ta!=tb:return False
    from v348_cross_document_legal import (finding_protocol_number, finding_clarification_number,
                                           same_original_identity, event_occurrence_text)
    left_code = str(a.get("code") or a.get("field_path") or "")
    right_code = str(b.get("code") or b.get("field_path") or "")
    same_origin=False
    for family, identifier in (("PROTOCOL-", finding_protocol_number),
                               ("CLARIFICATION-", finding_clarification_number)):
        if family in left_code and family in right_code:
            same_origin=same_original_identity(a,b)
            left, right = identifier(a), identifier(b)
            if left and right and left != right and not same_origin:
                return False
    # Distinct legal bases, documents, source URLs and real locations cannot
    # be hidden merely because the violation title is shared.
    for attr in ("status","kind","law","legal_basis","document","source_url"):
        if same_origin and attr in ("document","source_url"):continue
        x=str(a.get(attr) or "").strip().casefold()
        y=str(b.get(attr) or "").strip().casefold()
        if x and y and x!=y:return False
    ea=_proof(event_occurrence_text(a));eb=_proof(event_occurrence_text(b))
    if not ea or not eb:return ea==eb and bool(str(a.get("code") or a.get("field_path") or "").strip()) and bool(str(b.get("code") or b.get("field_path") or "").strip())
    if _different_fact(ea,eb):return False
    if ea==eb:return True
    short,long=sorted((ea,eb),key=len)
    if len(short)>=70 and short in long:return True
    # Truncated UI snippets can begin a few chars apart. Require a long
    # common quote, not just a similar label or the same number of days.
    import difflib
    match=difflib.SequenceMatcher(None,ea[:1600],eb[:1600],autojunk=False).find_longest_match()
    return match.size>=125 and match.size>=.70*len(short)


def collapse(items,field="title",violation_only=False):
    answer=[]
    for item in items:
        if not isinstance(item,dict):
            answer.append(item)
            continue
        if violation_only and item.get("status")!="violation":
            answer.append(item)
            continue
        found=next((i for i,existing in enumerate(answer)
                    if isinstance(existing,dict) and _same_finding(existing,item,field)),None)
        if found is None:
            answer.append(item)
        else:
            # Retain the best documented representation of the SAME proof.
            old=answer[found]
            def quality(obj):
                return (sum(bool(obj.get(k)) for k in
                            ("document","source_url","law","location","note"))*1000
                        +min(len(str(obj.get("evidence") or "")),1500))
            if quality(item)>quality(old):
                answer[found]=item
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
 // The legacy check-card renderer sometimes prints the same legal clause
 // several times with different wrappers ("Файл/источник" vs raw excerpt).
 // Compare the actual obligation, not the whole node text. This only changes
 // visible cards; source rows and original documents remain untouched.
 const main=document.querySelector('main')||document.body;
 const results=[...main.querySelectorAll('h2,h3')].find(h=>
    /^Результаты проверки$/i.test((h.textContent||'').trim()));
 const root=results?.closest('section,.card,article')||main;
 const candidates=[...root.querySelectorAll('article,.finding,.result-item,[style*="border-left"]')];
 const extra=[...main.querySelectorAll('article,.finding,.result-item,[style*="border-left"]')];
 // Some legacy layouts put the heading and cards in adjacent containers.
 const all=[...new Set([...candidates,...extra])].filter(n=>
   n.isConnected&&/^\/check-new\//.test(location.pathname));
 const unique=new Map();
 const compact=s=>String(s||'').toLowerCase().replace(/ё/g,'е')
   .replace(/\s+/g,' ').trim();
 function evidenceKey(body){
   body=compact(body).replace(/файл\s*\/\s*источник\s*:[^;]{0,220};\s*фрагмент\s*:\s*/gi,'');
   const pos=body.search(/(?:расчет производится|оплата производится|оплата осуществляется)/i);
   if(pos>=0){
      let part=body.slice(pos,pos+460).split('|',1)[0];
      const end=part.match(/(?:рабочих|календарных)\s+дн\w*[\s\S]{0,130}?(?:приемк\w*|приёмк\w*|подписани\w*|поставк\w*)[^.]*\./);
      if(end)part=part.slice(0,end.index+end[0].length);
      return norm(part);
   }
   return norm(body);
 }
 for(const node of all){
   const lead=node.querySelector('h3,h4,h5,strong,b');
   if(!lead)continue;
   const title=compact(lead.textContent);
   if(title.length<12 || title==='нарушение')continue;
   const contents=compact(node.textContent||'');
   if(!contents.includes('нарушение')&&!contents.includes('требует проверки'))continue;
   // Ignore broad containers that include several separately titled findings.
   if(node.querySelectorAll('h3,h4,h5').length>1)continue;
   const proof=evidenceKey(contents);
   if(proof.length<65)continue;
   const numbers=(contents.match(/\b\d+\s+(?:рабочих|календарных)\s+дн\w*/g)||[]).join('|');
   const key=norm(title)+'|'+numbers+'|'+proof.slice(0,500);
   const prev=unique.get(key);
   if(prev){
      // Preserve the version with more provenance in its visible text.
      if(/файл\s*\/\s*источник/i.test(contents)&&
         !/файл\s*\/\s*источник/i.test(prev.textContent||'')){
        prev.remove();unique.set(key,node);
      }else node.remove();
   }else unique.set(key,node);
 }

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
            return collapse(result,field="item",violation_only=False) if isinstance(result,list) else result
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
                        # The legacy findings endpoint mixes confirmed
                        # findings and checklist copies of the same episode.
                        # Preserve all source rows, but return each legal act once.
                        from v352_dashboard_truth import collapse_legal_events
                        from v365_consultant_refs import enrich
                        confirmed=[];others=[]
                        for item in obj["items"]:
                            if not isinstance(item,dict):
                                others.append(item);continue
                            state=str(item.get("status") or item.get("kind") or "violation").lower()
                            if state in ("violation","нарушение"):
                                confirmed.append(item)
                            else:
                                others.append(item)
                        obj["items"]=[enrich(x) for x in collapse_legal_events(confirmed)]+collapse(others)
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
