"""Offline presentation regressions: queue status never supersedes saved evidence."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fastapi import Response
from scripts import v366_account_manual as manual
from scripts import v375_staff_center as center


class StaffLegalStatusTests(unittest.TestCase):
    def progress(self, state, documents=2, previous=2, violations=1):
        row = {"reg_number": "32616412317", "job_status": "done",
               "documents_found": previous, "documents_checked": previous,
               "violations": violations, "legal_review_state": state,
               "legal_review_documents": documents,
               "legal_review_completed": state == "completed" and documents > 0,
               "card_available": True}
        original = copy.deepcopy(row)
        db = MagicMock()
        db.__enter__.return_value.execute.return_value.fetchall.return_value = [
            (row["reg_number"], "job-1", "2026-10-01", "2026-10-01")]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(eis223_manual_ns={"conn": None})))
        with patch.object(center, "own", return_value="owner"), \
             patch.object(center, "connect", return_value=db), \
             patch.object(manual, "_read_only_status", return_value=[row]):
            result = center.progress(request, Response())["checks"][0]
        self.assertEqual({key: result[key] for key in original}, original,
                         "presentation may only add labels; saved counts/state must remain intact")
        return result

    def test_explicit_queue_states_win_over_old_documents_and_download_completion(self):
        for state, label in (("pending", "Юридическая проверка в очереди"),
                             ("queued", "Юридическая проверка в очереди"),
                             ("running", "Идёт юридическая проверка"),
                             ("processing", "Идёт юридическая проверка"),
                             ("failed", "Ошибка юридической проверки"),
                             ("error", "Ошибка юридической проверки")):
            with self.subTest(state=state):
                row = self.progress(state)
                self.assertEqual(row["stage"], label)
                self.assertIn("результаты предыдущей проверки", row["previous_results_note"])
                self.assertFalse(row["legal_review_completed"])

    def test_initial_queue_does_not_claim_previous_results(self):
        row = self.progress("pending", documents=0, previous=0, violations=0)
        self.assertEqual(row["stage"], "Юридическая проверка в очереди")
        self.assertEqual(row["previous_results_note"], "")

    def test_zero_document_completed_is_not_a_success(self):
        self.assertEqual(self.progress("completed", documents=0)["stage"],
                         "Юридическая проверка не завершена: нет проверенных документов")
        self.assertEqual(self.progress("completed", documents=2)["stage"],
                         "Юридическая проверка завершена")

    def test_unknown_and_unreadable_states_do_not_invent_active_or_completed_audit(self):
        for state in ("unknown", "unrecognized-state"):
            with self.subTest(state=state):
                self.assertEqual(self.progress(state)["stage"], "Юридическая проверка ещё не завершена")
        self.assertEqual(self.progress("no_readable_documents")["stage"],
                         "Не удалось прочитать документы для юридической проверки")


class IntakeLegalStatusTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"status.sqlite"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,documents_found INTEGER,documents_checked INTEGER)")
            db.execute("INSERT INTO purchases VALUES('32616233661',4,3)")
        self.connections=[];self.calls=[]
        connections=self.connections
        class Tracked(sqlite3.Connection):
            closed=False
            def close(self):
                self.closed=True
                return super().close()
        def connect():
            db=sqlite3.connect(self.path,factory=Tracked);connections.append(db);return db
        self.ns={"conn":connect,"_v295_review_state":self.reader}
        self.state={"reg_number":"32616233661","status":"completed","checked_documents":3}

    def reader(self,reg):
        self.assertTrue(all(c.closed for c in self.connections),"close SQL readers before file status reads")
        self.calls.append(reg)
        return dict(self.state)

    def read(self):
        with patch("v352_dashboard_truth.load_counts",return_value={"32616233661":2}):
            return manual._read_only_status(self.ns,[{"reg_number":"32616233661","job_id":None,"last_started_at":""}])[0]

    def test_completed_manual_only_job_is_visible_and_preserves_saved_counts(self):
        row=self.read()
        self.assertEqual(row["legal_review_state"],"completed")
        self.assertTrue(row["legal_review_completed"])
        self.assertEqual((row["documents_found"],row["documents_checked"],row["violations"]),(4,3,2))
        self.assertEqual(self.calls,["32616233661"])

    def test_manual_only_queued_running_and_exhausted_partial_are_truthful(self):
        for status,exhausted,expected in (("queued",False,"queued"),("running",False,"running"),
                                          ("partial",False,"pending"),("partial",True,"failed")):
            with self.subTest(status=status,exhausted=exhausted):
                self.state.update(status=status,retry_exhausted=exhausted)
                row=self.read()
                self.assertEqual(row["legal_review_state"],expected)
                self.assertFalse(row["legal_review_completed"])
                self.assertEqual(row["violations"],2)

    def test_zero_document_or_foreign_status_cannot_claim_completion(self):
        self.state["checked_documents"]=0
        self.assertFalse(self.read()["legal_review_completed"])
        self.state.update(reg_number="32616233662",checked_documents=3)
        self.assertEqual(self.read()["legal_review_state"],"unknown")

    def test_general_queue_takes_precedence_without_reading_intake_state(self):
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.executescript("CREATE TABLE v332_audit_queue(reg TEXT,state TEXT,documents_read INTEGER,updated_at TEXT);"
                             "INSERT INTO v332_audit_queue VALUES('32616233661','running',2,'2026-10-19');")
        row=self.read()
        self.assertEqual(row["legal_review_state"],"running")
        self.assertEqual(self.calls,[])

    def test_general_card_reads_same_intake_status_outside_database(self):
        from scripts import v343_fast_cards as card
        self.state["status"]="queued"
        self.assertEqual(card.read_legal_state(self.ns,"32616233661"),{"state":"queued","documents_read":3})
        self.assertEqual(self.calls,["32616233661"])

    def test_intake_status_read_failure_is_unknown_not_completed(self):
        from scripts import v343_fast_cards as card
        self.ns["_v295_review_state"]=lambda reg:(_ for _ in ()).throw(OSError("temporary storage failure"))
        self.assertEqual(card.read_legal_state(self.ns,"32616233661")["state"],"unknown")


if __name__ == "__main__":
    unittest.main()
