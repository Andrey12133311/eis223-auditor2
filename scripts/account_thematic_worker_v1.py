"""Database primitives for an isolated thematic audit worker.

No EIS requests are performed by this module. Call only from a trusted worker,
never from a user-facing route.
"""
from datetime import datetime, timezone


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
