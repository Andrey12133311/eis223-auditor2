"""Regression checks for canonical findings and blank ODT inspection comments."""
import io
import zipfile
from xml.etree import ElementTree as ET
from v333_dedup_ui import collapse, issue_key, blank_inspection_comments, ODF_TABLE, ODF_TEXT

def run():
    x=[{"title":"Требование дополнительного соглашения в составе заявки","evidence":"abc"},
       {"title":"Требование дополнительного соглашения в составе заявки","evidence":"abc longer"},
       {"title":"Срок разъяснений ограничен раньше допустимого срока","evidence":"2026-10-06"},
       {"title":"Соответствие срока разъяснений актуальному сроку подачи заявок","evidence":"2026-10-06"}]
    result=collapse(x)
    # Same heading is NOT enough to merge different evidence.
    assert len(result)==3
    assert result[0]["evidence"]=="abc"
    assert result[1]["evidence"]=="abc longer"
    assert len(collapse([
        {"title":"Срок разъяснений","document":"a.docx","evidence":"2026-10-06"},
        {"title":"Срок разъяснений","document":"b.docx","evidence":"2026-10-06"},
        {"title":"Срок разъяснений","document":"a.docx","evidence":"2026-10-06"},
    ]))==2
    assert issue_key(x[2]["title"])==issue_key(x[3]["title"])
    # Real repeated SME payment finding: raw excerpt and file/source wrapper
    # describe the SAME clause even if a truncated snippet starts midword.
    payment="Срок оплаты требует проверки: превышен предел для МСП"
    paragraph=("ации. 9 | Форма, сроки и порядок оплаты товара, работы, услуги | "
               "Расчет производится на основании счета (счета-фактуры) "
               "в течение 15 рабочих дней с даты подписания Заказчиком "
               "документов о приемке товара. Сроки и порядок проведения "
               "процедур аукциона | Сроки и порядок")
    repeats=[
        {"title":payment,"kind":"violation","document":"Документация о поставке круп.docx",
         "evidence":"Файл/источник: Документация о поставке круп.docx; фрагмент: "+paragraph},
        {"title":payment,"kind":"violation","evidence":paragraph},
        {"title":payment,"kind":"violation","evidence":paragraph},
    ]
    unique=collapse(repeats)
    assert len(unique)==1,unique
    assert unique[0]["document"]=="Документация о поставке круп.docx"
    # Different factual deadlines must remain independent, even with the
    # same legal title and document.
    other=dict(repeats[1],evidence=paragraph.replace("15 рабочих","10 рабочих"))
    assert len(collapse([repeats[1],other]))==2
    # Different originals containing separately sourced clauses remain visible.
    other_doc=dict(repeats[0],document="Другой договор.docx")
    assert len(collapse([repeats[0],other_doc]))==2
    # A short generic common title is insufficient to erase distinct evidence.
    assert len(collapse([{"title":payment,"evidence":"Оплата 15 дней, раздел 2"},
                         {"title":payment,"evidence":"Оплата 15 дней, раздел 3"}]))==2
    root=ET.Element("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}document-content")
    table=ET.SubElement(root,"{"+ODF_TABLE+"}table")
    for number in range(3):
        row=ET.SubElement(table,"{"+ODF_TABLE+"}table-row")
        for col in range(2):
            cell=ET.SubElement(row,"{"+ODF_TABLE+"}table-cell")
            p=ET.SubElement(cell,"{"+ODF_TEXT+"}p")
            p.text=("Нарушение" if col==0 else "Комментарий") if number==0 else (
                "Вопрос "+str(number) if col==0 else "Удалить замечание "+str(number))
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,"w") as z:
        z.writestr("mimetype","application/vnd.oasis.opendocument.text")
        z.writestr("content.xml",ET.tostring(root,encoding="utf-8"))
    updated=blank_inspection_comments(buff.getvalue())
    with zipfile.ZipFile(io.BytesIO(updated)) as z:
        parsed=ET.fromstring(z.read("content.xml"))
    rows=list(parsed.iter("{"+ODF_TABLE+"}table-row"))
    assert len(rows)==3
    first=list(rows[0].iter("{"+ODF_TEXT+"}p"))
    assert [p.text for p in first]==["Нарушение","Комментарий"]
    for row in rows[1:]:
        columns=list(row.findall("{"+ODF_TABLE+"}table-cell"))
        assert list(columns[0].iter("{"+ODF_TEXT+"}p"))[0].text.startswith("Вопрос")
        assert list(columns[1].iter("{"+ODF_TEXT+"}p"))[0].text is None
    print("V333 QUALITY_SELFTEST_OK dedup_distinct_evidence=True comments_blank=True",flush=True)

if __name__=="__main__":
    run()
