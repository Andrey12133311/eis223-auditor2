"""Safe regression suite for actual owner-linked self-initiated 223-FZ checks.

Runs against a temporary SQLite file and stubbed PostgreSQL connection.
Never touches Railway volumes, original EIS files, user sessions or reports.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import inspect
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from scripts import v366_account_manual as manual
from scripts.account_pages_v1 import HOME
from scripts.account_site_ui_v1 import site_header
from scripts.account_schema_v1 import SCHEMA
from scripts.account_production_v1 import install
from v352_dashboard_truth import load_counts


def test_markup():
    shell=site_header().decode("utf-8")
    assert "Самостоятельные проверки" in shell
    assert "Сохранённые закупки" not in shell
    assert "eis223-my-procurements" in shell
    assert "get('/me/manual-checks')" in shell
    assert "fetch('/me/manual-checks/'" in shell
    assert "get('/me/procurements')" not in shell
    assert "fetch('/me/procurements/'" not in shell
    assert "'Самостоятельные',sumResponse.status==='fulfilled'?" in shell
    assert "summary.manual_checks" in shell
    assert "Promise.allSettled" in shell
    assert 'href="/account/home"' in shell
    # Keep the workbench in the employee navigation, not duplicated below
    # the self-initiated procurement list in the personal cabinet.
    assert '<a href="/account/workbench">Рабочее место сотрудника</a>' in shell
    assert 'href="/account/workbench">Все нарушения и тематические проверки →</a>' in shell
    manual_actions = shell.split('id="eis223-my-procurements"', 1)[1].split(
        '<form id="eis223-add-procurement">', 1)[0]
    assert 'href="/account/home"' in manual_actions
    assert '/account/workbench' not in manual_actions
    assert 'eis223-toggle-cabinet' in shell
    assert "Проверки, документы и результаты" not in shell  # no invented metrics
    assert "Мои самостоятельные проверки" in HOME
    assert "fetch('/me/manual-checks'" in HOME
    assert "method:'POST'" in HOME and "method:'DELETE'" in HOME
    assert "fetch('/me/procurements'" not in HOME
    assert "Добавить закупку" not in HOME
    assert "account_manual_checks" in SCHEMA
    assert "PRIMARY KEY(user_id, reg_number)" in SCHEMA
    assert "FROM account_manual_checks" in manual.MANUAL_OWNED
    assert "WHERE user_id=%s" in manual.MANUAL_OWNED
    assert 'app.include_router(real_manual_checks)' in inspect.getsource(install)


def test_no_false_manual_link():
    assert manual.valid_reg("32616412317")
    for value in ("", "0" * 11, "3261641231", "32616412317;DROP TABLE"):
        assert not manual.valid_reg(value)
    class Db:
        def __init__(self):
            self.calls=[]
        def execute(self,query,params):
            self.calls.append((query,params))
    class Context:
        def __init__(self,db):
            self.db=db
        def __enter__(self):
            return self.db
        def __exit__(self,*args):
            return False
    db=Db()
    with patch.object(manual,"connect",return_value=Context(db)):
        try:
            manual.link_run("owner-a","32616412317","BAD")
        except ValueError:
            pass
        else:
            raise AssertionError("Allowed fake EIS queue id")
        assert db.calls==[]
        manual.link_run("owner-a","32616412317","a"*24)
        manual.link_run("owner-b","32616412317","b"*24)
    assert db.calls[0][1]==("owner-a","32616412317","a"*24)
    assert db.calls[1][1]==("owner-b","32616412317","b"*24)
    assert "ON CONFLICT(user_id,reg_number)" in db.calls[0][0]
    assert "VALUES (%s,%s,%s)" in db.calls[0][0]


def test_unique_evidence_and_read_only_state():
    with TemporaryDirectory(prefix="eis366-") as root:
        path=Path(root)/"manual.sqlite"
        def conn():
            c=sqlite3.connect(path)
            c.row_factory=sqlite3.Row
            return c
        with conn() as db:
            db.executescript("""
            CREATE TABLE v301_search_jobs(
               id TEXT PRIMARY KEY, kind TEXT, target TEXT,
               status TEXT, detail TEXT, created_at TEXT
            );
            CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,
               documents_found INTEGER, documents_checked INTEGER);
            CREATE TABLE findings(reg_number TEXT,title TEXT,law TEXT,evidence TEXT,
               document TEXT,source_url TEXT,code TEXT,kind TEXT);
            CREATE TABLE checks(reg_number TEXT,item TEXT,law TEXT,evidence TEXT,
               document TEXT,source_url TEXT,status TEXT);
            """)
            db.execute("INSERT INTO v301_search_jobs VALUES(?,?,?,?,?,?)",
                       ("a"*24,"reg","32616412317","leased","",datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT INTO purchases VALUES(?,?,?)",("32616412317",12,4))
            proof="В документации срок оплаты установлен 25 рабочих дней после приёмки. "*4
            db.execute("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)",
                       ("32616412317","Нарушен срок оплаты МСП","ПП РФ № 1352 п. 28",
                        proof,"doc.pdf","https://zakupki.gov.ru/doc","1352-28","violation"))
            db.execute("INSERT INTO checks VALUES(?,?,?,?,?,?,?)",
                       ("32616412317","Срок оплаты МСП превышен","Положение № 1352 п. 28",
                        proof,"doc2.pdf","https://zakupki.gov.ru/doc2","violation"))
        now=datetime.now(timezone.utc)
        own=[{"reg_number":"32616412317","job_id":"a"*24,
              "last_started_at":now}]
        before=path.stat().st_size
        rows=manual._read_only_status({"conn":conn},own)
        after=path.stat().st_size
        assert before==after
        assert len(rows)==1,rows
        assert rows[0]["job_status"]=="leased"
        assert rows[0]["documents_found"]==12
        assert rows[0]["documents_checked"]==4
        assert rows[0]["violations"]==1  # dedup never triples evidence
        assert rows[0]["card_available"] is True
        with conn() as db:
            assert db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]==1
            assert db.execute("SELECT COUNT(*) FROM checks").fetchone()[0]==1


def test_ownership_delete_isolated():
    sql=inspect.getsource(manual.remove_manual_check)
    assert "DELETE FROM account_manual_checks WHERE user_id=%s AND reg_number=%s" in sql
    assert "DELETE FROM findings" not in sql
    assert "DELETE FROM purchases" not in sql
    assert "DELETE FROM v301_search_jobs" not in sql
    assert "account_procurements" not in sql
    assert 'getattr(request.app.state,"eis223_manual_ns",None)' in inspect.getsource(manual.start_manual_check)
    assert "_start_linked_recheck,ns" in inspect.getsource(manual.start_manual_check)
    assert '_enqueue(ns,"reg",reg_number' in inspect.getsource(manual._start_linked_recheck)


if __name__=="__main__":
    test_markup()
    test_no_false_manual_link()
    test_unique_evidence_and_read_only_state()
    test_ownership_delete_isolated()
    print("V366_PERSONAL_MANUAL_SELFTEST_OK real_job_only=True "
          "owner_scoped=True two_accounts_isolated=True "
          "documents_unmodified=True unique_violations=True "
          "actual_manual_screen=True no_legacy_bookmarks=True",flush=True)
