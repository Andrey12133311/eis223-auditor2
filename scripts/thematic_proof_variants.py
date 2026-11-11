"""Append-only exact thematic representations beside the historical UNIQUE key.

The old table, its rowids and public finding keys stay unchanged. The writer
can retain a current registered proof even when its display tuple collides
with historical evidence. These rows never establish legal qualification;
normal current-source reassessment and semantic event grouping still apply.
Only initialize() performs DDL, during schema startup.
"""
from __future__ import annotations

import hashlib
import json

TABLE = 'thematic_proof_variants'
FIELDS = ('category', 'title', 'law', 'evidence', 'code', 'document', 'source_url', 'law_url')


def _exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (TABLE,)).fetchone())


def initialize(db):
    """Create the additive store before audit workers start; never commit here."""
    db.execute('''CREATE TABLE IF NOT EXISTS thematic_proof_variants(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL,reg TEXT NOT NULL,storage_key TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',
        law TEXT NOT NULL DEFAULT '',evidence TEXT NOT NULL DEFAULT '',
        code TEXT NOT NULL DEFAULT '',document TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',law_url TEXT NOT NULL DEFAULT '',
        UNIQUE(batch_id,reg,storage_key))''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_thematic_proof_variants_purchase '
               'ON thematic_proof_variants(batch_id,reg,id)')


def storage_key(item):
    """Exact stored representation, distinct from journal and manager identities."""
    values = [str(item.get(field) or '') for field in FIELDS]
    return hashlib.sha256(json.dumps([1, *values], ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def source_sql(db):
    """Return a SELECT relation usable after FROM, including read-only legacy DBs.

    Each source id remains in its own domain. Callers choosing a bounded order
    must use both _source_storage and _source_rowid, not compare bare rowids.
    This function neither creates a table nor changes existing data.
    """
    fields = 'batch_id,reg,' + ','.join(FIELDS)
    legacy = ("SELECT " + fields + ", 'legacy' AS _source_storage, "
              "rowid AS _source_rowid FROM thematic_findings")
    if not _exists(db):
        return '(' + legacy + ')'
    return '(' + legacy + ' UNION ALL SELECT ' + fields + ", 'variant' AS _source_storage, " + \
        'id AS _source_rowid FROM ' + TABLE + ')'


def raw_rows(db, batch, reg=None):
    """Read both stores without collapsing source representations or verdicts."""
    condition, args = ('', (batch,)) if reg is None else (' AND reg=?', (batch, reg))
    cursor = db.execute('SELECT * FROM ' + source_sql(db) +
        " WHERE batch_id=?" + condition +
        " ORDER BY CASE _source_storage WHEN 'legacy' THEN 0 ELSE 1 END,_source_rowid", args)
    names = [item[0] for item in cursor.description]
    return [dict(row) if hasattr(row, 'keys') else dict(zip(names, row))
            for row in cursor.fetchall()]


def insert_variant(db, batch, reg, item):
    """Append inside the caller's guarded transaction; no DDL or commit."""
    if not batch or not reg:
        raise ValueError('A proof variant needs its thematic batch and procurement')
    values = [str(item.get(field) or '') for field in FIELDS]
    cursor = db.execute('INSERT OR IGNORE INTO ' + TABLE +
        '(batch_id,reg,storage_key,' + ','.join(FIELDS) + ') VALUES(' +
        ','.join('?' for _ in range(3 + len(FIELDS))) + ')',
        (batch, reg, storage_key(item), *values))
    return cursor.rowcount == 1


def revision(db, batch):
    """Cache stamp across the two independent SQL identity domains."""
    legacy = db.execute('SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM thematic_findings '
                        'WHERE batch_id=?', (batch,)).fetchone()
    variant = db.execute('SELECT COUNT(*),COALESCE(MAX(id),0) FROM ' + TABLE +
                         ' WHERE batch_id=?', (batch,)).fetchone() if _exists(db) else (0, 0)
    return tuple(int(value) for value in (*legacy, *variant))


def delete_batch_variants(db, batch):
    """Participate in an authorized batch deletion without affecting other history."""
    if not _exists(db):
        return 0
    return db.execute('DELETE FROM ' + TABLE + ' WHERE batch_id=?', (batch,)).rowcount
