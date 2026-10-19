"""Normalize ODT service memo body without changing original template branding.

Operates only on the already-rendered official FAS memo. No DB writes.
"""
from __future__ import annotations
import copy
import io
import math
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

OO="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TX="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ST="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
FO="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
DR="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
SVG="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
TB="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
XL="http://www.w3.org/1999/xlink"
STYLE_NAME="EIS223MemoBody321"
EXECUTOR_STYLE="EIS223MemoExecutor381"
EXECUTOR_TEXT_STYLE="EIS223MemoExecutorText381"
EXECUTOR_FRAME_STYLE="EIS223MemoExecutorFrame381"
XML_ID="{http://www.w3.org/XML/1998/namespace}id"
for prefix,uri in (("office",OO),("text",TX),("style",ST),("fo",FO),("draw",DR),("svg",SVG),("table",TB),("xlink",XL)):
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

def _leading_tabs(par):
    """Count the template's first-line tab stops, including tabs inside spans."""
    count=0
    def visit(node):
        nonlocal count
        if node.tag=="{"+TX+"}tab":
            count+=1;return True
        if node.text and node.text.strip():return False
        for child in node:
            if not visit(child):return False
            if child.tail and child.tail.strip():return False
        return True
    visit(par)
    return count

def _set(par,value):
    tabs=_leading_tabs(par)
    for child in list(par):par.remove(child)
    par.text=_nonbreaking(_clean(value))
    if tabs:
        value=par.text;par.text=""
        for _ in range(tabs):tab=ET.SubElement(par,"{"+TX+"}tab")
        tab.tail=value
    if not par.get("{"+TX+"}style-name"):
        par.set("{"+TX+"}style-name",STYLE_NAME)

def _flatten(par):
    """Normalize inline typography while retaining actual clickable article links."""
    parts=[];leading_tabs=_leading_tabs(par)
    def visit(node):
        nonlocal leading_tabs
        if node.tag=="{"+TX+"}a":
            link=copy.deepcopy(node)
            for child in list(link):link.remove(child)
            link.text=_nonbreaking(_clean(_odf_string(node)))
            link.tail=None
            parts.append(link)
        elif node.tag=="{"+TX+"}tab" and leading_tabs:
            parts.append(ET.Element(node.tag,dict(node.attrib)))
            leading_tabs-=1
        elif node.tag in {"{"+TX+"}"+name for name in ("s","tab","line-break","soft-page-break")}:
            parts.append(_odf_string(node))
        else:
            parts.append(node.text or "")
            for child in node:
                visit(child);parts.append(child.tail or "")
    visit(par)
    for child in list(par):par.remove(child)
    par.text=""
    last=None
    for part in parts:
        if not isinstance(part,str):
            par.append(part);last=part;continue
        part=part.replace("\u00a0"," ").replace("\u200b","")
        part=re.sub(r"\s+"," ","".join(c if c>=" " else " " for c in part))
        part=_nonbreaking(part)
        if last is None:par.text+=part
        else:last.tail=(last.tail or "")+part
    par.text=par.text.lstrip()
    if last is None:par.text=par.text.rstrip()
    elif last.tail:last.tail=last.tail.rstrip()

def _style(root,paragraphs):
    styles=root.find("{"+OO+"}automatic-styles")
    if styles is None:
        styles=ET.Element("{"+OO+"}automatic-styles")
        root.insert(0,styles)
    defined={s.get("{"+ST+"}name"):s for s in styles.findall("{"+ST+"}style")}
    text_properties={
        "{"+ST+"}font-name":"Times New Roman",
        "{"+FO+"}font-family":"Times New Roman",
        "{"+FO+"}font-size":"14pt",
        "{"+FO+"}font-weight":"normal",
        "{"+FO+"}font-style":"normal",
        "{"+FO+"}color":"#000000",
        "{"+ST+"}font-size-asian":"14pt",
        "{"+ST+"}font-size-complex":"14pt"}
    assigned={}
    for paragraph in paragraphs:
        original=paragraph.get("{"+TX+"}style-name","Standard")
        if original not in assigned:
            name=STYLE_NAME+("_"+str(len(assigned)) if assigned else "")
            # Automatic styles must be copied, not used as named-style parents.
            # Keep the uploaded template's margins, indentation, tabs and spacing.
            style=copy.deepcopy(defined[original]) if original in defined else ET.Element("{"+ST+"}style",{
                "{"+ST+"}family":"paragraph","{"+ST+"}parent-style-name":original})
            style.set("{"+ST+"}name",name)
            properties=style.find("{"+ST+"}paragraph-properties")
            if properties is None:
                properties=ET.SubElement(style,"{"+ST+"}paragraph-properties")
            if "{"+FO+"}text-align" not in properties.attrib:
                properties.set("{"+FO+"}text-align","justify")
            font=style.find("{"+ST+"}text-properties")
            if font is None:font=ET.SubElement(style,"{"+ST+"}text-properties")
            font.attrib.update(text_properties)
            styles.append(style);assigned[original]=name
        paragraph.set("{"+TX+"}style-name",assigned[original])


def _executor_block(root,body):
    """Anchor original executor/phone text at the bottom of the final page.

    A paragraph anchor follows the final signature across pagination, unlike
    fixed page numbers or blank spacer paragraphs. It appears once, never in
    a repeating page footer. The source signature and wording stay intact.
    """
    current=list(body.iter("{"+TX+"}p"))
    signer_idx=next((i for i,p in enumerate(current)
        if p.get(XML_ID)=="eis223-memo-signer-role"),-1)
    if signer_idx<0:
        signer_idx=next((i for i,p in enumerate(current)
            if p.get("{"+TX+"}style-name")=="P24" or "Заместитель начальника Управления" in _odf_string(p)),-1)
    executor_idx=next((i for i,p in enumerate(current)
        if p.get(XML_ID)=="eis223-memo-executor"),-1)
    if executor_idx<0:
        executor_idx=next((i for i,p in enumerate(current)
            if p.get("{"+TX+"}style-name")=="P21" and i>signer_idx),-1)
    if signer_idx<0 or executor_idx<=signer_idx:
        return
    direct=list(body)
    block=current[executor_idx:executor_idx+2]
    if not block:
        return
    # The real uploaded template keeps both signature lines inside a table.
    # Anchor to the signature paragraph, reserve space after its outer container.
    anchor=next((p for p in reversed(current[signer_idx:executor_idx])
                 if _clean(_odf_string(p))),None)
    if anchor is None:
        return
    parents={child:parent for parent in body.iter() for child in parent}
    container=anchor
    while parents.get(container) is not body:
        container=parents.get(container)
        if container is None:return
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
        properties.set("{"+FO+"}break-before","auto")
        properties.set("{"+FO+"}break-after","auto")
        paragraph.set("{"+TX+"}style-name",name)
    # Reserve the two small executor lines within the final content page.
    # The frame itself must not flow to a new page merely to clear its anchor.
    # Account fields allow 199 characters. A long name/phone wraps inside
    # the 16 cm frame, so a fixed two-line reserve can overlap the signature.
    # Bound line length conservatively at 56 em-width character units for
    # 7 pt text; wide Unicode glyphs count twice and combining marks do not.
    reserved_lines=0
    for paragraph in block:
        text=_odf_string(paragraph)
        if not text.strip():continue
        units=sum(0 if unicodedata.combining(c) else
                  2 if unicodedata.east_asian_width(c) in ("W","F") else 1
                  for c in text)
        reserved_lines+=max(1,math.ceil(units/56))+len(list(paragraph.iter("{"+TX+"}line-break")))
    reserve=(max(0.7,(reserved_lines*8.2+3)/28.34646) if reserved_lines else 0)
    anchor_original=anchor.get("{"+TX+"}style-name","Standard")
    anchor_style=copy.deepcopy(defined[anchor_original]) if anchor_original in defined else ET.Element("{"+ST+"}style",{
        "{"+ST+"}family":"paragraph","{"+ST+"}parent-style-name":anchor_original})
    anchor_style.set("{"+ST+"}name","EIS223MemoSignatureEnd")
    anchor_properties=anchor_style.find("{"+ST+"}paragraph-properties")
    if anchor_properties is None:anchor_properties=ET.SubElement(anchor_style,"{"+ST+"}paragraph-properties")
    anchor_properties.attrib.update({
        "{"+FO+"}margin-bottom":"0cm","{"+FO+"}keep-with-next":"always" if reserve else "auto",
        "{"+FO+"}break-before":"auto","{"+FO+"}break-after":"auto"})
    styles.append(anchor_style)
    anchor.set("{"+TX+"}style-name","EIS223MemoSignatureEnd")
    if container.tag=="{"+TB+"}table":
        original=container.get("{"+TB+"}style-name","")
        table_style=copy.deepcopy(defined[original]) if original in defined else ET.Element("{"+ST+"}style",{
            "{"+ST+"}family":"table"})
        table_style.set("{"+ST+"}name","EIS223MemoSignatureTable")
        properties=table_style.find("{"+ST+"}table-properties")
        if properties is None:properties=ET.SubElement(table_style,"{"+ST+"}table-properties")
        properties.set("{"+FO+"}keep-with-next","always" if reserve else "auto")
        styles.append(table_style)
        container.set("{"+TB+"}style-name","EIS223MemoSignatureTable")
    for paragraph in block:
        parents[paragraph].remove(paragraph)
    # A source template may keep the executor in a separate anchored frame.
    # Drop its now-empty wrapper so it cannot preserve an empty final page.
    for frame in list(body.iter("{"+DR+"}frame")):
        if (not _clean(_odf_string(frame)) and
                frame.find(".//{"+DR+"}image") is None):
            parent=parents.get(frame)
            if parent is not None and frame in list(parent):parent.remove(frame)
    for p in current[signer_idx+1:]:
        if p in list(body) and (p in block or not _clean(_odf_string(p))):
            body.remove(p)
    if not any(_clean(_odf_string(p)) for p in block):
        return
    # Bottom margins of the last paragraph can be discarded at a page edge.
    # Reserve a real line box kept with the signature instead, so any necessary
    # break moves the closing/signature together, never the executor alone.
    reserve_style=ET.SubElement(styles,"{"+ST+"}style",{
        "{"+ST+"}name":"EIS223MemoExecutorReserve","{"+ST+"}family":"paragraph"})
    ET.SubElement(reserve_style,"{"+ST+"}paragraph-properties",{
        "{"+FO+"}line-height":f"{reserve:.2f}cm","{"+FO+"}margin-top":"0cm",
        "{"+FO+"}margin-bottom":"0cm","{"+FO+"}keep-with-next":"auto",
        "{"+FO+"}break-before":"auto","{"+FO+"}break-after":"auto"})
    ET.SubElement(reserve_style,"{"+ST+"}text-properties",{"{"+FO+"}font-size":"1pt"})
    body.insert(list(body).index(container)+1,ET.Element("{"+TX+"}p",{
        "{"+TX+"}style-name":"EIS223MemoExecutorReserve"}))
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
        "{"+ST+"}wrap":"run-through","{"+ST+"}run-through":"foreground",
        "{"+ST+"}vertical-pos":"bottom",
        "{"+ST+"}vertical-rel":"page-content","{"+ST+"}horizontal-pos":"left",
        "{"+ST+"}horizontal-rel":"page-content","{"+ST+"}flow-with-text":"false",
        "{"+FO+"}margin-top":"0.15cm","{"+FO+"}margin-bottom":"0cm",
        "{"+FO+"}padding":"0cm","{"+FO+"}border":"none",
        "{"+DR+"}fill":"none","{"+DR+"}stroke":"none"})
    frame=ET.SubElement(anchor,"{"+DR+"}frame",{
        "{"+DR+"}name":"EIS223MemoExecutor","{"+DR+"}style-name":EXECUTOR_FRAME_STYLE,
        "{"+TX+"}anchor-type":"paragraph","{"+DR+"}z-index":"1","{"+SVG+"}width":"16cm",
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

def _finding(par,f,prefix):
    value=prefix+"«"+_document(f)+"» установлено: "+_title(f)
    law=_clean(f.get("law"),700)
    if law:value+=" Правовое основание: "+law.rstrip(" .;")+"."
    _set(par,value)
    from v365_consultant_refs import references
    for reference in references(f)[:3]:
        if len(par):
            if not (par[-1].tail or "").endswith((" ","\u00a0")):
                par[-1].tail=(par[-1].tail or "")+" "
        elif not (par.text or "").endswith((" ","\u00a0")):
            par.text=(par.text or "")+" "
        link=ET.SubElement(par,"{"+TX+"}a",{
            "{"+XL+"}type":"simple","{"+XL+"}href":reference["url"]})
        link.text="КонсультантПлюс: "+reference["label"]
        link.tail=" "

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
    _style(root,paras[20:30])
    main=findings[0]
    _finding(paras[26],main,"В документе ")
    # The raw source excerpt is already retained with the verified finding.
    # Do not repeat that technical file/fragment dump in the service memo.
    body.remove(paras[27])
    # Every verified violation remains in its own paragraph, including #3/#4.
    place=list(body).index(paras[28])
    for f in findings[1:]:
        desc=ET.Element("{"+TX+"}p",{"{"+TX+"}style-name":paras[26].get("{"+TX+"}style-name")})
        for _ in range(_leading_tabs(paras[26])):ET.SubElement(desc,"{"+TX+"}tab")
        _finding(desc,f,"Дополнительно в документе ")
        body.insert(place,desc);place+=1
    # The remaining boilerplate keeps the template's words and order; remove
    # only stray inline runs/tabs that produce incompatible font sizes.
    for par in paras[20:30]:
        _flatten(par)
    _executor_block(root,body)
    finished=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w") as target:
        for info,binary in entries:
            target.writestr(info,finished if info.filename=="content.xml" else binary)
    return buf.getvalue()
