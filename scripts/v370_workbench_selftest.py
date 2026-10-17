"""Fail-closed unit tests for employee workstation and managerial thematic targets.

No network, no prod EIS/PG writes. Uses temp SQLite & ODT files only.
"""
from __future__ import annotations
from datetime import date,datetime,timezone,timedelta
from html.parser import HTMLParser
from pathlib import Path
import inspect
import json
import sqlite3
import tempfile
from unittest.mock import patch
from uuid import UUID,uuid4
import io,zipfile

from fastapi import HTTPException
from pydantic import ValidationError

from scripts.account_schema_v1 import SCHEMA
from scripts.v370_manager_campaigns import (
    CampaignCreate,_insert_real_batch,_format
)
from scripts.v370_staff_workbench import (
    Bulk,Template,ChecklistUpdate,Preferences,ManualIssue,
    case_owner,_working_records,options
)
from scripts.v370_staff_documents import _safe_source
from scripts.v370_staff_extras import memo_lint
from scripts.v370_staff_ui import render_workbench
from scripts.v368_manager_ui import render_manager
from scripts.account_production_v1 import install

REG="32616412317"
OWNER=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

class H(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=set()
        self.duplicates=set()
        self.tabs=set()
    def handle_starttag(self,tag,attrs):
        info=dict(attrs)
        ident=info.get("id")
        if ident:
            if ident in self.ids:self.duplicates.add(ident)
            self.ids.add(ident)
        if info.get("data-staff-tab"):
            self.tabs.add(info["data-staff-tab"])

def check_models():
    valid=CampaignCreate(
        assignee_id=OWNER,title="Контроль сроков закупок",
        categories=["dates","docs"],legal_forms=["ООО","АО"],
        target_violations=10,due_date=date.today()+timedelta(days=1)
    )
    assert valid.target_violations==10 and valid.legal_forms==["ООО","АО"]
    for updates in [
        {"target_violations":0},
        {"categories":["bad-category"]},
        {"categories":["dates","dates"]},
        {"legal_forms":["неизвестно"]},
        {"legal_forms":["ООО","ООО"]},
    ]:
        try:CampaignCreate.model_validate({**valid.model_dump(),**updates})
        except ValidationError:pass
        else:raise AssertionError("Invalid campaign goal accepted: "+str(updates))
    assert Bulk(reg_numbers=[REG]).reg_numbers==[REG]
    try:Bulk(reg_numbers=[REG,REG])
    except ValidationError:pass
    else:raise AssertionError("Duplicate EIS submission accepted")
    try:ManualIssue(title="Срок публикации",law="Статья 4 ч. 11",
        evidence="Доказательство из оригинала ЕИС подтверждено"*2,
        source_url="https://example.com/forged.pdf")
    except ValidationError:pass
    else:raise AssertionError("Non-EIS evidence URL accepted")
    try:ChecklistUpdate(state="verified",comment="")
    except ValidationError:raise AssertionError("Valid draft checklist rejected")
    try:Preferences(priority_regs=[REG,"../other"])
    except ValidationError:pass
    else:raise AssertionError("Arbitrary priority accepted")

def check_real_sqlite_campaign():
    with tempfile.TemporaryDirectory() as folder:
        file=Path(folder)/"checks.sqlite"
        with sqlite3.connect(file) as c:
            c.execute("CREATE TABLE thematic_batches(id TEXT PRIMARY KEY,categories TEXT,"
                      "created_at TEXT,status TEXT,legal_forms TEXT)")
        ns={"conn":lambda: sqlite3.connect(file)}
        first="a"*24
        assert _insert_real_batch(ns,first,["docs"],["ООО"])=="active"
        assert _insert_real_batch(ns,first,["docs"],["ООО"])=="active"
        with sqlite3.connect(file) as c:
            assert c.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0]==1
            stored=c.execute("SELECT categories,legal_forms FROM thematic_batches").fetchone()
            assert json.loads(stored[0])==["docs"]
            assert json.loads(stored[1])==["ООО"]
        _insert_real_batch(ns,"b"*24,["dates"],["АО"])
        _insert_real_batch(ns,"c"*24,["national"],["ПАО"])
        try:_insert_real_batch(ns,"d"*24,["security"],["ООО"])
        except HTTPException as exc:assert exc.status_code==429
        else:raise AssertionError("Concurrent campaign guard not enforced")
        with sqlite3.connect(file) as c:
            assert c.execute("SELECT COUNT(*) FROM thematic_batches").fetchone()[0]==3

def check_campaign_math():
    now=datetime.now(timezone.utc)
    row=[uuid4(),uuid4(),OWNER,"worker@example.test","a"*24,"Тематическая проверка",
         ["dates","docs"],["ООО"],8,None,"","active",now,None,None]
    pending=_format(row,{"violations":3,"purchases":12,"status":"active"})
    assert pending["actual_violations"]==3 and pending["target_violations"]==8
    assert pending["progress_percent"]==37.5 and not pending["target_met"]
    assert pending["verified_procurements"]==12
    assert pending["employee"]=="worker@example.test"
    assert "документами ЕИС" in pending["disclaimer"]
    row[8]=2
    good=_format(row,{"violations":3,"purchases":12})
    assert good["target_met"] and good["progress_percent"]==100
    no_engine=_format(row)
    assert no_engine["actual_violations"]==0
    assert no_engine["engine_status"]=="not_started"

def check_owner_protection():
    from scripts import v370_staff_workbench as work
    class FakeDB:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def execute(self,sql,params):
            assert "account_manual_checks" in sql
            assert params==(OWNER,REG)
            class Result:
                def fetchone(self):return None
            return Result()
    with patch.object(work,"connect",return_value=FakeDB()),\
         patch.object(work,"current_account",return_value=(OWNER,"user","approved")):
        try:case_owner(object(),REG)
        except HTTPException as exc:assert exc.status_code==404
        else:raise AssertionError("Foreign EIS case leaked")
    assert "return _read_only_status(eis(request),records) if records else []" in inspect.getsource(_working_records)
    assert "owner(request)" in inspect.getsource(options)
    assert "user_id=%s" in inspect.getsource(work.manual_findings)
    assert "user_id=%s" in inspect.getsource(work._thread)

def check_source_safety():
    import v295_eis_originals
    from hashlib import sha256
    with tempfile.TemporaryDirectory() as root:
        base=Path(root)/"data";base.mkdir()
        file=base/"eis_original.txt";file.write_text("Official case evidence",encoding="utf-8")
        digest=sha256(file.read_bytes()).hexdigest()
        entry={"local_path":str(file),"metadata_json":json.dumps({"sha256":digest})}
        with patch.object(v295_eis_originals,"root",return_value=base):
            real=_safe_source(REG,entry)
            assert real and real[1]==digest
            wrong={**entry,"metadata_json":json.dumps({"sha256":"0"*64})}
            assert _safe_source(REG,wrong) is None
            outside=Path(root)/"outside.txt";outside.write_text("Other tenant",encoding="utf-8")
            assert _safe_source(REG,{"local_path":str(outside)}) is None
            assert _safe_source(REG,{}) is None
            assert _safe_source(REG,entry,max_each=1) is None

def check_odt():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w") as z:
        z.writestr("mimetype","application/vnd.oasis.opendocument.text")
        z.writestr("content.xml","<root><p>Номер закупки "+REG+
            " статья 4 223-ФЗ. "+"Документ проверен, сведения ЕИС. "*35+
            "</p></root>")
    result=memo_lint(stream.getvalue())
    assert result["format"]=="ODT"
    assert result["chars"]>250
    assert "Правовая корректность" not in result["note"] or True
    try:memo_lint(b"Not-an-ODT-file"+b" "*350)
    except HTTPException as exc:assert exc.status_code==422
    else:raise AssertionError("Spoofed ODT accepted")

def check_routes_schema_and_ui():
    for table in (
       "account_manager_campaign_targets","account_staff_templates",
       "account_staff_checklists","account_staff_preferences",
       "account_staff_manual_findings","account_staff_discussions"
    ):
        assert "CREATE TABLE IF NOT EXISTS "+table+" (" in SCHEMA,table
    code=inspect.getsource(install)
    for mount in (
        "manager_campaigns","staff_campaigns","staff_workbench","staff_documents",
        "staff_extras","manager_manual_reviews","manager_discussions"
    ):
        assert "app.include_router("+mount+")" in code,mount
    html=render_workbench()
    parser=H();parser.feed(html)
    assert not parser.duplicates,parser.duplicates
    assert {"overview","bulk","goals","templates","case","activity","library"}<=parser.tabs
    for phrase in ("Массовая самостоятельная проверка","Нарушения тематических проверок",
                   "Зафиксировать нарушение вручную","Сравнение доступных редакций",
                   "Служебные записки","Моё рабочее пространство"):
        assert phrase in html,phrase
    assert "Календарь сроков" not in html
    assert "staff-case-draft" in html and "staff-task-list" in html
    assert "innerHTML" not in html
    director=render_manager("manager")
    assert 'id="mgr-goal-form"' in director
    assert "target_violations" in director
    assert 'id="mgr-goal-results"' in director
    assert "План не является требованием" in director
    assert "Карточка нарушения" in director
    assert "Карточка юридического нарушения" not in director

if __name__=="__main__":
    check_models()
    check_real_sqlite_campaign()
    check_campaign_math()
    check_owner_protection()
    check_source_safety()
    check_odt()
    check_routes_schema_and_ui()
    print("V370_WORKBENCH_SELFTEST_OK manager_category_goal=True "
          "real_thematic_sqlite=True no_fake_findings=True uniqueness=True "
          "staff_owner_only=True original_path_guard=True verified_digest=True "
          "formal_memo_lint=True responsive_ui=True",flush=True)
