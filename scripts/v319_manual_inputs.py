"""Keep explicit registry-number submissions separate from discovery work.

Document download lanes, purchase source labels and INN search results are not
evidence that someone entered a registry number in the manual-check field.
The older tables stay intact; only the display membership changes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

REG = re.compile(r"^3[0-9]{10}$")
MANUAL_SOURCE_SQL = "SELECT reg,requested_at,job_id FROM v319_manual_inputs"


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS v319_manual_inputs(
        reg TEXT PRIMARY KEY,
        requested_at TEXT NOT NULL,
        provenance TEXT NOT NULL,
        job_id TEXT NOT NULL DEFAULT ''
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS v319_manual_order "
               "ON v319_manual_inputs(requested_at DESC)")
    db.execute("""CREATE TABLE IF NOT EXISTS v319_manual_migrations(
        marker TEXT PRIMARY KEY,
        migrated_at TEXT NOT NULL
    )""")


def record_input(db, reg, *, job_id="", requested_at=None, provenance="number_field"):
    """Call only from a registry-number submission, inside its transaction."""
    if not REG.fullmatch(reg):
        raise ValueError("Invalid 223-FZ registry number")
    now = requested_at or datetime.now(timezone.utc).isoformat()
    db.execute("""INSERT INTO v319_manual_inputs(reg,requested_at,provenance,job_id)
        VALUES(?,?,?,?) ON CONFLICT(reg) DO UPDATE SET
        requested_at=excluded.requested_at,
        provenance=excluded.provenance,
        job_id=excluded.job_id""", (reg, now, provenance, job_id))


def migrate_legacy_inputs(db):
    """Recover actual submission records once, never broad 'manual' labels.

    v233 stored explicit manual requests separately from document priorities.
    In v301, only root registry jobs at priority 0 came from POST /search;
    INN children have a parent and the repair migration uses priority 2.
    """
    ensure_schema(db)
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    sources = (
        ("v301_search_jobs", "legacy_v301_number_requests", "legacy_number_search",
         "SELECT target,created_at,id FROM v301_search_jobs "
         "WHERE kind='reg' AND page=1 AND priority=0 AND COALESCE(parent_id,'')='' "
         "ORDER BY created_at DESC,id DESC"),
        ("requests_v233", "legacy_v233_number_requests", "legacy_number_request",
         "SELECT reg,requested_at,'' FROM requests_v233 WHERE lane='manual'"),
    )
    migrated = 0
    for table, marker, provenance, query in sources:
        if table not in tables or db.execute(
                "SELECT 1 FROM v319_manual_migrations WHERE marker=?", (marker,)).fetchone():
            continue
        for reg, requested_at, job_id in db.execute(query).fetchall():
            if not isinstance(reg, str) or not REG.fullmatch(reg):
                continue
            migrated += db.execute("""INSERT OR IGNORE INTO v319_manual_inputs
                (reg,requested_at,provenance,job_id) VALUES(?,?,?,?)""",
                (reg, requested_at or datetime.now(timezone.utc).isoformat(),
                 provenance, job_id)).rowcount
        db.execute("INSERT INTO v319_manual_migrations(marker,migrated_at) VALUES(?,?)",
                   (marker, datetime.now(timezone.utc).isoformat()))
    return migrated
