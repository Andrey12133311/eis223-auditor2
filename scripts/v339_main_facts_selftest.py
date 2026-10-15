"""Regression for the requested MAIN snapshot and document/EIS comparisons."""
import sqlite3
import tempfile
from pathlib import Path
from v332_all_procurements_audit import dashboard_regs,DASHBOARD_MAX
from v339_fact_comparison import fact_checks
from v331_broad_legal import verified

SRC=("https://zakupki.gov.ru/223/filestore/public/1.0/"
     "download/fz223/file.html?uid=0123456789ABCDEF0123456789ABCDEF")

def doc(text,name="Извещение.docx",trusted=True):
    return {"name":name,"type":"notice","status":"checked","text":text,
            "meta":{"source":"official_eis_original" if trusted else "local_unverified",
                    "sha256":"a"*64,"source_url":SRC},"url":SRC}

def run():
    with tempfile.TemporaryDirectory(prefix="eis223-v339-") as tmp:
        path=Path(tmp)/"local.sqlite"
        def conn():
            c=sqlite3.connect(path)
            c.row_factory=sqlite3.Row
            return c
        db=conn()
        db.executescript("""CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,last_seen TEXT);
                           CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY);""")
        regs=[str(32600000000+x) for x in range(1,1301)]
        db.executemany("INSERT INTO purchases VALUES(?,?)",((reg,str(i).zfill(5)) for i,reg in enumerate(regs)))
        db.execute("INSERT INTO v319_manual_inputs VALUES(?)",(regs[-1],))
        db.commit();db.close()
        ns={"conn":conn,"_verified_set235":lambda:set(regs)}
        selected=dashboard_regs(ns)
        assert len(selected)==DASHBOARD_MAX==1253,len(selected)
        assert regs[-1] not in selected
        assert selected[0]==regs[-2]
        assert selected[-1]==regs[len(regs)-1-DASHBOARD_MAX]
        assert not set(selected).intersection({regs[-1]})
        ns["_verified_set235"]=lambda:set(regs[:5])
        assert len(dashboard_regs(ns))==5
        del ns["_verified_set235"]
        try:dashboard_regs(ns)
        except RuntimeError:pass
        else:raise AssertionError("Could have re-audited entire DB without main verified set")

    purchase={
        "reg":"32616233661","submission_start":"2026-10-02T08:00",
        "submission_end":"2026-10-14T19:00",
        "price_num":250000,"price_kind":"subject_cost",
        "price":"250 000","method":"Аукцион в электронной форме",
        "customer_inn":"7701234567","url":"https://zakupki.gov.ru/223/"
    }
    text=("Стоимость предмета закупки: 300 000,00 руб.\n"
          "Дата и время окончания срока подачи заявок: 13.10.2026\n"
          "Способ закупки: Конкурс\n"
          "ИНН заказчика: 7701234568\n"
          "Расчет производится на основании счета в течение 15 рабочих дней "
          "с даты подписания документов о приемке товара.\n")
    d=doc(text)
    assert verified(d)
    result=fact_checks(purchase,{},{"summary":{"price_kind":"subject_cost"}},[d])
    names={c["field_path"] for c in result}
    for want in ("v339-price-difference-subject_cost","v339-date-submission_end",
                 "v339-purchase-method","v339-customer-inn","v339-payment-15-рабо"):
        assert want in names,(want,names)
    assert all(c["status"]=="check" and c["evidence"] and c["source_url"] for c in result)
    # Do not confuse different price types. Subject cost != legal NMCD.
    nmcd=doc("Начальная (максимальная) цена договора: 120 000,00 руб.")
    assert not any("price-difference" in c["field_path"] for c in
                   fact_checks(purchase,{},{"summary":{"price_kind":"subject_cost"}},[nmcd]))
    # The same calendar day is not a discrepancy, regardless of hour.
    same=doc("Дата и время окончания срока подачи заявок: 14.10.2026")
    assert not any("date-submission_end" in c["field_path"] for c in
                   fact_checks(purchase,{},{"summary":{}},[same]))
    # An unverified example never creates established violations.
    assert all(c["status"]!="violation" for c in fact_checks(purchase,{},{"summary":{}},[doc(text,trusted=False)]))
    print("V339_MAIN_FACT_SELFTEST_OK exactly_1253=True manual_separate=True "
          "dates=day_only typed_subject_cost=True method_and_inn=True payment_review=True",
          flush=True)

if __name__=="__main__":run()
