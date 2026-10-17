"""V372 automatic protocol/regulation tests: all work from saved EIS originals.

Run during the image build; tests never call Railway, EIS or a real DB.
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from v372_auto_regulation_protocols import (
    evaluate,install,LAW_REG,LAW_PROT,LAW_4,
    _reg_doc,_protocol_doc
)

EIS="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32
P={"reg":"32616412317","method":"Открытый конкурс",
   "published":"2026-02-12","title":"Поставка приборов"}
def official(name,body,original=True):
    return {"name":name,"status":"checked","text":body,
      "meta":{"source":"official_eis_original" if original else "unknown",
              "sha256":"a"*64,"source_url":EIS},
      "url":EIS}

PROTOCOL_HEAD=("Протокол рассмотрения заявок. По итогам конкурентной закупки "
               "комиссия рассмотрела документы участников. ")
def test_explicit():
    text=PROTOCOL_HEAD+(
      "По заявке № 12 комиссия приняла решение: отклонена заявка № 12, "
      "основания отклонения не указаны. Итоги рассмотрения зафиксированы.")
    doc=official("Протокол рассмотрения заявок.pdf",text)
    snapshot=deepcopy(doc)
    finds,checks=evaluate(P,[doc])
    assert len(finds)==1,finds
    result=finds[0]
    assert result["kind"]=="violation"
    assert result["code"]=="v372-protocol-rejected-bid-no-reasons"
    assert "ст. 3.2 ч. 13" in result["law"]
    assert result["law_url"]==LAW_PROT
    assert result["source_url"]==EIS
    assert result["document"]==doc["name"]
    assert result["evidence"] and "заявка № 12" in result["evidence"]
    assert snapshot==doc,"Do not change original payload"
    assert len(checks)<=2
    # A verified EIS source is mandatory even when the text admits the error.
    assert evaluate(P,[official(doc["name"],text,False)])==([],[])
    assert evaluate({"method":"Закупка у единственного поставщика"},[doc])==([],[])
    assert evaluate(P,[{"name":doc["name"],"text":text,"status":"checked"}])==([],[])
    # Quotes of hypothetical violations in templates cannot be findings.
    assert evaluate(P,[official(doc["name"],"Образец протокола. "+text)])[0]==[]

def test_protocol_review():
    text=PROTOCOL_HEAD+"Отклонена заявка № 24 в связи с несоответствием требованиям, заявка не допущена. "
    findings,checks=evaluate(P,[official("Итоговый протокол.pdf",text)])
    assert not findings
    assert any(c["field_path"]=="v372-protocol-rejected-bid-document-clause" for c in checks)
    assert all(c["status"]=="check" for c in checks)
    assert all(c["law_url"]==LAW_PROT for c in checks)
    grounded=PROTOCOL_HEAD+"Отклонена заявка № 24 в связи с несоответствием пункту 4.3 документации. "
    finds,checks=evaluate(P,[official("Итоговый протокол.pdf",grounded)])
    assert not finds
    assert not any(x["field_path"]=="v372-protocol-rejected-bid-document-clause" for x in checks)
    # Annexes, scanned or unpublished documents must never yield fake findings.
    assert evaluate(P,[official("Протокол отмены разъяснений.pdf",text)])==([],[])
    assert evaluate(P,[official("Документация.docx",text)])==([],[])

def test_regulation():
    reg_text=(
      "Положение о закупке товаров и услуг. "
      "Дата утверждения положения о закупке: 01.02.2026. "
      "Дата размещения положения о закупке в ЕИС: 01.03.2026. "
      "Способы закупки: аукцион, запрос котировок. "
    )+"Общие положения закупок и договоров. "*25
    original=official("Положение о закупке.docx",reg_text)
    found,checks=evaluate(P,[original])
    assert not found,"A date declaration in one file is not independent proof of breach"
    paths={x["field_path"] for x in checks}
    assert "v372-regulation-publication-timing" in paths,checks
    assert "v372-regulation-purchase-method" in paths,checks
    for c in checks:
      assert c["status"]=="check"
      assert c["law_url"] in (LAW_REG,LAW_4)
      assert c["evidence"] and c["document"]==original["name"]
    finds,review=evaluate(P,[official("КД.docx",reg_text)])
    assert not finds
    assert not any(x["field_path"].startswith("v372-regulation") for x in review)
    assert evaluate(P,[official("Положение о закупке.docx",reg_text,False)])==([],[])
    # Explicit later approval cannot establish that earlier rules were invalid.
    p={**P,"published":"2026-01-03"}
    _,review=evaluate(p,[original])
    assert any(c["field_path"]=="v372-regulation-version" for c in review)
    assert all(c["status"]=="check" for c in review)

def test_integrated_auto():
    proto=official("Протокол рассмотрения заявок.pdf",
        PROTOCOL_HEAD+"По заявке № 18 отклонена заявка № 18, основания отклонения не указаны.")
    ns={"analyze":lambda p,docs:[{"kind":"violation","code":"old",
                                  "title":"Существующее нарушение"}],
        "build_checks":lambda p,detail,card,docs:[
            {"status":"check","field_path":"prior-review","item":"Старое замечание"}]}
    install(ns)
    assert getattr(ns["analyze"],"__v372__",False)
    finds=ns["analyze"](P,[proto])
    assert len(finds)==2 and finds[0]["code"]=="old" and finds[1]["code"].startswith("v372-")
    checks=ns["build_checks"](P,{}, {},[proto])
    assert checks[0]["field_path"]=="prior-review"
    assert all(x["status"]=="check" for x in checks[1:])
    install(ns)
    assert len(ns["analyze"](P,[proto]))==2,"Second startup must not wrap rules twice"
    # Active entrypoint (not an unused parallel demo or a manual button)
    entry=Path(__file__).with_name("railway_start_v282.py").read_text(encoding="utf-8")
    assert "v372_auto_regulation_protocols.install(vars(entry))" in entry
    assert entry.index("v348_cross_document_legal.install(vars(entry))")<entry.index(
        "v372_auto_regulation_protocols.install(vars(entry))")<entry.index(
        "v342_thematic_queue.install(vars(entry))")
    from v332_all_procurements_audit import RULE_REVISION
    assert "v372" in RULE_REVISION,"Existing saved purchases should be automatically requeued"
    from v342_thematic_queue import independent_review
    assert 'ns.get("analyze")' in __import__("inspect").getsource(independent_review)

if __name__=="__main__":
    test_explicit()
    test_protocol_review()
    test_regulation()
    test_integrated_auto()
    print("V372_AUTO_LEGAL_SELFTEST_OK real_protocols=True regulations=True "
          "no_absence_inferred_violation=True receipt_only=True "
          "generic_and_thematic=True automatic_reaudit=True idempotent_install=True "
          "evidence_unmodified=True",flush=True)
