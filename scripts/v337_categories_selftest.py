"""No-production-data regression for the comprehensive legal catalog."""
from v337_all_legal_categories import (
    CATALOG, STRUCTURAL, CHAPTERS, list_categories, parse_catalog, signals,
)
import re

def document(text,name="Условия закупки.docx",trusted=True):
    return {"name":name,"status":"checked","text":text,
            "meta":{"source":"official_eis_original" if trusted else "external_reference",
                    "sha256":"a"*64,
                    "source_url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=0123456789ABCDEF0123456789ABCDEF"}}

def run():
    report=list_categories()
    assert len(CHAPTERS)>=15
    assert len(CATALOG)>=140,len(CATALOG)
    assert len(STRUCTURAL)>=30,len(STRUCTURAL)
    assert report["total"]>=200,report["total"]
    ids=[x["id"] for x in report["items"]]
    assert len(set(ids))==len(ids),{"duplicates":[x for x in ids if ids.count(x)>1][:10]}
    assert all(x["law"] and x["title"] and x["qualification"] for x in report["items"])
    assert all(x["qualification"]!="automatic_violation" for x in report["items"])
    cases=[
        ("Реестр недобросовестных поставщиков: сведения представлены заказчику.","rnp"),
        ("В составе заявки участник обязан представить банковскую справку.","bank_certificates"),
        ("Протокол рассмотрения заявок по запросу предложений.","application_protocol"),
        ("Заказчик заключает прямой договор, поскольку конкурентная закупка признана несостоявшейся.","negotiated_single"),
    ]
    for sample,code in cases:
        records=signals({"reg":"32616233661"},[document(sample)])
        assert all(x["status"]=="check" for x in records),records
        assert any(x["field_path"]=="v337-"+code for x in records),(code,records)
        assert all(x["evidence"] and x["law"] and x["document"] for x in records)
    no_original=signals({},[document("В составе заявки участник обязан представить банковскую справку.",trusted=False)])
    assert any(x["field_path"]=="v337-bank_certificates" for x in no_original)
    assert all(x["status"]=="check" for x in no_original)
    # Source-only checks must not be automatically reported for missing data:
    assert signals({},[document("Техническое задание на поставку столов.")])==[]
    assert signals({},[{"name":"blank.pdf","status":"download_error","text":""}])==[]
    try:parse_catalog("not_a_group~bad~bad~anchor~badregex")
    except ValueError:pass
    else:raise AssertionError("Unrecognized group accepted")
    assert len(signals({},[document("В составе заявки участник обязан представить банковскую справку.")]*1000))<=100
    print("V337_ALL_CATEGORIES_SELFTEST_OK total="+str(report["total"])+
          " text="+str(len(CATALOG))+" structural="+str(len(STRUCTURAL))+
          " no_auto_violations=True anti_spam=True",flush=True)

if __name__=="__main__":run()
