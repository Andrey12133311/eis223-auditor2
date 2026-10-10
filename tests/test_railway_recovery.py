"""Offline safety tests; no Railway credentials or network required."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import railway_backup_runtime as backup
from scripts import railway_start_restored as restore


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private-backup"
        self.sources = Path(self.temp.name) / "source"
        self.sources.mkdir()
        (self.sources / "entry.py").write_text("app = 'patched'\n", encoding="utf-8")
        (self.sources / "all_documents.py").write_text("def install(x): pass\n", encoding="utf-8")
        self.old_backup_root = backup.ROOT
        self.old_restore_root = restore.ROOT
        self.old_sources = backup.SOURCES
        backup.ROOT = self.root
        restore.ROOT = self.root
        backup.SOURCES = {
            "entry.py": self.sources / "entry.py",
            "all_documents.py": self.sources / "all_documents.py",
        }
        self.env_old = {k: os.environ.get(k) for k in ("V999_PATCH_PY", "UNITTEST_API_KEY")}
        os.environ["V999_PATCH_PY"] = "print('code')"
        os.environ["UNITTEST_API_KEY"] = "do-not-copy"

    def tearDown(self):
        backup.ROOT = self.old_backup_root
        restore.ROOT = self.old_restore_root
        backup.SOURCES = self.old_sources
        for k, previous in self.env_old.items():
            if previous is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = previous
        self.remove_snapshot_sys_path()

    def remove_snapshot_sys_path(self):
        sys.path[:] = [p for p in sys.path if not str(p).startswith(str(self.root))]

    def test_full_snapshot_and_restore_blank_variable(self):
        backup.snapshot()
        current = json.loads((self.root / "current.json").read_text())
        folder = self.root / current["snapshot"]
        saved = json.loads((folder / "program-env.json").read_text())
        self.assertEqual(saved["V999_PATCH_PY"], "print('code')")
        self.assertNotIn("UNITTEST_API_KEY", saved)
        self.assertTrue((folder / "entry.py").exists())
        self.assertTrue((folder / "all_documents.py").exists())
        os.environ["V999_PATCH_PY"] = ""
        restored_path, count = restore.restore()
        self.assertEqual(folder, restored_path)
        self.assertGreaterEqual(count, 1)
        self.assertEqual(os.environ["V999_PATCH_PY"], "print('code')")

    def test_corrupt_snapshot_fails_closed(self):
        backup.snapshot()
        folder = self.root / json.loads((self.root / "current.json").read_text())["snapshot"]
        (folder / "entry.py").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Corrupt recovery artifact"):
            restore.restore()

    def test_absent_compiled_entry_refuses_to_backup(self):
        (self.sources / "entry.py").unlink()
        with self.assertRaisesRegex(RuntimeError, "No /tmp/entry.py"):
            backup.snapshot()


if __name__ == "__main__":
    unittest.main()
