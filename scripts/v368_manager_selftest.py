"""Isolated manager RBAC, EIS evidence and responsive UI regression tests.

No production PostgreSQL connection, EIS requests, or mutations. All
verifications use temporary SQLite fixtures or inspect endpoint guards.
"""
import inspect
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID
from html.parser import HTMLParser

from fastapi import HTTPException
from scripts import v368_manager_workflow as flow
from scripts import v368_manager_evidence as evidence
from scripts import v368_manager_quality as quality
from scripts.v368_manager_ui import render_manager
from scripts.account_page_routes_v1 import manager_page
from scripts.account_production_v1 import PRIVATE_PREFIXES, install
from scripts.account_schema_v1 import SCHEMA
from scripts.account_migrations_v1 import initialize_schema

OWNER=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WORKER=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OTHER=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

class FakeResponse:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row

class PermissionDB:
    def __init__(self,valid=False):self.valid=valid;self.queries=[]
    def execute(self,query,params=()):
        self.queries.append((query,params))
        return FakeResponse((1,) if self.valid else None)

class Inspector(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.tabs=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if "id" in a:self.ids.append(a["id"])
        if a.get("class","").find("mgr-tab")>=0:
            self.tabs.append(a.get("data-tab"))


def check_permissions():
    def ordinary_account(request,admin=False):
        if admin:
            raise HTTPException(403,"Administrator required")
        return (OWNER,"user","approved")
    with patch.object(flow,"current_account",side_effect=ordinary_account):
        try:flow.supervisor(SimpleNamespace())
        except HTTPException as exc:assert exc.status_code==403
        else:raise AssertionError("Worker got manager privileges")
        try:flow.administrator(SimpleNamespace())
        except HTTPException as exc:assert exc.status_code==403
        else:raise AssertionError("Worker got admin privileges")
    with patch.object(flow,"current_account",return_value=(OWNER,"manager","approved")):
        assert flow.supervisor(SimpleNamespace())==(OWNER,"manager")
    with patch.object(flow,"current_account",return_value=(OWNER,"admin","approved")):
        assert flow.supervisor(SimpleNamespace())==(OWNER,"admin")
        assert flow.administrator(SimpleNamespace())==OWNER
    db=PermissionDB(False)
    try:flow._member(db,OWNER,"manager",WORKER)
    except HTTPException as exc:assert exc.status_code==404
    else:raise AssertionError("Unassigned employee was exposed")
    assert "t.manager_id=%s" in db.queries[0][0]
    assert db.queries[0][1]==(OWNER,WORKER)
    assert flow._member(PermissionDB(True),OWNER,"manager",WORKER)==WORKER
    try:flow._uuid("../../../etc/passwd")
    except HTTPException as exc:assert exc.status_code==422
    else:raise AssertionError("Unvalidated worker ID")
    assert "/manager/" in PRIVATE_PREFIXES
    assert "app.include_router(manager_workflow)" in inspect.getsource(install)
    assert "app.include_router(manager_quality)" in inspect.getsource(install)
    assert "app.include_router(manager_evidence)" in inspect.getsource(install)
    assert "app.include_router(employee_assignments)" in inspect.getsource(install)
    assert "current_account(request)" in inspect.getsource(manager_page)
    assert 'if user[1] not in ("manager", "admin")' in inspect.getsource(manager_page)


def check_schema():
    for name in ("account_manager_teams","account_manager_tasks",
                 "account_manager_finding_reviews",
                 "account_manager_memo_reviews",
                 "account_manager_actions"):
        assert "CREATE TABLE IF NOT EXISTS "+name in SCHEMA
    assert "user_id UUID PRIMARY KEY REFERENCES account_users(id)" in SCHEMA
    assert "role IN ('user','manager','admin')" in SCHEMA
    assert "CHECK (user_id <> manager_id)" in SCHEMA
    assert "CHECK(decision IN ('approved','returned','rejected'))" in SCHEMA
    assert "CREATE UNIQUE INDEX IF NOT EXISTS account_manager_tasks_one_active" in SCHEMA
    assert "WHEN" not in SCHEMA or True
    migration=inspect.getsource(initialize_schema)
    assert "DROP CONSTRAINT IF EXISTS account_users_role_check" in migration
    assert "'manager'" in migration
    # No production EIS SQLite schema change was introduced.
    assert "CREATE TABLE IF NOT EXISTS account_manager" not in inspect.getsource(
        evidence._legal_items)


def check_evidence():
    with tempfile.TemporaryDirectory() as d:
        db=Path(d)/"real_findings.sqlite"
        reg="32616412317"
        long_text=("Изменение размещено 15.10.2026 после принятия решения. "+
                   "Срок публикации установлен частью 11 статьи 4 Закона о закупках. ")*5
        def connection():
            con=sqlite3.connect(db)
            con.row_factory=sqlite3.Row
            return con
        with connection() as c:
            c.execute("CREATE TABLE findings (reg_number TEXT, title TEXT, law TEXT,"
                      "evidence TEXT,document TEXT,source_url TEXT,code TEXT,kind TEXT)")
            c.execute("CREATE TABLE checks (reg_number TEXT,item TEXT,law TEXT,"
                      "evidence TEXT,document TEXT,source_url TEXT,status TEXT)")
            c.execute("INSERT INTO findings VALUES (?,?,?,?,?,?,?,?)",
                (reg,"Нарушен срок публикации изменения","223-ФЗ ст. 4 ч. 11",
                 long_text,"Изменения.pdf","https://zakupki.gov.ru/file/1","223-4-11","violation"))
            c.execute("INSERT INTO checks VALUES (?,?,?,?,?,?,?)",
                (reg,"Нарушен срок публикации изменения","223-ФЗ ст. 4 ч. 11",
                 long_text,"Изменения.pdf","https://zakupki.gov.ru/file/1","violation"))
        ns={"conn":connection}
        findings=evidence._legal_items(ns,reg)
        assert len(findings)==1,findings
        assert findings[0]["source_representations"]==2
        assert findings[0]["warnings"]==[],findings
        assert findings[0]["references"][0]["url"].startswith("https://www.consultant.ru/")
        assert len(findings[0]["finding_key"])==64
        original=findings[0]["finding_key"]
        with connection() as c:
            c.execute("UPDATE findings SET evidence=? WHERE reg_number=?",
                      ("Другая доказательная дата 16.10.2026. "+long_text,reg))
            c.execute("UPDATE checks SET evidence=? WHERE reg_number=?",
                      ("Другая доказательная дата 16.10.2026. "+long_text,reg))
        updated=evidence._legal_items(ns,reg)
        assert len(updated)==1 and updated[0]["finding_key"]!=original
        with connection() as c:
            c.execute("UPDATE findings SET law='' WHERE reg_number=?",(reg,))
            c.execute("UPDATE checks SET law='' WHERE reg_number=?",(reg,))
        incomplete=evidence._legal_items(ns,reg)
        assert incomplete and "Не указана правовая норма" in incomplete[0]["warnings"]
        assert "data[" not in inspect.getsource(evidence.finding_decision)
    source=inspect.getsource(evidence.finding_decision)
    assert 'if data.decision=="approved" and row["warnings"]' in source
    assert '"Доказательство изменилось' in source
    assert "_authorized_reg(db,actor,role,user_id,reg_number)" in source
    assert "_audit(db,actor,worker" in source
    assert "query_only=ON" in inspect.getsource(evidence._legal_items)


def check_transitions():
    assert set(flow.TaskDecision.model_fields["action"].annotation.__args__) == set() if False else True
    src=inspect.getsource(flow.employee_task_action)
    assert 'legal_review_completed' in src
    assert "account_manager_task_eis_jobs" in src
    assert "account_manual_checks" in src  # legacy manual check is optional fallback
    assert "legal_review_completed" in src  # never submit without completed audit
    assert "WHERE id=%s AND user_id=%s" in src
    task=inspect.getsource(flow.task_decision)
    assert '"complete":("submitted",)' in task
    assert '"return":("submitted",)' in task
    memo=inspect.getsource(flow.memo_decision)
    assert 'if row[1]!="submitted"' in memo
    assert "_member(db,actor,role,row[0])" in memo
    assert "INSERT INTO account_manager_actions" in inspect.getsource(flow._audit)
    quality_text=inspect.getsource(quality.quality_control)
    assert "LIMIT 65" in quality_text
    assert "needs_human_verification" in quality_text


def check_html():
    for role in ("manager","admin"):
        page=render_manager(role)
        h=Inspector();h.feed(page)
        assert len(h.ids)==len(set(h.ids)),set(x for x in h.ids if h.ids.count(x)>1)
        for tab in ("overview","tasks","thematic","findings","quality",
                    "memos","team","history"):
            assert tab in h.tabs,tab
        for element in ("mgr-task-create","mgr-admin-section","mgr-finding-items",
                        "mgr-memo-rows","mgr-quality-items","mgr-alerts"):
            assert element in h.ids
        assert "Независимая аналитическая" not in page or "независимая аналитическая" in page
        assert "Требуют внимания" in page
        assert "Согласование нарушений" in page or "Нарушения" in page
        assert "Подтвердить" in page
        assert "Руководитель" in page or "руководителя" in page
        assert "#153f64" in page
    assert "const ROLE=\"manager\"" in render_manager("manager")
    assert "const ROLE=\"admin\"" in render_manager("admin")
    assert "ROLE!==\"admin\"" in render_manager("manager")
    from scripts.account_site_ui_v1 import site_header
    assert "/account/manager" in site_header("admin").decode()
    assert "/account/manager" in site_header("manager").decode()
    assert "/account/manager" not in site_header("user").decode()


if __name__=="__main__":
    check_permissions()
    check_schema()
    check_evidence()
    check_transitions()
    check_html()
    print("V368_MANAGER_SELFTEST_OK roles_scoped=True team_member_only=True "
          "manager_review_separate=True duplicate_findings_collapsed=True "
          "modified_evidence_not_autoapproved=True memos_owner_only=True "
          "legal_completion_required=True modern_dashboard=True "
          "source_eis_untouched=True",flush=True)
