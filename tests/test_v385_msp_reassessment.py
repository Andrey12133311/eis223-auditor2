"""Targeted SME rechecks through real account routes and append-only storage.

The account fixture executes the endpoint SQL against SQLite, adapting UUIDs,
PostgreSQL placeholders and row locks. This proves authorization, source scope,
audit effects and rendered counts, not production PostgreSQL concurrency.
No live EIS request, external service or production data is used.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

import test_thematic_finding_submissions as thematic_fixture
import test_v382_finding_submissions as fixture
from scripts import v385_msp_reassessment as reassessment
import v342_thematic_queue as thematic


BATCH_A = thematic_fixture.BATCH_A
BATCH_B = thematic_fixture.BATCH_B
BATCH_C = thematic_fixture.BATCH_C
CODE = "223-SME-BID-SECURITY-OVER-TWO-PERCENT"
TITLE = "Размер обеспечения заявки превышает 2% НМЦД в закупке только для МСП"
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32
LABEL = "Участниками закупки могут быть только субъекты малого и среднего предпринимательства"


def current_sources(state="no"):
    purchase = {"reg": fixture.REG_A, "method": "Аукцион в электронной форме",
                "price": "38720976", "price_num": 38720976}
    text = ("Начальная (максимальная) цена договора: 38 720 976 рублей.\n"
            "Обеспечение заявки составляет: 5% от начальной (максимальной) цены договора.\n")
    if state in ("yes", "no", "conflict"):
        text += LABEL + ": " + ("Да" if state == "yes" else "Нет")
    else:
        text += ("При проведении закупки только среди субъектов малого и среднего "
                 "предпринимательства размер обеспечения заявки не может превышать 2 процента.")
    if state == "conflict":
        purchase["fact_evidence"] = {"sme_only": {"value": True,
            "source": "official_eis_card", "evidence": LABEL + ": Да"}}
    doc = {"name": "current-notice.txt", "status": "checked", "text": text,
           "meta": {"source": "official_eis_original", "source_url": URL,
                    "sha256": hashlib.sha256(text.encode()).hexdigest()}}
    return purchase, [doc]


class MspReassessmentTests(unittest.TestCase):
    def setUp(self):
        self.t = thematic_fixture.ThematicFindingTests()
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.f, self.ns = self.t.f, self.t.ns
        self.addCleanup(thematic._HISTORY_COUNT_CACHE.clear)
        thematic._HISTORY_COUNT_CACHE.clear()
        if hasattr(reassessment, "connect"):
            self.f.stack.enter_context(patch.object(reassessment, "connect", self.f.connect))
        path = "/me/thematic/{batch}/{reg}/findings/{finding_key}/msp-recheck"
        if not any(getattr(route, "path", None) == path for route in self.ns["app"].routes):
            self.ns["app"].include_router(reassessment.router)
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute("DELETE FROM thematic_findings WHERE category='national'")
            db.execute("UPDATE thematic_batches SET categories=?", ('["protocols","security"]',))
            for batch in (BATCH_A, BATCH_B, BATCH_C):
                db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,"
                           "code,document,source_url) VALUES(?,?,?,?,?,?,?,?,?)", (
                    batch, fixture.REG_A, "security", TITLE,
                    "223-ФЗ, ст. 3 ч. 8; ПП РФ № 1352, п. 23",
                    "В документации предусмотрено обеспечение заявки в размере 5% НМЦД.",
                    CODE, "historical-notice.pdf", URL))
        self.initial = self.item()
        self.key = self.initial["finding_key"]
        self.original_rows = self.evidence_snapshot()

    def item(self, batch=BATCH_A, user=fixture.EMPLOYEE_A):
        response = self.t.get(batch=batch, user=user)
        self.assertEqual(response.status_code, 200, response.text)
        return next(item for item in response.json()["findings"] if item["code"] == CODE)

    def endpoint(self, batch=BATCH_A, reg=fixture.REG_A, key=None):
        return f"/me/thematic/{batch}/{reg}/findings/{key or self.key}/msp-recheck"

    def post(self, state="no", *, batch=BATCH_A, reg=fixture.REG_A, key=None,
             user=fixture.EMPLOYEE_A, headers=None, sources=None):
        with patch.object(reassessment, "collect_sources", new_callable=AsyncMock,
                          return_value=sources or current_sources(state)) as collect:
            response = self.f.client.post(self.endpoint(batch, reg, key),
                headers=self.f.headers(user) if headers is None else headers)
        return response, collect

    def evidence_snapshot(self):
        with sqlite3.connect(self.f.evidence_path) as db:
            return {table: db.execute("SELECT rowid,* FROM " + table + " ORDER BY rowid").fetchall()
                    for table in ("thematic_findings", "thematic_items", "thematic_batches",
                                  "findings", "checks", "purchases", "docs")}

    def audit_rows(self):
        with sqlite3.connect(self.f.evidence_path) as db:
            db.row_factory = sqlite3.Row
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                              "AND name LIKE '%reassess%'")]
            self.assertEqual(len(tables), 1, "One append-only thematic reassessment audit table is required")
            return [dict(row) for row in db.execute("SELECT * FROM " + tables[0] + " ORDER BY rowid")]

    def assert_no_submission(self):
        self.assertEqual(self.f.rows("account_finding_submissions"), [])
        self.assertEqual(self.f.rows("account_manager_finding_reviews"), [])

    def assert_manager_preserves_approval(self, effective_status, stale=False):
        owner_item = self.item()
        for manager in (fixture.MANAGER_A, fixture.ADMIN):
            with self.subTest(manager=manager):
                queued = next(row for row in self.f.queue(manager) if row["finding_key"] == self.key)
                detail = self.f.manager_finding(manager=manager)
                for row in (queued, detail):
                    self.assertEqual(row["finding_key"], self.key)
                    self.assertEqual(row["source_batch"], BATCH_A)
                    self.assertEqual(row["effective_status"], effective_status)
                    self.assertEqual(row["reassessment"], owner_item["reassessment"])
                    self.assertEqual(row["reassessment"].get("stale", False), stale)
                    self.assertTrue(row["reassessment"]["reason"])
                self.assertEqual(queued["status"], "approved")
                self.assertEqual(detail["submission"]["status"], "approved")
                self.assertEqual(detail["review"]["decision"], "approved")
                self.assertEqual(detail["evidence"], self.initial["evidence"])
        self.assertEqual(self.f.queue(fixture.MANAGER_B), [])

    def assert_outcome(self, response, effective_status, scope):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers.get("cache-control", ""))
        item = self.item()
        self.assertEqual(item["finding_key"], self.key)
        self.assertEqual(item["effective_status"], effective_status)
        self.assertTrue(item["reassessment"])
        self.assertEqual(item["reassessment"]["scope"]["state"], scope)
        self.assertEqual(response.json()["effective_status"], effective_status)
        self.assertEqual(response.json()["reassessment"], item["reassessment"])
        self.assertRegex(item["reassessment"]["source_fingerprint"], r"^[0-9a-f]{64}$")
        for field in ("title", "law", "evidence", "document", "source_url", "code"):
            self.assertEqual(item[field], self.initial[field], field)
        self.assertEqual(self.evidence_snapshot(), self.original_rows)
        return item

    def test_exclusive_yes_keeps_confirmed_finding_and_key(self):
        response, collect = self.post("yes")
        self.assert_outcome(response, "confirmed", "yes")
        collect.assert_awaited_once()
        self.assertEqual(self.t.submit(self.key).status_code, 200)

    def test_explicit_no_supersedes_without_deleting_historical_finding(self):
        response, _ = self.post("no")
        self.assert_outcome(response, "superseded", "no")
        self.assertEqual(self.t.submit(self.key).status_code, 409)
        self.assert_no_submission()

    def test_unknown_is_review_required_and_cannot_be_submitted(self):
        response, _ = self.post("unknown")
        self.assert_outcome(response, "requires_review", "unknown")
        self.assertEqual(self.t.submit(self.key).status_code, 409)
        self.assert_no_submission()

    def test_conflicting_official_sources_are_review_required(self):
        response, _ = self.post("conflict")
        self.assert_outcome(response, "requires_review", "conflict")
        self.assertEqual(self.t.submit(self.key).status_code, 409)

    def test_unverified_document_does_not_supersede(self):
        purchase, docs = current_sources("no")
        docs[0]["meta"]["source"] = "existing_local_unverified"
        response, _ = self.post(sources=(purchase, docs))
        self.assert_outcome(response, "requires_review", "unknown")

    def test_client_cannot_supply_a_replacement_scope(self):
        with patch.object(reassessment, "collect_sources", new_callable=AsyncMock,
                          return_value=current_sources("unknown")):
            response = self.f.client.post(self.endpoint(), headers=self.f.headers(fixture.EMPLOYEE_A),
                json={"sme_only": False, "effective_status": "superseded",
                      "fact_evidence": {"sme_only": {"value": False, "source": "official_eis_card"}}})
        self.assert_outcome(response, "requires_review", "unknown")

    def test_missing_revoked_and_blocked_sessions_cannot_recheck(self):
        response, collect = self.post(headers={})
        self.assertEqual(response.status_code, 401, response.text)
        collect.assert_not_awaited()
        with sqlite3.connect(self.f.identity_path) as db:
            db.execute("UPDATE account_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=?",
                       (str(fixture.EMPLOYEE_A),))
            db.execute("UPDATE account_users SET status='blocked' WHERE id=?", (str(fixture.EMPLOYEE_B),))
        for user, batch in ((fixture.EMPLOYEE_A, BATCH_A), (fixture.EMPLOYEE_B, BATCH_B)):
            with self.subTest(user=user):
                response, collect = self.post(user=user, batch=batch)
                self.assertEqual(response.status_code, 401, response.text)
                collect.assert_not_awaited()
        self.assertEqual(self.evidence_snapshot(), self.original_rows)

    def test_only_exact_owner_can_recheck_even_manager_or_admin(self):
        for user, batch in ((fixture.EMPLOYEE_B, BATCH_A), (fixture.EMPLOYEE_A, BATCH_B),
                            (fixture.MANAGER_A, BATCH_A), (fixture.ADMIN, BATCH_A)):
            with self.subTest(user=user, batch=batch):
                response, collect = self.post(user=user, batch=batch)
                self.assertEqual(response.status_code, 404, response.text)
                collect.assert_not_awaited()
        self.assertEqual(self.evidence_snapshot(), self.original_rows)

    def test_origin_required_and_cross_origin_denied_before_collection(self):
        for origin in (None, "https://untrusted.example.test"):
            headers = self.f.headers(fixture.EMPLOYEE_A)
            if origin is None:
                headers.pop("origin")
            else:
                headers["origin"] = origin
            response, collect = self.post(headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
            collect.assert_not_awaited()
        self.assertEqual(self.evidence_snapshot(), self.original_rows)

    def test_other_codes_stale_keys_and_nonmember_purchase_are_rejected(self):
        protocol = next(x for x in self.t.get().json()["findings"] if x["code"] != CODE)
        for params, status in (({"key": protocol["finding_key"]}, 422),
                               ({"key": "f" * 64}, 409), ({"reg": fixture.REG_B}, 404)):
            with self.subTest(params=params):
                response, collect = self.post(**params)
                self.assertEqual(response.status_code, status, response.text)
                collect.assert_not_awaited()
        self.assertEqual(self.evidence_snapshot(), self.original_rows)

    def test_malformed_batch_registration_and_key_are_rejected(self):
        for params in ({"batch": "invalid"}, {"reg": "not-a-reg"}, {"key": "bad-key"}):
            with self.subTest(params=params):
                response, collect = self.post(**params)
                self.assertIn(response.status_code, (409, 422), response.text)
                collect.assert_not_awaited()
        self.assertEqual(self.audit_rows(), [])
        self.f.assert_no_state()

    def test_submitted_evidence_preserved_and_later_approval_blocked(self):
        self.assertEqual(self.t.submit(self.key).status_code, 200)
        before = {table: self.f.rows(table) for table in (
            "account_finding_submissions", "account_manager_finding_reviews", "account_manager_actions")}
        response, _ = self.post("no")
        item = self.assert_outcome(response, "superseded", "no")
        self.assertEqual(item["submission"]["status"], "submitted")
        rejected = self.f.decision(key=self.key, version=1)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        for table, rows in before.items():
            actual = self.f.rows(table)
            self.assertEqual(actual[:len(rows)] if table == "account_manager_actions" else actual, rows, table)
        self.assertEqual([row[4] for row in self.f.audits()], ["submit", "reassess"])

    def test_preexisting_approval_and_review_survive_reassessment(self):
        self.assertEqual(self.t.submit(self.key).status_code, 200)
        approval = self.f.decision(key=self.key, version=1, comment="Проверено по прежней редакции")
        self.assertEqual(approval.status_code, 200, approval.text)
        before = {table: self.f.rows(table) for table in (
            "account_finding_submissions", "account_manager_finding_reviews", "account_manager_actions")}
        response, _ = self.post("no")
        item = self.assert_outcome(response, "superseded", "no")
        self.assertEqual(item["submission"]["status"], "approved")
        self.assertEqual(item["review"]["decision"], "approved")
        for table, rows in before.items():
            actual = self.f.rows(table)
            self.assertEqual(actual[:len(rows)] if table == "account_manager_actions" else actual, rows, table)
        self.assertEqual([row[4] for row in self.f.audits()], ["submit", "approved", "reassess"])
        self.assertEqual(self.f.queue()[0]["status"], "approved")
        self.assert_manager_preserves_approval("superseded")

    def test_history_cache_and_overview_only_count_active_findings(self):
        def history():
            return {row["id"]: row for row in thematic.thematic_history_records(
                self.ns, [BATCH_A, BATCH_C])["batches"]}
        def overview():
            response = self.f.client.get("/api/thematic/" + BATCH_A,
                                         headers=self.f.headers(fixture.EMPLOYEE_A))
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["confirmed_matching_findings"]
        warm = history()
        self.assertEqual(warm[BATCH_A]["violations"], 2)
        self.assertEqual(len(overview()), 2)
        self.assertEqual(self.post("no")[0].status_code, 200)
        after = history()
        self.assertEqual(after[BATCH_A]["violations"], 1)
        self.assertNotIn(TITLE, after[BATCH_A]["violation_types"])
        self.assertEqual(after[BATCH_C], warm[BATCH_C])
        self.assertEqual([item["title"] for item in overview()], [thematic_fixture.TITLE])
        self.assertEqual(len(self.t.get().json()["findings"]), 2)
        self.assertEqual(self.post("yes")[0].status_code, 200)
        self.assertEqual(history()[BATCH_A]["violations"], 2)
        self.assertEqual(len(overview()), 2)

    def test_same_key_in_other_batch_and_general_findings_are_unchanged(self):
        other = deepcopy(self.item(BATCH_C))
        foreign = deepcopy(self.item(BATCH_B, fixture.EMPLOYEE_B))
        general = self.f.get_findings().json()
        response, _ = self.post("no")
        self.assert_outcome(response, "superseded", "no")
        self.assertEqual(self.item(BATCH_C), other)
        self.assertEqual(self.item(BATCH_B, fixture.EMPLOYEE_B), foreign)
        self.assertEqual(self.f.get_findings().json(), general)
        self.assertEqual(self.t.submit(self.key, BATCH_C).status_code, 200)

    def test_repeated_identical_sources_are_idempotent_and_new_evidence_appends(self):
        first, _ = self.post("no")
        self.assertEqual(first.status_code, 200, first.text)
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(first.json()["already_reassessed"])
        serialized = json.dumps(rows[0], ensure_ascii=False)
        for expected in (str(fixture.EMPLOYEE_A), BATCH_A, fixture.REG_A, self.key, "superseded"):
            self.assertIn(expected, serialized)
        snapshot = json.loads(rows[0]["finding_snapshot"])
        for field in ("finding_key", "title", "law", "evidence", "document", "source_url", "code"):
            self.assertEqual(snapshot[field], self.initial[field], field)
        for _ in range(2):
            repeated, _ = self.post("no")
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertTrue(repeated.json()["already_reassessed"])
            self.assertEqual(self.audit_rows(), rows)
        self.assertEqual([row[4] for row in self.f.audits()], ["reassess"])
        changed, _ = self.post("yes")
        self.assert_outcome(changed, "confirmed", "yes")
        after = self.audit_rows()
        self.assertEqual(len(after), 2)
        self.assertEqual(after[0], rows[0], "Previous reassessment must remain byte-for-byte unchanged")

    def prepare_stored_original(self, state="no", receipt=True):
        """Create genuine local receipt/hash fixtures, without mocking verification."""
        root = Path(self.f.evidence_path).parent / "source-data"
        self.f.stack.enter_context(patch.dict("os.environ", {"EIS223_DATA_DIR": str(root)}))
        _, docs = current_sources(state)
        body = ("<document>\n" + docs[0]["text"] + "\n</document>").encode()
        digest = hashlib.sha256(body).hexdigest()
        folder = root / "eis223_official_originals" / fixture.REG_A / ("A" * 32)
        folder.mkdir(parents=True)
        path = folder / (digest + ".xml")
        path.write_bytes(body)
        if receipt:
            (folder / "receipt.json").write_text(json.dumps({
                "reg_number": fixture.REG_A, "uid": "A" * 32,
                "extension": ".xml", "sha256": digest, "bytes": len(body)}))
        with sqlite3.connect(self.f.evidence_path) as db:
            for column in ("published_at", "local_path", "metadata_json", "final_url"):
                db.execute("ALTER TABLE docs ADD COLUMN " + column + " TEXT")
            db.execute("INSERT INTO docs(reg_number,name,status,url,local_path,metadata_json) "
                       "VALUES(?,?,?,?,?,?)", (fixture.REG_A, "current-notice.xml", "checked", URL,
                       str(path), json.dumps({"source": "official_eis_original", "sha256": digest,
                                             "source_url": URL})))
        def connection():
            db = sqlite3.connect(self.f.evidence_path)
            db.row_factory = sqlite3.Row
            return db
        self.ns["conn"] = connection
        self.ns["extract_text"] = lambda filename: Path(filename).read_text()
        return path

    def test_real_source_collection_authenticates_receipt_and_hash(self):
        path = self.prepare_stored_original("no")
        before = self.evidence_snapshot()
        original_bytes = path.read_bytes()
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["meta"]["source"], "official_eis_original")
        result = reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                                  fixture.REG_A, self.key, purchase, docs)
        self.assertEqual(result["effective_status"], "superseded")
        self.assertEqual(result["reassessment"]["scope"]["state"], "no")
        self.assertEqual(self.evidence_snapshot(), before)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(len(self.audit_rows()), 1)

    def test_real_collection_rejects_claimed_official_source_without_receipt(self):
        self.prepare_stored_original("no", receipt=False)
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        self.assertEqual(docs, [])
        result = reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                                  fixture.REG_A, self.key, purchase, docs)
        self.assertEqual(result["effective_status"], "requires_review")
        self.assertEqual(result["reassessment"]["scope"]["state"], "unknown")

    def test_real_collection_rejects_tampered_original_bytes(self):
        path = self.prepare_stored_original("no")
        path.write_bytes(path.read_bytes().replace("Нет".encode(), "Да!".encode()))
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        self.assertEqual(docs, [])
        result = reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                                  fixture.REG_A, self.key, purchase, docs)
        self.assertEqual(result["effective_status"], "requires_review")

    def test_changed_source_token_rejected_without_reassessment(self):
        self.prepare_stored_original("no")
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute("UPDATE docs SET name='changed-notice.xml'")
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                               fixture.REG_A, self.key, purchase, docs)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.audit_rows(), [])

    def test_ownership_is_rechecked_after_slow_source_collection(self):
        async def revoked(_ns, _reg):
            with sqlite3.connect(self.f.identity_path) as db:
                db.execute("DELETE FROM account_thematic_batches WHERE user_id=? AND batch_id=?",
                           (str(fixture.EMPLOYEE_A), BATCH_A))
            return current_sources("no")
        with patch.object(reassessment, "collect_sources", side_effect=revoked):
            response = self.f.client.post(self.endpoint(), headers=self.f.headers(fixture.EMPLOYEE_A))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.evidence_snapshot(), self.original_rows)
        self.f.assert_no_state()

    def test_wrong_purchase_sources_cannot_reassess_matching_key(self):
        purchase, docs = current_sources("no")
        purchase["reg"] = fixture.REG_B
        response, _ = self.post(sources=(purchase, docs))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.evidence_snapshot(), self.original_rows)
        self.f.assert_no_state()

    def test_source_collection_failure_preserves_existing_evidence_and_status(self):
        for failure in (sqlite3.OperationalError, RuntimeError, OSError):
            with self.subTest(failure=failure), patch.object(
                    reassessment, "collect_sources", new_callable=AsyncMock,
                    side_effect=failure("fixture source unavailable")):
                response = self.f.client.post(self.endpoint(), headers=self.f.headers(fixture.EMPLOYEE_A))
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(self.item(), self.initial)
            self.assertEqual(self.audit_rows(), [])
            self.assertEqual(self.evidence_snapshot(), self.original_rows)
            self.f.assert_no_state()

    def test_failed_identity_audit_retry_repairs_journal_without_repeating_evidence_event(self):
        with patch.object(fixture.workflow, "_audit", side_effect=sqlite3.OperationalError("fixture audit down")):
            failed, _ = self.post("no")
        self.assertEqual(failed.status_code, 503, failed.text)
        events = self.audit_rows()
        self.assertEqual(len(events), 1)
        self.f.assert_no_state()
        repaired, _ = self.post("no")
        self.assert_outcome(repaired, "superseded", "no")
        self.assertTrue(repaired.json()["already_reassessed"])
        self.assertEqual(self.audit_rows(), events)
        self.assertEqual([row[4] for row in self.f.audits()], ["reassess"])

    def assert_persisted_source_staleness(self, scope):
        self.prepare_stored_original(scope)
        self.assertEqual(self.t.submit(self.key).status_code, 200)
        approved = self.f.decision(key=self.key, version=1, comment="Проверено по сохранённой редакции")
        self.assertEqual(approved.status_code, 200, approved.text)
        identity = {table: self.f.rows(table) for table in (
            "account_finding_submissions", "account_manager_finding_reviews", "account_manager_actions")}
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        result = reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                                  fixture.REG_A, self.key, purchase, docs)
        original_status = "confirmed" if scope == "yes" else "superseded"
        self.assertEqual(result["effective_status"], original_status)
        journal = self.audit_rows()
        self.assertEqual(journal[0]["source_token"], purchase["_msp_source_token"])
        def history():
            return thematic.thematic_history_records(self.ns, [BATCH_A])["batches"][0]
        warm = history()
        self.assertEqual(warm["violations"], 2 if scope == "yes" else 1)
        cache_stamp = thematic._HISTORY_COUNT_CACHE[BATCH_A][0]
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute("UPDATE docs SET name='new-source-name.xml' WHERE reg_number=?", (fixture.REG_A,))
        sources_after_change = self.evidence_snapshot()
        stale = self.item()
        self.assertEqual(stale["effective_status"], "requires_review")
        self.assertTrue(stale["reassessment"]["stale"])
        self.assertEqual(stale["reassessment"]["original_effective_status"], original_status)
        self.assertEqual(stale["reassessment"]["id"], result["reassessment"]["id"])
        self.assertEqual(stale["finding_key"], self.key)
        self.assertEqual(stale["submission"]["status"], "approved")
        self.assertEqual(stale["review"]["decision"], "approved")
        self.assert_manager_preserves_approval("requires_review", stale=True)
        for field in ("title", "law", "evidence", "document", "source_url", "code"):
            self.assertEqual(stale[field], self.initial[field], field)
        self.assertEqual(history()["violations"], 1)
        self.assertEqual(history()["reassessed_findings"], 1)
        self.assertNotEqual(thematic._HISTORY_COUNT_CACHE[BATCH_A][0], cache_stamp)
        current = self.f.client.get("/api/thematic/" + BATCH_A,
                                   headers=self.f.headers(fixture.EMPLOYEE_A)).json()
        self.assertEqual(len(current["confirmed_matching_findings"]), 1)
        self.assertEqual(current["reassessed_findings"][0]["effective_status"], "requires_review")
        self.assertEqual(self.audit_rows(), journal, "Read-time staleness must not rewrite the journal")
        self.assertEqual(self.evidence_snapshot(), sources_after_change)
        for table, rows in identity.items():
            self.assertEqual(self.f.rows(table), rows, table)
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        fresh = reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                                 fixture.REG_A, self.key, purchase, docs)
        self.assertEqual(fresh["effective_status"], original_status)
        self.assertFalse(self.item()["reassessment"].get("stale", False))
        self.assertEqual(len(self.audit_rows()), 2)
        self.assertEqual(self.audit_rows()[0], journal[0])

    def test_persisted_confirmed_event_becomes_review_required_when_sources_change(self):
        self.assert_persisted_source_staleness("yes")

    def test_persisted_superseded_event_becomes_review_required_when_sources_change(self):
        self.assert_persisted_source_staleness("no")

    def test_original_replaced_or_missing_at_same_path_invalidates_persisted_reassessment(self):
        path = self.prepare_stored_original("no")
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                         fixture.REG_A, self.key, purchase, docs)
        journal, original_rows = self.audit_rows(), self.evidence_snapshot()
        self.assertEqual(self.item()["effective_status"], "superseded")
        thematic.thematic_history_records(self.ns, [BATCH_A])
        cache_stamp = thematic._HISTORY_COUNT_CACHE[BATCH_A][0]
        # Replace bytes at the saved path without touching any database row.
        path.write_bytes(path.read_bytes().replace("Нет".encode(), "Да!".encode()))
        replaced = self.item()
        self.assertEqual(replaced["effective_status"], "requires_review")
        self.assertTrue(replaced["reassessment"]["stale"])
        thematic.thematic_history_records(self.ns, [BATCH_A])
        self.assertNotEqual(thematic._HISTORY_COUNT_CACHE[BATCH_A][0], cache_stamp)
        path.unlink()
        missing = self.item()
        self.assertEqual(missing["effective_status"], "requires_review")
        self.assertTrue(missing["reassessment"]["stale"])
        self.assertEqual(self.audit_rows(), journal)
        self.assertEqual(self.evidence_snapshot(), original_rows)

    def test_unavailable_persisted_sources_cannot_remain_confirmed(self):
        self.prepare_stored_original("yes")
        purchase, docs = asyncio.run(reassessment.collect_sources(self.ns, fixture.REG_A))
        reassessment.record_reassessment(self.ns, fixture.EMPLOYEE_A, BATCH_A,
                                         fixture.REG_A, self.key, purchase, docs)
        journal = self.audit_rows()
        with patch.object(reassessment, "source_token", side_effect=sqlite3.OperationalError("fixture read failed")):
            stale = self.item()
        self.assertEqual(stale["effective_status"], "requires_review")
        self.assertTrue(stale["reassessment"]["stale"])
        self.assertEqual(stale["reassessment"]["original_effective_status"], "confirmed")
        self.assertEqual(self.audit_rows(), journal)

    def test_thematic_card_preserves_revised_evidence_and_counts_only_active(self):
        path = f"/thematic/card/{BATCH_A}/{fixture.REG_A}"
        headers = self.f.headers(fixture.EMPLOYEE_A)
        original = self.f.client.get(path, headers=headers)
        self.assertEqual(original.status_code, 200, original.text)
        self.assertIn('<h2>Нарушения: 2</h2>', original.text)
        self.assertIn('data-finding-review="' + self.key + '"', original.text)
        self.assertIn('data-review-batch="' + BATCH_A + '"', original.text)
        self.assertIn("Перепроверить признак МСП", original.text)
        self.assertEqual(self.post("no")[0].status_code, 200)
        revised = self.f.client.get(path, headers=headers)
        self.assertEqual(revised.status_code, 200, revised.text)
        self.assertIn('<h2>Нарушения: 1</h2>', revised.text)
        self.assertIn("Первоначальный вывод — пересмотрен", revised.text)
        self.assertIn("Нет, участие не ограничено МСП (по перепроверке)", revised.text)
        self.assertIn(self.item()["reassessment"]["reason"], revised.text)
        self.assertIn(TITLE, revised.text)
        self.assertIn(self.initial["evidence"], revised.text)
        self.assertIn('data-finding-review="' + self.key + '"', revised.text)
        self.assertIn("no-store", revised.headers["cache-control"])

    def test_printed_card_labels_correction_and_excludes_it_from_confirmed_count(self):
        self.assertEqual(self.post("no")[0].status_code, 200)
        printed = self.f.client.get(f"/thematic/card/{BATCH_A}/{fixture.REG_A}?print=1",
                                    headers=self.f.headers(fixture.EMPLOYEE_A))
        self.assertEqual(printed.status_code, 200, printed.text)
        self.assertIn("Подтверждённых нарушений: 1", printed.text)
        self.assertIn("Нет, участие не ограничено МСП (по перепроверке)", printed.text)
        self.assertIn("Пересмотренный вывод / требуется проверка.", printed.text)
        self.assertIn(self.item()["reassessment"]["reason"], printed.text)
        self.assertIn(TITLE, printed.text)
        self.assertIn(self.initial["evidence"], printed.text)
        self.assertIn(thematic_fixture.TITLE, printed.text)
        self.assertNotIn("Перепроверить признак МСП", printed.text)
        self.assertNotIn("Направить на согласование руководителю", printed.text)

    def test_staff_campaign_filter_precedes_cross_batch_dedup_and_retains_revised_evidence(self):
        from scripts import v370_manager_campaigns as campaigns

        class CampaignIdentity(fixture.SQLiteIdentity):
            def execute(self, sql, params=()):
                # The production reader uses PostgreSQL ANY for exact visible IDs.
                if "finding_key=ANY(%s)" in sql:
                    keys = params[-1]
                    sql = sql.replace("finding_key=ANY(%s)",
                                      "finding_key IN (" + ",".join("%s" for _ in keys) + ")")
                    params = (*params[:-1], *keys)
                return super().execute(sql, params)

        self.f.stack.enter_context(patch.object(campaigns, "connect",
            lambda: CampaignIdentity(self.f.identity_path, self.f.calls)))
        with sqlite3.connect(self.f.identity_path) as db:
            db.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value)))
            for table in ("account_manager_campaign_targets", "account_staff_finding_decisions"):
                ddl = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\(.*?\n\);",
                                fixture.schema.SCHEMA, re.S).group()
                db.execute(ddl.replace("TIMESTAMPTZ", "TIMESTAMP").replace(" ~ ", " REGEXP "))
            for index, (batch, owner, manager) in enumerate((
                (BATCH_A, fixture.EMPLOYEE_A, fixture.MANAGER_A),
                (BATCH_B, fixture.EMPLOYEE_B, fixture.MANAGER_B),
                (BATCH_C, fixture.EMPLOYEE_A, fixture.MANAGER_A))):
                db.execute("INSERT INTO account_manager_campaign_targets(id,manager_id,user_id,batch_id,title,"
                    "categories,legal_forms,target_violations,status) VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(UUID(int=1000 + index)), str(manager), str(owner), batch, "Проверка обеспечения",
                     '["protocols","security"]', '["ООО"]', 1, "active"))
        self.ns["app"].include_router(campaigns.staff_router)

        def reader(user=fixture.EMPLOYEE_A):
            response = self.f.client.get("/me/campaign-goals/violations", headers=self.f.headers(user))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("no-store", response.headers["cache-control"])
            return response.json()

        before = reader()
        self.assertEqual(before["count"], 2, "Identical findings across owned campaigns must deduplicate")
        foreign_before = reader(fixture.EMPLOYEE_B)
        self.assertEqual(self.post("no")[0].status_code, 200)
        corrected = reader()
        self.assertEqual(corrected["count"], 2)
        active_msp = [row for row in corrected["findings"] if row["title"] == TITLE]
        self.assertEqual(len(active_msp), 1)
        self.assertEqual(active_msp[0]["batch_id"], BATCH_C,
                         "The still-confirmed campaign must supply the active evidence link")
        self.assertEqual(len(corrected["reassessed_findings"]), 1)
        revised = corrected["reassessed_findings"][0]
        self.assertEqual((revised["batch_id"], revised["finding_key"], revised["effective_status"]),
                         (BATCH_A, self.key, "superseded"))
        self.assertEqual(revised["evidence"], self.initial["evidence"])
        self.assertEqual(reader(fixture.EMPLOYEE_B), foreign_before)
        # Equal-quality duplicates use different representatives in ascending
        # canonical cards and the descending bounded staff slice. The revision
        # must follow the proven same incident without spilling into batch C.
        duplicate = self.initial["evidence"].replace(" ", "\u00a0", 1)
        with sqlite3.connect(self.f.evidence_path) as db:
            db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) "
                "VALUES(?,?,?,?,?,?,?,?,?)", (BATCH_A, fixture.REG_A, "security", TITLE,
                self.initial["law"], duplicate, CODE, self.initial["document"], self.initial["source_url"]))
        after_duplicate = self.evidence_snapshot()
        self.assertEqual(self.item()["finding_key"], self.key, "Canonical card keeps the original equal-quality representative")
        duplicated = reader()
        self.assertEqual(duplicated["count"], 2)
        self.assertEqual(next(row for row in duplicated["findings"] if row["title"] == TITLE)["batch_id"], BATCH_C)
        duplicate_revision = duplicated["reassessed_findings"][0]
        self.assertNotEqual(duplicate_revision["finding_key"], self.key)
        self.assertEqual(duplicate_revision["reassessment"]["id"], revised["reassessment"]["id"])
        self.assertEqual(duplicate_revision["effective_status"], "superseded")
        self.assertEqual(self.post("no", batch=BATCH_C)[0].status_code, 200)
        both_revised = reader()
        self.assertEqual(both_revised["count"], 1)
        self.assertEqual([row["title"] for row in both_revised["findings"]], [thematic_fixture.TITLE])
        self.assertEqual({row["batch_id"] for row in both_revised["reassessed_findings"]}, {BATCH_A, BATCH_C})
        self.assertEqual(self.evidence_snapshot(), after_duplicate)

    def test_staff_duplicate_overlay_never_matches_other_rule_incident_batch_or_purchase(self):
        from scripts.v384_thematic_findings import canonical_item
        self.assertEqual(self.post("no")[0].status_code, 200)
        duplicate = canonical_item(fixture.REG_A,
            {**self.initial, "evidence": self.initial["evidence"].replace(" ", "\u00a0", 1)})
        self.assertNotEqual(duplicate["finding_key"], self.key)
        changed_fact = canonical_item(fixture.REG_A,
            {**self.initial, "evidence": self.initial["evidence"].replace("5%", "4%")})
        changed_rule = {**duplicate, "code": "223-OTHER-SECURITY-RULE"}
        with sqlite3.connect(self.f.evidence_path) as db:
            db.row_factory = sqlite3.Row
            exact = reassessment.apply_reassessments(db, BATCH_A, fixture.REG_A, [duplicate])
            self.assertEqual(exact[0]["effective_status"], "confirmed",
                             "Canonical card and manager paths must retain exact-key matching")
            bounded = reassessment.apply_reassessments(db, BATCH_A, fixture.REG_A,
                [duplicate, changed_fact, changed_rule], same_incident=True)
            self.assertEqual([row["effective_status"] for row in bounded],
                             ["superseded", "confirmed", "confirmed"])
            for batch, reg in ((BATCH_C, fixture.REG_A), (BATCH_A, fixture.REG_B)):
                separate = reassessment.apply_reassessments(db, batch, reg, [duplicate], same_incident=True)
                self.assertEqual(separate[0]["effective_status"], "confirmed")

    def test_journal_rejects_update_and_delete_without_changing_source_or_audit(self):
        self.assertEqual(self.post("no")[0].status_code, 200)
        journal = self.audit_rows()
        for statement in ("UPDATE thematic_finding_reassessments SET reason='changed'",
                          "DELETE FROM thematic_finding_reassessments"):
            with sqlite3.connect(self.f.evidence_path) as db:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    db.execute(statement)
        self.assertEqual(self.audit_rows(), journal)
        self.assertEqual(self.evidence_snapshot(), self.original_rows)


if __name__ == "__main__":
    unittest.main()
