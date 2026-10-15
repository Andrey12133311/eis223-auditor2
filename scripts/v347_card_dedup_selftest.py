"""Regression: repeated documentary proof must show one card, without deleting records."""
import sqlite3
import tempfile
from pathlib import Path
from v343_fast_cards import render,render_thematic_panel
from v333_dedup_ui import collapse

REG="32615969056"
TITLE="Срок оплаты требует проверки: превышен предел для МСП"
LAW="часть 5.3 статьи 3 223-ФЗ; пункт 28 Положения № 1352"
DOC="Документация о поставке круп.docx"
CLAUSE=("Расчет производится на основании счета (счета-фактуры) в течение "
        "15 рабочих дней с даты подписания Заказчиком документов о приемке товара.")
A="Файл/источник: "+DOC+"; фрагмент: ации. 9 | Форма, сроки и порядок оплаты товара, работы, услуги | "+CLAUSE+" Сроки и порядок проведения процедур аукциона"
B="ации. 9 | Форма, сроки и порядок оплаты товара, работы, услуги | "+CLAUSE
C="Файл/источник: "+DOC+"; фрагмент: оплата в течение 25 рабочих дней после приемки"
def mk_evidence(title,evidence,status=None):
    return {"title":title,"evidence":evidence,"law":LAW,"document":DOC,
      "source_url":"https://zakupki.gov.ru/epz/something","status":status}
def run():
    f=mk_evidence(TITLE,A)
    f["code"]="PAYMENT"
    check=mk_evidence(TITLE,B,"violation")
    check["item"]=TITLE
    # One underlying contractual condition appears in findings and checks.
    compressed=collapse([f,check])
    assert len(compressed)==1,compressed
    other=mk_evidence(TITLE,"Другой договор: расчет в течение 25 рабочих дней с даты приемки")
    assert len(collapse([f,other]))==2
    other_doc=mk_evidence(TITLE,B)
    other_doc["document"]="Отдельный договор.docx"
    assert len(collapse([f,other_doc]))==2
    data=(
      {"reg_number":REG,"title":"Поставка материалов","customer":"ООО Тест",
       "customer_inn":"7700000000","method":"Конкурс","price":"100 000 ₽",
       "documents_checked":2,"documents_found":2},
      [{**f,"kind":"violation","law_url":""}],
      [{"item":TITLE,"status":"violation","law":LAW,
        "evidence":B,"document":DOC,"source_url":f["source_url"],"law_url":""}],
      [],1,1,0)
    html=render(REG,data)
    assert html.count('<article class="legal">')==1,html
    assert "Подтверждённые нарушения: 1" in html
    assert "Исходных подтверждённых записей: 2" in html
    # Results in thematic tab use the same collapse without writing to DB.
    themed={"batch":"a"*24,"findings":[{"title":TITLE,"evidence":A,
       "category":"sme","law":LAW},{"title":TITLE,"evidence":B,
       "category":"contract","law":LAW}],
       "category_labels":{"sme":"МСП","contract":"Договор"}}
    panel=render_thematic_panel(REG,themed)
    assert "Нарушения тематической проверки: 1" in panel
    assert panel.count('<article class="legal">')==1
    assert html.count("Документы ЕИС")==1
    print("V347_CARD_DEDUP_SELFTEST_OK same_event_one_card=True different_payment_days_retained=True different_documents_retained=True cross_table=True thematic=True")
if __name__=="__main__":
    run()
