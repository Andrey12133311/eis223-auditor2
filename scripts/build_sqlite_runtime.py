"""Build the pinned, WAL-reset-fixed SQLite shared library (build/CI only).

Source provenance: https://www.sqlite.org/releaselog/3_51_3.html
The official SHA3-256 of sqlite3.c is checked before compiling any source.
No application data, runtime credentials or database connections are used.
"""
from __future__ import annotations
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import zipfile

VERSION = "3.51.3"
URL = "https://www.sqlite.org/2026/sqlite-amalgamation-3510300.zip"
SOURCE_SHA3 = "32d5424f97e0a7fc5ed2f6335afbb58be4e0298bd7117a34e39d345ff13d859e"
MAX_ARCHIVE = 8 * 1024 * 1024


def build(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with urlopen(URL, timeout=60) as response:
        archive = response.read(MAX_ARCHIVE + 1)
    if len(archive) > MAX_ARCHIVE:
        raise RuntimeError("SQLite source archive exceeds the fixed build budget")
    with tempfile.TemporaryDirectory(prefix="eis-sqlite-build-") as folder:
        root = Path(folder)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            source = zipped.read("sqlite-amalgamation-3510300/sqlite3.c")
        if hashlib.sha3_256(source).hexdigest() != SOURCE_SHA3:
            raise RuntimeError("Official SQLite source checksum mismatch")
        path = root / "sqlite3.c"
        path.write_bytes(source)
        output = root / "libsqlite3.so.0"
        subprocess.run([
            "cc", "-O2", "-fPIC", "-shared", "-Wl,-soname,libsqlite3.so.0",
            "-DSQLITE_THREADSAFE=1", "-DSQLITE_ENABLE_FTS3", "-DSQLITE_ENABLE_FTS4",
            "-DSQLITE_ENABLE_FTS5", "-DSQLITE_ENABLE_RTREE",
            "-DSQLITE_ENABLE_COLUMN_METADATA", "-DSQLITE_ENABLE_MATH_FUNCTIONS",
            "-DSQLITE_ENABLE_DBSTAT_VTAB", "-DSQLITE_ENABLE_UNLOCK_NOTIFY",
            str(path), "-o", str(output), "-lm", "-ldl", "-lpthread",
        ], check=True, timeout=240)
        target = destination / "libsqlite3.so.0"
        target.write_bytes(output.read_bytes())
        target.chmod(0o755)
        env = dict(os.environ, LD_LIBRARY_PATH=str(destination))
        subprocess.run([sys.executable, "-c",
            "import sqlite3; c=sqlite3.connect(':memory:'); "
            "assert sqlite3.sqlite_version == '3.51.3'; "
            "assert c.execute('SELECT sqlite_version()').fetchone()[0] == '3.51.3'; "
            "assert sqlite3.threadsafety > 0; "
            "print('SQLITE_BUILD_VERIFIED version=3.51.3 source_sha3_verified=True')"],
            env=env, check=True, timeout=15)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: build_sqlite_runtime.py OUTPUT_DIRECTORY")
    build(sys.argv[1])
