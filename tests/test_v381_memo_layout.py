"""ODT structure and optional real LibreOffice/PDF pagination regression.

Run EIS_MEMO_RENDER_TESTS=1 with soffice and PyMuPDF for visual geometry checks.
All rendered identities and procurement facts are synthetic.
"""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from v304_official_memo_odt import _render_odt
from v321_memo_layout import (OO,TX,ST,FO,DR,STYLE_NAME,EXECUTOR_STYLE,
                              EXECUTOR_FRAME_STYLE,_odf_string)
from memo_fixture import memo_template

SOURCE_NAME='Государственное автономное учреждение "центр ИТ РАН"'
PURCHASE={"reg_number":"32615969056","title":"Поставка оборудования","method":"Аукцион"}


def memo(count=1,repeats=1,fields=None,word_styles=False):
    findings=[{"title":"Нарушение срока размещения протокола № "+str(i+1),
               "law":"223-ФЗ, ст. 4 ч. 12","document":"Протокол № "+str(i+1)+".pdf",
               "evidence":"Протокол подписан 01.10.2026, размещен 15.10.2026. "*repeats}
              for i in range(count)]
    with zipfile.ZipFile(io.BytesIO(memo_template())) as archive:
        entries={name:archive.read(name) for name in archive.namelist()}
    root=ET.fromstring(entries['content.xml'])
    paragraphs=list(root.iter('{'+TX+'}p'))
    # Vary substantive body length, not the removed technical source excerpt.
    paragraphs[21].text=' '.join('Правовая оценка по 223-ФЗ учитывает установленные обстоятельства закупки.' for _ in range(repeats))
    if word_styles:
        for node in root.iter():
            for key in ('{'+ST+'}name','{'+TX+'}style-name'):
                if node.get(key) in ('P21','P22','P24'):
                    node.set(key,'Word'+node.get(key))
        styles=root.find('{'+OO+'}automatic-styles')
        style=next(s for s in styles if s.get('{'+ST+'}name')=='WordP21')
        ET.SubElement(style,'{'+ST+'}paragraph-properties',{'{'+FO+'}break-before':'page'})
    entries['content.xml']=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    source=io.BytesIO()
    with zipfile.ZipFile(source,'w') as archive:
        for name,binary in entries.items():archive.writestr(name,binary)
    return _render_odt(source.getvalue(),PURCHASE,findings,SOURCE_NAME,fields or {})


class MemoLayoutTests(unittest.TestCase):
    def test_footer_anchored_once_to_signature_with_exact_contents(self):
        for fields,expected in (({},"А. Б. Исполнитель"),
                                ({"executor":"Т. Т. Новый исполнитель"},"Т. Т. Новый исполнитель"),
                                ({"executor":""},"")):
            with self.subTest(fields=fields),zipfile.ZipFile(io.BytesIO(memo(fields=fields))) as z:
                root=ET.fromstring(z.read("content.xml"))
                body=root.find(".//{"+OO+"}text")
                frames=list(body.iter("{"+DR+"}frame"))
                self.assertEqual(len(frames),1)
                frame=frames[0]
                self.assertEqual(frame.get("{"+TX+"}anchor-type"),"paragraph")
                parents={child:parent for parent in body.iter() for child in parent}
                self.assertEqual(parents[frame].text,"И. И. Подписант")
                paras=list(frame.iter("{"+TX+"}p"))
                self.assertEqual([_odf_string(p) for p in paras],[expected,"+7 (000) 123-45-67"])
                self.assertTrue(all(p.get("{"+TX+"}style-name")==EXECUTOR_STYLE for p in paras))
                direct=list(body)
                self.assertIs(direct[-2],parents[frame])
                self.assertEqual(direct[-1].get('{'+TX+'}style-name'),'EIS223MemoExecutorReserve')
                self.assertFalse(_odf_string(direct[-1]))
                self.assertIn('"Центр ИТ РАН"',_odf_string(body))
                self.assertNotIn('"центр ИТ РАН"',_odf_string(body))

    def test_explicit_footer_and_consistent_body_styles(self):
        with zipfile.ZipFile(io.BytesIO(memo())) as z:
            root=ET.fromstring(z.read("content.xml"))
        styles={s.get("{"+ST+"}name"):s for s in root.findall(".//{"+ST+"}style")}
        font=styles[EXECUTOR_STYLE].find("{"+ST+"}text-properties")
        self.assertEqual(font.get("{"+FO+"}font-size"),"7pt")
        self.assertEqual(font.get("{"+FO+"}color"),"#000000")
        frame=styles[EXECUTOR_FRAME_STYLE].find("{"+ST+"}graphic-properties")
        self.assertEqual(frame.get("{"+ST+"}vertical-pos"),"bottom")
        self.assertEqual(frame.get("{"+ST+"}vertical-rel"),"page-content")
        self.assertEqual(frame.get("{"+ST+"}horizontal-rel"),"page-content")
        self.assertEqual(frame.get("{"+ST+"}flow-with-text"),"false")
        paragraph=styles[STYLE_NAME].find("{"+ST+"}paragraph-properties")
        for key,expected in (("text-align","justify"),("text-indent","1.25cm"),
                             ("margin-left","0cm"),("margin-right","0cm"),
                             ("margin-top","0cm"),("margin-bottom","0.11cm"),
                             ("line-height","120%")):
            self.assertEqual(paragraph.get("{"+FO+"}"+key),expected)

    def test_technical_evidence_dump_removed_but_each_violation_preserved(self):
        with zipfile.ZipFile(io.BytesIO(memo(count=4))) as archive:
            root=ET.fromstring(archive.read('content.xml'))
        text=_odf_string(root)
        self.assertNotIn('Подтверждающие обстоятельства',text)
        self.assertNotIn('Протокол подписан 01.10.2026',text)
        for i in range(1,5):
            self.assertIn('Нарушение срока размещения протокола № '+str(i),text)
            self.assertIn('Протокол № '+str(i)+'.pdf',text)
        self.assertIn('223-ФЗ',text)

    def test_word_style_names_and_source_page_break_do_not_detach_executor(self):
        with zipfile.ZipFile(io.BytesIO(memo(word_styles=True))) as archive:
            root=ET.fromstring(archive.read('content.xml'))
        body=root.find('.//{'+OO+'}text')
        frames=list(body.iter('{'+DR+'}frame'))
        self.assertEqual(len(frames),1)
        last=list(body)[-2]
        self.assertIs(frames[0],last.find('{'+DR+'}frame'))
        self.assertIn('А. Б. Исполнитель',_odf_string(last))
        for p in frames[0].iter('{'+TX+'}p'):
            self.assertEqual(p.get('{'+TX+'}style-name'),EXECUTOR_STYLE)

    def test_empty_requisites_have_no_frame_or_reserved_line(self):
        with zipfile.ZipFile(io.BytesIO(memo(fields={'executor':'','phone':''}))) as archive:
            root=ET.fromstring(archive.read('content.xml'))
        body=root.find('.//{'+OO+'}text')
        self.assertEqual(list(body.iter('{'+DR+'}frame')),[])
        self.assertFalse(any(p.get('{'+TX+'}style-name')=='EIS223MemoExecutorReserve'
                             for p in body.iter('{'+TX+'}p')))

    @unittest.skipUnless(os.getenv('EIS_MEMO_RENDER_TESTS')=='1','optional LibreOffice rendering')
    def test_maximum_width_requisites_do_not_overlap_body_or_lose_characters(self):
        import fitz
        fields={'executor':'Ш'*199,'phone':'W'*199}
        with tempfile.TemporaryDirectory(prefix='eis-memo-boundary-') as tmp:
            path=Path(tmp)/'boundary.odt'
            path.write_bytes(memo(1,5,fields,word_styles=True))
            result=subprocess.run(['soffice','--headless','--convert-to','pdf',
                                   '--outdir',tmp,str(path)],capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            with fitz.open(path.with_suffix('.pdf')) as pdf:
                seen=[]
                for index,page in enumerate(pdf):
                    footer=[];body=[]
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines',[]):
                            for span in line['spans']:
                                (footer if abs(span['size']-7)<.1 else body).append(span)
                    self.assertGreater(sum(len(s['text']) for s in body),80)
                    if footer:
                        self.assertEqual(index,len(pdf)-1)
                        self.assertIn('И. И. Подписант',page.get_text())
                        self.assertLess(max(s['bbox'][3] for s in body),min(s['bbox'][1] for s in footer))
                        self.assertTrue(all(s['color']==0 for s in footer))
                        self.assertLessEqual(max(s['bbox'][3] for s in footer),page.rect.height-50)
                        seen.extend(s['text'] for s in footer)
                self.assertEqual(''.join(seen),'Ш'*199+'W'*199)

    @unittest.skipUnless(os.getenv("EIS_MEMO_RENDER_TESTS")=="1","optional LibreOffice rendering")
    def test_real_pdf_short_long_and_boundary_pagination(self):
        self.assertIsNotNone(shutil.which("soffice"),"LibreOffice is required for requested render QA")
        import fitz
        with tempfile.TemporaryDirectory(prefix="eis381-memo-render-") as tmp:
            directory=Path(tmp)
            cases=((1,1),(1,8),(2,6),(3,10),(4,14))
            sources=[]
            for count,repeats in cases:
                path=directory/f"memo-{count}-{repeats}.odt"
                path.write_bytes(memo(count,repeats,word_styles=True));sources.append(path)
            # Match production: one document per isolated conversion. Writer's
            # multi-document batch export can retain stale floating-object layout.
            for path in sources:
                result=subprocess.run(["soffice","--headless","--convert-to","pdf","--outdir",tmp,
                                       str(path)],capture_output=True,text=True,timeout=40,
                                       env={**os.environ,"XDG_CACHE_HOME":tmp})
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            counts=[]
            for path in sources:
                with self.subTest(path=path.name),fitz.open(path.with_suffix(".pdf")) as pdf:
                    counts.append(len(pdf));footer=[]
                    for page_number,page in enumerate(pdf):
                        # A footer-only or blank trailing page is never acceptable.
                        text=page.get_text()
                        self.assertGreater(len(text.replace("А. Б. Исполнитель","").replace("+7 (000) 123-45-67","")),80)
                        other=[]
                        for block in page.get_text("dict")["blocks"]:
                            for line in block.get("lines",[]):
                                for span in line["spans"]:
                                    if "Исполнитель" in span["text"] or "+7 (000)" in span["text"]:
                                        footer.append((page_number,span))
                                    else:other.append(span)
                        if page_number==len(pdf)-1:
                            self.assertIn("И. И. Подписант",text)
                            self.assertIn("Центр",text)
                            for _,span in footer:
                                self.assertAlmostEqual(span["size"],7.0,places=1)
                                self.assertEqual(span["color"],0)
                                self.assertAlmostEqual(span["bbox"][0],56.7,delta=1.0)
                                self.assertGreater(span["bbox"][1],page.rect.height-80)
                                self.assertLessEqual(span["bbox"][3],page.rect.height-50)
                                self.assertLess(max(s["bbox"][3] for s in other),span["bbox"][1])
                    self.assertEqual(len(footer),2)
                    self.assertTrue(all(i==len(pdf)-1 for i,_ in footer))
            self.assertEqual(counts[0],1)
            self.assertGreater(counts[-1],1)


if __name__=="__main__":
    unittest.main()
