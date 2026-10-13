"""Render *only* ODT service memos from the user's exact uploaded FAS template.

Original branding, styles, footer, recipient and signature stay in the ODT;
macros and dialogs are stripped when the user registers the template.
Only procurement-specific paragraphs change. No fabricated violations or
unverified Rusprofile claims.
"""
from __future__ import annotations
import asyncio
from datetime import datetime,timedelta,timezone
import io
import json
import logging
import os
import re
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from fastapi import HTTPException,Request
from fastapi.responses import Response,HTMLResponse
from v295_eis_originals import auth,root,save_atomic

logger=logging.getLogger(__name__)
MIME="application/vnd.oasis.opendocument.text"
ODT_SIZE_LIMIT=4*1024*1024
TEXT="{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
NS_RE=("http://www.w3.org/2001/XMLSchema",)
REG=re.compile(r"[0-9]{11,20}$")
INN=re.compile(r"(?:[0-9]{10}|[0-9]{12})$")
SHORT_PREPS=re.compile(
    r"(?<![\w-])((?:в|во|к|ко|на|по|о|об|обо|с|со|у|из|за|до|от|а|и|но|при))"
    r"[\u0020\t]+(?=[А-Яа-яЁёA-Za-z0-9«])",re.IGNORECASE)

def template_path():
    return root()/"eis223_service_memo"/"template_clean.odt"

def _safe_template(raw:bytes)->bytes:
    if len(raw)<1000 or len(raw)>ODT_SIZE_LIMIT:
        raise ValueError("Шаблон ODT имеет недопустимый размер")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zin:
            if zin.read("mimetype")!=MIME.encode():
                raise ValueError("Требуется ODT LibreOffice, а не DOCX")
            infos=zin.infolist()
            if len(infos)>2500 or sum(z.file_size for z in infos)>24*1024*1024:
                raise ValueError("Превышены безопасные лимиты ODT")
            content=zin.read("content.xml")
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ValueError("Неподдерживаемые XML сущности")
            doc=ET.fromstring(content)
            ps=doc.findall(".//"+TEXT+"p")
            words=" ".join("".join(p.itertext()) for p in ps)
            if ("СЛУЖЕБНАЯ ЗАПИСКА" not in words or
                "Управление контроля размещения государственного заказа" not in words or
                "С учетом вышеизложенного" not in words or
                len(ps)<35):
                raise ValueError("ODT не соответствует приложенному двухстраничному шаблону")
            out=io.BytesIO()
            with zipfile.ZipFile(out,"w") as z:
                for info in infos:
                    name=info.filename
                    if (name.startswith(("Scripts/","Dialogs/","Configurations2/","Thumbnails/")) or
                        name.startswith("/") or ".." in Path(name).parts):
                        continue
                    payload=zin.read(info)
                    if name=="META-INF/manifest.xml":
                        xml=ET.fromstring(payload)
                        field="{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}full-path"
                        for item in list(xml):
                            p=item.get(field,"")
                            if p.startswith(("Scripts/","Dialogs/","Configurations2/","Thumbnails/")):
                                xml.remove(item)
                        payload=ET.tostring(xml,encoding="utf-8",xml_declaration=True)
                    z.writestr(name,payload,
                         compress_type=zipfile.ZIP_STORED if name=="mimetype" else zipfile.ZIP_DEFLATED)
            return out.getvalue()
    except (zipfile.BadZipFile,KeyError,ET.ParseError) as exc:
        raise ValueError("ODT повреждён или не соответствует шаблону") from exc

def _plain(paragraph):
    return "".join(paragraph.itertext()).strip()

def _copy_text(paragraph,value):
    # Retain paragraph style and all page layout. For changed case-specific
    # paragraphs, keep their original leading inline style if present.
    leading=next((el for el in paragraph if el.tag==TEXT+"span"),None)
    attributes=dict(leading.attrib) if leading is not None else None
    for el in list(paragraph):paragraph.remove(el)
    if attributes:
        para_span=ET.SubElement(paragraph,TEXT+"span",attributes)
        para_span.text=value
        paragraph.text=None
    else:
        paragraph.text=value

def _nonbreaking(text):
    return SHORT_PREPS.sub(lambda m:m.group(1)+"\u00a0",text)

def _sentence(text,maxlen=1500):
    text=re.sub(r"\s+"," ",str(text or "")).strip()
    text=text.replace("\0","").replace("\u2028"," ")
    return text[:maxlen]

def _human_org(value):
    # Keep legal-form acronyms but never put a FULL organisation name in caps.
    value=_sentence(value,450)
    if not value:return ""
    if sum(ch.isupper() for ch in value if ch.isalpha()) >= .85*max(1,sum(ch.isalpha() for ch in value)):
        acronyms=r"(?:ООО|АО|ПАО|ЗАО|ОАО|ФГУП|ФГБУ|ФГБОУ|ГБУ|МБУ|АНО|НКО|ГУП|МУП)"
        saved=[]
        def save(m):
            saved.append(m.group(0))
            return "@LEGAL"+str(len(saved)-1)+"@"
        value=re.sub(r"\b"+acronyms+r"\b",save,value)
        value=value.lower()
        value=value[0].upper()+value[1:]
        value=re.sub(r"@legal([0-9]+)@",lambda m:saved[int(m.group(1))],value,flags=re.I)
        value=re.sub(r"([.!?]\s+)([а-яё])",lambda m:m.group(1)+m.group(2).upper(),value)
        value=re.sub(r"«([а-яё])",lambda m:"«"+m.group(1).upper(),value)
    return value

def _fetch_company_rusprofile(inn,provided_name):
    # Rusprofile has no publicly guaranteed API. Only trust a scrape that
    # contains the EXACT INN and a matching company card in the same block.
    if not INN.fullmatch(inn):
        return None
    cache=root()/"eis223_service_memo"/"rusprofile"/(inn+".json")
    try:
        record=json.loads(cache.read_text(encoding="utf8"))
        age=datetime.now(timezone.utc)-datetime.fromisoformat(record["fetched_at"])
        if age<timedelta(days=5) and record.get("verified_inn")==inn:
            return _human_org(record["name"])
    except (OSError,ValueError,KeyError,TypeError):
        pass
    try:
        import httpx
        from bs4 import BeautifulSoup
        url="https://www.rusprofile.ru/search?"+urlencode({"query":inn})
        with httpx.Client(timeout=httpx.Timeout(7,connect=3),follow_redirects=True,
                          headers={"User-Agent":"Mozilla/5.0"}) as client:
            r=client.get(url)
            if r.status_code!=200 or "rusprofile.ru" not in str(r.url.host):
                return None
            soup=BeautifulSoup(r.text[:450000],"html.parser")
            selectors=(".company-item",".company-card",".search-results__item",
                       ".company-search-item","article")
            for selector in selectors:
                for block in soup.select(selector)[:25]:
                    if not re.search(r"(?<!\d)"+inn+r"(?!\d)",block.get_text(" ",strip=True)):
                        continue
                    name_el=block.select_one("h2,h3,.company-name,.company-item__title,a[href*='/id/']")
                    name=name_el.get_text(" ",strip=True) if name_el else ""
                    if 4<=len(name)<=450:
                        save_atomic(cache,json.dumps({
                          "verified_inn":inn,"name":name,"fetched_at":datetime.now(timezone.utc).isoformat(),
                          "source":"https://www.rusprofile.ru"
                        },ensure_ascii=False).encode("utf8"))
                        return _human_org(name)
    except Exception as exc:
        logger.info("Rusprofile unavailable for INN length=%s, reason=%s",
                    len(inn),type(exc).__name__)
    return None

def _query(ns,reg):
    conn=ns["conn"]()
    try:
        conn.row_factory=__import__("sqlite3").Row
        purchase=conn.execute(
            "SELECT reg_number,title,customer,customer_inn,method,violations,"
            "documents_checked FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        if purchase is None:raise HTTPException(404,"Закупка отсутствует в базе")
        findings=conn.execute(
          "SELECT title,evidence,document,code FROM findings "
          "WHERE reg_number=? AND kind='violation' "
          "ORDER BY id DESC LIMIT 12",(reg,)).fetchall()
        return dict(purchase),[dict(r) for r in findings]
    finally:conn.close()

def _modify_memo(template,reg,purchase,findings,organization,org_source):
    with zipfile.ZipFile(io.BytesIO(template)) as zin:
        original=zin.read("content.xml")
        tree=ET.fromstring(original)
        ps=tree.findall(".//"+TEXT+"p")
        if len(ps)<35 or "СЛУЖЕБНАЯ ЗАПИСКА" not in " ".join(_plain(p) for p in ps):
            raise ValueError("Неподходящий шаблон служебной записки")
        method=_sentence(purchase.get("method") or "закупки",250).lower()
        subject=_sentence(purchase.get("title") or "предмет закупки",600)
        # Original introductory sentence stays in the same paragraph and style.
        start=("Управление контроля размещения государственного заказа "
               "(далее – Управление) в ходе проведения анализа ")
        intro=(f"{start}{method} на право заключения договора по предмету «{subject}» "
               f"(извещение № {reg}) установило следующее.")
        _copy_text(ps[20],_nonbreaking(intro))
        if not findings:
            raise ValueError("Отсутствуют документально подтверждённые нарушения")
        # These paragraphs in the example describe article 3.4 and specific
        # points of OTHER procurement documentation. Reusing them for an
        # unrelated violation would be a false legal assertion. Replace ONLY
        # the case-specific analysis, preserving permanent text and placement.
        paragraphs=[]
        for i,item in enumerate(findings[:5],1):
            title=_sentence(item.get("title"),450)
            evidence=_sentence(item.get("evidence"),1650)
            doc=_sentence(item.get("document"),280)
            if not title or len(evidence)<15:continue
            paragraphs.append(f"{i}. Установлено следующее нарушение: {title}.")
            paragraphs.append("Установленные обстоятельства: "+evidence.rstrip(".")+"." )
            if doc:paragraphs.append("Документ, содержащий соответствующие сведения: "+doc+".")
        if not paragraphs:
            raise ValueError("В базе отсутствует подтверждающее содержание нарушения")
        # Keep boilerplate §1 on 223-FZ, replace only false example-specific law.
        analysis_slots=ps[22:29]
        for i,p in enumerate(analysis_slots):
            _copy_text(p,_nonbreaking(paragraphs[i])) if i<len(paragraphs) else _copy_text(p,"")
        # Supplement long statements by adding same-styled paragraphs before
        # conclusion rather than silently dropping any finding.
        if len(paragraphs)>len(analysis_slots):
            parent=next((el for el in tree.iter() if ps[29] in el),None)
            if parent is None:raise ValueError("Повреждена структура ODT")
            for para in paragraphs[len(analysis_slots):]:
                el=ET.Element(TEXT+"p",ps[28].attrib)
                el.text=_nonbreaking(para)
                parent.insert(list(parent).index(ps[29]),el)
        conclusion=("С учетом вышеизложенного, прошу рассмотреть вопрос "
                    "о наличии/отсутствии признаков состава административного "
                    "правонарушения в действиях "+organization.rstrip(".")+".")
        _copy_text(ps[29],_nonbreaking(conclusion))
        # Exactly ONE blank line between final paragraph and signature table,
        # instead of the two blank paragraphs in the uploaded example.
        if _plain(ps[30])=="" and _plain(ps[31])=="":
            parent=next((el for el in tree.iter() if ps[30] in el),None)
            if parent is not None:parent.remove(ps[30])
        # Keep original headings, addressee, signature, contact information,
        # logo, page layout, font sizes, and full body of static 223-FZ law.
        # Replace normal spaces after short prepositions to eliminate dangling
        # single-letter and short prepositions on justified line breaks.
        for p in ps[21:29]:
            for elt in p.iter():
                if elt.text:elt.text=_nonbreaking(elt.text)
                if elt.tail:elt.tail=_nonbreaking(elt.tail)
        xml=ET.tostring(tree,encoding="utf-8",xml_declaration=True)
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w") as z:
            for info in zin.infolist():
                name=info.filename
                if name.startswith(("Scripts/","Dialogs/")):continue
                blob=xml if name=="content.xml" else zin.read(name)
                z.writestr(name,blob,
                    compress_type=zipfile.ZIP_STORED if name=="mimetype" else zipfile.ZIP_DEFLATED)
        return out.getvalue()

def install(ns):
    app=ns["app"]

    @app.get("/api/v304/memo/template-status")
    async def template_status():
        path=template_path()
        return {"ready":path.is_file(),"format":"ODT","template_source":
                "Приложенный шаблон служебной записки ФАС",
                "macros_allowed":False,"pdf_generation":False,
                "upload_page":"/api/v304/memo/setup"}

    @app.post("/api/v304/memo/upload-template")
    async def upload_template(request:Request):
        auth(request)
        if request.headers.get("content-length","").isdigit():
            if int(request.headers["content-length"])>ODT_SIZE_LIMIT:
                raise HTTPException(413,"Шаблон превышает 4 МБ")
        incoming=await request.body()
        try:
            safe=await asyncio.to_thread(_safe_template,incoming)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        await asyncio.to_thread(save_atomic,template_path(),safe)
        print("V304 MEMO_TEMPLATE_READY bytes="+str(len(safe)),flush=True)
        return {"ready":True,"format":"ODT","bytes":len(safe),"scripts_removed":True}

    @app.get("/api/v304/memo/setup",response_class=HTMLResponse)
    async def setup_page():
        return HTMLResponse("""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Шаблон служебной записки ФАС</title>
<style>body{max-width:650px;margin:3rem auto;font:16px system-ui;line-height:1.5;padding:14px;color:#193956}
input,button{display:block;margin:12px 0;padding:12px;border-radius:7px;max-width:100%}
button{background:#225b99;color:white;border:0;cursor:pointer}</style>
<h1>Установка шаблона служебной записки</h1>
<p>Один раз выберите приложенный ODT. Он сохраняется на сервере без макросов;
при следующих проверках повторная загрузка не требуется.</p>
<label>Исходный файл ODT<input id="file" type="file" accept=".odt,application/vnd.oasis.opendocument.text"></label>
<label>Ключ загрузки Railway<input id="token" type="password" autocomplete="off"></label>
<button id="upload">Установить шаблон</button><pre id="result" role="status"></pre>
<script>
document.getElementById('upload').onclick=async function(){
const file=document.getElementById('file').files[0],key=document.getElementById('token').value;
const info=document.getElementById('result');
if(!file||!key){info.textContent='Выберите ODT и введите ключ Railway';return}
this.disabled=true;info.textContent='Проверяю и сохраняю шаблон...';
try{let r=await fetch('/api/v304/memo/upload-template',{
method:'POST',headers:{'x-eis223-upload-token':key,
'content-type':'application/vnd.oasis.opendocument.text'},body:file});
let result=await r.json();
info.textContent=r.ok?'Шаблон сохранён. Теперь формируется только ODT.':
  'Ошибка: '+(result.detail||r.status);
}catch(e){info.textContent='Ошибка сети: '+e.message}
finally{this.disabled=false}
}</script></html>""",headers={"Cache-Control":"no-store"})

    @app.get("/api/purchases/{reg}/service-memo.odt")
    async def render_service_memo(reg:str):
        if not REG.fullmatch(reg):raise HTTPException(400,"Недопустимый номер закупки")
        path=template_path()
        if not path.is_file():
            raise HTTPException(503,"Не загружен утверждённый шаблон ODT: /api/v304/memo/setup")
        purchase,findings=await asyncio.to_thread(_query,ns,reg)
        if not findings:raise HTTPException(422,"Подтверждённых нарушений для служебной записки нет")
        name=_human_org(purchase.get("customer",""))
        org_source="ЕИС (Rusprofile недоступен)"
        inn=str(purchase.get("customer_inn") or "").strip()
        if INN.fullmatch(inn):
            checked=await asyncio.to_thread(_fetch_company_rusprofile,inn,name)
            if checked:
                name=checked
                org_source="Rusprofile; проверка по ИНН"
        if not name:raise HTTPException(422,"Наименование организации не установлено")
        try:
            out=await asyncio.to_thread(_modify_memo,path.read_bytes(),reg,purchase,findings,name,org_source)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        print(f"V304 MEMO_ODT_CREATED reg={reg} findings={len(findings)} "
              f"org_source={org_source} bytes={len(out)}",flush=True)
        return Response(content=out,media_type=MIME,headers={
          "Content-Disposition":f"attachment; filename=service-memo-{reg}.odt",
          "X-Organization-Source": "Rusprofile" if org_source.startswith("Rusprofile") else "EIS-fallback",
          "Cache-Control":"no-store"})

    print("V304 MEMO_ODT_GENERATOR_READY strict_template=True original_text_preserved=True",flush=True)
