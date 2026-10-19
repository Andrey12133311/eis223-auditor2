"""Local-only owner-route tests with real session/ownership SQL and SQLite queues."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from scripts import account_api_v1 as accounts
from scripts import v366_account_manual as manual
from scripts import v370_staff_extras as extras
from scripts import v370_staff_workbench as workbench
from scripts.account_schema_v1 import SCHEMA
from scripts.account_security_v1 import digest_session_token
from scripts.account_session_policy_v1 import SESSION_COOKIE
import scoped_legal_recheck as scoped
import v301_search_jobs as downloads
import v319_manual_inputs as inputs
import v332_all_procurements_audit as worker

A="32616233661"
B="32616324151"
NEW="32616287360"


class IdentityDB:
    """Exercise real owner/session queries, translating PostgreSQL syntax only."""
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=10)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.create_function("regexp",2,lambda pattern,value:bool(re.fullmatch(pattern,value or "")))
    def __enter__(self):
        self.db.__enter__()
        return self
    def __exit__(self,*exc):
        try:return self.db.__exit__(*exc)
        finally:self.db.close()
    def execute(self,sql,args=()):
        return self.db.execute(sql.replace("%s","?"),args)


class OwnerLegalRecheckTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="owner-scoped-recheck-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.path=self.root/"eis223.db"
        self.identity=self.root/"identity.db"
        self.ns={"conn":lambda:sqlite3.connect(self.path,timeout=10)}
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,documents_found INTEGER,
                    documents_checked INTEGER,customer TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT);
                CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,next_retry_at REAL,
                    updated_at TEXT,documents_read INTEGER,last_doc_id INTEGER,audit_revision TEXT,
                    last_source_version TEXT,last_result_token TEXT,last_counted_token TEXT,
                    retry_count INTEGER,error TEXT,PRIMARY KEY(campaign,reg));
                CREATE TABLE findings(reg_number TEXT,evidence BLOB);
                CREATE TABLE checks(reg_number TEXT,evidence BLOB);
                CREATE TABLE manager_signoffs(reg_number TEXT,decision BLOB);
                CREATE TABLE v332_audit_run(campaign TEXT PRIMARY KEY,history BLOB);
                CREATE TABLE v301_search_jobs(id TEXT PRIMARY KEY,kind TEXT,target TEXT,page INTEGER,
                    status TEXT,priority INTEGER,created_at TEXT,updated_at TEXT,parent_id TEXT,
                    detail TEXT DEFAULT '',not_before INTEGER DEFAULT 0,lease_until INTEGER DEFAULT 0);
            """)
            inputs.ensure_schema(db)
            for index,reg in enumerate((A,B),1):
                file=self.root/(reg+".pdf")
                file.write_bytes(b"preserved synthetic original\x00\xff")
                db.execute("INSERT INTO purchases VALUES(?,3,2,'ООО Заказчик')",(reg,))
                db.execute("INSERT INTO docs VALUES(?,?,?,'checked')",(index,reg,str(file)))
                db.execute("INSERT INTO v332_audit_queue VALUES(?,?,'completed',999,'old',2,?,?,'same-source',"
                           "'result-token','counted-token',7,'retained error')",
                           (scoped.CAMPAIGN,reg,index,worker.RULE_REVISION))
                for table in ("findings","checks","manager_signoffs"):
                    db.execute("INSERT INTO "+table+" VALUES(?,?)",(reg,b"history\x00\xff"))
            db.execute("INSERT INTO v332_audit_run VALUES(?,?)",(scoped.CAMPAIGN,b"run-history\x00"))
        with IdentityDB(self.identity) as db:
            for table in ("account_users","account_sessions","account_manual_checks"):
                ddl=re.search(r"CREATE TABLE IF NOT EXISTS "+table+r"\s*\(.*?\n\);",SCHEMA,re.S).group()
                db.execute(ddl.replace("TIMESTAMPTZ","TIMESTAMP").replace("reg_number ~","reg_number REGEXP"))
            for uid,token,reg in (("owner-a","s"*48,A),("owner-b","t"*48,B)):
                db.execute("INSERT INTO account_users(id,email,password_hash,status) VALUES(%s,%s,'unused','approved')",(uid,uid+"@test.example"))
                db.execute("INSERT INTO account_sessions(id,user_id,token_digest,expires_at) "
                           "VALUES(%s,%s,%s,datetime('now','+1 day'))",(uid,uid,digest_session_token(token)))
                db.execute("INSERT INTO account_manual_checks(user_id,reg_number,job_id) VALUES(%s,%s,%s)",(uid,reg,"a"*24))
            for table in ("account_manager_actions","account_manager_finding_reviews","account_manager_memo_reviews"):
                db.execute("CREATE TABLE "+table+"(owner TEXT,reg TEXT,history BLOB)")
                db.execute("INSERT INTO "+table+" VALUES(%s,%s,%s)",("owner-a",A,b"approved\x00"))
        self.app=FastAPI()
        self.app.state.eis223_manual_ns=self.ns
        self.app.include_router(manual.router)
        self.app.include_router(extras.router)
        self.client=TestClient(self.app)
        self.addCleanup(self.client.close)
        for module in (accounts,manual,workbench):
            self.enterContext(patch.object(module,"connect",side_effect=lambda:IdentityDB(self.identity)))
        self.enterContext(patch.dict(os.environ,{"EIS223_PUBLIC_ORIGIN":"https://app.test"}))

    def post(self,reg=A,*,route="manual",token="s"*48,origin="https://app.test",**kwargs):
        path="/me/manual-checks/"+reg if route=="manual" else "/me/workbench/"+reg+"/recheck"
        headers={"origin":origin,"cookie":SESSION_COOKIE+"="+token}
        return self.client.post(path,headers=headers,**kwargs)

    def snapshot(self,path=None):
        with sqlite3.connect(path or self.path) as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {t:db.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall() for t in tables}

    def queue(self,reg=A):
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM v332_audit_queue WHERE campaign=? AND reg=?",(scoped.CAMPAIGN,reg)).fetchone()
            return dict(row) if row else None

    def jobs(self):
        return self.snapshot()["v301_search_jobs"]

    def test_both_owner_routes_queue_exact_reg_without_rule_or_evidence_changes(self):
        for route in ("manual","workbench"):
            with self.subTest(route=route):
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE v332_audit_queue SET state='completed',next_retry_at=999 WHERE reg=?",(A,))
                before=self.snapshot();account_before=self.snapshot(self.identity)
                queue=self.queue();original=(self.root/(A+".pdf")).read_bytes()
                result=self.post(route=route)
                self.assertEqual(result.status_code,202,result.text)
                self.assertEqual(result.json()["legal_recheck"],{
                    "action":"queued","reason":"explicit_scoped_recheck","state":"pending"})
                self.assertEqual(self.queue(),{**queue,"state":"pending","next_retry_at":0})
                after=self.snapshot()
                for table in ("purchases","docs","findings","checks","manager_signoffs","v332_audit_run"):
                    self.assertEqual(after[table],before[table],table)
                self.assertEqual(self.queue(B),dict(zip(queue.keys(),before["v332_audit_queue"][1])))
                for table,rows in account_before.items():
                    if table!="account_manual_checks":self.assertEqual(self.snapshot(self.identity)[table],rows,table)
                self.assertEqual((self.root/(A+".pdf")).read_bytes(),original)
                self.assertNotIn(str(self.root),result.text)
                self.assertEqual(self.queue()["audit_revision"],worker.RULE_REVISION)
                own=self.snapshot(self.identity)["account_manual_checks"]
                self.assertEqual(own[0][0:3],("owner-a",A,result.json()["job_id"]))
                self.assertEqual(own[1],account_before["account_manual_checks"][1])

    def test_pending_and_running_are_unchanged_and_repeated_starts_reuse_work(self):
        first=self.post()
        for state in ("pending","running"):
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE v332_audit_queue SET state=?,next_retry_at=123 WHERE reg=?",(state,A))
            before=self.queue()
            for route in ("manual","workbench"):
                response=self.post(route=route)
                self.assertEqual(response.status_code,202,response.text)
                self.assertEqual(response.json()["job_id"],first.json()["job_id"])
                self.assertEqual(response.json()["legal_recheck"],{
                    "action":"skipped","reason":"already_"+state,"state":state})
                self.assertEqual(self.queue(),before)
        self.assertEqual(len(self.jobs()),1)

    def test_owner_get_reports_pending_not_old_completed_after_recheck(self):
        # Supply the read endpoint's evidence columns without changing the
        # retained result payloads used by the queue-mutation assertions.
        with sqlite3.connect(self.path) as db:
            for table,columns in (("findings",("title","law","document","source_url","code","kind")),
                                  ("checks",("item","law","document","source_url","field_path","status"))):
                for column in columns:db.execute("ALTER TABLE "+table+" ADD COLUMN "+column+" TEXT")
        headers={"cookie":SESSION_COOKIE+"="+"s"*48}
        previous=self.client.get("/me/manual-checks",headers=headers)
        self.assertEqual(previous.status_code,200,previous.text)
        self.assertTrue(previous.json()[0]["legal_review_completed"])
        self.assertEqual(self.post().status_code,202)
        current=self.client.get("/me/manual-checks",headers=headers)
        self.assertEqual(current.status_code,200,current.text)
        self.assertEqual(current.json()[0]["legal_review_state"],"pending")
        self.assertFalse(current.json()[0]["legal_review_completed"])
        self.assertEqual(current.json()[0]["legal_review_documents"],2)

    def test_concurrent_starts_only_queue_once_and_share_download(self):
        real_prepare=manual._prepare_legal_recheck
        ready=threading.Barrier(2)
        def prepare(*args):
            plan=real_prepare(*args)
            ready.wait(timeout=5)
            return plan
        with patch.object(manual,"_prepare_legal_recheck",side_effect=prepare),ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda route:self.post(route=route),("manual","workbench")))
        self.assertEqual([r.status_code for r in results],[202,202])
        self.assertEqual(len({r.json()["job_id"] for r in results}),1)
        self.assertEqual(sorted(r.json()["legal_recheck"]["action"] for r in results),["queued","skipped"])
        self.assertEqual(self.queue()["state"],"pending")
        self.assertEqual(len(self.jobs()),1)

    def test_concurrent_worker_claim_and_completion_survive_delayed_owner_link(self):
        real_link=manual.link_run
        for finish in (False,True):
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE v332_audit_queue SET state='completed',updated_at='old',next_retry_at=999 WHERE reg=?",(A,))
            def link_then_worker(*args):
                real_link(*args)
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE v332_audit_queue SET state='pending',next_retry_at=0 WHERE reg=?",(A,))
                self.assertEqual(worker.claim_next(self.ns),A)
                if finish:
                    with sqlite3.connect(self.path) as db:
                        db.execute("UPDATE v332_audit_queue SET state='completed',updated_at='new-result',last_result_token='new-token' WHERE reg=?",(A,))
                self.claimed=self.queue()
            with self.subTest(finish=finish),patch.object(manual,"link_run",side_effect=link_then_worker):
                result=self.post()
            self.assertEqual(result.status_code,202,result.text)
            self.assertEqual(result.json()["legal_recheck"]["reason"],"changed_since_preflight" if finish else "already_running")
            self.assertEqual(self.queue(),self.claimed)

    def test_auth_owner_csrf_and_invalid_regs_fail_before_queue_access(self):
        before=self.snapshot();identities=self.snapshot(self.identity)
        with patch.object(manual,"_prepare_legal_recheck",side_effect=AssertionError("unauthorized queue access")) as prepare:
            for route in ("manual","workbench"):
                self.assertEqual(self.post(route=route,token="").status_code,401)
                self.assertEqual(self.post(route=route,origin="https://evil.test").status_code,403)
                self.assertEqual(self.post(route=route,origin="").status_code,403)
                for bad in ("3261623366","42616233661","32616233661 ","all"):
                    self.assertEqual(self.post(bad,route=route).status_code,422)
            self.assertEqual(self.post(B,route="workbench").status_code,404)
            self.assertEqual(self.post(A,route="workbench",token="t"*48).status_code,404)
            prepare.assert_not_called()
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.snapshot(self.identity),identities)

    def test_blocked_expired_and_revoked_sessions_cannot_start(self):
        for sql in ("UPDATE account_users SET status='blocked' WHERE id='owner-a'",
                    "UPDATE account_sessions SET expires_at='2000-01-01' WHERE user_id='owner-a'",
                    "UPDATE account_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id='owner-a'"):
            with IdentityDB(self.identity) as db:
                db.execute("UPDATE account_users SET status='approved'")
                db.execute("UPDATE account_sessions SET expires_at=datetime('now','+1 day'),revoked_at=NULL")
                db.execute(sql)
            with patch.object(manual,"_prepare_legal_recheck") as prepare:
                for route in ("manual","workbench"):
                    self.assertEqual(self.post(route=route).status_code,401)
                prepare.assert_not_called()
        self.assertEqual(self.jobs(),[])

    def test_new_purchase_missing_queue_and_missing_original_report_actual_skip(self):
        result=self.post(NEW)
        self.assertEqual(result.status_code,202,result.text)
        self.assertEqual(result.json()["legal_recheck"],{"action":"skipped","reason":"missing_purchase","state":None})
        self.assertIsNone(self.queue(NEW))
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO purchases VALUES(?,0,0,'ООО Новый')",(NEW,))
        result=self.post(NEW,route="workbench")
        self.assertEqual(result.json()["legal_recheck"]["reason"],"missing_queue_row")
        self.assertIsNone(self.queue(NEW))
        (self.root/(A+".pdf")).unlink()
        before=self.queue()
        result=self.post()
        self.assertEqual(result.json()["legal_recheck"]["reason"],"no_local_original")
        self.assertEqual(self.queue(),before)

    def test_postgres_link_failure_never_requeues_legal_and_retry_reuses_download(self):
        before=self.queue();history=self.snapshot(self.identity)
        with patch.object(manual,"link_run",side_effect=RuntimeError("private-db-password /internal/path")):
            result=self.post()
        self.assertEqual(result.status_code,503)
        self.assertIn("не подтверждена",result.json()["detail"])
        self.assertNotIn("private-db-password",result.text)
        self.assertEqual(self.queue(),before)
        self.assertEqual(self.snapshot(self.identity),history)
        identifier=self.jobs()[0][0]
        retry=self.post()
        self.assertEqual(retry.status_code,202,retry.text)
        self.assertEqual(retry.json()["job_id"],identifier)
        self.assertEqual(len(self.jobs()),1)
        self.assertEqual(self.queue()["state"],"pending")

    def test_sqlite_reset_failure_reports_partial_acceptance_and_retry_is_safe(self):
        before=self.queue()
        with patch.object(scoped,"_apply_plan",side_effect=sqlite3.OperationalError("database is locked /internal/path")):
            result=self.post(route="workbench")
        self.assertEqual(result.status_code,503)
        self.assertIn("сохранено в личном кабинете",result.json()["detail"])
        self.assertIn("не подтверждена",result.json()["detail"])
        self.assertNotIn("/internal/path",result.text)
        self.assertEqual(self.queue(),before)
        identifier=self.jobs()[0][0]
        self.assertEqual(self.snapshot(self.identity)["account_manual_checks"][0][2],identifier)
        retry=self.post(route="workbench")
        self.assertEqual(retry.status_code,202,retry.text)
        self.assertEqual(retry.json()["job_id"],identifier)
        self.assertEqual(self.queue()["state"],"pending")
        self.assertEqual(len(self.jobs()),1)

    def test_preflight_failure_has_no_download_or_owner_mutation(self):
        before=self.snapshot();identities=self.snapshot(self.identity)
        with patch.object(scoped,"_read_plan",side_effect=sqlite3.DatabaseError("private/path")):
            response=self.post()
        self.assertEqual(response.status_code,503)
        self.assertNotIn("private/path",response.text)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.snapshot(self.identity),identities)

    def test_download_failure_or_full_queue_never_links_or_resets(self):
        from fastapi import HTTPException
        before=self.snapshot();identities=self.snapshot(self.identity)
        for error,code in ((sqlite3.OperationalError("private/path"),503),(HTTPException(429,"Очередь ЕИС заполнена"),429)):
            with patch.object(downloads,"_enqueue",side_effect=error):
                result=self.post()
            self.assertEqual(result.status_code,code)
            self.assertNotIn("private/path",result.text)
            self.assertEqual(self.snapshot(),before)
            self.assertEqual(self.snapshot(self.identity),identities)

    def test_internal_sqlite_path_ignores_client_supplied_database_and_attached_files(self):
        unrelated=self.root/"unrelated.db"
        with sqlite3.connect(unrelated) as db:db.execute("CREATE TABLE secret(value TEXT)")
        before=unrelated.read_bytes()
        original_connection=self.ns["conn"]
        def connect():
            db=original_connection()
            db.execute("ATTACH DATABASE ? AS other",(str(unrelated),))
            return db
        self.ns["conn"]=connect
        response=self.post(params={"database":str(unrelated),"reg_numbers":B},
                           json={"database":str(unrelated),"originals_root":"/","regs":[B]})
        self.assertEqual(response.status_code,202,response.text)
        self.assertEqual(self.queue()["state"],"pending")
        self.assertEqual(self.queue(B)["state"],"completed")
        self.assertEqual(unrelated.read_bytes(),before)
        self.assertNotIn(str(self.root),response.text)

    def test_authorizer_blocks_history_trigger_and_rolls_back_only_legal_reset(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER forbidden AFTER UPDATE OF state ON v332_audit_queue "
                       "BEGIN INSERT INTO manager_signoffs VALUES(NEW.reg,'changed'); END")
        before=self.snapshot()
        response=self.post()
        self.assertEqual(response.status_code,503)
        after=self.snapshot()
        for table in before:
            if table not in ("v301_search_jobs","v319_manual_inputs"):
                self.assertEqual(after[table],before[table],table)
        self.assertEqual(len(self.jobs()),1)

    def test_original_removed_after_owner_link_is_skipped_without_touching_results(self):
        real_link=manual.link_run
        before=self.queue()
        def link_then_remove(*args):
            real_link(*args)
            (self.root/(A+".pdf")).unlink()
        with patch.object(manual,"link_run",side_effect=link_then_remove):
            response=self.post()
        self.assertEqual(response.status_code,202,response.text)
        self.assertEqual(response.json()["legal_recheck"]["reason"],"no_local_original")
        self.assertEqual(self.queue(),before)


if __name__=="__main__":unittest.main()
