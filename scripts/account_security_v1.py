"""Database-independent account security helpers.

Production integration requires HTTPS, origin checks, rate limits and a
complete server-side access-control gate before any signup endpoint is live.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timezone


def new_session_token():
    return secrets.token_urlsafe(48)


def digest_session_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_token_matches(presented_token, stored_digest):
    return hmac.compare_digest(digest_session_token(presented_token), stored_digest)


def normalize_email(email):
    result = email.strip().casefold()
    if len(result) > 254 or "@" not in result or any(c.isspace() for c in result):
        raise ValueError("Invalid email")
    return result


def can_access_session(account_status, expiry, revoked_at=None, current_time=None):
    if account_status != "approved" or revoked_at is not None:
        return False
    now = current_time or datetime.now(timezone.utc)
    return expiry > now


def owner_access(actor_id, owner_id, account_status):
    return account_status == "approved" and actor_id == owner_id
