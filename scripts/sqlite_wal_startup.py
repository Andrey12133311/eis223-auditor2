"""Bounded backup-first WAL conversion before installing application workers.

Only the existing main DB is used. Never reset queues, erase findings, delete
originals/WAL sidecars, lower synchronous durability or run a live TRUNCATE
checkpoint. A failed precondition leaves the existing DB serving in its old
mode. Do not roll back to an image with an unpatched SQLite after conversion.
"""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

MIN_VERSION = (3, 51, 3)
RESERVE_BYTES = 512 * 1024 * 1024
BUDGET_SECONDS = 45.0
LOCAL_FILESYSTEMS = frozenset(("ext4", "ext3", "xfs", "btrfs", "overlay", "tmpfs"))


def _filesystem(path):
    """Reject network/FUSE storage: WAL needs shared memory and local locking."""
    candidates = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields = left.split()
        mount = fields[4]
        for before, after in ((r"\040", " "), (r"\011", "\t"), (r"\012", "\n"), (r"\134", "\\")):
            mount = mount.replace(before, after)
        if path.is_relative_to(Path(mount)):
            candidates.append((len(mount), right.split()[0]))
    return max(candidates)[1] if candidates else "unknown"


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare(ns, *, budget_seconds=BUDGET_SECONDS):
    """Called synchronously once before workers start; tests use isolated DBs."""
    report = {"enabled": False, "version": sqlite3.sqlite_version}
    db = None
    temporary = None
    deadline = time.monotonic() + budget_seconds

    def within_budget(*_args):
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite startup backup budget exhausted")

    try:
        if os.environ.get("EIS223_SQLITE_WAL", "1") != "1":
            return {**report, "reason": "disabled_by_configuration"}
        if sqlite3.sqlite_version_info < MIN_VERSION or sqlite3.sqlite_version_info == (3, 52, 0):
            return {**report, "reason": "patched_sqlite_required"}
        db = ns["conn"]()
        if db.in_transaction:
            return {**report, "reason": "connection_not_idle"}
        main = next((row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"), "")
        if not main:
            return {**report, "reason": "persistent_database_required"}
        path = Path(main)
        if path.is_symlink() or not path.is_file():
            return {**report, "reason": "regular_database_required"}
        path = path.resolve()
        filesystem = _filesystem(path)
        report["filesystem"] = filesystem
        if filesystem not in LOCAL_FILESYSTEMS:
            return {**report, "reason": "local_filesystem_required"}
        mode = db.execute("PRAGMA journal_mode").fetchone()[0].lower()
        report["previous_mode"] = mode
        if mode == "wal":
            return {**report, "enabled": True, "mode": mode, "reason": "already_enabled"}
        if mode != "delete":
            return {**report, "reason": "unexpected_journal_mode"}
        if shutil.disk_usage(path.parent).free < 2 * path.stat().st_size + RESERVE_BYTES:
            return {**report, "reason": "insufficient_backup_space"}
        lock_path = path.parent / ".eis223-wal-migration.lock"
        # O_NOFOLLOW rejects a malicious symlink instead of truncating a target.
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**report, "reason": "another_migration_in_progress"}
            # A peer may have finished between the first read and file lock.
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                return {**report, "enabled": True, "mode": "wal", "reason": "already_enabled"}
            folder = path.parent / "eis223-sqlite-backups"
            if folder.is_symlink():
                return {**report, "reason": "unsafe_backup_directory"}
            folder.mkdir(mode=0o700, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix=".pre-wal-", suffix=".sqlite3", dir=folder)
            os.close(handle)
            temporary = Path(name)
            target = sqlite3.connect(temporary, timeout=1)
            try:
                db.backup(target, pages=256, progress=within_budget, sleep=0.05)
                target.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("SQLite backup integrity check failed")
                target.set_progress_handler(None, 0)
            finally:
                target.close()
            within_budget()
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = folder / ("pre-wal-" + stamp + "-" + temporary.name[9:])
            # The random suffix and exclusive link avoid overwriting a backup.
            os.link(temporary, backup)
            temporary.unlink()
            temporary = None
            _fsync_directory(folder)
            report["backup"] = backup.name
            db.execute("PRAGMA busy_timeout=" + str(max(1, min(5000, int((deadline-time.monotonic())*1000)))))
            mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()
            report.update(enabled=mode == "wal", mode=mode,
                          reason="backup_verified_and_enabled" if mode == "wal" else "conversion_not_available")
            return report
    except (OSError, sqlite3.Error, TimeoutError, RuntimeError) as exc:
        # Do not hide failure or restore over a live database. Existing data and
        # a completed backup remain intact; a later startup may safely retry.
        return {**report, "reason": "migration_deferred", "error_type": type(exc).__name__}
    finally:
        if db is not None:
            db.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install(ns):
    report = prepare(ns)
    print("SQLITE_WAL_STARTUP " + json.dumps(report, sort_keys=True), flush=True)
    return report
