from pathlib import Path
import sqlite3
import tempfile
import unittest

from legacy_import_backfill import MARKER, patch_file, patch_text


SOURCE = '''events = []
def install_v204(ns):
    def initialize():
        events.append("initialize")
    def enqueue(reg, docs):
        events.append((reg, docs))
    def db():
        return ns["conn"]()
    def recover_queue():
        with db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS document_jobs_v204(reg TEXT PRIMARY KEY, status TEXT, due REAL)")
            c.execute("UPDATE document_jobs_v204 SET status='pending',due=0 WHERE status='running'")
            rows = [dict(x) for x in c.execute('SELECT * FROM docs')]
        for r in rows:enqueue(r['reg_number'], [r['name']])
    def explicit_recovery_handler():
        recover_queue()
    initialize()
    try:
        recover_queue()
    except Exception:
        events.append("recovery-error")
    ns["recover"] = explicit_recovery_handler
    ns["queue"] = ["existing job"]
'''


class LegacyStartupBackfill(unittest.TestCase):
    def test_only_import_recovery_is_skipped_and_manual_recovery_is_retained(self):
        context = {}
        exec(patch_text(SOURCE), context)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.sqlite"
            def conn():
                c = sqlite3.connect(path)
                c.row_factory = sqlite3.Row
                return c
            with conn() as c:
                c.execute("CREATE TABLE docs(reg_number TEXT, name TEXT)")
                c.execute("INSERT INTO docs VALUES('32612345678','original')")
                c.execute("CREATE TABLE document_jobs_v204(reg TEXT PRIMARY KEY, status TEXT, due REAL)")
                c.execute("INSERT INTO document_jobs_v204 VALUES('existing','running',99)")
            ns = {"conn": conn}
            context["install_v204"](ns)
            self.assertEqual(context["events"], ["initialize"])
            with conn() as c:
                self.assertEqual(tuple(c.execute("SELECT * FROM document_jobs_v204").fetchone()), ("existing", "running", 99))
            ns["recover"]()
            self.assertEqual(context["events"], ["initialize", ("32612345678", ["original"])])

    def test_cold_start_keeps_schema_initialization(self):
        context = {}
        exec(patch_text(SOURCE), context)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cold.sqlite"
            context["install_v204"]({"conn": lambda: sqlite3.connect(path)})
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM document_jobs_v204").fetchone()[0], 0)

    def test_patching_is_idempotent_and_unicode_offsets_are_preserved(self):
        source = SOURCE.replace("recover_queue()\n    except", "recover_queue() # восстановление\n    except")
        updated = patch_text(source)
        self.assertEqual(patch_text(updated), updated)
        self.assertIn("# восстановление", updated)
        self.assertEqual(updated.count(MARKER), 1)

    def test_unknown_installer_or_ambiguous_recovery_fails_closed(self):
        with self.assertRaises(RuntimeError):
            patch_text(SOURCE.replace("install_v204", "other"))
        with self.assertRaises(RuntimeError):
            patch_text(SOURCE.replace("    initialize()", "    recover_queue()\n    initialize()"))

    def test_atomic_file_patch_requires_the_active_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entry.py"
            path.write_text(SOURCE)
            with self.assertRaises(ValueError):
                patch_file(path, source_mode="legacy")
            self.assertEqual(path.read_text(), SOURCE)
            self.assertTrue(patch_file(path, source_mode="OFFICIAL_EIS_VPS_ONLY"))
            self.assertFalse(patch_file(path, source_mode="OFFICIAL_EIS_VPS_ONLY"))


if __name__ == "__main__":
    unittest.main()
