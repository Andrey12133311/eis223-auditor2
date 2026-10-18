"""Normalize ODT service memo body without changing original template branding.

Operates only on the already-rendered official FAS memo. No DB writes.
"""
from __future__ import annotations
import copy
import io
import re
import zipfile
from xml.etree import ElementTree as ET

OO="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TX="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ST="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
FO="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
DR="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
SVG="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
STYLE_NAME="EIS223MemoBody321"
EXECUTOR_STYLE="EIS223MemoExecutor381"
EXECUTOR_TEXT_STYLE="EIS223MemoExecutorText381"
EXECUTOR_FRAME_STYLE="EIS223MemoExecutorFrame381"
for prefix,uri in (("office",OO),("text",TX),("style",ST),("fo",FO),("draw",DR),("svg",SVG)):
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
        # The memo body is justified; signature and address blocks keep template styles.
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
        "{"+FO+"}color":"#000000",
        "{"+ST+"}font-size-asian":"14pt",
        "{"+ST+"}font-size-complex":"14pt"})


def _executor_block(root,body):
    """Anchor original executor/phone text at the bottom of the final page.

    A paragraph anchor follows the final signature across pagination, unlike
    fixed page numbers or blank spacer paragraphs. It appears once, never in
    a repeating page footer. The source signature and wording stay intact.
    """
    current=list(body.iter("{"+TX+"}p"))
    signer_idx=next((i for i,p in enumerate(current)
        if p.get("{"+TX+"}style-name")=="P24" or "Заместитель начальника Управления" in _odf_string(p)),-1)
    executor_idx=next((i for i,p in enumerate(current)
        if p.get("{"+TX+"}style-name")=="P21" and i>signer_idx),-1)
    if signer_idx<0 or executor_idx<=signer_idx:
        return
    direct=list(body)
    block=current[executor_idx:executor_idx+2]
    if not block or any(p not in direct for p in block):
        return
    # Use the last actual signature paragraph as anchor, never a layout blank.
    anchor=next((p for p in reversed(current[signer_idx:executor_idx])
                 if _clean(_odf_string(p)) and p in direct),None)
    if anchor is None:
        return
    styles=root.find("{"+OO+"}automatic-styles")
    # Keep the closing request and both signature lines together. Otherwise
    # the last name alone can become the anchor of an almost-empty last page.
    closing_idx=next((i for i in range(signer_idx-1,-1,-1)
                      if _clean(_odf_string(current[i])) and current[i] in direct),signer_idx)
    defined={s.get("{"+ST+"}name"):s for s in styles.findall("{"+ST+"}style")}
    for index,paragraph in enumerate(current[closing_idx:current.index(anchor)]):
        if paragraph not in direct:
            continue
        original=paragraph.get("{"+TX+"}style-name","Standard")
        name="EIS223MemoClosing381_"+str(index)
        if original in defined:
            style=copy.deepcopy(defined[original])
            style.set("{"+ST+"}name",name)
            styles.append(style)
        else:
            style=ET.SubElement(styles,"{"+ST+"}style",{
                "{"+ST+"}name":name,"{"+ST+"}family":"paragraph",
                "{"+ST+"}parent-style-name":original})
        properties=style.find("{"+ST+"}paragraph-properties")
        if properties is None:
            properties=ET.SubElement(style,"{"+ST+"}paragraph-properties")
        properties.set("{"+FO+"}keep-with-next","always")
        paragraph.set("{"+TX+"}style-name",name)
    for p in current[signer_idx+1:]:
        if p in list(body) and (p in block or not _clean(_odf_string(p))):
            body.remove(p)
    if not any(_clean(_odf_string(p)) for p in block):
        return
    for name,family in ((EXECUTOR_STYLE,"paragraph"),(EXECUTOR_TEXT_STYLE,"text")):
        style=ET.SubElement(styles,"{"+ST+"}style",{
            "{"+ST+"}name":name,"{"+ST+"}family":family})
        if family=="paragraph":
            ET.SubElement(style,"{"+ST+"}paragraph-properties",{
                "{"+FO+"}text-align":"left","{"+FO+"}text-indent":"0cm",
                "{"+FO+"}margin-left":"0cm","{"+FO+"}margin-right":"0cm",
                "{"+FO+"}margin-top":"0cm","{"+FO+"}margin-bottom":"0cm",
                "{"+FO+"}line-height":"100%","{"+FO+"}keep-with-next":"always"})
        ET.SubElement(style,"{"+ST+"}text-properties",{
            "{"+ST+"}font-name":"Times New Roman","{"+FO+"}font-family":"Times New Roman",
            "{"+FO+"}font-size":"7pt","{"+ST+"}font-size-asian":"7pt",
            "{"+ST+"}font-size-complex":"7pt","{"+FO+"}color":"#000000",
            "{"+ST+"}use-window-font-color":"false",
            "{"+FO+"}font-weight":"normal","{"+FO+"}font-style":"normal"})
    frame_style=ET.SubElement(styles,"{"+ST+"}style",{
        "{"+ST+"}name":EXECUTOR_FRAME_STYLE,"{"+ST+"}family":"graphic"})
    ET.SubElement(frame_style,"{"+ST+"}graphic-properties",{
        "{"+ST+"}wrap":"none","{"+ST+"}vertical-pos":"bottom",
        "{"+ST+"}vertical-rel":"page-content","{"+ST+"}horizontal-pos":"left",
        "{"+ST+"}horizontal-rel":"page-content","{"+ST+"}flow-with-text":"true",
        "{"+FO+"}margin-top":"0.15cm","{"+FO+"}margin-bottom":"0cm",
        "{"+FO+"}padding":"0cm","{"+FO+"}border":"none",
        "{"+DR+"}fill":"none","{"+DR+"}stroke":"none"})
    frame=ET.SubElement(anchor,"{"+DR+"}frame",{
        "{"+DR+"}name":"EIS223MemoExecutor","{"+DR+"}style-name":EXECUTOR_FRAME_STYLE,
        "{"+TX+"}anchor-type":"paragraph","{"+SVG+"}width":"16cm",
        "{"+SVG+"}height":"0.5cm"})
    box=ET.SubElement(frame,"{"+DR+"}text-box",{"{"+FO+"}min-height":"0.5cm"})
    for paragraph in block:
        paragraph.set("{"+TX+"}style-name",EXECUTOR_STYLE)
        for child in paragraph.iter("{"+TX+"}span"):
            child.set("{"+TX+"}style-name",EXECUTOR_TEXT_STYLE)
        box.append(paragraph)

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
    _executor_block(root,body)
    finished=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w") as target:
        for info,binary in entries:
            target.writestr(info,finished if info.filename=="content.xml" else binary)
    return buf.getvalue()
