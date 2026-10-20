"""Migration tests use only temporary DBs; no /data or network access."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import sqlite_wal_startup as startup


class WalStartup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "eis223.db"
        self.ns = {"conn": self.connect}
        with closing(self.connect()) as c:
            c.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
            c.execute("INSERT INTO evidence VALUES(1, 'preserve-original-receipt')")
            c.commit()
        self.flags = patch.dict(os.environ, {"EIS223_SQLITE_WAL": "1"})
        self.flags.start(); self.addCleanup(self.flags.stop)

    def connect(self):
        return sqlite3.connect(self.path, timeout=.05)

    def prepare(self, **kwargs):
        # Unit tests also run on an unpatched CI host; the dedicated workflow
        # additionally runs the actual compiled fixed SQLite in a new process.
        with patch.object(sqlite3, "sqlite_version_info", (3, 51, 3)):
            return startup.prepare(self.ns, **kwargs)

    def contents(self, path=None):
        c = sqlite3.connect(path or self.path)
        try:
            return c.execute("SELECT * FROM evidence ORDER BY id").fetchall()
        finally:
            c.close()

    def mode(self):
        with closing(self.connect()) as c:
            return c.execute("PRAGMA journal_mode").fetchone()[0]

    def test_backup_matches_source_before_conversion_and_repeat_is_idempotent(self):
        before = self.contents()
        result = self.prepare()
        self.assertTrue(result["enabled"], result)
        self.assertEqual(self.mode(), "wal")
        backup = self.path.parent / "eis223-sqlite-backups" / result["backup"]
        self.assertEqual(self.contents(backup), before)
        self.assertEqual(self.contents(), before)
        with closing(self.connect()) as c:
            c.execute("INSERT INTO evidence VALUES(2, 'new-original-receipt')")
            c.commit()
        self.assertTrue(self.prepare()["enabled"])
        self.assertEqual(len(list(backup.parent.glob("pre-wal-*"))), 1)
        self.assertEqual(self.contents(backup), before)
        self.assertEqual(len(self.contents()), 2)

    def test_active_reader_no_longer_blocks_writer_commit(self):
        self.assertTrue(self.prepare()["enabled"])
        reader = self.connect(); writer = self.connect()
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM evidence").fetchall()
            writer.execute("INSERT INTO evidence VALUES(2, 'concurrent')")
            writer.commit()
            self.assertEqual(len(reader.execute("SELECT * FROM evidence").fetchall()), 1)
            reader.rollback()
            self.assertEqual(len(reader.execute("SELECT * FROM evidence").fetchall()), 2)
        finally:
            writer.close(); reader.close()

    def test_old_or_withdrawn_sqlite_never_enables_wal(self):
        for version in ((3, 46, 1), (3, 51, 2), (3, 52, 0)):
            with self.subTest(version=version), patch.object(sqlite3, "sqlite_version_info", version):
                self.assertEqual(startup.prepare(self.ns)["reason"], "patched_sqlite_required")
        self.assertEqual(self.mode(), "delete")

    def test_unknown_or_network_filesystem_is_not_converted(self):
        for kind in ("nfs", "cifs", "fuse.sshfs", "unknown"):
            with self.subTest(kind=kind), patch.object(startup, "_filesystem", return_value=kind):
                self.assertEqual(self.prepare()["reason"], "local_filesystem_required")
        self.assertEqual(self.mode(), "delete")

    def test_insufficient_space_leaves_data_and_mode_unchanged(self):
        with patch.object(startup.shutil, "disk_usage", return_value=type("Disk", (), {"free": 1})()):
            self.assertEqual(self.prepare()["reason"], "insufficient_backup_space")
        self.assertEqual(self.mode(), "delete")
        self.assertEqual(len(self.contents()), 1)

    def test_backup_deadline_leaves_source_untouched(self):
        result = self.prepare(budget_seconds=0)
        self.assertFalse(result["enabled"])
        self.assertEqual(self.mode(), "delete")
        self.assertEqual(len(self.contents()), 1)
        folder = self.path.parent / "eis223-sqlite-backups"
        self.assertEqual(list(folder.glob("*.sqlite3")), [])

    def test_backup_failure_prevents_mode_change(self):
        with patch.object(startup, "_fsync_directory", side_effect=OSError("test disk failure")):
            result = self.prepare()
        self.assertFalse(result["enabled"])
        self.assertEqual(self.mode(), "delete")
        self.assertEqual(len(self.contents()), 1)

    def test_disabled_flag_does_not_touch_source_or_backups(self):
        with patch.dict(os.environ, {"EIS223_SQLITE_WAL": "0"}):
            self.assertEqual(self.prepare()["reason"], "disabled_by_configuration")
        self.assertEqual(self.mode(), "delete")
        self.assertFalse((self.path.parent / "eis223-sqlite-backups").exists())

    def test_backup_directory_symlink_is_rejected(self):
        (self.path.parent / "eis223-sqlite-backups").symlink_to(self.path.parent, target_is_directory=True)
        self.assertEqual(self.prepare()["reason"], "unsafe_backup_directory")
        self.assertEqual(self.mode(), "delete")

    def test_migration_lock_does_not_override_another_process(self):
        import fcntl
        with (self.path.parent / ".eis223-wal-migration.lock").open("wb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.prepare()["reason"], "another_migration_in_progress")
        self.assertEqual(self.mode(), "delete")


if __name__ == "__main__":
    unittest.main()
