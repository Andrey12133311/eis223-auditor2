"""ODT-only memo using the uploaded (macro-stripped) official layout and unchanged boilerplate.

Memo is a DRAFT, never invent violations, recipient, document reference or company.
The FAS ODT attachment is uploaded once to the private Railway /data volume.
"""
from __future__ import annotations
import asyncio
import os
import hashlib
import base64
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from v295_eis_originals import root as storage, save_atomic, auth
from v342_identity import organization_display_name

OO="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TX="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
DR="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
XL="http://www.w3.org/1999/xlink"
SC="urn:oasis:names:tc:opendocument:xmlns:script:1.0"
EV="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
for prefix,value in (("office",OO),("text",TX),("draw",DR),("xlink",XL)):
    ET.register_namespace(prefix,value)
REG=re.compile(r"^3[0-9]{10}$")
INN=re.compile(r"^[0-9]{10}(?:[0-9]{2})?$")
ORG_ABBR=frozenset(("ООО","АО","ПАО","НАО","ОАО","ЗАО","ИП","ГУП","ФГУП","МУП",
                     "ФГБОУ","ФГБУ","ФКУ","ГБУ","МАУ","АНО","НКО","ФГАУ","ФКП"))
TEMPLATE_DIR="eis223_official_memo_template"
MAX_TEMPLATE_BYTES=2*1024*1024
MAX_ARCHIVE_UNPACKED=16*1024*1024
MAX_FINDINGS=4

def template_path():
    return storage()/TEMPLATE_DIR/"memo_template.odt"

def status():
    p=template_path()
    return {"template_installed":p.is_file(),
            "template":"ODT ФАС — служебная записка (двухстраничный образец)",
            "format":"odt","requires_verified_violation":True,
            "name_source":"Rusprofile при доступности; иначе ЕИС (отмечается как резерв)"}

def _safe_odt(binary):
    """No embedded ODF scripts, event handlers, external resources or unsafe ZIP members."""
    if not isinstance(binary,bytes) or not 3000<len(binary)<=MAX_TEMPLATE_BYTES:
        raise ValueError("Ожидается ODT-файл шаблона, до 2 МБ")
    result={}
    try:
        with zipfile.ZipFile(io.BytesIO(binary)) as archive:
            entries=archive.infolist()
            if len(entries)>150 or sum(e.file_size for e in entries)>MAX_ARCHIVE_UNPACKED:
                raise ValueError("Превышен безопасный размер шаблона")
            media=archive.read("mimetype").decode("ascii")
            if media.strip()!="application/vnd.oasis.opendocument.text":
                raise ValueError("Файл не является документом LibreOffice ODT")
            for entry in entries:
                name=entry.filename
                if name in ("content.xml","styles.xml","mimetype","META-INF/manifest.xml") \
                   or (name.startswith("Pictures/") and name.lower().endswith((".png",".jpg",".jpeg"))):
                    if ".." in Path(name).parts:continue
                    raw=archive.read(entry)
                    if entry.file_size!=len(raw):raise ValueError("Повреждён ODT")
                    result[name]=raw
    except (zipfile.BadZipFile,KeyError,UnicodeDecodeError) as exc:
        raise ValueError("Файл ODT повреждён или содержит неверную структуру") from exc
    if "content.xml" not in result or "styles.xml" not in result:
        raise ValueError("Отсутствуют страницы шаблона")
    for name in ("content.xml","styles.xml"):
        data=result[name]
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("Небезопасная структура XML")
        node=ET.fromstring(data)
        for parent in node.iter():
            for child in list(parent):
                if child.tag in ("{"+OO+"}scripts","{"+OO+"}event-listeners") or \
                   child.tag.startswith("{"+SC+"}"):
                    parent.remove(child)
            for k in list(parent.attrib.keys()):
                if k.startswith("{"+SC+"}") or k.startswith("{"+EV+"}event"):
                    del parent.attrib[k]
        result[name]=ET.tostring(node,encoding="utf-8",xml_declaration=True)
    if not _compatible_template(result["content.xml"]):
        raise ValueError("Загружен другой шаблон. Нужна служебная записка с двумя страницами из приложения.")
    return _zip_content(result)

def _compatible_template(xml):
    try:
        node=ET.fromstring(xml)
        pars=list(node.iter("{"+TX+"}p"))
        corpus=" ".join("".join(x.itertext()) for x in pars)
        return (len(pars)>35 and "СЛУЖЕБНАЯ ЗАПИСКА" in corpus
          and "С учетом вышеизложенного" in corpus
          and "Заместитель начальника Управления" in corpus
          and "223-ФЗ" in corpus)
    except ET.ParseError:return False

def _zip_content(items):
    mem=io.BytesIO()
    with zipfile.ZipFile(mem,"w") as z:
        for name,binary in items.items():
            z.writestr(name,binary,compress_type=zipfile.ZIP_STORED if name=="mimetype" else zipfile.ZIP_DEFLATED)
    return mem.getvalue()

def _read_purchase(ns,reg):
    c=ns["conn"]()
    try:
        c.row_factory=None
        cur=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,))
        names=[d[0] for d in cur.description]
        row=cur.fetchone()
        if not row:raise HTTPException(404,"Закупка не найдена в базе")
        p=dict(zip(names,row))
        cur=c.execute(
            "SELECT * FROM findings WHERE reg_number=? AND kind='violation' "
            "ORDER BY id DESC LIMIT 180",(reg,))
        names=[d[0] for d in cur.description]
        findings=[dict(zip(names,x)) for x in cur.fetchall()]
        # Distinct facts survive; repeated representations do not enter the memo.
        from v352_dashboard_truth import collapse_legal_events
        from v365_consultant_refs import enrich
        documented=[]
        for finding in findings:
            title=str(finding.get("title") or "").strip()
            evidence=str(finding.get("evidence") or "").strip()
            if not title or not evidence or re.search(
                 r"риск|требует проверки|не найдена информация",title,re.I):
                continue
            documented.append(finding)
        accepted=[enrich(f) for f in collapse_legal_events(documented)[:MAX_FINDINGS]]
        if not accepted:
            raise HTTPException(409,"Служебная записка не формируется: нет нарушения с документальными основаниями")
        return p,accepted
    finally:c.close()

def _plain_company(text):
    text=re.sub(r"\s+"," ",str(text or "")).strip().strip(".")
    return organization_display_name(text)

async def _rusprofile_company(inn):
    """Best effort direct Rusprofile lookup, not an unofficial API or fabricated match."""
    if not INN.fullmatch(inn or ""):return None
    try:
        import httpx
        from bs4 import BeautifulSoup
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0,connect=3.5),
                                      follow_redirects=True) as client:
            r=await client.get(
                "https://www.rusprofile.ru/search",
                params={"query":inn,"type":"ul"},
                headers={"User-Agent":"Mozilla/5.0","Accept":"text/html"})
        if r.status_code!=200 or not r.url.host.endswith("rusprofile.ru"):
            return None
        if inn not in r.text:return None
        soup=BeautifulSoup(r.text[:400000],"html.parser")
        # Accept only a Rusprofile result card with the exact INN in the
        # SAME card. A search page heading is not a verified company name.
        for selector in (".company-item",".company-card",
                         ".search-results__item",".company-search-item","article"):
            for card in soup.select(selector)[:40]:
                if not re.search(r"(?<![0-9])"+re.escape(inn)+r"(?![0-9])",
                                 card.get_text(" ",strip=True)):
                    continue
                node=card.select_one("h2,h3,.company-name,.company-item__title,a[href*='/id/']")
                candidate=_plain_company(node.get_text(" ",strip=True) if node else "")
                if 5<=len(candidate)<=300 and not re.search(
                    r"поиск|результат|404|не найден",candidate,re.I):
                    return candidate
        return None
    except Exception as exc:
        print("V304 RUSPROFILE_UNAVAILABLE "+type(exc).__name__,flush=True)
        return None

async def company_name(p):
    inn=str(p.get("customer_inn") or "").strip()
    cache=storage()/TEMPLATE_DIR/"rusprofile_cache"
    if INN.fullmatch(inn):
        path=cache/(inn+".json")
        try:
            obj=json.loads(path.read_text(encoding="utf-8"))
            if obj.get("source")=="rusprofile" and obj.get("inn")==inn and obj.get("name"):
                return obj["name"],"rusprofile"
        except (OSError,ValueError,TypeError):pass
        name=await _rusprofile_company(inn)
        if name:
            await asyncio.to_thread(save_atomic,path,json.dumps(
                {"source":"rusprofile","inn":inn,"name":name},
                ensure_ascii=False).encode("utf-8"))
            return name,"rusprofile"
    return _plain_company(p.get("customer") or ""),"eis_fallback"

def _form_of_purchase(method):
    text=str(method or "").lower()
    if "аукцион" in text:return "аукциона в электронной форме" if "электрон" in text else "аукциона"
    if "конкурс" in text:return "конкурса в электронной форме" if "электрон" in text else "конкурса"
    if "котиров" in text:return "запроса котировок"
    if "предлож" in text:return "запроса предложений"
    if "единствен" in text:return "закупки у единственного поставщика"
    return "закупки"

def _text(element):
    return "".join(element.itertext())

def _set_paragraph(par,new):
    for child in list(par):par.remove(child)
    par.text=str(new)

def _append_consultant_links(par, finding):
    """Clickable statute link in ODT; leave all legal and evidentiary text as is."""
    from v365_consultant_refs import references
    refs=references(finding)
    for ref in refs[:3]:
        if not len(par):
            par.text=(par.text or "")+" "
        link=ET.SubElement(par,"{"+TX+"}a",{
            "{"+XL+"}type":"simple",
            "{"+XL+"}href":ref["url"]})
        link.text="КонсультантПлюс: "+ref["label"]
        link.tail=" "


def _nonbreaking(text):
    preps=r"в|во|на|по|из|с|со|к|ко|о|об|от|за|до|у|и|а|но|не|для|при|без|над|под|перед|между|после"
    return re.sub(r"(?i)(?<![а-яё])("+preps+r") +(?=\S)",
                  lambda m:m.group(1)+"\u00a0",text)

def _memo_body_format(body,paragraphs):
    """Uniform body typography without touching the crest, header or signature."""
    style_ns="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
    fo_ns="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
    office_styles=body.getparent() if hasattr(body,"getparent") else None
    # ODF automatic styles belong to content.xml's office:automatic-styles.
    return style_ns,fo_ns

def _clean_memo_fragment(value):
    text=str(value or "").replace("\r"," ").replace("\n"," ").replace("\t"," ")
    text=re.sub(r"[\u200b\u200e\u200f\ufeff]","",text)
    return re.sub(r"\s+"," ",text).strip()

def _format_memo_body(node,body,paragraphs):
    """Apply a dedicated paragraph style; keep all original template metadata."""
    style_ns="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
    fo_ns="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
    auto=node.find("{"+OO+"}automatic-styles")
    if auto is None:
        auto=ET.Element("{"+OO+"}automatic-styles")
        node.insert(0,auto)
    name="EIS223MemoBodyUniform"
    for existing in list(auto):
        if existing.tag=="{"+style_ns+"}style" and existing.get("{"+style_ns+"}name")==name:
            auto.remove(existing)
    style=ET.SubElement(auto,"{"+style_ns+"}style",{
        "{"+style_ns+"}name":name,"{"+style_ns+"}family":"paragraph"})
    ET.SubElement(style,"{"+style_ns+"}paragraph-properties",{
        "{"+fo_ns+"}text-align":"justify",
        "{"+fo_ns+"}margin-top":"0cm",
        "{"+fo_ns+"}margin-bottom":"0cm",
        "{"+fo_ns+"}text-indent":"1.25cm",
        "{"+fo_ns+"}line-height":"115%"})
    ET.SubElement(style,"{"+style_ns+"}text-properties",{
        "{"+style_ns+"}font-name":"Times New Roman",
        "{"+fo_ns+"}font-family":"Times New Roman",
        "{"+fo_ns+"}font-size":"14pt",
        "{"+fo_ns+"}font-weight":"normal",
        "{"+fo_ns+"}font-style":"normal"})
    # The original template uses paragraph 20 for the introductory body and
    # paragraphs 22-29 for findings. Other positions contain official fields.
    for idx in [20,*range(22,30)]:
        if idx>=len(paragraphs):continue
        para=paragraphs[idx]
        value=_clean_memo_fragment(_text(para))
        _set_paragraph(para,value)
        para.set("{"+TX+"}style-name",name)

def _render_odt(template,p,findings,organization,fields):
    try:
        with zipfile.ZipFile(io.BytesIO(template)) as z:
            items={n:z.read(n) for n in z.namelist()}
    except (zipfile.BadZipFile,KeyError) as exc:
        raise HTTPException(503,"Сохранённый шаблон ODT повреждён") from exc
    node=ET.fromstring(items["content.xml"])
    body=node.find(".//{"+OO+"}text")
    if body is None:raise HTTPException(503,"Шаблон ODT не содержит текста")
    paras=list(body.iter("{"+TX+"}p"))
    if len(paras)<35:raise HTTPException(503,"Неверная структура служебной записки")
    # Identify semantic fields before removing spacers or adding findings.
    # Word-exported templates can rename P21/P24, so final-page layout must
    # not depend on their incidental paragraph-style names.
    for idx,key in ((32,"signer-role"),(33,"signer"),(55,"executor"),(56,"phone")):
        if idx<len(paras):
            paras[idx].set("{http://www.w3.org/XML/1998/namespace}id","eis223-memo-"+key)
    reg=str(p.get("reg_number"))
    title=re.sub(r"\s+"," ",str(p.get("title") or "предмет закупки не установлен")).strip()
    title=title[:580]
    intro=("Управление контроля размещения государственного заказа "
           "(далее – Управление) в ходе проведения анализа "+
           _form_of_purchase(p.get("method"))+
           " на право заключения договора на "+title+
           " (извещение № "+reg+") (далее – Закупка) установило следующее.")
    _set_paragraph(paras[20],intro)
    main=findings[0]
    law=str(main.get("law") or "").strip()[:350]
    evidence=str(main.get("evidence") or "").strip()[:800]
    document=str(main.get("document") or "закупочной документации").strip()[:180]
    finding_title=str(main.get("title") or "Нарушение условий закупки")[:220]
    if not (law and "3.4" in law and re.search(r"МСП|дополнительн|документ|заявк",finding_title,re.I)):
        # The SME-only rules from the sample must not be presented as applicable
        # to another procurement. Preserve the other general 223-FZ boilerplate.
        _set_paragraph(paras[22],
            "В ходе анализа документации Закупки выявлены положения, требующие оценки "
            "на соответствие Закону о закупках.")
        _set_paragraph(paras[23],
            "При определении правовой оценки применяются требования Закона о закупках "
            "и положения документации рассматриваемой Закупки.")
        _set_paragraph(paras[24],
            ("Правовое основание выявленного нарушения: "+law+".")
            if law else "Правовое основание устанавливается по конкретным положениям Закона о закупках.")
        _append_consultant_links(paras[24],main)
        _set_paragraph(paras[25],
            "Приведённые ниже обстоятельства установлены по тексту "
            "полученных оригиналов документов закупки.")
    _set_paragraph(paras[26],
       "В "+document+" установлено следующее: "+finding_title+". "+evidence)
    if len(findings)>1:
        other=findings[1]
        _set_paragraph(paras[27],
           "Дополнительно выявлено: "+str(other.get("title") or "")[:210]+
           ". "+str(other.get("evidence") or "")[:650]+
           ". Правовое основание: "+str(other.get("law") or "требует уточнения")[:300]+".")
        _append_consultant_links(paras[27],other)
    else:
        _set_paragraph(paras[27],
           "Указанные обстоятельства установлены непосредственно по "
           "содержанию сохранённых оригиналов закупочной документации.")
    _set_paragraph(paras[28],
       "Вместе с тем указанные положения подлежат оценке на предмет "
       "соответствия требованиям законодательства о закупках товаров, работ, "
       "услуг отдельными видами юридических лиц.")
    _set_paragraph(paras[29],
       "С учетом вышеизложенного, прошу рассмотреть вопрос о наличии/отсутствии "
       "признаков состава административного правонарушения в действиях "+
       organization_display_name(organization).rstrip(".")+".")
    if len(paras)>31 and not _text(paras[30]).strip() and not _text(paras[31]).strip():
        body.remove(paras[31])
    # Final typography is applied after filling metadata by v321 formatter.
    # Do not flatten ODF spans here: text:s/tab data would be lost.
    # The source document ID is another official memo's registration number.
    # It is not a valid identifier for the newly created draft.
    for para in body.iter("{"+TX+"}p"):
        if para.text:para.text=_nonbreaking(para.text)
        for child in para.iter():
            if child is para:continue
            if child.text:child.text=_nonbreaking(child.text)
            if child.tail:child.tail=_nonbreaking(child.tail)
    for para in body.iter("{"+TX+"}p"):
        if "2026-139205" in _text(para):
            _set_paragraph(para,"")
    for el in node.iter():
        if el.text and "2026-139205" in el.text:
            el.text=el.text.replace("2026-139205","")
    # Optional fields are copied only where sample already contains those
    # official recipient/signature lines.
    for idx,key in ((13,"recipient"),(18,"greeting"),(32,"signer_role"),(33,"signer"),
                    (55,"executor"),(56,"phone")):
        v=str(fields.get(key) or "").strip()
        if (v or (key in ("recipient","executor","phone") and key in fields)) and idx<len(paras) and len(v)<200:
            _set_paragraph(paras[idx],v)
    # Remove the barcode belonging to the original registered memo, preserve crest.
    style=ET.fromstring(items["styles.xml"])
    for parent in style.iter():
        for child in list(parent):
            if child.tag=="{"+DR+"}frame":
                for image in child.iter():
                    if image.attrib.get("{"+XL+"}href","").endswith("A1000000253879166AE7F8E4B2.jpg"):
                        parent.remove(child);break
    items["content.xml"]=ET.tostring(node,encoding="utf-8",xml_declaration=True)
    items["styles.xml"]=ET.tostring(style,encoding="utf-8",xml_declaration=True)
    # Preserve the exact official template; standardize only memo body styles,
    # verified evidence paragraphs and trailing empty spacers.
    from v321_memo_layout import format_service_memo
    return format_service_memo(_zip_content(items), findings)

UPLOAD_FORM='''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Шаблон служебной записки · ODT</title>
<style>body{font:16px system-ui;background:#f4f7fc;color:#173c63;max-width:720px;
margin:40px auto;padding:20px}section{background:#fff;border:1px solid #d6e5f4;
border-radius:14px;padding:24px}button,input{box-sizing:border-box;display:block;
width:100%;margin:12px 0;padding:13px;font:inherit;border:1px solid #c9d9e8;
border-radius:8px}button{color:white;background:#266bac;border:0;cursor:pointer}
p{line-height:1.6}</style><section><h2>Установить шаблон служебной записки</h2>
<p>Загрузите приложенный ODT один раз. Он будет сохранён на Railway,
а все будущие служебные записки будут формироваться по нему, только в ODT.
Встроенные макросы и исходный регистрационный номер не используются.</p>
<label>Ключ загрузки Railway<input id="token" type="password" autocomplete="off"></label>
<label>Шаблон LibreOffice ODT<input id="file" type="file" accept=".odt,application/vnd.oasis.opendocument.text"></label>
<button id="install">Сохранить шаблон</button><p id="status" role="status"></p>
<a href="/">Вернуться к закупкам</a></section>
<script>
document.getElementById('install').onclick=async()=>{
const b=document.getElementById('install'),label=document.getElementById('status');
const file=document.getElementById('file').files[0],token=document.getElementById('token').value.trim();
if(!file||!token){label.textContent='Выберите ODT и укажите ключ Railway';return}
b.disabled=true;label.textContent='Проверяю шаблон...';
try{const r=await fetch('/api/v304/memo-template',{method:'POST',
headers:{'x-eis223-upload-token':token,'content-type':'application/vnd.oasis.opendocument.text'},
body:await file.arrayBuffer()});const d=await r.json();
if(!r.ok)throw Error(d.detail||r.status);
label.textContent='Готово. Шаблон сохранён; служебные записки будут создаваться в ODT.';
}catch(e){label.textContent='Не удалось установить: '+e.message}finally{b.disabled=false}
};
</script></html>'''

def install(ns):
    # Deploy the user's uploaded, macro-stripped FAS ODT from private
    # Railway configuration once. Keep this fallback so no workstation,
    # extension or manual browser visit is needed for the original template.
    if not template_path().is_file():
        parts=[os.environ.get(f"EIS223_MEMO_TEMPLATE_B64_{n:02d}","")
               for n in range(1,7)]
        if all(parts):
            try:
                original=base64.b64decode("".join(parts),validate=True)
                expected="9445a0ec77e733f9429bf91138fe2eb21d577ac94ab0623e099a7839d0888c73"
                if hashlib.sha256(original).hexdigest()!=expected:
                    raise ValueError("Uploaded ODT checksum mismatch")
                clean=_safe_odt(original)
                save_atomic(template_path(),clean)
                print("V304 OFFICIAL_TEMPLATE_AUTO_INSTALLED bytes="+str(len(clean)),flush=True)
            except Exception as exc:
                print("V304 TEMPLATE_BOOT_ERROR "+type(exc).__name__,flush=True)
    app=ns["app"]
    @app.get("/api/v304/memo-template/status")
    async def template_status():
        return status()

    @app.get("/api/v304/memo-template/upload",response_class=HTMLResponse)
    async def template_upload_page():
        return HTMLResponse(UPLOAD_FORM,headers={"Cache-Control":"no-store"})

    @app.post("/api/v304/memo-template")
    async def template_upload(request:Request):
        auth(request)
        if request.headers.get("content-length","").isdigit() and int(request.headers["content-length"])>MAX_TEMPLATE_BYTES:
            raise HTTPException(413,"Шаблон превышает 2 МБ")
        raw=await request.body()
        try:
            safe=await asyncio.to_thread(_safe_odt,raw)
        except (ValueError,ET.ParseError) as exc:
            raise HTTPException(422,str(exc)) from exc
        await asyncio.to_thread(save_atomic,template_path(),safe)
        print("V304 OFFICIAL_MEMO_TEMPLATE_SAVED bytes="+str(len(safe)),flush=True)
        return {"installed":True,"format":"ODT","macros_removed":True,"bytes":len(safe)}

    async def build_memo(reg,fields=None):
        if not REG.fullmatch(reg):raise HTTPException(422,"Неверный номер закупки")
        pfile=template_path()
        if not pfile.is_file():
            raise HTTPException(409,"Шаблон ODT ещё не установлен. Откройте /api/v304/memo-template/upload")
        p,findings=await asyncio.to_thread(_read_purchase,ns,reg)
        org,source=await company_name(p)
        if not org:raise HTTPException(409,"Отсутствует наименование заказчика")
        fields=fields if isinstance(fields,dict) else {}
        odt=await asyncio.to_thread(_render_odt,pfile.read_bytes(),p,findings,org,fields)
        print(f"V304 MEMO_GENERATED reg={reg} verified_findings={len(findings)} format=odt name_source={source}",flush=True)
        return Response(odt,media_type="application/vnd.oasis.opendocument.text",
            headers={"Content-Disposition":f'attachment; filename="sluzhebnaya_zapiska_{reg}.odt"',
                     "X-Company-Name-Source":source,
                     "Cache-Control":"no-store"})

    app.state.eis223_build_memo=build_memo

    def saved_fields(request,reg):
        # The outer account gate supplies this identity; never trust request IDs.
        owner=request.scope.get("eis223_account_id")
        if owner is None:return {}
        from scripts.v367_private_features import _memo_requisites
        return _memo_requisites(owner,reg)

    @app.get("/api/v304/memo/{reg}.odt")
    async def memo_file(reg:str,request:Request):
        fields=await asyncio.to_thread(saved_fields,request,reg)
        return await build_memo(reg,fields)

    # Existing memo download button POSTs to the v218 route. Intercept here
    # rather than ship the obsolete memo text and layout.
    @app.middleware("http")
    async def odt_only_existing_memo(request,call_next):
        m=re.fullmatch(r"/api/v218/memo/(3[0-9]{10})",request.url.path)
        if request.method=="POST" and m:
            try:
                raw=await request.body()
                if len(raw)>20000:raise HTTPException(413,"Слишком много реквизитов")
                body=json.loads(raw)
            except (ValueError,TypeError):
                body={}
            try:
                fields=await asyncio.to_thread(saved_fields,request,m[1])
                supplied=body.get("fields") if isinstance(body,dict) else {}
                if isinstance(supplied,dict):fields.update(supplied)
                return await build_memo(m[1],fields)
            except HTTPException as exc:
                # Middleware executes outside FastAPI's exception handler.
                # Return an actionable 4xx, not an unhandled 500/stack trace.
                return JSONResponse({"detail":exc.detail},status_code=exc.status_code)
        return await call_next(request)

    print("V304 OFFICIAL_MEMO_ODT_READY template_installed="+str(template_path().is_file()),flush=True)
