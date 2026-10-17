"""Session persistence helpers; caller must authorize account and use a transaction."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from scripts.account_security_v1 import new_session_token, digest_session_token


def create_session(db, user_id, hours=12):
    token = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)
    db.execute(
        """INSERT INTO account_sessions(id,user_id,token_digest,expires_at)
           VALUES (%s,%s,%s,%s)""",
        (uuid4(), user_id, digest_session_token(token), expires)
    )
    return token, expires


def resolve_session(db, token):
    if not isinstance(token, str) or len(token) < 40:
        return None
    return db.execute(
        """SELECT u.id,u.role,u.status FROM account_sessions s
           JOIN account_users u ON u.id=s.user_id
           WHERE s.token_digest=%s AND s.revoked_at IS NULL
           AND s.expires_at>CURRENT_TIMESTAMP AND u.status='approved'""",
        (digest_session_token(token),)
    ).fetchone()


def revoke_session(db, token):
    if not isinstance(token, str) or len(token) < 40:
        return
    db.execute(
        """UPDATE account_sessions SET revoked_at=CURRENT_TIMESTAMP
           WHERE token_digest=%s""",
        (digest_session_token(token),)
    )
