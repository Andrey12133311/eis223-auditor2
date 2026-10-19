"""Evidence changes must reopen an audit without holding SQLite readers."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_source_version as sources


class SourceVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.db_path = self.folder / "audit.sqlite"
        self.db = sqlite3.connect(self.db_path, timeout=.05)
        self.addCleanup(self.db.close)
        self.db.execute("""CREATE TABLE docs(id INTEGER PRIMARY KEY,
            reg_number TEXT,local_path TEXT,status TEXT,metadata_json TEXT,
            checked_at TEXT,error TEXT)""")
        self.path = self.folder / "original.docx"
        self.db.execute("INSERT INTO docs VALUES(1,'reg',?,'downloaded_no_text',?,'first',NULL)",
                        (str(self.path), json.dumps({"source":"official_eis_original",
                                                   "sha256":"first"})))
        self.db.commit()

    def version(self):
        return sources.source_version(self.db, "reg")

    def test_same_row_becomes_readable_and_file_is_restored(self):
        missing = self.version()
        self.path.write_bytes(b"official original")
        restored = self.version()
        self.assertNotEqual(missing, restored)
        self.db.execute("UPDATE docs SET status='checked' WHERE id=1")
        self.db.commit()
        self.assertNotEqual(restored, self.version())
        self.assertEqual(self.db.execute("SELECT id FROM docs").fetchone()[0], 1)

    def test_same_path_replacement_detected_even_with_same_size_and_mtime(self):
        self.path.write_bytes(b"first")
        before = self.version()
        original_stat = self.path.stat()
        replacement = self.folder / "replacement.docx"
        replacement.write_bytes(b"other")
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        os.replace(replacement, self.path)
        self.assertEqual(self.path.stat().st_size, original_stat.st_size)
        self.assertEqual(self.path.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.assertNotEqual(before, self.version())

    def test_operational_updates_do_not_repeat_unchanged_evidence(self):
        self.path.write_bytes(b"original")
        before = self.version()
        self.db.execute("UPDATE docs SET checked_at='later',error='temporary warning',metadata_json=?",
                        (json.dumps({"sha256":"first", "source":"official_eis_original",
                                     "checked_at":"later", "extraction_ms":125}),))
        self.db.commit()
        self.assertEqual(before, self.version())
        self.db.execute("UPDATE docs SET metadata_json=?",
                        (json.dumps({"sha256":"corrected", "source":"official_eis_original"}),))
        self.db.commit()
        self.assertNotEqual(before, self.version())

    def test_source_snapshot_does_not_read_original_bytes(self):
        self.path.write_bytes(b"large original")
        with patch("builtins.open", side_effect=AssertionError("Snapshot read file bytes")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("Snapshot read file bytes")):
            self.assertEqual(self.version(), self.version())

    def test_all_sql_readers_close_before_filesystem_inspection(self):
        # DELETE journal makes an outstanding reader block another connection's
        # commit. Multiple rows catch accidental iteration across file stats.
        self.db.execute("INSERT INTO docs SELECT 2,reg_number,local_path,status,metadata_json,checked_at,error FROM docs WHERE id=1")
        self.db.commit()
        writer = sqlite3.connect(self.db_path, timeout=.05)
        self.addCleanup(writer.close)
        original_stat = os.stat
        writes = []

        def inspect_path(path):
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE docs SET checked_at='concurrent writer'")
            writer.commit()
            writes.append(path)
            return original_stat(path)

        with patch.object(sources.os, "stat", side_effect=inspect_path):
            self.version()
        self.assertEqual(len(writes), 2)

    def test_minimal_schema_and_row_factory_are_supported(self):
        minimal = sqlite3.connect(":memory:")
        self.addCleanup(minimal.close)
        empty = sources.source_version(minimal, "reg")
        minimal.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,local_path TEXT)")
        self.assertEqual(empty, sources.source_version(minimal, "reg"))
        minimal.execute("INSERT INTO docs VALUES(1,'reg','checked',NULL)")
        tuple_version = sources.source_version(minimal, "reg")
        minimal.row_factory = sqlite3.Row
        self.assertEqual(tuple_version, sources.source_version(minimal, "reg"))
        self.assertNotEqual(empty, tuple_version)


if __name__ == "__main__":
    unittest.main()
