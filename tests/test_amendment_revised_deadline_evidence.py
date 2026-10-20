"""A source-specific revised deadline is retained with its amendment event.

Synthetic originals exercise the actual analyzer and append-only persistence.
The present card deadline is deliberately different from the dated revision.
"""
import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v330_amendments_223 as rules
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
import v299_browser_intake as intake
import test_official_publication_evidence as saved
from v352_dashboard_truth import load_counts

REG = "32610002001"
PURCHASE = {"reg": REG, "method": "Конкурс", "published": "2026-10-01",
            "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32
DECISION = "Дата принятия решения о внесении изменений в документацию: 13.10.2026."
PUBLICATION = "Дата публикации изменений в ЕИС: 13.10.2026."
DEADLINE = "После внесения изменений дата окончания срока подачи заявок: 12.10.2026."
CODE = "223-4-11-CHANGE-AFTER-BID-DEADLINE"


def original(text, name="Изменение № 1.pdf", *, source="official_eis_original"):
    return {"name": name, "status": "checked", "text": text, "url": SOURCE,
            "meta": {"source": source, "source_url": SOURCE,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


class AmendmentRevisedDeadlineEvidence(unittest.TestCase):
    def assess(self, text, purchase=PURCHASE, name="Изменение № 1.pdf", events=()):
        with patch.object(rules, "manifest_changes", return_value=list(events)):
            return rules.evaluation(purchase, [original(text, name)])

    def positive_text(self, deadline=DEADLINE):
        return "Изменение № 1.\n" + DECISION + "\n" + PUBLICATION + "\n" + deadline

    def after_deadline(self, findings):
        return [item for item in findings if item["code"] == CODE]

    def test_explicit_revised_deadline_is_not_lost_without_a_manifest(self):
        findings, _ = self.assess(self.positive_text())
        found = self.after_deadline(findings)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source_url"], SOURCE)
        self.assertEqual(found[0]["document"], "Изменение № 1.pdf")
        for value in ("2026-10-13", "2026-10-12", "После внесения изменений"):
            self.assertIn(value, found[0]["evidence"])
        self.assertNotIn("2026-10-30", found[0]["evidence"])

    def test_explicit_new_deadline_labels_share_the_same_date_only_rule(self):
        for label in ("Новая дата окончания срока подачи заявок",
                      "Новый срок окончания подачи заявок",
                      "Новый срок подачи заявок",
                      "Дата и время окончания подачи заявок после внесения изменений",
                      "Дата окончания срока подачи заявок в новой редакции извещения",
                      "Дата окончания срока подачи заявок по новой редакции документации"):
            with self.subTest(label=label):
                found, _ = self.assess(self.positive_text(label + " | «12» октября 2026"))
                self.assertEqual(len(self.after_deadline(found)), 1)

    def test_unique_reversed_table_and_publication_only_original(self):
        for text in (DEADLINE + "\n" + PUBLICATION + "\n" + DECISION,
                     PUBLICATION + "\n" + DEADLINE):
            with self.subTest(text=text):
                self.assertEqual(len(self.after_deadline(self.assess(text)[0])), 1)

    def test_future_or_same_day_new_deadline_is_not_an_after_deadline_violation(self):
        for deadline in ("13.10.2026", "20.10.2026"):
            with self.subTest(deadline=deadline):
                found, _ = self.assess(self.positive_text(DEADLINE.replace("12.10.2026", deadline)))
                self.assertFalse(self.after_deadline(found))

    def test_old_deadline_cannot_replace_the_explicit_new_date(self):
        text = (DECISION + "\n" + PUBLICATION +
                "\nДо внесения изменений дата окончания срока подачи заявок: 12.10.2026."
                "\nНовая дата окончания срока подачи заявок: 20.10.2026.")
        self.assertFalse(self.after_deadline(self.assess(text)[0]))
        # The explicitly labelled old row may accompany the real new row.
        text = text.replace("заявок: 12.10.2026", "заявок: 20.10.2026", 1)
        text = text.rsplit("20.10.2026", 1)[0] + "12.10.2026."
        self.assertEqual(len(self.after_deadline(self.assess(text)[0])), 1)

    def test_current_card_generic_deadline_and_duration_are_not_substitutes(self):
        for text in (DECISION + "\n" + PUBLICATION,
                     DECISION + "\n" + PUBLICATION + "\nДата окончания срока подачи заявок: 12.10.2026.",
                     DECISION + "\n" + PUBLICATION + "\nНовый срок подачи заявок: 7 календарных дней."):
            with self.subTest(text=text):
                found, _ = self.assess(text, purchase=dict(PURCHASE, submission_end="2026-10-12"))
                self.assertFalse(self.after_deadline(found))

    def test_competing_revised_or_current_dates_remain_review(self):
        for extra in ("Новая дата окончания срока подачи заявок: 20.10.2026.",
                      "Дата окончания срока подачи заявок: 20.10.2026.",
                      "Дата принятия решения о внесении изменений: 12.10.2026.",
                      "Дата публикации изменений в ЕИС: 14.10.2026."):
            with self.subTest(extra=extra):
                found, reviews = self.assess(self.positive_text() + "\n" + extra)
                self.assertFalse(self.after_deadline(found))
                self.assertTrue(any("соответствие" in item["reason"] for item in reviews))

    def test_different_amendment_rows_or_title_identity_never_cross_pair(self):
        for text, name in (("Изменение № 1. " + PUBLICATION + "\nИзменение № 2. " + DEADLINE, "Изменения.pdf"),
                           ("Редакция № 1. " + PUBLICATION + "\nРедакция № 2. " + DEADLINE, "Изменения.pdf"),
                           ("Изменение № 2. " + PUBLICATION + "\n" + DEADLINE, "Изменение № 1.pdf")):
            with self.subTest(text=text):
                found, reviews = self.assess(text, name=name)
                self.assertFalse(self.after_deadline(found))
                self.assertTrue(reviews)

    def test_bounded_proximity_and_no_publication_remain_review(self):
        for text in (PUBLICATION + "x" * 1600 + DEADLINE,
                     DECISION + "\n" + DEADLINE,
                     DECISION + "\nДата публикации извещения в ЕИС: 13.10.2026.\n" + DEADLINE):
            with self.subTest(text=text[:120]):
                found, reviews = self.assess(text)
                self.assertFalse(self.after_deadline(found))
                self.assertTrue(reviews)

    def test_templates_invalid_dates_and_unverified_originals_do_not_confirm(self):
        text = self.positive_text()
        for name in ("Проект изменений.pdf", "Образец.docx", "Пример.pdf"):
            with self.subTest(name=name):self.assertFalse(self.assess(text, name=name)[0])
        for prefix in ("Проект: ", "Пример: ", "Шаблон: "):
            self.assertFalse(self.after_deadline(self.assess(prefix + text)[0]))
        self.assertFalse(self.after_deadline(self.assess(text.replace("12.10.2026", "31.02.2026"))[0]))
        doc = original(text)
        doc["status"] = "unverified"
        with patch.object(rules, "manifest_changes", return_value=[]):
            self.assertFalse(rules.evaluation(PURCHASE, [doc])[0])

    def test_planned_revised_deadline_or_publication_is_not_an_actual_event(self):
        for prefix in ("Плановая", "Предполагаемая", "Предлагаемая", "Ориентировочная",
                       "Рекомендуемая", "Предварительная", "Ожидаемая", "Не утвержденная"):
            for text in (self.positive_text(prefix + " новая дата окончания срока подачи заявок: 12.10.2026."),
                         DECISION + "\n" + prefix + " " + PUBLICATION + "\n" + DEADLINE):
                with self.subTest(prefix=prefix, text=text):
                    findings, reviews = self.assess(text)
                    self.assertFalse(findings)
                    self.assertTrue(reviews)

    def test_embedded_example_section_is_not_a_revised_date(self):
        for heading in ("Пример заполнения формы: ", "Образец заполнения документа:\n",
                        "Шаблон заявки:\n\n", "Например, "):
            for text in (DECISION + "\n" + PUBLICATION + "\n" + heading +
                         "Новая дата окончания срока подачи заявок: 12.10.2026.",
                         DECISION + "\n" + heading + PUBLICATION + "\n" + DEADLINE):
                with self.subTest(heading=heading, text=text):
                    findings, reviews = self.assess(text)
                    self.assertFalse(findings)
                    self.assertTrue(reviews)

    def test_unrelated_distant_example_does_not_poison_actual_event(self):
        text = "Пример заполнения формы: не является сведениями о закупке.\n" + "x" * 500 + "\n" + self.positive_text()
        self.assertEqual(len(self.after_deadline(self.assess(text)[0])), 1)

    def test_unknown_noncompetitive_and_historical_scope_do_not_confirm(self):
        for purchase in (dict(PURCHASE, method="Не установлено"),
                         dict(PURCHASE, method="Единственный поставщик"),
                         dict(PURCHASE, published="2018-10-01")):
            with self.subTest(purchase=purchase):self.assertFalse(self.assess(self.positive_text(), purchase=purchase)[0])

    def test_history_and_original_are_one_same_dated_event(self):
        event = {"kind": "eis_history", "publication_date": date(2026, 10, 13),
                 "submission_end": date(2026, 10, 12), "document": "История ЕИС",
                 "source": "https://zakupki.gov.ru/223/purchase/public/purchase/info/changes.html?regNumber=" + REG}
        found, _ = self.assess(self.positive_text(), events=[event])
        self.assertEqual(len(self.after_deadline(found)), 1)

    def test_actual_analyzer_result_is_saved_and_replayed_without_duplicate(self):
        found, _ = self.assess(self.positive_text())
        self.assertEqual(len(found), 1)
        with tempfile.TemporaryDirectory(prefix="revised-deadline-") as folder:
            path = Path(folder) / "audit.sqlite"
            def connect():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db
            with connect() as db:
                db.executescript("""
                    CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,violations INTEGER DEFAULT 0,
                        checks_ok INTEGER DEFAULT 0,checks_attention INTEGER DEFAULT 0);
                    CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,kind TEXT,
                        title TEXT,law TEXT,law_url TEXT,evidence TEXT,note TEXT,document TEXT,
                        source_url TEXT,location TEXT,match_text TEXT);
                    CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,item TEXT,
                        status TEXT,evidence TEXT,source TEXT,source_url TEXT,document TEXT,
                        field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT);
                """)
                db.execute("INSERT INTO purchases(reg_number) VALUES(?)", (REG,))
            self.assertEqual(general.persist({"conn": connect}, REG, found, []), (1, 0))
            self.assertEqual(general.persist({"conn": connect}, REG, found, []), (0, 0))
            with connect() as db:
                self.assertEqual(db.execute("SELECT code FROM findings").fetchall()[0][0], CODE)
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 1)


class RevisedDeadlinePipelineParity(unittest.TestCase):
    """Real DOCX receipts and verified XML reach both durable audit writers."""
    connect = saved.OfficialPublicationEvidenceTests.connect
    make_xml = saved.OfficialPublicationEvidenceTests.make_xml
    write_archive = saved.OfficialPublicationEvidenceTests.write_archive
    write_original = saved.OfficialPublicationEvidenceTests.write_original
    collect = saved.OfficialPublicationEvidenceTests.collect

    def setUp(self):
        saved.OfficialPublicationEvidenceTests.setUp(self)
        rules.install(self.ns)
        previous_tick = thematic.tick
        previous_state = dict(thematic.WORKER_STATE)
        def restore_worker():
            thematic.tick = previous_tick
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(previous_state)
        self.addCleanup(restore_worker)
        thematic.init(self.ns)
        fast.install(self.ns)

    def seed_actual_original(self, text):
        # The notice authenticates the original's UID and keeps the current
        # card deadline at 30 October. The proof must use this original's
        # explicit revised 12 October date instead.
        shutil.rmtree(self.root / "eis223_official_archive_inbox" / saved.REG)
        self.write_archive(self.make_xml("purchaseNotice", filename="Изменение № 1.docx"))
        self.write_original(text)
        page = "https://zakupki.gov.ru/223/purchase/public/purchase/info/documents.html?regNumber=" + saved.REG
        manifest = intake._parse_manifest({"reg_number": saved.REG, "source_page": page,
            "documents": [{"url": saved.URL, "name": "Изменение № 1.docx"}], "changes": []})
        intake._save_manifest(manifest)
        self.assertEqual(rules.manifest_changes(saved.REG), [])

        collected = self.collect()
        self.assertEqual(len(collected["docs"]), 1)
        doc = collected["docs"][0]
        self.assertEqual(doc["meta"]["source"], "official_eis_original")
        self.assertEqual(doc["meta"]["source_url"], saved.URL)
        self.assertIn("После внесения изменений", doc["text"])
        self.assertEqual(doc["meta"]["sha256"], hashlib.sha256(self.original_path.read_bytes()).hexdigest())
        purchase = collected["p"]
        self.assertEqual(purchase["submission_end"][:10], "2026-10-30")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO purchases(reg_number) VALUES(?)", (saved.REG,))
            db.execute("UPDATE purchases SET title=?,customer=?,method=?,published_at=?,"
                       "submission_end=?,card_fields_json=? WHERE reg_number=?",
                       (purchase["title"], "ООО Тест", purchase["method"], purchase["published"],
                        purchase["submission_end"], "{}", saved.REG))
            for doc in collected["docs"]:
                metadata = dict(doc["meta"])
                metadata.pop("official_publication_evidence", None)
                db.execute("INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,"
                           "status,metadata_json,final_url) VALUES(?,?,?,?,?,?,?,?,?)",
                           (saved.REG, doc["name"], doc["url"], doc["type"], doc["published"],
                            doc["local"], doc["status"], json.dumps(metadata), doc["final_url"]))
            db.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms,"
                       "date_from,date_to) VALUES('revised-deadline','[\"dates\"]','fixture','active',"
                       "'[\"ООО\"]','2026-10-01','2026-10-31')")
            db.execute("INSERT INTO thematic_items(batch_id,reg,status,updated_at,reservation_token)"
                       "VALUES('revised-deadline',?,'queued','fixture','first-lease')", (saved.REG,))
        return collected

    def thematic_attempt(self, lease):
        with self.connect() as db:
            db.execute("UPDATE thematic_items SET status='queued',reservation_token=? "
                       "WHERE batch_id='revised-deadline' AND reg=?", (lease, saved.REG))
            campaign = db.execute("SELECT * FROM thematic_batches WHERE id='revised-deadline'").fetchone()
            doc_id = db.execute("SELECT MAX(id) FROM docs WHERE reg_number=?", (saved.REG,)).fetchone()[0]
        item = {"batch": "revised-deadline", "reg": saved.REG, "categories": ["dates"],
                "ready": 1, "doc_id": doc_id, "reservation_token": lease,
                "campaign_guard": fast._campaign_identity(campaign)}
        async def run():
            return await fast._one(self.ns, item, asyncio.Semaphore(1))
        result = asyncio.run(run())
        self.assertEqual(result[1], "completed")
        self.assertEqual(fast._persist(self.ns, [result]), 1)
        return result[2]

    def assert_pipeline_counts(self, expected):
        first = asyncio.run(general.inspect_one(self.ns, saved.REG))
        repeated = asyncio.run(general.inspect_one(self.ns, saved.REG))
        self.assertEqual((first[0], first[1]["new_findings"]), ("completed", expected))
        self.assertEqual((repeated[0], repeated[1]["new_findings"]), ("completed", 0))
        selected = self.thematic_attempt("first-lease")
        replayed = self.thematic_attempt("replay-lease")
        self.assertEqual(len(selected), expected)
        self.assertEqual(selected, replayed)
        with self.connect() as db:
            general_rows = [dict(row) for row in db.execute("SELECT * FROM findings")]
            thematic_rows = [dict(row) for row in db.execute("SELECT * FROM thematic_findings")]
            self.assertEqual(len(general_rows), expected)
            self.assertEqual(len(thematic_rows), expected)
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: expected})
            self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], expected)
            self.assertEqual(db.execute("SELECT status,reservation_token FROM thematic_items").fetchone()[:],
                             ("completed", ""))
        self.assertEqual({row["code"] for row in general_rows}, {row["code"] for row in thematic_rows})
        return general_rows, thematic_rows

    def test_revised_deadline_from_docx_is_saved_by_general_and_thematic_once(self):
        self.seed_actual_original("Изменение № 1.\n" + DECISION + "\n" + PUBLICATION + "\n" + DEADLINE)
        general_rows, thematic_rows = self.assert_pipeline_counts(1)
        for row in general_rows + thematic_rows:
            self.assertEqual(row["code"], CODE)
            self.assertEqual(row["source_url"], saved.URL)
            self.assertIn("2026-10-12", row["evidence"])
            self.assertIn("После внесения изменений", row["evidence"])

    def test_competing_new_deadlines_in_docx_remain_review_in_both_paths(self):
        collected = self.seed_actual_original("Изменение № 1.\n" + DECISION + "\n" + PUBLICATION + "\n" +
            DEADLINE + "\nНовая дата окончания срока подачи заявок: 20.10.2026.")
        findings, reviews = rules.evaluation(collected["p"], collected["docs"])
        self.assertFalse(findings)
        self.assertTrue(any("соответствие" in item["reason"] for item in reviews))
        self.assert_pipeline_counts(0)
        with self.connect() as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM checks WHERE status='check'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
