"""Tenant-scoped SQL statements for personal resources.

All reads and mutations MUST include authenticated owner_id, including
downloads and thematic batches. No client-supplied owner override.
"""

PROCUREMENTS = """
SELECT reg_number,created_at FROM account_procurements
WHERE user_id=%s ORDER BY created_at DESC LIMIT 500
"""

THEMATIC_BATCHES = """
SELECT batch_id FROM account_thematic_batches
WHERE user_id=%s ORDER BY batch_id DESC LIMIT 500
"""

MEMOS = """
SELECT id,reg_number,storage_key,created_at FROM account_memos
WHERE user_id=%s ORDER BY created_at DESC LIMIT 500
"""

MEMO_DOWNLOAD = """
SELECT storage_key FROM account_memos
WHERE id=%s AND user_id=%s
"""

BATCH_OWNERSHIP = """
SELECT 1 FROM account_thematic_batches
WHERE batch_id=%s AND user_id=%s
"""


def owned_memo_key(db, memo_id, owner_id):
    row = db.execute(MEMO_DOWNLOAD, (memo_id, owner_id)).fetchone()
    return row[0] if row else None


def owns_batch(db, batch_id, owner_id):
    return bool(db.execute(BATCH_OWNERSHIP, (batch_id, owner_id)).fetchone())
