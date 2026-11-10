"""Repeated partial audits retain exact source names without duplicate writes."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v332_all_procurements_audit as general


REG = "32616483282"
URL = ("https://zakupki.gov.ru/223/filestore/public/1.0/download/"
       "fz223/file.html?uid=" + "A" * 32)
ARCHIVE_PREFIX = "Документация о закупке и приложения — " + "условия_договора_" * 18
LONG_DOCUMENT = ARCHIVE_PREFIX + " / Раздел 1 / Сроки исполнения.docx"


class ChecklistDocumentIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audit.sqlite"
        self.trace = []
        self.ns = {"conn": self.connect}
        with closing(self.connect()) as db:
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, customer TEXT,
                    violations INTEGER DEFAULT 0, checks_ok INTEGER DEFAULT 0,
                    checks_attention INTEGER DEFAULT 0);
                CREATE TABLE findings(id INTEGER PRIMARY KEY, reg_number TEXT,
                    code TEXT, kind TEXT, title TEXT, law TEXT, law_url TEXT,
                    evidence TEXT, note TEXT, document TEXT, source_url TEXT,
                    location TEXT, match_text TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY, reg_number TEXT,
                    scope TEXT, item TEXT, status TEXT, evidence TEXT, source TEXT,
                    source_url TEXT, document TEXT, field_path TEXT, note TEXT,
                    law TEXT, law_url TEXT, location TEXT);
            """)
            db.execute("INSERT INTO purchases(reg_number,customer) VALUES(?,?)",
                       (REG, "ООО Проверяемый заказчик"))
            db.commit()
        self.assertGreater(len(LONG_DOCUMENT), 255)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=.05)
        db.row_factory = sqlite3.Row
        db.set_trace_callback(self.trace.append)
        return db

    def check(self, document=LONG_DOCUMENT, *, status="check", field="vp-contract-payment"):
        return {"scope": "Условия договора", "item": "Проверить срок оплаты",
                "status": status, "evidence": "Оплата в течение семи рабочих дней.",
                "source": document, "source_url": URL, "document": document,
                "field_path": field, "law": "223-ФЗ, часть 5.3 статьи 3",
                "note": "Необходимо сопоставить редакции договора"}

    def stored_checks(self):
        with closing(self.connect()) as db:
            return [tuple(row) for row in db.execute("SELECT * FROM checks ORDER BY id")]

    def test_repeated_partial_checks_do_not_grow_rows_or_rewrite_counters(self):
        checks = [self.check(status="ok", field="vp-term-ok"), self.check()]
        self.assertEqual(general.persist(self.ns, REG, [], checks), (0, 2))
        before = self.stored_checks()
        self.trace.clear()
        for _ in range(20):
            self.assertEqual(general.persist(self.ns, REG, [], checks), (0, 0))
        writes = [sql for sql in self.trace
                  if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(writes, [], "Unchanged checklist rows must not acquire DML work")
        self.assertEqual(self.stored_checks(), before)
        with closing(self.connect()) as db:
            self.assertEqual(tuple(db.execute(
                "SELECT violations,checks_ok,checks_attention FROM purchases WHERE reg_number=?",
                (REG,)).fetchone()), (0, 1, 1))

    def test_same_source_repeated_in_one_result_is_saved_once(self):
        check = self.check()
        self.assertEqual(general.persist(self.ns, REG, [], [check, dict(check), dict(check)]), (0, 1))
        self.assertEqual(len(self.stored_checks()), 1)

    def test_distinct_archive_members_with_same_long_prefix_are_preserved(self):
        second = ARCHIVE_PREFIX + " / Раздел 2 / Сроки исполнения.docx"
        self.assertEqual(LONG_DOCUMENT[:255], second[:255])
        checks = [self.check(), self.check(second)]
        self.assertEqual(general.persist(self.ns, REG, [], checks), (0, 2))
        self.assertEqual(general.persist(self.ns, REG, [], checks), (0, 0))
        with closing(self.connect()) as db:
            self.assertEqual([row[0] for row in db.execute(
                "SELECT document FROM checks ORDER BY id")], [LONG_DOCUMENT, second])

    def test_existing_shortened_source_cannot_hide_a_full_source(self):
        shortened = LONG_DOCUMENT[:255]
        general.persist(self.ns, REG, [], [self.check(shortened)])
        old = self.stored_checks()[0]
        self.assertEqual(general.persist(self.ns, REG, [], [self.check()]), (0, 1))
        self.assertEqual(self.stored_checks()[0], old)
        with closing(self.connect()) as db:
            self.assertEqual([row[0] for row in db.execute(
                "SELECT document FROM checks ORDER BY id")], [shortened, LONG_DOCUMENT])

    def test_existing_duplicate_history_is_retained_without_more_duplicates(self):
        general.persist(self.ns, REG, [], [self.check()])
        with closing(self.connect()) as db:
            db.execute("""INSERT INTO checks(reg_number,scope,item,status,evidence,source,
                source_url,document,field_path,note,law,law_url,location)
                SELECT reg_number,scope,item,status,evidence,source,source_url,document,
                    field_path,note,law,law_url,location FROM checks""")
            db.execute("UPDATE purchases SET checks_attention=2 WHERE reg_number=?", (REG,))
            db.commit()
        before = self.stored_checks()
        self.assertEqual(general.persist(self.ns, REG, [], [self.check()]), (0, 0))
        self.assertEqual(self.stored_checks(), before)

    def test_stale_owner_cannot_save_even_when_checklist_is_unchanged(self):
        general.persist(self.ns, REG, [], [self.check()])
        with closing(self.connect()) as db:
            db.execute("CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,reservation_token TEXT)")
            db.execute("INSERT INTO v332_audit_queue VALUES(?,?,'running','new-owner')",
                       (general.CAMPAIGN, REG))
            db.commit()
        before = self.stored_checks()
        stale = general.AuditReservation(REG, "old-owner")
        with self.assertRaisesRegex(LookupError, "superseded"):
            general.persist(self.ns, stale, [], [self.check()])
        self.assertEqual(self.stored_checks(), before)


if __name__ == "__main__":
    unittest.main()
