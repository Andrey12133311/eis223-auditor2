"""HTTP regression tests for explicit employee finding submissions.

All state is in temporary SQLite fixture databases. The identity adapter runs
the actual endpoint SQL, translating psycopg placeholders/UUIDs and omitting
PostgreSQL row locks. Thus these tests cover transactions, joins, ownership,
upserts and audit effects, but do not prove PostgreSQL locking/concurrency or
production migration deployment. Session-cookie lookup, the production gate,
fast-card rendering, origin checks, role/team checks, evidence extraction,
keys, Pydantic validation and endpoint dispatch are real.
"""
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import account_api_v1 as accounts
from scripts import account_schema_v1 as schema
from scripts.account_production_v1 import ProductionAccountGate
from scripts.account_security_v1 import digest_session_token
from scripts.account_session_policy_v1 import SESSION_COOKIE
from scripts.v343_fast_cards import FastCardASGI
from scripts import v368_manager_evidence as evidence
from scripts import v368_manager_workflow as workflow
from scripts import v369_evidence_case as cases
from scripts import v382_finding_submissions as submissions


ORIGIN = "https://fixture.example.test"
REG_A = "32616412317"
REG_B = "32616412318"
EMPLOYEE_A = UUID("00000000-0000-0000-0000-000000000001")
EMPLOYEE_B = UUID("00000000-0000-0000-0000-000000000002")
MANAGER_A = UUID("00000000-0000-0000-0000-000000000003")
MANAGER_B = UUID("00000000-0000-0000-0000-000000000004")
ADMIN = UUID("00000000-0000-0000-0000-000000000005")


class SQLiteIdentity:
    """Execute endpoint queries against real fixture tables, not canned rows."""

    def __init__(self, path, calls):
        self.connection = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.calls = calls

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self.connection.__exit__(*exc)
        finally:
            self.connection.close()

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        sql = re.sub(r"\s+FOR UPDATE\b", "", sql, flags=re.I).replace("%s", "?")
        params = tuple(str(value) if isinstance(value, UUID) else value for value in params)
        return self.connection.execute(sql, params)


class FindingSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="finding-submissions-"))
        self.identity_path = str(Path(directory) / "identity.sqlite")
        self.evidence_path = str(Path(directory) / "evidence.sqlite")
        self.calls = []
        self.users = {
            EMPLOYEE_A: ("employee-a@example.test", "user", "approved"),
            EMPLOYEE_B: ("employee-b@example.test", "user", "approved"),
            MANAGER_A: ("manager-a@example.test", "manager", "approved"),
            MANAGER_B: ("manager-b@example.test", "manager", "approved"),
            ADMIN: ("admin@example.test", "admin", "approved"),
        }
        self._create_identity()
        self._create_evidence()
        self.original_evidence = self.evidence_rows()
        for module in (accounts, submissions, evidence, workflow, cases):
            self.stack.enter_context(patch.object(module, "connect", self.connect))
        self.stack.enter_context(patch.dict("os.environ", {
            "EIS223_PUBLIC_ORIGIN": ORIGIN, "EIS223_AUTH_ENFORCE": "true",
        }))
        app = self.app = FastAPI()
        app.state.eis223_manual_ns = {"conn": lambda: sqlite3.connect(self.evidence_path)}
        app.include_router(submissions.router)
        app.include_router(evidence.router)
        app.include_router(cases.router)
        app.include_router(cases.manager_router)
        self.client = self.stack.enter_context(TestClient(ProductionAccountGate(
            FastCardASGI(app, app.state.eis223_manual_ns), connection=self.connect)))

    def connect(self):
        return SQLiteIdentity(self.identity_path, self.calls)

    def token(self, user):
        return "fixture-only-session-" + str(user)

    def _create_identity(self):
        tables = (
            "account_users", "account_sessions", "account_manual_checks", "account_manager_teams",
            "account_manager_finding_reviews", "account_finding_submissions",
            "account_manager_actions", "account_case_notes",
        )
        with sqlite3.connect(self.identity_path) as db:
            db.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value)))
            db.execute("PRAGMA foreign_keys=ON")
            for table in tables:
                match = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\(.*?\n\);", schema.SCHEMA, re.S)
                self.assertIsNotNone(match, "Production migration must declare " + table)
                ddl = match.group().replace("TIMESTAMPTZ", "TIMESTAMP")
                ddl = ddl.replace("BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY", "INTEGER PRIMARY KEY")
                ddl = ddl.replace("reg_number ~", "reg_number REGEXP")
                db.execute(ddl)
            for user, (email, role, status) in self.users.items():
                db.execute("INSERT INTO account_users(id,email,password_hash,role,status) VALUES(?,?,?,?,?)",
                           (str(user), email, "fixture-only-unused", role, status))
                db.execute("INSERT INTO account_sessions(id,user_id,token_digest,expires_at) "
                           "VALUES(?,?,?,datetime('now','+1 day'))",
                           (str(UUID(int=user.int + 100)), str(user), digest_session_token(self.token(user))))
            for employee, manager, reg in ((EMPLOYEE_A, MANAGER_A, REG_A), (EMPLOYEE_B, MANAGER_B, REG_B)):
                db.execute("INSERT INTO account_manager_teams(user_id,manager_id,assigned_by) VALUES(?,?,?)",
                           (str(employee), str(manager), str(ADMIN)))
                db.execute("INSERT INTO account_manual_checks(user_id,reg_number) VALUES(?,?)", (str(employee), reg))

    def _create_evidence(self):
        with sqlite3.connect(self.evidence_path) as db:
            db.execute("""CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,kind TEXT,title TEXT,
                          law TEXT,evidence TEXT,document TEXT,source_url TEXT,code TEXT)""")
            db.execute("""CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,
                          url TEXT,documents_found INTEGER,documents_checked INTEGER)""")
            db.execute("""CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,item TEXT,
                          law TEXT,evidence TEXT,document TEXT,source_url TEXT)""")
            db.execute("""CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,
                          doc_type TEXT,status TEXT,url TEXT,text_chars INTEGER)""")
            for reg in (REG_A, REG_B):
                db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?)", (
                    reg, "Синтетическая закупка", "Синтетический заказчик",
                    "https://zakupki.gov.ru/223/purchase/" + reg, 1, 1,
                ))
                db.execute("INSERT INTO findings(reg_number,kind,title,law,evidence,document,source_url,code) VALUES(?,?,?,?,?,?,?,?)", (
                    reg, "violation", "Нарушен срок размещения протокола",
                    "223-ФЗ, ч. 12 ст. 4", "Протокол подписан 01.09.2026 и размещён 15.09.2026.",
                    "fixture-protocol.pdf", "https://zakupki.gov.ru/223/evidence/" + reg,
                    "223-4-12",
                ))

    def headers(self, user):
        return {"cookie": SESSION_COOKIE + "=" + self.token(user), "origin": ORIGIN}

    def get_findings(self, user=EMPLOYEE_A, reg=REG_A):
        return self.client.get("/me/finding-submissions/" + reg, headers=self.headers(user))

    def key(self, user=EMPLOYEE_A, reg=REG_A):
        response = self.get_findings(user, reg)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["findings"][0]
        self.assertEqual(item["warnings"], [])
        return item["finding_key"]

    def submit(self, user=EMPLOYEE_A, reg=REG_A, key=None):
        key = key or self.key(user, reg)
        return self.client.post(f"/me/finding-submissions/{reg}/{key}/submit", headers=self.headers(user))

    def queue(self, manager=MANAGER_A):
        response = self.client.get("/manager/finding-submissions", headers=self.headers(manager))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def manager_finding(self, manager=MANAGER_A, employee=EMPLOYEE_A, reg=REG_A):
        response = self.client.get(f"/manager/findings/{employee}/{reg}", headers=self.headers(manager))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["findings"][0]

    def decision(self, decision="approved", comment="", manager=MANAGER_A,
                 employee=EMPLOYEE_A, reg=REG_A, key=None, version=None):
        key = key or self.key(employee, reg)
        if version is None:
            version = self.manager_finding(manager, employee, reg)["submission"]["version"]
        return self.client.post(
            f"/manager/findings/{employee}/{reg}/{key}/decision",
            headers=self.headers(manager),
            json={"decision": decision, "comment": comment, "submission_version": version},
        )

    def rows(self, table):
        with sqlite3.connect(self.identity_path) as db:
            return db.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall()

    def evidence_rows(self):
        with sqlite3.connect(self.evidence_path) as db:
            return db.execute("SELECT * FROM findings ORDER BY rowid").fetchall()

    def audits(self):
        with sqlite3.connect(self.identity_path) as db:
            return db.execute("SELECT actor_id,target_user_id,entity_type,entity_id,action,details "
                              "FROM account_manager_actions ORDER BY id").fetchall()

    def assert_no_state(self):
        for table in ("account_finding_submissions", "account_manager_finding_reviews", "account_manager_actions"):
            self.assertEqual(self.rows(table), [], table)

    def clear_manual_checks(self):
        with sqlite3.connect(self.identity_path) as db:
            db.execute("DELETE FROM account_manual_checks")
        self.assertEqual(self.rows("account_manual_checks"), [])

    def test_shared_fast_card_to_final_result_without_any_personal_check(self):
        self.clear_manual_checks()
        # This is the real authenticated shared-card route, not render() alone.
        card = self.client.get("/check-new/" + REG_A, headers=self.headers(EMPLOYEE_A))
        self.assertEqual(card.status_code, 200, card.text)
        self.assertEqual(card.headers["x-audit-card"], "saved-evidence-v343")
        self.assertEqual(card.headers["cache-control"], "no-store")
        keys = re.findall(r'data-finding-review="([0-9a-f]{64})"', card.text)
        self.assertEqual(len(keys), 1)
        key = keys[0]
        self.assertIn("Направить на согласование руководителю", card.text)
        self.assertNotIn("Правовое основание:", card.text)
        self.assertIn("Применённая норма:", card.text)
        self.assertIn("Протокол подписан 01.09.2026 и размещён 15.09.2026.", card.text)
        self.assertIn("https://www.consultant.ru/document/", card.text)
        state = self.get_findings().json()["findings"][0]
        self.assertEqual(state["finding_key"], key)
        self.assertIsNone(state["submission"])
        self.assertEqual(self.queue(), [])
        sent = self.submit(key=key)
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertFalse(sent.json()["already_submitted"])
        queued = self.queue()
        self.assertEqual([(row["user_id"], row["finding_key"]) for row in queued], [(str(EMPLOYEE_A), key)])
        manager_item = self.manager_finding()
        self.assertEqual(manager_item["finding_key"], key)
        decided = self.decision("rejected", "Документ не подтверждает этот вывод", key=key,
                                version=manager_item["submission"]["version"])
        self.assertEqual(decided.status_code, 200, decided.text)
        final = self.get_findings().json()["findings"][0]
        self.assertEqual(final["submission"]["status"], "rejected")
        self.assertEqual(final["review"], {"decision": "rejected", "comment": "Документ не подтверждает этот вывод"})
        self.assertEqual([row[4] for row in self.audits()], ["submit", "rejected"])
        self.assertEqual(self.rows("account_manual_checks"), [], "Shared submission must not manufacture a personal check")
        self.assertEqual(self.evidence_rows(), self.original_evidence)
        locks = [(sql, params) for sql, params in self.calls if "FOR UPDATE" in sql]
        self.assertEqual(len(locks), 2)
        self.assertTrue(all("account_users" in sql and str(params[0]) == str(EMPLOYEE_A) for sql, params in locks))

    def test_shared_only_manager_cannot_see_or_decide_unsubmitted_sibling(self):
        self.clear_manual_checks()
        key = self.key()
        with sqlite3.connect(self.evidence_path) as db:
            db.execute("INSERT INTO findings(reg_number,kind,title,law,evidence,document,source_url,code) VALUES(?,?,?,?,?,?,?,?)", (
                REG_A, "violation", "Недостаточный срок подачи заявок", "223-ФЗ, ч. 19 ст. 3.2",
                "Извещение размещено 01.10.2026; срок подачи заявок завершился 02.10.2026.",
                "fixture-notice.pdf", "https://zakupki.gov.ru/223/evidence/notice", "223-3.2-19",
            ))
        all_items = self.get_findings().json()["findings"]
        self.assertEqual(len(all_items), 2)
        sibling = next(item["finding_key"] for item in all_items if item["finding_key"] != key)
        self.assertEqual(self.submit(key=key).status_code, 200)
        response = self.client.get(f"/manager/findings/{EMPLOYEE_A}/{REG_A}", headers=self.headers(MANAGER_A))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["finding_key"] for item in response.json()["findings"]], [key])
        before = self.audits()
        for manager in (MANAGER_A, ADMIN):
            with self.subTest(manager=manager):
                denied = self.decision(manager=manager, key=sibling, version=1)
                self.assertEqual(denied.status_code, 404, denied.text)
        self.assertEqual(self.audits(), before)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])

    def test_shared_submission_does_not_grant_private_case_access(self):
        self.clear_manual_checks()
        key = self.key()
        self.assertEqual(self.submit(key=key).status_code, 200)
        for path in (f"/me/cases/{REG_A}", f"/me/cases/{REG_A}/data"):
            with self.subTest(path=path):
                denied = self.client.get(path, headers=self.headers(EMPLOYEE_A))
                self.assertEqual(denied.status_code, 404, denied.text)
        denied_note = self.client.post(f"/me/cases/{REG_A}/notes", headers=self.headers(EMPLOYEE_A),
                                      json={"body": "Недопустимое личное замечание", "finding_key": key})
        self.assertEqual(denied_note.status_code, 404, denied_note.text)
        manager_notes = self.client.get(f"/manager/cases/{EMPLOYEE_A}/{REG_A}/notes", headers=self.headers(MANAGER_A))
        self.assertEqual(manager_notes.status_code, 404, manager_notes.text)
        self.assertEqual(self.rows("account_case_notes"), [])
        self.assertEqual(self.rows("account_manual_checks"), [])
        self.assertEqual([row[4] for row in self.audits()], ["submit"])

    def test_shared_only_submission_remains_invisible_to_other_team(self):
        self.clear_manual_checks()
        key = self.key()
        self.assertEqual(self.submit(key=key).status_code, 200)
        self.assertEqual(self.queue(MANAGER_B), [])
        response = self.client.get(f"/manager/findings/{EMPLOYEE_A}/{REG_A}", headers=self.headers(MANAGER_B))
        self.assertEqual(response.status_code, 404, response.text)
        denied = self.decision(manager=MANAGER_B, key=key, version=1)
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])
        self.assertEqual([row[4] for row in self.audits()], ["submit"])

    def test_gate_rejects_missing_revoked_and_blocked_sessions(self):
        key = self.key()
        paths = (f"/check-new/{REG_A}", f"/me/finding-submissions/{REG_A}")
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 401)
        with sqlite3.connect(self.identity_path) as db:
            db.execute("UPDATE account_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=?", (str(EMPLOYEE_A),))
            db.execute("UPDATE account_users SET status='blocked' WHERE id=?", (str(EMPLOYEE_B),))
        for user in (EMPLOYEE_A, EMPLOYEE_B):
            with self.subTest(user=user):
                for path in paths:
                    self.assertEqual(self.client.get(path, headers=self.headers(user)).status_code, 401)
                self.assertEqual(self.submit(user=user, reg=REG_A, key=key).status_code, 401)
        self.assert_no_state()

    def test_employee_submit_manager_approval_employee_status(self):
        initial = self.get_findings()
        self.assertEqual(initial.status_code, 200)
        self.assertIn("no-store", initial.headers["cache-control"])
        self.assertTrue(initial.json()["manager_assigned"])
        self.assertIsNone(initial.json()["findings"][0]["submission"])
        self.assertIsNone(initial.json()["findings"][0]["review"])
        self.assertEqual(self.queue(), [])
        key = self.key()
        result = self.submit(key=key)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "submitted")
        self.assertFalse(result.json()["already_submitted"])
        queued = self.queue()
        self.assertEqual(len(queued), 1)
        self.assertEqual((queued[0]["user_id"], queued[0]["finding_key"], queued[0]["status"]),
                         (str(EMPLOYEE_A), key, "submitted"))
        datetime.fromisoformat(queued[0]["submitted_at"])
        approved = self.decision(comment="Проверено по оригиналу", key=key)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertTrue(approved.json()["evidence_unchanged"])
        item = self.get_findings().json()["findings"][0]
        self.assertEqual(item["submission"]["status"], "approved")
        self.assertEqual(item["review"], {"decision": "approved", "comment": "Проверено по оригиналу"})
        self.assertEqual(self.queue()[0]["status"], "approved")
        self.assertEqual([row[4] for row in self.audits()], ["submit", "approved"])
        self.assertEqual(self.audits()[0][:4], (str(EMPLOYEE_A), str(EMPLOYEE_A), "finding", key))
        self.assertEqual(self.audits()[1][:4], (str(MANAGER_A), str(EMPLOYEE_A), "finding", key))
        self.assertEqual(self.evidence_rows(), self.original_evidence)

    def test_repeated_submit_is_one_row_one_audit(self):
        key = self.key()
        first = self.submit(key=key)
        self.assertEqual(first.status_code, 200, first.text)
        stored = self.rows("account_finding_submissions")
        for _ in range(3):
            response = self.submit(key=key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["already_submitted"])
            self.assertEqual(response.json()["status"], "submitted")
        self.assertEqual(self.rows("account_finding_submissions"), stored)
        self.assertEqual([row[4] for row in self.audits()], ["submit"])
        lock_queries = [sql for sql, _ in self.calls if "FOR UPDATE" in sql]
        self.assertEqual(len(lock_queries), 4, "Every submit must lock its owner before reading submission state")
        self.assertTrue(all("account_users" in sql for sql in lock_queries))

    def test_return_is_not_allowed_for_explicit_submission(self):
        self.assertEqual(self.submit().status_code, 200)
        before = self.audits()
        returned = self.decision("returned", "Уточните дату публикации")
        self.assertEqual(returned.status_code, 422, returned.text)
        self.assertEqual(self.audits(), before)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])
        self.assertEqual(self.get_findings().json()["findings"][0]["submission"]["status"], "submitted")
        self.assertEqual(self.queue()[0]["status"], "submitted")

    def test_stale_submission_version_cannot_decide(self):
        self.assertEqual(self.submit().status_code, 200)
        version = self.manager_finding()["submission"]["version"]
        with sqlite3.connect(self.identity_path) as db:
            db.execute("UPDATE account_finding_submissions SET version=version+1")
        before = self.audits()
        stale = self.decision("approved", version=version)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self.audits(), before)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])
        self.assertEqual(self.get_findings().json()["findings"][0]["submission"]["status"], "submitted")

    def test_rejection_is_visible_and_cannot_be_resubmitted(self):
        self.assertEqual(self.submit().status_code, 200)
        result = self.decision("rejected", "Вывод не подтверждён документом")
        self.assertEqual(result.status_code, 200, result.text)
        item = self.get_findings().json()["findings"][0]
        self.assertEqual(item["submission"]["status"], "rejected")
        self.assertEqual(item["review"]["decision"], "rejected")
        self.assertEqual(self.queue()[0]["comment"], "Вывод не подтверждён документом")
        before = self.audits()
        repeat = self.submit()
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertTrue(repeat.json()["already_submitted"])
        self.assertEqual(repeat.json()["status"], "rejected")
        self.assertEqual(self.audits(), before)
        repeated = self.decision("rejected", "Попытка изменить окончательную причину")
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["already_reviewed"])
        self.assertEqual(self.audits(), before)
        self.assertEqual(self.get_findings().json()["findings"][0]["review"]["comment"],
                         "Вывод не подтверждён документом")
        conflict = self.decision("approved")
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(self.audits(), before)

    def test_decision_replay_idempotent_and_conflicting_terminal_decision_denied(self):
        self.assertEqual(self.submit().status_code, 200)
        version = self.manager_finding()["submission"]["version"]
        self.assertEqual(self.decision("approved", version=version).status_code, 200)
        before = self.audits()
        self.assertEqual(self.decision("approved", version=version).status_code, 200)
        self.assertEqual(self.audits(), before)
        conflict = self.decision("rejected", "Несовместимое повторное решение", version=version)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(self.audits(), before)
        repeat = self.submit()
        self.assertTrue(repeat.json()["already_submitted"])
        self.assertEqual(repeat.json()["status"], "approved")
        self.assertEqual(self.audits(), before)

    def test_rejection_requires_meaningful_comment(self):
        self.assertEqual(self.submit().status_code, 200)
        before = self.audits()
        for decision in ("rejected",):
            for comment in ("", "    ", " four "):
                with self.subTest(decision=decision, comment=comment):
                    result = self.decision(decision, comment)
                    self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(self.audits(), before)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])
        self.assertEqual(self.queue()[0]["status"], "submitted")

    def test_manager_queue_is_team_scoped_admin_sees_both(self):
        self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(self.submit(EMPLOYEE_B, REG_B).status_code, 200)
        self.assertEqual([row["user_id"] for row in self.queue(MANAGER_A)], [str(EMPLOYEE_A)])
        self.assertEqual([row["user_id"] for row in self.queue(MANAGER_B)], [str(EMPLOYEE_B)])
        self.assertEqual({row["user_id"] for row in self.queue(ADMIN)}, {str(EMPLOYEE_A), str(EMPLOYEE_B)})

    def test_other_employee_can_submit_shared_finding_without_inheriting_state(self):
        key = self.key()
        self.assertEqual(self.submit(key=key).status_code, 200)
        self.assertEqual(self.decision(key=key).status_code, 200)
        response = self.get_findings(EMPLOYEE_B, REG_A)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["findings"][0]["submission"])
        self.assertIsNone(response.json()["findings"][0]["review"])
        response = self.submit(EMPLOYEE_B, REG_A, key)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "submitted")
        first = self.get_findings(EMPLOYEE_A, REG_A).json()["findings"][0]
        second = self.get_findings(EMPLOYEE_B, REG_A).json()["findings"][0]
        self.assertEqual(first["submission"]["status"], "approved")
        self.assertEqual(second["submission"]["status"], "submitted")
        self.assertIsNone(second["review"])
        self.assertEqual([row["user_id"] for row in self.queue(MANAGER_B)], [str(EMPLOYEE_B)])
        self.assertEqual(len(self.rows("account_finding_submissions")), 2)

    def test_same_procurement_different_owners_do_not_share_submission_status(self):
        self.assertEqual(self.submit().status_code, 200)
        with sqlite3.connect(self.identity_path) as db:
            db.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value)))
            db.execute("INSERT INTO account_manual_checks(user_id,reg_number) VALUES(?,?)", (str(EMPLOYEE_B), REG_A))
        other = self.get_findings(EMPLOYEE_B, REG_A)
        self.assertEqual(other.status_code, 200, other.text)
        self.assertIsNone(other.json()["findings"][0]["submission"])
        self.assertIsNone(other.json()["findings"][0]["review"])
        self.assertEqual(self.queue(MANAGER_B), [])

    def test_other_manager_cannot_review_employee(self):
        self.assertEqual(self.submit().status_code, 200)
        key = self.key()
        before = self.audits()
        view = self.client.get(f"/manager/findings/{EMPLOYEE_A}/{REG_A}", headers=self.headers(MANAGER_B))
        self.assertEqual(view.status_code, 404, view.text)
        response = self.decision(manager=MANAGER_B, key=key, version=1)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.audits(), before)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])

    def test_employee_cannot_enter_manager_queue_or_decide(self):
        self.assertEqual(self.submit().status_code, 200)
        key = self.key()
        queue = self.client.get("/manager/finding-submissions", headers=self.headers(EMPLOYEE_A))
        self.assertEqual(queue.status_code, 403, queue.text)
        response = self.decision(manager=EMPLOYEE_A, key=key, version=1)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual([row[4] for row in self.audits()], ["submit"])

    def test_stale_evidence_key_denied_for_submit_and_decision(self):
        key = self.key()
        self.assertEqual(self.submit(key=key).status_code, 200)
        with sqlite3.connect(self.evidence_path) as db:
            db.execute("UPDATE findings SET evidence=? WHERE reg_number=?", ("Исправлено: протокол размещён 02.09.2026.", REG_A))
        current = self.get_findings().json()["findings"][0]
        self.assertNotEqual(current["finding_key"], key)
        self.assertIsNone(current["submission"])
        before = self.audits()
        stale_submit = self.submit(key=key)
        self.assertEqual(stale_submit.status_code, 409, stale_submit.text)
        stale_decision = self.decision(key=key, version=1)
        self.assertEqual(stale_decision.status_code, 409, stale_decision.text)
        self.assertEqual(self.audits(), before)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])

    def test_unassigned_employee_cannot_submit(self):
        with sqlite3.connect(self.identity_path) as db:
            db.execute("DELETE FROM account_manager_teams WHERE user_id=?", (str(EMPLOYEE_A),))
        self.assertFalse(self.get_findings().json()["manager_assigned"])
        response = self.submit()
        self.assertEqual(response.status_code, 409, response.text)
        self.assert_no_state()

    def test_origin_required_on_both_mutations(self):
        key = self.key()
        headers = self.headers(EMPLOYEE_A)
        del headers["origin"]
        response = self.client.post(f"/me/finding-submissions/{REG_A}/{key}/submit", headers=headers)
        self.assertEqual(response.status_code, 403, response.text)
        self.assert_no_state()
        self.assertEqual(self.submit(key=key).status_code, 200)
        response = self.client.post(f"/manager/findings/{EMPLOYEE_A}/{REG_A}/{key}/decision",
                                    headers={**self.headers(MANAGER_A), "origin": "https://untrusted.example.test"},
                                    json={"decision": "approved", "submission_version": 1})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual([row[4] for row in self.audits()], ["submit"])

    def test_missing_submission_version_cannot_decide_submitted_finding(self):
        self.assertEqual(self.submit().status_code, 200)
        key = self.key()
        result = self.client.post(f"/manager/findings/{EMPLOYEE_A}/{REG_A}/{key}/decision",
                                  headers=self.headers(MANAGER_A), json={"decision": "approved"})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])
        self.assertEqual([row[4] for row in self.audits()], ["submit"])

    def test_missing_evidence_store_returns_503_without_writes(self):
        key = self.key()
        self.app.state.eis223_manual_ns = None
        response = self.submit(key=key)
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_no_state()

    def test_submission_and_audit_are_one_transaction(self):
        key = self.key()
        with patch.object(submissions, "_audit", side_effect=RuntimeError("fixture audit failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture audit failure"):
                self.submit(key=key)
        self.assert_no_state()
        self.assertEqual(self.evidence_rows(), self.original_evidence)

    def test_incomplete_evidence_cannot_be_approved(self):
        with sqlite3.connect(self.evidence_path) as db:
            db.execute("UPDATE findings SET law='' WHERE reg_number=?", (REG_A,))
        item = self.get_findings().json()["findings"][0]
        self.assertTrue(item["warnings"])
        key = item["finding_key"]
        self.assertEqual(self.submit(key=key).status_code, 200)
        response = self.decision(key=key)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.rows("account_manager_finding_reviews"), [])
        self.assertEqual(self.queue()[0]["status"], "submitted")
        self.assertEqual([row[4] for row in self.audits()], ["submit"])


if __name__ == "__main__":
    unittest.main()
