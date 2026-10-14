"""Non-destructive regression checks for ODT, verified price and manual audit status."""
from __future__ import annotations
import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from v311_verified_customer import _verified_money
from v321_memo_layout import format_service_memo, OO, TX, ST, FO, STYLE_NAME
from v257_features import manual_progress


def run():
    assert _verified_money("1 234 567,89 ₽")[1] == 1234567.89
    assert _verified_money("250000.00 руб.")[1] == 250000.0
    for malformed in ("не установлена", "10000-50000", "по формуле", "", "12abc"):
        try:
            _verified_money(malformed)
            raise AssertionError("Price accepted without concrete official amount: "+repr(malformed))
        except ValueError:
            pass

    root=ET.Element("{"+OO+"}document-content")
    ET.SubElement(root,"{"+OO+"}automatic-styles")
    body=ET.SubElement(ET.SubElement(root,"{"+OO+"}body"),"{"+OO+"}text")
    for i in range(45):
        p=ET.SubElement(body,"{"+TX+"}p")
        p.text="СЛУЖЕБНАЯ ЗАПИСКА" if i==16 else "Исходный текст."
    input_data=io.BytesIO()
    with zipfile.ZipFile(input_data,"w") as z:
        z.writestr("mimetype","application/vnd.oasis.opendocument.text")
        z.writestr("content.xml",ET.tostring(root,encoding="utf-8"))
    findings=[{"title":"Установлено нарушение срока разъяснений",
               "document":"Извещение 123.docx",
               "evidence":"Дата завершения разъяснений указана раньше допустимой."},
              {"title":"Второе подтверждённое нарушение",
               "document":"Протокол 456.docx",
               "evidence":"Из документа следует подтверждённое обстоятельство."}]
    new=format_service_memo(input_data.getvalue(),findings)
    with zipfile.ZipFile(io.BytesIO(new)) as z:
        document=ET.fromstring(z.read("content.xml"))
    pstyle=next(x for x in document.findall(".//{"+ST+"}style")
                if x.get("{"+ST+"}name")==STYLE_NAME)
    properties=pstyle.find("{"+ST+"}paragraph-properties")
    assert properties.get("{"+FO+"}text-align")=="justify"
    assert pstyle.find("{"+ST+"}text-properties").get("{"+FO+"}font-size")=="14pt"
    para_texts=["".join(p.itertext()) for p in document.findall(".//{"+TX+"}p")]
    assert any("Второе подтверждённое нарушение" in p for p in para_texts)
    assert any("Протокол 456.docx" in p for p in para_texts)

    reg="32616287360"
    uid="1234567890ABCDEF1234567890ABCDEF"
    with tempfile.TemporaryDirectory(prefix="v323-audit-") as tmp:
        old=os.environ.get("EIS223_DATA_DIR")
        os.environ["EIS223_DATA_DIR"]=tmp
        try:
            # A completed search without uploaded originals MUST NOT look
            # like a completed legal audit.
            state=manual_progress(reg,0,"done")
            assert not state["audit_complete"] and state["audit_status"]=="search_only"
            receiptdir=Path(tmp)/"eis223_official_originals"/reg/uid
            receiptdir.mkdir(parents=True)
            raw=b"%PDF-1.7\nTest verified bytes\n"
            digest=hashlib.sha256(raw).hexdigest()
            (receiptdir/(digest+".pdf")).write_bytes(raw)
            (receiptdir/"receipt.json").write_text(json.dumps({
                "reg_number":reg,"uid":uid,"extension":".pdf",
                "sha256":digest,"bytes":len(raw)}),encoding="utf-8")
            manifest=Path(tmp)/"eis223_public_browser_intake"/reg
            manifest.mkdir(parents=True)
            (manifest/"manifest.json").write_text(json.dumps({
                "reg_number":reg,"source":"public_eis_223_browser",
                "declared_attachment_count":1,
                "documents":[{"url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+uid,
                              "name":"Оригинал.pdf"}]}),encoding="utf-8")
            reviews=Path(tmp)/"eis223_official_review_jobs"
            reviews.mkdir()
            (reviews/(reg+".json")).write_text(json.dumps({
                "reg_number":reg,"status":"completed",
                "originals_received":1,"checked_documents":1}),encoding="utf-8")
            state=manual_progress(reg,1,"done")
            assert state["audit_complete"] and state["audit_status"]=="completed"
            state=manual_progress(reg,0,"done")
            assert not state["audit_complete"]
        finally:
            if old is None:os.environ.pop("EIS223_DATA_DIR",None)
            else:os.environ["EIS223_DATA_DIR"]=old
    print("V323 MEMO_NMCD_MANUAL_SELFTEST_OK odt_justified=True real_price=True real_original_proof=True",flush=True)


if __name__=="__main__":
    run()
