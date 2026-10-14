"""Normalize ODT service memo body without changing original template branding.

Operates only on the already-rendered official FAS memo. No DB writes.
"""
from __future__ import annotations
import io
import re
import zipfile
from xml.etree import ElementTree as ET

OO="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TX="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ST="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
FO="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
STYLE_NAME="EIS223MemoBody321"
for prefix,uri in (("office",OO),("text",TX),("style",ST),("fo",FO)):
    ET.register_namespace(prefix,uri)

def _odf_string(element):
    """Return ODF text with correct spaces, tabs and line-breaks."""
    if element.tag=="{"+TX+"}s":
        try:count=min(20,max(1,int(element.get("{"+TX+"}c","1"))))
        except ValueError:count=1
        return " "*count
    if element.tag in ("{"+TX+"}tab","{"+TX+"}line-break"):return " "
    if element.tag=="{"+TX+"}soft-page-break":return ""
    value=element.text or ""
    for child in element:
        value+=_odf_string(child)+(child.tail or "")
    return value

def _clean(text,limit=1700):
    text=str(text or "")[:limit].replace("\r"," ").replace("\n"," ")
    text=text.replace("\t"," ").replace("\u00a0"," ").replace("\u200b","")
    text="".join(c if c>=" " else " " for c in text)
    text=re.sub(r"\s+"," ",text).strip()
    return re.sub(r"(?<=[А-Яа-яЁёA-Za-z0-9])\((?=[А-Яа-яЁёA-Za-z])"," (",text)

def _nonbreaking(text):
    prep=r"в|во|к|ко|на|по|о|об|обо|с|со|у|из|за|до|от|а|и|но|не|для|при|без|над|под|перед|между|после"
    return re.sub(r"(?i)(?<![а-яёa-z])("+prep+r") +(?=\S)",
                  lambda m:m.group(1)+"\u00a0",text)

def _set(par,value):
    for child in list(par):par.remove(child)
    par.text=_nonbreaking(_clean(value))
    par.set("{"+TX+"}style-name",STYLE_NAME)

def _style(root):
    styles=root.find("{"+OO+"}automatic-styles")
    if styles is None:
        styles=ET.Element("{"+OO+"}automatic-styles")
        root.insert(0,styles)
    for old in list(styles):
        if old.tag=="{"+ST+"}style" and old.get("{"+ST+"}name")==STYLE_NAME:
            styles.remove(old)
    style=ET.SubElement(styles,"{"+ST+"}style",{
        "{"+ST+"}name":STYLE_NAME,
        "{"+ST+"}family":"paragraph",
        "{"+ST+"}parent-style-name":"Standard"})
    ET.SubElement(style,"{"+ST+"}paragraph-properties",{
        "{"+FO+"}text-align":"justify",
        "{"+ST+"}justify-single-word":"false",
        "{"+FO+"}text-indent":"1.25cm",
        "{"+FO+"}margin-left":"0cm",
        "{"+FO+"}margin-right":"0cm",
        "{"+FO+"}margin-top":"0cm",
        "{"+FO+"}margin-bottom":"0.11cm",
        "{"+FO+"}line-height":"120%",
        "{"+FO+"}orphans":"2",
        "{"+FO+"}widows":"2"})
    ET.SubElement(style,"{"+ST+"}text-properties",{
        "{"+ST+"}font-name":"Times New Roman",
        "{"+FO+"}font-family":"Times New Roman",
        "{"+FO+"}font-size":"14pt",
        "{"+FO+"}font-weight":"normal",
        "{"+FO+"}font-style":"normal",
        "{"+ST+"}font-size-asian":"14pt",
        "{"+ST+"}font-size-complex":"14pt"})

def _title(f):
    return _clean(f.get("title") or "Нарушение условий закупки",260).rstrip(" .;:")+"."

def _document(f):
    return _clean(f.get("document") or "закупочной документации",220).rstrip(" .;:")

def _evidence(f):
    s=_clean(f.get("evidence"),1700)
    return s.rstrip(" .;:")+"." if s else ""

def format_service_memo(data:bytes,findings:list[dict])->bytes:
    """Normalize the memo as a structured ODT, without changing its findings."""
    if not findings:
        raise ValueError("Для записки нужно документально подтверждённое нарушение")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=[(info,archive.read(info.filename)) for info in archive.infolist()]
    xml=dict((info.filename,binary) for info,binary in entries)["content.xml"]
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("Недопустимая структура шаблона ODT")
    root=ET.fromstring(xml)
    body=root.find(".//{"+OO+"}text")
    if body is None:raise ValueError("В ODT отсутствует основной текст")
    paras=list(body.iter("{"+TX+"}p"))
    if len(paras)<34 or "СЛУЖЕБНАЯ ЗАПИСКА" not in _odf_string(paras[16]):
        raise ValueError("Неизвестная структура официального ODT-шаблона")
    _style(root)
    main=findings[0]
    _set(paras[26],"В документе «"+_document(main)+"» установлено следующее: "+_title(main))
    _set(paras[27],"Подтверждающие обстоятельства: "+_evidence(main))
    # Every verified violation goes into its own paragraphs, including #3/#4.
    place=list(body).index(paras[28])
    for f in findings[1:]:
        desc=ET.Element("{"+TX+"}p")
        _set(desc,"Дополнительно в документе «"+_document(f)+"» установлено: "+_title(f))
        body.insert(place,desc);place+=1
        if _evidence(f):
            detail=ET.Element("{"+TX+"}p")
            _set(detail,"Подтверждающие обстоятельства: "+_evidence(f))
            body.insert(place,detail);place+=1
    # The remaining boilerplate keeps the template's words and order; remove
    # only stray inline runs/tabs that produce incompatible font sizes.
    for par in paras[20:30]:
        _set(par,_odf_string(par))
    # The sample has 19 empty layout paragraphs after the signatory; when
    # text expands they spill the executor details onto an empty third page.
    current=list(body.iter("{"+TX+"}p"))
    signer_idx=next((i for i,p in enumerate(current)
        if "Заместитель начальника Управления" in _odf_string(p)),-1)
    executor_idx=next((i for i,p in enumerate(current)
        if p.get("{"+TX+"}style-name")=="P21" and i>signer_idx),-1)
    if signer_idx>=0 and executor_idx>signer_idx:
        blanks=[p for p in current[signer_idx+1:executor_idx]
                if not _clean(_odf_string(p)) and p in list(body)]
        for p in blanks[:max(0,len(blanks)-7)]:
            body.remove(p)
    finished=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w") as target:
        for info,binary in entries:
            target.writestr(info,finished if info.filename=="content.xml" else binary)
    return buf.getvalue()
