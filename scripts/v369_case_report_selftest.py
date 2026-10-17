"""Non-destructive dossier + report regression on temporary SQLite EIS fixtures."""
from __future__ import annotations

from datetime import date,timedelta
from html.parser import HTMLParser
import inspect
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from scripts.v369_case_ui import render_case
from scripts.v369_evidence_case import _case_snapshot,_check_reg,_owner,add_case_note,manager_case_notes
from scripts.v369_manager_report import _dates,_safe_cell,_period_report
from scripts.v368_manager_evidence import _legal_items
from scripts.account_schema_v1 import SCHEMA
from scripts.account_production_v1 import install

REG="32616412317"
OWNER=UUID("12345678-1234-4123-8123-123456789abc")
DOC=("Изменение извещения принято 01.10.2026 и размещено "
     "в единой информационной системе 06.10.2026. ")*5


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=set();self.duplicate_ids=set();self.tabs=set()
    def handle_starttag(self,name,attrs):
        a=dict(attrs)
        id_=a.get("id")
        if id_:
            if id_ in self.ids:self.duplicate_ids.add(id_)
            self.ids.add(id_)
        if a.get("data-case-tab"):self.tabs.add(a["data-case-tab"])


def fixture():
    directory=tempfile.TemporaryDirectory(prefix="case-selftest-")
    path=Path(directory.name)/"eis.sqlite"
    def open_db():
        c=sqlite3.connect(path)
        c.row_factory=sqlite3.Row
        return c
    with open_db() as c:
        c.executescript("""
            CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,
                customer_inn TEXT,method TEXT,price TEXT,price_num REAL,published_at TEXT,
                submission_start TEXT,submission_end TEXT,url TEXT);
            CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,kind TEXT,title TEXT,
                law TEXT,evidence TEXT,document TEXT,source_url TEXT,code TEXT,law_url TEXT);
            CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,item TEXT,
                law TEXT,evidence TEXT,document TEXT,source_url TEXT,field_path TEXT,law_url TEXT);
            CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,doc_type TEXT,
                status TEXT,url TEXT,text_chars INTEGER,published_at TEXT);
            CREATE TABLE v332_audit_queue(reg TEXT,state TEXT,documents_read INTEGER,
                documents_unreadable INTEGER,updated_at TEXT);
        """)
        c.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (REG,"Поставка офисной техники","Заказчик","7707083893",
             "Конкурентная закупка","1500000",1500000,"2026-09-29",
             "2026-09-30","2026-10-22",
             "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html?regNumber="+REG))
        c.execute("INSERT INTO findings VALUES(NULL,?,?,?,?,?,?,?,?,?)",
            (REG,"violation","Несвоевременное размещение изменений",
             "223-ФЗ ст. 4 ч. 11",DOC,"Изменения.pdf",
             "https://zakupki.gov.ru/doc/1","223-4-11","https://www.consultant.ru/document/cons_doc_LAW_116964/"))
        c.execute("INSERT INTO checks VALUES(NULL,?,?,?,?,?,?,?,?,?)",
            (REG,"violation","Несвоевременное размещение изменений",
             "223-ФЗ ст. 4 ч. 11",DOC,"Изменения.pdf",
             "https://zakupki.gov.ru/doc/1","223-4-11",""))
        c.execute("INSERT INTO checks VALUES(NULL,?,?,?,?,?,?,?,?,?)",
            (REG,"check","Сверить окончание подачи заявок",
             "223-ФЗ ст. 4 ч. 9 п. 7","Карточка 22.10, документация 21.10",
             "Извещение.pdf","https://zakupki.gov.ru/doc/2",
             "v339-date-submission_end",""))
        c.execute("INSERT INTO docs VALUES(NULL,?,?,?,?,?,?,?)",
            (REG,"Изменения.pdf","Изменения","checked","https://zakupki.gov.ru/doc/1",
             1500,"2026-10-06"))
        c.execute("INSERT INTO v332_audit_queue VALUES(?,?,?,?,?)",
            (REG,"completed",1,0,"2026-10-17T12:00:00"))
    return directory,{"conn":open_db}


def check_case():
    temp,ns=fixture()
    try:
        results=_case_snapshot(ns,REG)
        assert results["findings_count"]==1,results
        assert results["original_findings_records"]==2
        assert results["duplicates_grouped"]==1
        assert len(results["comparisons"])==1
        assert results["comparisons"][0]["code"]=="v339-date-submission_end"
        assert results["review_items"]==[]
        assert results["audit"]["state"]=="completed"
        assert results["documents"][0]["text_chars"]==1500
        assert results["findings"][0]["consultant_references"]
        assert len(results["findings"][0]["finding_key"])==64
        matching=_legal_items(ns,REG)
        assert matching[0]["finding_key"]==results["findings"][0]["finding_key"]
        assert any(x["title"]=="Окончание подачи заявок" for x in results["timeline"])
        with ns["conn"]() as con:
            before=con.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        _case_snapshot(ns,REG)
        with ns["conn"]() as con:
            assert con.execute("SELECT COUNT(*) FROM findings").fetchone()[0]==before
        try:_check_reg("../etc/passwd")
        except HTTPException as exc:assert exc.status_code==422
        else:raise AssertionError("Unsafe reg accepted")
    finally:temp.cleanup()


def check_guard():
    from scripts import v369_evidence_case as case
    class FakeDB:
        def execute(self,sql,params=()):
            assert "account_manual_checks" in sql
            assert params==(OWNER,REG)
            class Result:
                def fetchone(self):return None
            return Result()
        def __enter__(self):return self
        def __exit__(self,*a):return False
    with patch.object(case,"connect",return_value=FakeDB()),\
         patch.object(case,"current_account",return_value=(OWNER,"user","approved")):
        try:_owner(object(),REG)
        except HTTPException as exc:assert exc.status_code==404
        else:raise AssertionError("Foreign case became visible")
    assert "user_id=%s" in inspect.getsource(case._owned_notes)
    assert "_member(db,actor,role,worker)" in inspect.getsource(manager_case_notes)
    assert "require_origin(request)" in inspect.getsource(add_case_note)
    assert "account_case_notes" in SCHEMA
    assert "account_manual_checks" in inspect.getsource(_owner)
    assert "app.include_router(account_cases)" in inspect.getsource(install)
    assert "app.include_router(manager_report)" in inspect.getsource(install)


def check_pages():
    h=render_case(REG)
    parser=Collector();parser.feed(h)
    assert not parser.duplicate_ids,parser.duplicate_ids
    assert {"findings","compare","review","dates","docs","notes"}==parser.tabs
    for label in ("Карточка нарушения","Требует проверки","Сопоставление",
                  "Оригиналы документов ЕИС","Сроки и изменения"):
        assert label in h,label
    assert "Карточка юридического нарушения" not in h
    assert "innerHTML" not in h
    from scripts.account_site_ui_v1 import site_header
    assert "/me/cases/" in site_header().decode("utf-8")
    from scripts.account_pages_v1 import HOME
    assert "/me/cases/" in HOME


def check_reports():
    start,end=_dates(None,None)
    assert end==date.today()
    assert (end-start).days==29
    try:_dates("2026-11-01","2026-01-01")
    except HTTPException as exc:assert exc.status_code==422
    else:raise AssertionError("Reverse date range allowed")
    try:_dates("2024-01-01","2026-10-17")
    except HTTPException as exc:assert exc.status_code==422
    else:raise AssertionError("Unbounded report allowed")
    assert _safe_cell("=2+2").startswith("'")
    assert _safe_cell("\t=HYPERLINK(").startswith("'")
    assert _safe_cell("Сотрудник № 1")=="Сотрудник № 1"
    source=inspect.getsource(_period_report)
    assert "team.manager_id=%s" in source
    assert "team_checks" in source
    from scripts.v368_manager_ui import render_manager
    ui=render_manager("manager")
    assert 'data-tab="reports"' in ui
    assert "/manager/report.csv" in ui
    assert "Кабинет руководителя" in ui
    assert "Карточка нарушения" in ui


if __name__=="__main__":
    check_case()
    check_guard()
    check_pages()
    check_reports()
    print("V369_CASE_REPORT_SELFTEST_OK violation_card=True "
          "cross_document_mismatch_review_only=True one_duplicate_not_counted=True "
          "owner_guard=True manager_guard=True verified_eis_dates=True "
          "append_only_comments=True manager_report_scoped=True csv_safe=True",flush=True)
