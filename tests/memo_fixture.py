"""Synthetic canonical ODT layout, never a user's document or identity."""
import io
import zipfile
from xml.etree import ElementTree as ET

from v321_memo_layout import OO,TX,ST,FO


def memo_template():
    root=ET.Element("{"+OO+"}document-content",{"{"+OO+"}version":"1.2"})
    styles=ET.SubElement(root,"{"+OO+"}automatic-styles")
    for name,size in (("Empty","4pt"),("P21","9pt"),("P22","9pt"),("P24","14pt")):
        style=ET.SubElement(styles,"{"+ST+"}style",{
            "{"+ST+"}name":name,"{"+ST+"}family":"paragraph",
            "{"+ST+"}parent-style-name":"Standard"})
        ET.SubElement(style,"{"+ST+"}text-properties",{
            "{"+FO+"}font-size":size,"{"+FO+"}color":"#808080" if name in ("P21","P22") else "#000000"})
    body=ET.SubElement(ET.SubElement(root,"{"+OO+"}body"),"{"+OO+"}text")
    contents={13:"Руководителю Управления",16:"СЛУЖЕБНАЯ ЗАПИСКА",18:"Уважаемый коллега!",
              20:"Текст вводной части.",21:"Правовая оценка по 223-ФЗ.",
              **{i:"Исходный текст положения по 223-ФЗ." for i in range(22,29)},
              29:"С учетом вышеизложенного, прошу рассмотреть вопрос.",
              32:"Заместитель начальника Управления",33:"И. И. Подписант",
              55:"А. Б. Исполнитель",56:"+7 (000) 123-45-67"}
    for index in range(57):
        name="P21" if index==55 else "P22" if index==56 else "P24" if index==32 else "Standard" if index in contents else "Empty"
        p=ET.SubElement(body,"{"+TX+"}p",{"{"+TX+"}style-name":name})
        p.text=contents.get(index,"")
    document=ET.Element("{"+OO+"}document-styles",{"{"+OO+"}version":"1.2"})
    common=ET.SubElement(document,"{"+OO+"}styles")
    default=ET.SubElement(common,"{"+ST+"}default-style",{"{"+ST+"}family":"paragraph"})
    ET.SubElement(default,"{"+ST+"}paragraph-properties",{"{"+FO+"}margin-top":"0cm","{"+FO+"}margin-bottom":"0cm"})
    ET.SubElement(default,"{"+ST+"}text-properties",{"{"+FO+"}font-family":"Times New Roman","{"+FO+"}font-size":"14pt"})
    ET.SubElement(common,"{"+ST+"}style",{"{"+ST+"}name":"Standard","{"+ST+"}family":"paragraph"})
    automatic=ET.SubElement(document,"{"+OO+"}automatic-styles")
    layout=ET.SubElement(automatic,"{"+ST+"}page-layout",{"{"+ST+"}name":"pm1"})
    ET.SubElement(layout,"{"+ST+"}page-layout-properties",{
        "{"+FO+"}page-width":"21cm","{"+FO+"}page-height":"29.7cm",
        "{"+FO+"}margin-left":"2cm","{"+FO+"}margin-right":"2cm",
        "{"+FO+"}margin-top":"2cm","{"+FO+"}margin-bottom":"2cm"})
    masters=ET.SubElement(document,"{"+OO+"}master-styles")
    ET.SubElement(masters,"{"+ST+"}master-page",{"{"+ST+"}name":"Standard","{"+ST+"}page-layout-name":"pm1"})
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w") as z:
        z.writestr("mimetype","application/vnd.oasis.opendocument.text")
        z.writestr("content.xml",ET.tostring(root,encoding="utf-8",xml_declaration=True))
        z.writestr("styles.xml",ET.tostring(document,encoding="utf-8",xml_declaration=True))
        z.writestr("META-INF/manifest.xml",'''<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
</manifest:manifest>''')
    return buf.getvalue()
