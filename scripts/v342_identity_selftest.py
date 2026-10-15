"""Identity repair, fixed re-audit scope and independently dated events."""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from v299_browser_intake import _apply_customer, _known
from v311_verified_customer import _name
from v314_eis_metadata import parse_text
from v342_identity import subject, valid_inn
from v332_all_procurements_audit import persist, dashboard_regs, DASHBOARD_MAX
from v341_precise_legal import evaluate

REG = "32616422163"
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32


def original(text):
    return {"name":"Извещение и документация.docx", "status":"checked", "text":text,
            "url":SOURCE,"meta":{"source":"official_eis_original",
                                  "sha256":"a"*64,"source_url":SOURCE}}


def run():
    assert valid_inn("6500000024 / 650101001") == "6500000024"
    assert valid_inn("6500000025") == ""
    assert _name("Наименование (полное) - Публичное акционерное общество «Сахалинэнерго»") == "Публичное акционерное общество «Сахалинэнерго»"
    assert _name("Наименование (полное)") == ""
    assert not _name("Уточняем заказчика")
    for value in ("Подача заявок", "ПОДАЧА ЗАЯВОК", "Приём заявок", "Подведение итогов",
                  "Закупка № 32616422163", "Закупка - совокупность действий заказчика"):
        assert not subject(value), value
    assert subject("Поставка реактивов") == "Поставка реактивов"
    fields=parse_text("Сведения о заказчике |\nНаименование | ПАО «Сахалинэнерго»\n"
                      "ИНН / КПП | 6500000024 / 650101001\nПредмет договора | Поставка сервера")
    assert fields["customer_inn"] == "6500000024", fields
    assert fields["title"] == "Поставка сервера", fields

    p={"reg":REG,"method":"Аукцион в электронной форме","submission_end":"2026-10-22"}
    payment=original("Участники закупки | Только субъекты малого и среднего предпринимательства\n"
                     "Оплата оказанных услуг по договору производится\nв течение 15 рабочих дней "
                     "с даты подписания акта сдачи-приемки услуг.")
    found,_=evaluate(p,[payment])
    assert any(f["code"] == "1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS" for f in found),found
    generic=original(payment["text"].replace("Участники закупки | Только", "Общие сведения о поддержке: Только"))
    assert not evaluate(p,[generic])[0]
    untrusted=original(payment["text"]);untrusted["meta"]["source"]="unverified"
    assert not evaluate(p,[untrusted])[0]
    timely=original(payment["text"].replace("15 рабочих", "7 рабочих"))
    assert not evaluate(p,[timely])[0]
    contradicted={**p,"method":"Конкурс только субъекты малого и среднего предпринимательства",
        "fact_evidence":{"sme_only":{"value":False,"source":"official_eis_card"}}}
    assert not evaluate(contradicted,[payment])[0]

    cancelled=original("Дата принятия решения об отмене закупки: 01.10.2026\n"
        "Дата размещения извещения об отмене закупки в ЕИС: 02.10.2026\n"
        "Дата принятия решения об отмене закупки: 08.10.2026\n"
        "Дата размещения извещения об отмене закупки в ЕИС: 09.10.2026")
    findings,_=evaluate(p,[cancelled,cancelled.copy()])
    assert len(findings)==2,findings

    with tempfile.TemporaryDirectory(prefix="eis-v342-test-") as directory:
        path=Path(directory)/"test.sqlite"
        def conn():
            db=sqlite3.connect(path);db.row_factory=sqlite3.Row
            return db
        db=conn()
        db.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,customer_inn TEXT,
            price TEXT,price_num REAL,documents_checked INT DEFAULT 1,violations INT DEFAULT 0,
            checks_ok INT DEFAULT 0,checks_attention INT DEFAULT 0,last_seen TEXT);
          CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,kind TEXT,title TEXT,
            law TEXT,law_url TEXT,evidence TEXT,note TEXT,document TEXT,source_url TEXT,location TEXT,match_text TEXT);
          CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,item TEXT,status TEXT,
            evidence TEXT,source TEXT,source_url TEXT,document TEXT,field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT);
          CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY);
        """)
        db.execute("INSERT INTO purchases(reg_number,title,customer,price,price_num,last_seen) VALUES(?,?,?,?,?,?)",
            (REG,"Подача заявок","Наименование (полное) - ПАО «Сахалинэнерго»","1000",1000,"2026-10-15"))
        db.commit();db.close()
        ns={"conn":conn}
        import v299_browser_intake as intake
        old_allowed,old_saved=intake.allowed_documents,intake.saved_document
        try:
            intake.allowed_documents=lambda reg:{}
            intake.saved_document=lambda reg,uid:None
            assert _known(ns,[REG])[REG]["needs_metadata"]
            update={"reg_number":REG,"title":"Поставка сервера","customer_inn":"6500000024"}
            assert _apply_customer(ns,update)
            assert not _apply_customer(ns,update)
            assert not _known(ns,[REG])[REG]["needs_metadata"]
            assert not _apply_customer(ns,{"reg_number":REG,"title":"Подача заявок","customer_inn":"6500000025"})
        finally:
            intake.allowed_documents,intake.saved_document=old_allowed,old_saved
        db=conn();row=dict(db.execute("SELECT * FROM purchases WHERE reg_number=?",(REG,)).fetchone());db.close()
        assert row["title"] == "Поставка сервера" and row["customer_inn"] == "6500000024",row
        assert row["customer"] == "ПАО «Сахалинэнерго»",row
        # A subject can arrive before the EIS buyer section is available.
        # Require the same authenticated official origin for this partial update.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from v311_verified_customer import install as install_buyer
        ns["app"]=FastAPI()
        install_buyer(ns)
        db=conn();db.execute("UPDATE purchases SET title='Подача заявок' WHERE reg_number=?",(REG,));db.commit();db.close()
        previous_key=os.environ.get("EIS223_UPLOAD_TOKEN")
        os.environ["EIS223_UPLOAD_TOKEN"]="v342-local-selftest-key"
        try:
            client=TestClient(ns["app"])
            payload={"reg_number":REG,"source_page":"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+REG,
                     "title":"Поставка сервера"}
            headers={"origin":"https://zakupki.gov.ru","x-eis223-collector-version":"342",
                     "x-eis223-upload-token":"v342-local-selftest-key"}
            assert client.post("/api/v311/eis223/customer",json=payload).status_code==401
            result=client.post("/api/v311/eis223/customer",json=payload,headers=headers)
            assert result.status_code==200 and result.json()["updated"],result.text
            assert client.post("/api/v311/eis223/customer",json=payload,headers={**headers,"origin":"https://example.com"}).status_code==403
        finally:
            if previous_key is None:os.environ.pop("EIS223_UPLOAD_TOKEN",None)
            else:os.environ["EIS223_UPLOAD_TOKEN"]=previous_key
        assert persist(ns,REG,findings,[]) == (2,0)
        assert persist(ns,REG,findings,[]) == (0,0)
        # A different date spelling of the same factual event is a duplicate.
        same=[{**f,"evidence":f["evidence"].replace("2026-10-01","01.10.2026")} for f in findings]
        assert persist(ns,REG,same,[]) == (0,0)
        db=conn()
        regs=["3"+str(n).zfill(10) for n in range(1,1261)]
        db.executemany("INSERT OR IGNORE INTO purchases(reg_number,last_seen) VALUES(?,'2026-10-14')",((v,) for v in regs))
        db.execute("INSERT INTO v319_manual_inputs VALUES(?)",(regs[-1],));db.commit();db.close()
        ns["_verified_set235"]=lambda:regs
        selected=dashboard_regs(ns)
        assert DASHBOARD_MAX==1253 and len(selected)==1253
        assert regs[-1] not in selected and REG not in selected
    print("V342_IDENTITY_SELFTEST_OK unicode_repair=True missing_inn_retry=True exact_1253_scope=True "
          "dated_events_persisted=True sme_scope_verified=True",flush=True)


if __name__ == "__main__":
    run()
