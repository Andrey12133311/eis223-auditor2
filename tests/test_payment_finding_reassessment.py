"""Payment conclusions change without deleting evidence or approval history.

Use real SQLite persistence, saved readers, counters and thematic memo gating.
The supplied confirmed output is synthetic; receipt/legal validation is covered
by the legal-pipeline tests, not reimplemented in these persistence regressions.
"""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException
import payment_finding_reassessment as assessment
import v304_official_memo_odt as memo
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import v352_dashboard_truth as truth
import v384_thematic_findings as sources


REG = "32616410694"
BATCH = "a" * 24
ENGINE = "current-payment-engine"
PAYMENT = {"category": "sme_payment", "code": "1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS",
    "kind": "violation", "title": "Установлен срок оплаты свыше семи рабочих дней",
    "law": "Постановление Правительства РФ № 1352, Положение, п. 28",
    "evidence": "Оплата производится в течение 30 рабочих дней со дня подписания акта приемки.",
    "document": "Документация.7z / проект-договора.docx",
    "source_url": "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "1" * 32}


class PaymentAssessmentStorage(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "audit.sqlite"
        self.ns = {"conn": self.connect}
        self.engine = patch.object(assessment, "_engine", return_value=ENGINE)
        self.engine.start()
        self.addCleanup(self.engine.stop)
        prior_state, prior_tick = dict(thematic.WORKER_STATE), thematic.tick
        def restore():
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(prior_state)
            thematic.tick = prior_tick
            thematic._HISTORY_COUNT_CACHE.clear()
        self.addCleanup(restore)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,published_at TEXT,
                    method TEXT,card_fields_json TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,local_path TEXT,
                    status TEXT,metadata_json TEXT);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,kind TEXT,
                    title TEXT,law TEXT,evidence TEXT,document TEXT,source_url TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,field_path TEXT,status TEXT,
                    item TEXT,law TEXT,evidence TEXT,document TEXT,source_url TEXT);
                CREATE TABLE approved_decisions(finding_key TEXT,decision TEXT,comment TEXT);
            """)
        thematic.init(self.ns)
        fast.install(self.ns)
        with self.connection() as db:
            db.execute("INSERT INTO purchases VALUES(?,'ООО Пример','2026-10-19',"
                       "'Конкурс только для субъектов МСП','{}')", (REG,))
            db.execute("INSERT INTO docs VALUES(1,?,'project.docx','/fixture/project.docx',"
                       "'checked','{\"sha256\":\"original-30\"}')", (REG,))
            db.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) "
                       "VALUES(?,'[\"sme_payment\"]','2026-11-06','active','[\"ООО\"]')", (BATCH,))
            db.execute("INSERT INTO thematic_items(batch_id,reg,status,updated_at,reservation_token) "
                       "VALUES(?,?,'running','fixture','lease-a')", (BATCH, REG))
            db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (BATCH, REG, *(PAYMENT[key] for key in
                       ("category", "title", "law", "evidence", "code", "document", "source_url"))))
            db.execute("INSERT INTO findings(reg_number,code,kind,title,law,evidence,document,source_url) "
                       "VALUES(?,?,?,?,?,?,?,?)", (REG, *(PAYMENT[key] for key in
                       ("code", "kind", "title", "law", "evidence", "document", "source_url"))))
            db.execute("INSERT INTO approved_decisions VALUES(?,'approved','Историческое решение')",
                       (sources.canonical_item(REG, PAYMENT)["finding_key"],))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=.05)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def retained(self):
        with self.connection() as db:
            return {table: [tuple(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")]
                    for table in ("findings", "thematic_findings", "approved_decisions")}

    def event(self, findings, scope="general", state="completed"):
        with self.connection() as db:
            return assessment.prepare_assessment(REG, findings, scope=scope,
                batch=BATCH if scope == "thematic" else "", engine_signature=ENGINE,
                source_version="verified-documents-a", db_token=assessment.db_source_token(db, REG), state=state)

    def record(self, findings, scope="general", state="completed"):
        event = self.event(findings, scope, state)
        with self.connection() as db:
            result = assessment.record_assessment(db, event)
            if scope == "thematic":
                db.execute("UPDATE thematic_items SET status=?,last_semantic_version=?,last_engine_signature=? "
                           "WHERE batch_id=? AND reg=?", (state, event["source_version"], ENGINE, BATCH, REG))
            return result

    def item(self):
        with self.connection() as db:
            db.execute("UPDATE thematic_items SET status='running',reservation_token='lease-a' "
                       "WHERE batch_id=? AND reg=?", (BATCH, REG))
            campaign = db.execute("SELECT * FROM thematic_batches WHERE id=?", (BATCH,)).fetchone()
            return {"batch": BATCH, "reg": REG, "ready": 1, "doc_id": 1,
                    "categories": ["sme_payment"], "reservation_token": "lease-a",
                    "campaign_guard": fast._campaign_identity(campaign),
                    "source_version": "verified-documents-a", "semantic_version": "verified-documents-a",
                    "engine_signature": ENGINE, "_payment_db_token": assessment.db_source_token(db, REG),
                    "_payment_sources_stable": True}

    def thematic_rows(self):
        with self.connection() as db:
            return sources.read_thematic_items(db, BATCH, REG)

    def test_legacy_payment_is_review_without_erasing_evidence_or_approval(self):
        before = self.retained()
        rows = self.thematic_rows()
        self.assertEqual(rows[0]["effective_status"], "requires_review")
        self.assertTrue(rows[0]["presentation_gaps"])
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG]), {REG: 0})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM " + assessment.TABLE).fetchone()[0], 0)
        self.assertEqual(self.retained(), before)
        with self.assertRaises(HTTPException) as failure:
            memo._read_thematic_purchase(self.ns, BATCH, REG)
        self.assertEqual(failure.exception.status_code, 409)

    def test_fresh_exact_finding_reconfirms_existing_row_before_deduplication(self):
        before = self.retained()
        self.assertEqual(fast._persist(self.ns, [(self.item(), "completed", [deepcopy(PAYMENT)], "checked")]), 1)
        self.assertEqual(self.thematic_rows()[0]["effective_status"], "confirmed")
        self.assertEqual(len(memo._read_thematic_purchase(self.ns, BATCH, REG)[1]), 1)
        self.assertEqual(self.retained(), before)
        self.record([PAYMENT])
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG]), {REG: 1})

    def test_recheck_without_old_payment_finding_revises_saved_card_and_memo(self):
        self.record([PAYMENT], "thematic")
        before = self.retained()
        self.assertEqual(self.thematic_rows()[0]["effective_status"], "confirmed")
        # The legal stage now treats 30/7-day alternatives as unresolved. Its
        # fresh confirmed output is empty, while old evidence stays visible.
        self.assertEqual(fast._persist(self.ns, [(self.item(), "completed", [], "checked")]), 1)
        row = self.thematic_rows()[0]
        self.assertEqual(row["effective_status"], "requires_review")
        self.assertFalse(row["payment_reassessment"]["stale"])
        self.assertIn("не подтверждён", row["payment_reassessment"]["reason"])
        self.assertEqual(self.retained(), before)
        with self.assertRaises(HTTPException) as failure:
            memo._read_thematic_purchase(self.ns, BATCH, REG)
        self.assertEqual(failure.exception.status_code, 409)

    def test_partial_read_cannot_reconfirm_even_a_returned_old_finding(self):
        self.record([PAYMENT], "thematic")
        self.assertEqual(fast._persist(self.ns, [(self.item(), "retry", [deepcopy(PAYMENT)], "partial")]), 1)
        self.assertEqual(self.thematic_rows()[0]["effective_status"], "requires_review")
        self.record([PAYMENT], state="retry")
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG]), {REG: 0})

    def test_similar_heading_or_shared_url_does_not_confirm_another_clause(self):
        alternate = {**PAYMENT, "evidence": PAYMENT["evidence"].replace("30", "15")}
        self.record([alternate], "thematic")
        self.assertEqual(self.thematic_rows()[0]["effective_status"], "requires_review")
        self.record([{**PAYMENT, "document": "Другой лот.docx"}])
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG]), {REG: 0})

    def test_equal_payment_quotes_keep_current_and_revised_sources_separate(self):
        current = {**PAYMENT, "document": "Актуальный договор.docx",
                   "source_url": PAYMENT["source_url"].replace("1" * 32, "2" * 32),
                   # Thematic storage has a historical title/evidence UNIQUE
                   # key. A nonbreaking space makes this a distinct stored
                   # source while keeping its normalized quotation identical.
                   "evidence": PAYMENT["evidence"].replace(" ", "\u00a0", 1)}
        general_current = {**current, "evidence": PAYMENT["evidence"]}
        old_key = sources.canonical_item(REG, PAYMENT)["finding_key"]
        current_key = sources.canonical_item(REG, current)["finding_key"]
        self.assertNotEqual(old_key, current_key)
        # Without current classifications these equal quotes are a normal
        # duplicate; this deliberately exercises the representative-selection risk.
        self.assertEqual(len(truth.collapse_legal_events([PAYMENT, current])), 1)
        with self.connection() as db:
            db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (BATCH, REG, *(current[key] for key in
                       ("category", "title", "law", "evidence", "code", "document", "source_url"))))
            db.execute("INSERT INTO findings(reg_number,code,kind,title,law,evidence,document,source_url) "
                       "VALUES(?,?,?,?,?,?,?,?)", (REG, *(general_current[key] for key in
                       ("code", "kind", "title", "law", "evidence", "document", "source_url"))))
        before = self.retained()
        self.record([current], "thematic")
        self.record([general_current])
        rows = self.thematic_rows()
        self.assertEqual({item["finding_key"]: item["effective_status"] for item in rows},
                         {old_key: "requires_review", current_key: "confirmed"})
        self.assertEqual([item["source_representations"] for item in rows], [1, 1])
        self.assertEqual(sum(item["effective_status"] == "confirmed" for item in rows), 1)
        self.assertEqual([item["finding_key"] for item in
                          memo._read_thematic_purchase(self.ns, BATCH, REG)[1]], [current_key])
        self.assertEqual([item["document"] for item in memo._read_purchase(self.ns, REG)[1]],
                         [current["document"]])
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG]), {REG: 1})
            classified = assessment.apply_general_reassessments(db, REG, [PAYMENT, general_current])
        for values in (classified, list(reversed(classified))):
            collapsed = truth.collapse_legal_events(values)
            self.assertEqual({item["document"]: item["effective_status"] for item in collapsed},
                             {PAYMENT["document"]: "requires_review", current["document"]: "confirmed"})
            self.assertEqual([item["raw_count"] for item in collapsed], [1, 1])
        self.assertEqual(self.retained(), before)
        self.assertEqual(before["approved_decisions"], [(old_key, "approved", "Историческое решение")])

    def test_payment_status_boundary_preserves_existing_same_status_deduplication(self):
        first = {**PAYMENT, "effective_status": "confirmed", "payment_reassessment": {"id": 1}}
        duplicate = {**first, "document": "Копия проекта договора.docx"}
        collapsed = truth.collapse_legal_events([first, duplicate])
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["raw_count"], 2)
        self.assertEqual(sources.canonical_item(REG, collapsed[0])["finding_key"],
                         sources.canonical_item(REG, PAYMENT)["finding_key"])
        # Existing nonpayment overlays and their canonical grouping are not
        # changed by this payment-specific boundary.
        unrelated = [{**PAYMENT, "code": "223-OTHER-RULE", "effective_status": status}
                     for status in ("requires_review", "confirmed")]
        self.assertEqual(len(truth.collapse_legal_events(unrelated)), 1)

    def test_manual_labels_use_the_same_current_payment_evidence_as_counts(self):
        from scripts import v366_account_manual as manual
        entries = [{"reg_number": REG, "job_id": "", "last_started_at": ""}]
        with self.connection() as db:
            db.execute("ALTER TABLE purchases ADD COLUMN documents_found INTEGER DEFAULT 1")
            db.execute("ALTER TABLE purchases ADD COLUMN documents_checked INTEGER DEFAULT 1")
            labels = {REG: ["Устаревшая подпись"]}
            self.assertEqual(truth.load_counts(db, [REG], labels=labels), {REG: 0})
            self.assertEqual(labels, {REG: []})
        old = manual._read_only_status(self.ns, entries)[0]
        self.assertEqual((old["violations"], old["violation_types"]), (0, []))

        current = {**PAYMENT, "title": "Подтверждён иной применимый срок оплаты",
                   "document": "Актуальный договор.docx",
                   "source_url": PAYMENT["source_url"].replace("1" * 32, "2" * 32)}
        with self.connection() as db:
            db.execute("INSERT INTO findings(reg_number,code,kind,title,law,evidence,document,source_url) "
                       "VALUES(?,?,?,?,?,?,?,?)", (REG, *(current[key] for key in
                       ("code", "kind", "title", "law", "evidence", "document", "source_url"))))
        self.record([current])
        before = self.retained()
        with self.connection() as db:
            labels = {}
            self.assertEqual(truth.load_counts(db, [REG], labels=labels), {REG: 1})
            self.assertEqual(labels, {REG: [current["title"]]})
            self.assertEqual(truth.load_counts(db, [REG]), {REG: 1})
        with patch("builtins.open", side_effect=AssertionError("original read from manual status")):
            row = manual._read_only_status(self.ns, entries)[0]
        self.assertEqual((row["violations"], row["violation_types"]), (1, [current["title"]]))
        self.assertNotIn(PAYMENT["title"], row["violation_types"])
        self.assertEqual(self.retained(), before)

    def test_rule_change_or_same_id_document_edit_invalidates_current_classification(self):
        self.record([PAYMENT], "thematic")
        with self.connection() as db:
            first_stamp = assessment.revision(db, BATCH)
        with patch.object(assessment, "_engine", return_value="new-engine"):
            self.assertEqual(self.thematic_rows()[0]["effective_status"], "requires_review")
        with self.connection() as db:
            db.execute("UPDATE docs SET metadata_json='{\"sha256\":\"replacement-7\"}' WHERE id=1")
            second_stamp = assessment.revision(db, BATCH)
        row = self.thematic_rows()[0]
        self.assertEqual(row["effective_status"], "requires_review")
        self.assertTrue(row["payment_reassessment"]["stale"])
        self.assertNotEqual(first_stamp, second_stamp)

    def test_saved_views_do_not_read_original_files(self):
        self.record([PAYMENT], "thematic")
        self.record([PAYMENT])
        with patch("audit_source_version.source_version", side_effect=AssertionError("file reader used")), \
             patch("audit_source_version.semantic_source_version", side_effect=AssertionError("file reader used")), \
             patch("builtins.open", side_effect=AssertionError("original read from request handler")):
            self.assertEqual(self.thematic_rows()[0]["effective_status"], "confirmed")
            with self.connection() as db:
                self.assertEqual(truth.load_counts(db, [REG]), {REG: 1})

    def test_journal_is_idempotent_append_only_and_scoped(self):
        event = self.event([PAYMENT], "thematic")
        with self.connection() as db:
            first = assessment.record_assessment(db, event)
            self.assertEqual(assessment.record_assessment(db, event), first)
            self.assertEqual(assessment.apply_general_reassessments(db, REG, [PAYMENT])[0]["effective_status"], "requires_review")
            self.assertEqual(assessment.apply_thematic_reassessments(db, "b" * 24, REG, [PAYMENT])[0]["effective_status"], "requires_review")
            for sql in ("UPDATE " + assessment.TABLE + " SET reason='changed'", "DELETE FROM " + assessment.TABLE):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_legacy_check_aliases_and_unrelated_rules_keep_independent_counts(self):
        check = {"field_path": PAYMENT["code"], "item": PAYMENT["title"], "status": "violation",
                 **{key: PAYMENT[key] for key in ("law", "evidence", "document", "source_url")}}
        self.record([check])
        with self.connection() as db:
            current = assessment.apply_general_reassessments(db, REG, [check])[0]
            self.assertEqual(current["effective_status"], "confirmed")
            unrelated = {**PAYMENT, "code": "223-4-12-PROTOCOL-PUBLICATION-LATE"}
            self.assertEqual(assessment.apply_general_reassessments(db, REG, [unrelated]), [unrelated])

    def test_evidence_arriving_during_worker_pass_cannot_reconfirm_old_payment(self):
        self.record([PAYMENT], "thematic")
        item = self.item()
        with self.connection() as db:
            db.execute("UPDATE thematic_items SET status='queued' WHERE batch_id=?", (BATCH,))
        async def review(ns, reg, categories):
            with self.connection() as db:
                db.execute("UPDATE docs SET metadata_json='{\"sha256\":\"replacement-7\"}' WHERE id=1")
            return "completed", [deepcopy(PAYMENT)]
        with patch.object(fast, "_watermark", return_value=("verified-documents-a", ENGINE)), \
             patch.object(thematic, "independent_review", side_effect=review):
            result = asyncio.run(fast._one(self.ns, item, asyncio.Semaphore(1)))
        self.assertFalse(result[0].get("_payment_sources_stable"))
        self.assertEqual(fast._persist(self.ns, [result]), 1)
        self.assertEqual(self.thematic_rows()[0]["effective_status"], "requires_review")

    def test_transaction_rollback_does_not_publish_assessment_without_evidence(self):
        with self.connection() as db:
            before = db.execute("SELECT COUNT(*) FROM " + assessment.TABLE).fetchone()[0]
            db.execute("CREATE TRIGGER break_thematic_completion BEFORE UPDATE ON thematic_items "
                       "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            fast._persist(self.ns, [(self.item(), "completed", [deepcopy(PAYMENT)], "checked")])
        with self.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM " + assessment.TABLE).fetchone()[0], before)
        self.assertEqual(self.thematic_rows()[0]["effective_status"], "requires_review")


class PaymentAssessmentAccountRoutes(unittest.TestCase):
    def test_revised_payment_retains_real_manager_approval_and_blocks_resubmission(self):
        import test_thematic_finding_submissions as fixture
        import test_v382_finding_submissions as accounts
        test = fixture.ThematicFindingTests()
        test.setUp()
        self.addCleanup(test.doCleanups)
        old_tick, old_state = thematic.tick, dict(thematic.WORKER_STATE)
        def restore():
            thematic.tick = old_tick
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(old_state)
        self.addCleanup(restore)
        fast.install(test.ns)
        reg, batch = accounts.REG_A, fixture.BATCH_A
        with sqlite3.connect(test.f.evidence_path) as db:
            db.execute("UPDATE thematic_batches SET categories='[\"sme_payment\"]' WHERE id=?", (batch,))
            db.execute("UPDATE thematic_findings SET category='sme_payment',title=?,law=?,evidence=?,code=?,"
                       "document=?,source_url=? WHERE batch_id=? AND category='protocols'",
                       (*(PAYMENT[key] for key in ("title", "law", "evidence", "code", "document", "source_url")), batch))

        def record(findings):
            with sqlite3.connect(test.f.evidence_path) as db:
                event = assessment.prepare_assessment(reg, findings, scope="thematic", batch=batch,
                    engine_signature=ENGINE, source_version="verified-documents-a",
                    db_token=assessment.db_source_token(db, reg))
                assessment.record_assessment(db, event)
                db.execute("UPDATE thematic_items SET status='completed',last_semantic_version=?,"
                           "last_engine_signature=? WHERE batch_id=? AND reg=?",
                           (event["source_version"], ENGINE, batch, reg))

        with patch.object(assessment, "_engine", return_value=ENGINE):
            record([PAYMENT])
            original = test.get().json()["findings"][0]
            key = original["finding_key"]
            self.assertEqual(original["effective_status"], "confirmed")
            self.assertEqual(test.submit(key).status_code, 200)
            approved = test.f.decision(key=key, version=1)
            self.assertEqual(approved.status_code, 200, approved.text)
            decisions = test.f.rows("account_manager_finding_reviews")
            submissions = test.f.rows("account_finding_submissions")
            record([])
            current = test.get().json()["findings"][0]
            self.assertEqual(current["effective_status"], "requires_review")
            self.assertEqual(current["finding_key"], key)
            self.assertEqual(current["evidence"], original["evidence"])
            self.assertEqual(current["submission"]["status"], "approved")
            queued = next(row for row in test.f.queue() if row["finding_key"] == key)
            self.assertEqual((queued["status"], queued["effective_status"]), ("approved", "requires_review"))
            self.assertEqual(test.submit(key).status_code, 409)
            self.assertEqual(test.f.rows("account_manager_finding_reviews"), decisions)
            self.assertEqual(test.f.rows("account_finding_submissions"), submissions)


if __name__ == "__main__":
    unittest.main()
