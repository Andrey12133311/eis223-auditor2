"""Database primitives for an isolated thematic audit worker.

No EIS requests are performed by this module. Call only from a trusted worker,
never from a user-facing route.
"""

def claim_next(db):
    """Atomically claim one pending job without blocking another worker."""
    row = db.execute(
        """WITH next_job AS (
               SELECT j.user_id,j.batch_id,j.reg_number
               FROM account_thematic_jobs AS j
               JOIN account_users AS u ON u.id=j.user_id
               WHERE j.status='queued' AND u.status='approved'
               ORDER BY j.created_at,j.user_id,j.batch_id,j.reg_number
               FOR UPDATE OF j SKIP LOCKED LIMIT 1
           )
           UPDATE account_thematic_jobs AS j
           SET status='running',attempts=j.attempts+1,
               started_at=CURRENT_TIMESTAMP,finished_at=NULL
           FROM next_job AS n
           WHERE j.user_id=n.user_id AND j.batch_id=n.batch_id
             AND j.reg_number=n.reg_number
           RETURNING j.user_id,j.batch_id,j.reg_number,j.attempts"""
    ).fetchone()
    return tuple(row) if row else None


def complete_job(db, user_id, batch_id, reg_number, attempts, success):
    """Use attempt fencing so a stale worker cannot overwrite a newer attempt."""
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("Invalid attempt number")
    state = 'completed' if success else 'failed'
    row = db.execute(
        """UPDATE account_thematic_jobs
           SET status=%s,finished_at=CURRENT_TIMESTAMP
           WHERE user_id=%s AND batch_id=%s AND reg_number=%s
             AND attempts=%s AND status='running'
           RETURNING status""",
        (state,user_id,batch_id,reg_number,attempts),
    ).fetchone()
    return row is not None


def requeue_stale(db, max_attempts=3, stale_minutes=30):
    """Recover abandoned claims with bounded retries.

    Run periodically in a trusted worker transaction. Attempt fencing in
    complete_job prevents a late worker from finishing a reclaimed attempt.
    """
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("Invalid retry limit")
    if not isinstance(stale_minutes, int) or stale_minutes < 1:
        raise ValueError("Invalid stale timeout")
    rows = db.execute(
        """UPDATE account_thematic_jobs AS j
           SET status=CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END,
               finished_at=CASE WHEN attempts >= %s THEN CURRENT_TIMESTAMP ELSE NULL END
           WHERE j.status='running'
             AND j.started_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 minute')
           RETURNING j.user_id,j.batch_id,j.reg_number,j.status""",
        (max_attempts,max_attempts,stale_minutes),
    ).fetchall()
    return [tuple(row) for row in rows]


def process_one(connect, audit_callback):
    """Execute one job with a trusted audit callback and separate transactions.

    The callback must return True only after evidence and results are durably
    saved. No callback is installed by default; this does not access EIS.
    """
    if not callable(audit_callback):
        raise TypeError("A trusted audit callback is required")
    with connect() as db:
        claimed = claim_next(db)
    if claimed is None:
        return None
    owner_id, batch_id, reg_number, attempts = claimed
    succeeded = False
    try:
        succeeded = audit_callback(owner_id, batch_id, reg_number) is True
    except Exception:
        # The failure is recorded below; never treat an exception as success.
        succeeded = False
    with connect() as db:
        recorded = complete_job(
            db, owner_id, batch_id, reg_number, attempts, succeeded
        )
    return {
        "batch_id": batch_id,
        "reg_number": reg_number,
        "attempts": attempts,
        "status": ("completed" if succeeded else "failed") if recorded else "stale",
    }
