import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from v314_eis_metadata import (
    apply_metadata, from_documents, parse_text, stored_metadata, valid_inn,
)

URL="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32


def test_docx_label_and_subject_not_status():
    fields=parse_text("""Наименование Заказчика | Государственное автономное учреждение «Агентство»
Наименование закупки (предмет договора) | Оказание услуг по организации мероприятий
ИНН 6501309746
""")
    assert fields["customer"]=="Государственное автономное учреждение «Агентство»"
    assert fields["title"]=="Оказание услуг по организации мероприятий"
    assert fields["customer_inn"]=="6501309746"
    assert not parse_text("Наименование закупки | Подача заявок").get("title")


def test_wrapped_binary_doc_buyer_and_subject():
    fields=parse_text("""|3. |Наименование, место |ГАУЗ АО «АОКБ» |
|   |нахождения, адрес,  |675028, Амурская область |
|   |Заказчика           |тел.: 8 (4162) 23-85-38 |
|5. |Предмет закупки     |Поставка лекарственного препарата |
|   |                    |«Метронидазол». |
|ИНН 2801020845 / КПП 280101001 | |
""")
    assert fields["customer"]=="ГАУЗ АО «АОКБ»"
    assert fields["title"]=="Поставка лекарственного препарата «Метронидазол»."
    assert fields["customer_inn"]=="2801020845"


def test_full_short_names_and_address_wrapped_in_two_columns():
    fields=parse_text("""|Наименование Заказчика |Открытое акционерное общество «Владивостокский |
|(полное, сокращенное) |морской рыбный порт» |
|                      |ОАО «Владморрыбпорт» |
|                      |Место нахождения: 690012, г. Владивосток |
""")
    assert fields["customer"]=="Открытое акционерное общество «Владивостокский морской рыбный порт»"


def test_nested_buyer_section():
    fields=parse_text("""3 | Сведения о Заказчике
Наименование: | Краевое государственное автономное учреждение «Охрана камчатских лесов»
4 | Сведения о закупке
Наименование: | Общество с ограниченной ответственностью «Поставщик»
""")
    assert fields["customer"]=="Краевое государственное автономное учреждение «Охрана камчатских лесов»"


def test_supplier_organizer_and_recipient_inn_are_not_buyer():
    assert not parse_text("Наименование организатора | ООО «Организатор»").get("customer")
    assert not parse_text("Наименование участника | ООО «Поставщик»").get("customer")
    fields=parse_text("Наименование заказчика | ООО «Заказчик»\nИНН получателя: 4101143965")
    assert not fields.get("customer_inn")
    fields=parse_text("Наименование заказчика | ООО «Заказчик»\nПоставщик:\nИНН 2801020845")
    assert not fields.get("customer_inn")
    fields=parse_text("Наименование заказчика | ООО «Заказчик»\nИНН 6501309746\nИНН 2801020845")
    assert not fields.get("customer_inn")
    assert valid_inn("6501309746")=="6501309746"
    assert valid_inn("6501309747")==""


def test_contract_adds_inn_only_to_same_named_buyer():
    def doc(text,source="official_eis_original"):
        return {"name":"Извещение.docx","url":URL,"text":text,"meta":{"source":source}}
    fields=from_documents("32616467075",[
        doc("Наименование заказчика | Государственное автономное учреждение «Агентство» (ГАУ «Агентство»)"),
        doc('Государственное автономное учреждение «Агентство» (далее – ГАУ «Агентство»), именуемое в дальнейшем «Заказчик»\nИНН 6501309746'),
        doc("Наименование заказчика | ООО «Посторонний»\nИНН 2801020845","user_upload"),
    ])
    assert fields["customer_inn"]=="6501309746"
    assert not from_documents("32616467075",[doc("Наименование заказчика | ООО «Посторонний»","user_upload")])


def database(tmp_path):
    path=tmp_path/"test.sqlite"
    def conn():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row;return db
    db=conn()
    db.executescript("""
      CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, customer TEXT, customer_inn TEXT,
        title TEXT, org_form TEXT, card_fields_json TEXT, last_seen TEXT);
      CREATE TABLE v311_verified_buyers(reg TEXT PRIMARY KEY,name TEXT,inn TEXT,
        evidence_page TEXT,evidence_source TEXT,captured_at TEXT);
      CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,url TEXT,
        doc_type TEXT,local_path TEXT,metadata_json TEXT,status TEXT);
      CREATE TABLE findings(id INTEGER PRIMARY KEY,evidence TEXT);
      INSERT INTO findings VALUES(1,'existing finding');
      INSERT INTO purchases VALUES('32616467075',NULL,NULL,'Подача заявок','Не определена',
        '{"status":"partial","summary":{}}','2026-10-14');
    """)
    db.commit();db.close()
    return {"conn":conn,"org_form":lambda x:"ГАУ"}


def test_backfill_updates_metadata_preserving_findings_and_partial_status(tmp_path):
    ns=database(tmp_path)
    metadata={"customer":"Государственное автономное учреждение «Агентство»",
              "customer_inn":"6501309746","title":"Оказание услуг по организации мероприятий",
              "source_url":URL}
    assert apply_metadata(ns,"32616467075",metadata)
    assert not apply_metadata(ns,"32616467075",metadata)
    with ns["conn"]() as db:
        p=dict(db.execute("SELECT * FROM purchases").fetchone())
        assert p["customer"]==metadata["customer"] and p["customer_inn"]==metadata["customer_inn"]
        assert p["title"]==metadata["title"]
        assert json.loads(p["card_fields_json"])["status"]=="partial"
        assert db.execute("SELECT evidence FROM findings").fetchone()[0]=="existing finding"
        assert db.execute("SELECT evidence_source FROM v311_verified_buyers").fetchone()[0]=="official_eis_document"


def test_original_hash_must_match_receipt(tmp_path,monkeypatch):
    ns=database(tmp_path)
    path=tmp_path/"original.doc";path.write_bytes(b"authentic original bytes")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with ns["conn"]() as db:
        db.execute("INSERT INTO docs VALUES(1,?,?,?,?,?,?,?)",(
            "32616467075","Извещение.doc",URL,"notice",str(path),
            json.dumps({"source":"official_eis_original","sha256":digest}),"checked"))
    import v314_eis_metadata as module
    monkeypatch.setattr(module,"allowed_documents",lambda reg:{"A"*32:{"url":URL}})
    monkeypatch.setattr(module,"saved_document",lambda reg,uid:(path,{"sha256":digest}))
    ns["extract_text"]=lambda p:"Наименование заказчика | ООО «Заказчик»"
    assert stored_metadata(ns,"32616467075")["customer"]=="ООО «Заказчик»"
    path.write_bytes(b"modified bytes")
    assert not stored_metadata(ns,"32616467075")
