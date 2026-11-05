"""Saved explicit dates survive card, audit and inspection projections."""
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from saved_eis_dates import explicit_date, recovered_date, with_saved_dates
from v343_fast_cards import recovered_submission_start, render

START = "14.10.2026 (МСК+6)"
END = "22.10.2026 09:00 (МСК+6)"


class SavedEisDateTests(unittest.TestCase):
    def row(self, card):
        return {"submission_start": "", "card_fields_json": json.dumps(card, ensure_ascii=False)}

    def test_explicit_card_date_recovery(self):
        self.assertEqual(recovered_submission_start(self.row({"submissionStart": START})), START)

    def test_real_saved_summary_schema(self):
        row = self.row({"summary": {"submission_start": START, "submission_end": END,
                                  "published": "13.10.2026"}})
        result = with_saved_dates(row)
        self.assertEqual(result["submission_start"], START)
        self.assertEqual(result["submission_end"], END)
        self.assertEqual(result["published"], "13.10.2026")
        self.assertEqual(row["submission_start"], "")

    def test_dictionary_card_schema(self):
        self.assertEqual(recovered_submission_start({"card_fields_json": {"summary": {
            "submission_start": START}}}), START)

    def test_current_summary_precedes_older_page(self):
        row = self.row({"summary": {"submission_start": START},
            "pairs": [["common: Дата начала срока подачи заявок", "12.10.2026"]]})
        self.assertEqual(recovered_submission_start(row), START)

    def test_exact_labelled_common_page_pairs(self):
        row = self.row({"pages": {"common": {"status": "ok", "pairs": [
            ["Дата начала срока подачи заявок", START],
            ["Дата и время окончания срока подачи заявок (по местному времени заказчика)", END]]}}})
        self.assertEqual(recovered_submission_start(row), START)
        self.assertEqual(recovered_date(row, "submission_end"), END)

    def test_saved_prefixed_pairs(self):
        row = self.row({"pairs": [["common: Дата начала срока подачи заявок", START]]})
        self.assertEqual(recovered_submission_start(row), START)

    def test_historical_change_page_is_not_current_date(self):
        row = self.row({"pairs": [["changes: Дата начала срока подачи заявок", START]],
            "pages": {"changes": {"status": "ok", "pairs": [["Дата начала срока подачи заявок", START]]}}})
        self.assertEqual(recovered_submission_start(row), "")

    def test_no_inferred_start_date(self):
        row = {"published_at": "14.10.2026", "submission_end": END,
               "card_fields_json": json.dumps({"publicationDate": "14.10.2026"})}
        self.assertEqual(recovered_submission_start(row), "")

    def test_no_unlabelled_text_or_unrelated_label_date(self):
        for card in ({"visible_text": START}, {"pairs": [["Дата размещения извещения", START]]},
                     {"pairs": [["Планируемая дата начала срока подачи заявок", START]]}):
            with self.subTest(card=card):
                self.assertEqual(recovered_submission_start(self.row(card)), "")

    def test_saved_column_has_priority(self):
        self.assertEqual(recovered_submission_start({"submission_start": "2026-10-14",
            "card_fields_json": json.dumps({"submissionStart": "2026-10-15"})}), "2026-10-14")

    def test_official_conflict_cannot_be_backfilled_from_stale_summary(self):
        row = self.row({"summary": {"submission_start": START}, "fact_evidence": {
            "submission_start": {"source": "official_eis_context_conflict", "value": None}}})
        self.assertEqual(recovered_submission_start(row), "")
        row["submission_start"] = "2026-10-14"
        self.assertEqual(recovered_submission_start(row), "")

    def test_conflicting_pairs_are_unknown(self):
        row = self.row({"pairs": [["common: Дата начала срока подачи заявок", START],
                                 ["common: Дата начала срока подачи заявок", "15.10.2026"]]})
        self.assertEqual(recovered_submission_start(row), "")

    def test_explicit_fact_evidence(self):
        row = self.row({"fact_evidence": {"submission_start": {
            "source": "official_eis_card", "value": START}}})
        self.assertEqual(recovered_submission_start(row), START)

    def test_invalid_dates_and_malformed_saved_data(self):
        for value in ("31.02.2026", "2026-13-01", "14.10.2026 25:00", "14.10.2026 текст", "\\s\\d", 20261014):
            with self.subTest(value=value):
                self.assertEqual(recovered_submission_start(self.row({"submissionStart": value})), "")
        for raw in ("{", "[]", "null", 42):
            self.assertEqual(recovered_submission_start({"card_fields_json": raw}), "")
        for pairs in (42, True, {"label": START}, "invalid"):
            self.assertEqual(recovered_submission_start(self.row({"pairs": pairs})), "")
            self.assertEqual(recovered_submission_start(self.row({"pages": {
                "common": {"status": "ok", "pairs": pairs}}})), "")

    def test_iso_timezone_and_source_local_timezone_are_preserved(self):
        for value in (START, END, "2026-10-14T00:00:00+09:00", "2026-10-14T00:00:00.000Z", "2026-10-14"):
            with self.subTest(value=value):
                self.assertEqual(explicit_date(value), value)

    def test_audit_adapter_and_inspection_export_use_same_date(self):
        from v332_all_procurements_audit import as_procurement
        from vp_inspection_export import metadata
        from vp_inspection_engine import evaluate
        row = {**self.row({"summary": {"submission_start": START, "submission_end": END,
                                      "published": "13.10.2026"}}),
               "reg_number": "32616469206", "title": "Тестовая закупка"}
        p = as_procurement(row)
        self.assertEqual(p["submission_start"], START)
        self.assertEqual(metadata(row)["SUBMISSION_START"], START)
        self.assertEqual(evaluate(p, [])["metadata"]["submission_start"], START)
        self.assertEqual(metadata(row)["PUBLISHED"], "13.10.2026")

    def test_fast_card_renders_saved_start_and_publication(self):
        row = {**self.row({"summary": {"submission_start": START, "published": "13.10.2026"}}),
               "reg_number": "32616469206", "customer": "АО Тест", "title": "Тестовая закупка"}
        result = render("32616469206", (row, [], [], [], 0, 0, 0))
        self.assertIn("Начало подачи заявок:</b> " + START, result)
        self.assertIn("Дата размещения:</b> 13.10.2026", result)

    def test_export_and_engine_do_not_fall_back_to_conflicted_detail(self):
        from vp_inspection_export import metadata
        from vp_inspection_engine import evaluate
        row = {"reg_number": "32616469206", "fact_evidence": {
            "submission_start": {"source": "official_eis_context_conflict"}}}
        detail = {"submissionStart": START}
        self.assertEqual(metadata(row, detail)["SUBMISSION_START"], "")
        self.assertEqual(evaluate(row, [], detail=detail)["metadata"]["submission_start"], "")

    def test_resolved_end_updates_legacy_deadline_and_conflict_clears_it(self):
        row = {"submission_end": END, "deadline": "2026-10-21"}
        self.assertEqual(with_saved_dates(row)["deadline"], END)
        row["fact_evidence"] = {"submission_end": {"source": "official_eis_context_conflict"}}
        resolved = with_saved_dates(row)
        self.assertEqual(resolved["submission_end"], "")
        self.assertEqual(resolved["deadline"], "")


class MetadataDateBackfillTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        from tempfile import TemporaryDirectory
        import v338_card_prices as cards
        import v301_search_jobs as jobs
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "metadata.sqlite"
        self.ns = {"conn": lambda: sqlite3.connect(path)}
        cards.initialize(self.ns)
        jobs._init(self.ns)
        with self.ns["conn"]() as db:
            db.executescript('''DELETE FROM v301_search_jobs;
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,
                price TEXT,price_num REAL,method TEXT,submission_start TEXT,
                submission_end TEXT,published_at TEXT,documents_checked INTEGER,last_seen TEXT);
                CREATE TABLE v332_audit_queue(reg TEXT,campaign TEXT);
                CREATE TABLE thematic_batches(id TEXT,status TEXT);
                CREATE TABLE thematic_items(batch_id TEXT,reg TEXT,status TEXT);
                INSERT INTO thematic_batches VALUES('active','active'),('stopped','stopped');''')
        self.cards = cards

    def purchase(self, reg="32616469206", missing="submission_start", scope="thematic", checked=2):
        from v332_all_procurements_audit import CAMPAIGN
        values = {"price": "1000", "price_num": 1000, "method": "Аукцион",
            "submission_start": START, "submission_end": END, "published_at": "13.10.2026"}
        values[missing] = ""
        with self.ns["conn"]() as db:
            db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?)", (reg, values["price"],
                values["price_num"], values["method"], values["submission_start"], values["submission_end"],
                values["published_at"], checked, "2026-11-05"))
            if scope == "general":
                db.execute("INSERT INTO v332_audit_queue VALUES(?,?)", (reg, CAMPAIGN))
            elif scope in ("thematic", "stopped"):
                db.execute("INSERT INTO thematic_items VALUES(?,?,?)",
                    ("active" if scope == "thematic" else "stopped", reg, "queued"))

    def test_thematic_only_missing_start_enters_existing_official_queue_once(self):
        self.purchase()
        self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 1)
        self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 0)
        with self.ns["conn"]() as db:
            rows = db.execute("SELECT kind,target,parent_id FROM v301_search_jobs").fetchall()
        self.assertEqual(rows, [("reg", "32616469206", "v341-card-facts")])

    def test_general_missing_publication_is_repaired(self):
        self.purchase(missing="published_at", scope="general")
        self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 1)

    def test_stopped_unselected_and_unchecked_rows_are_not_scheduled(self):
        self.purchase("32616469201", scope="stopped")
        self.purchase("32616469202", scope="none")
        self.purchase("32616469203", checked=0)
        self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 0)

    def test_six_hour_spacing_and_three_attempt_budget_are_retained(self):
        self.purchase()
        for attempt in (1, 2, 3):
            self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 1)
            self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 0)
            with self.ns["conn"]() as db:
                self.assertEqual(db.execute("SELECT attempts FROM v338_price_requests").fetchone()[0], attempt)
                db.execute("UPDATE v301_search_jobs SET status='error'")
                db.execute("UPDATE v338_price_requests SET created_at=datetime('now','-7 hours')")
        self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 0)

    def test_remaining_queue_capacity_and_eight_job_batch_cap_are_retained(self):
        import v301_search_jobs as jobs
        for index in range(23):
            jobs._enqueue(self.ns, "reg", str(32616000000 + index))
        for index in range(10):
            self.purchase(str(32616469200 + index))
        self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 1)
        with self.ns["conn"]() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_search_jobs").fetchone()[0], 24)
            db.execute("DELETE FROM v301_search_jobs")
            db.execute("DELETE FROM v338_price_requests")
        self.assertEqual(self.cards.enqueue_missing_prices(self.ns), 8)


if __name__ == "__main__":
    unittest.main()
