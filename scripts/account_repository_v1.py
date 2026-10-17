"""Repository operations for the isolated account PostgreSQL database.

Caller owns the transaction and authenticated actor identity.
"""
from uuid import uuid4
from scripts.account_status_v1 import registration_status


def create_pending_user(db, email, password_hash, last_name="", first_name="", patronymic=""):
    user_id = uuid4()
    row = db.execute(
        """INSERT INTO account_users
           (id,email,password_hash,status,role,last_name,first_name,patronymic)
           VALUES (%s,%s,%s,%s,'user',%s,%s,%s)
           ON CONFLICT DO NOTHING RETURNING id""",
        (user_id, email, password_hash, registration_status(),
         last_name, first_name, patronymic)
    ).fetchone()
    return row[0] if row else None


def pending_users(db, limit=100):
    return db.execute(
        """SELECT id,email,created_at FROM account_users
           WHERE status='pending' ORDER BY created_at ASC LIMIT %s""",
        (min(max(int(limit), 1), 100),)
    ).fetchall()


def approve_user(db, actor_id, user_id):
    row = db.execute(
        """UPDATE account_users SET status='approved',
           approved_at=CURRENT_TIMESTAMP,approved_by=%s
           WHERE id=%s AND status='pending' RETURNING id""",
        (actor_id, user_id)
    ).fetchone()
    if row:
        db.execute(
            """INSERT INTO account_audit(actor_id,subject_id,action)
               VALUES (%s,%s,'approve')""",
            (actor_id, user_id)
        )
    return bool(row)


def reject_user(db, actor_id, user_id):
    row = db.execute(
        """UPDATE account_users SET status='rejected'
           WHERE id=%s AND status='pending' RETURNING id""",
        (user_id,)
    ).fetchone()
    if row:
        db.execute(
            """INSERT INTO account_audit(actor_id,subject_id,action)
               VALUES (%s,%s,'reject')""",
            (actor_id, user_id)
        )
    return bool(row)


def block_user(db, actor_id, user_id):
    row = db.execute(
        """UPDATE account_users SET status='blocked'
           WHERE id=%s AND role='user' RETURNING id""",
        (user_id,)
    ).fetchone()
    if row:
        db.execute(
            "UPDATE account_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=%s",
            (user_id,)
        )
        db.execute(
            """INSERT INTO account_audit(actor_id,subject_id,action)
               VALUES (%s,%s,'block')""",
            (actor_id, user_id)
        )
    return bool(row)
