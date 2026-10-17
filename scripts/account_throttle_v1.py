"""Shared PostgreSQL throttle for login and registration requests."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os


def bucket_key(kind, identifier):
    secret = os.environ.get("EIS223_RATE_LIMIT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Missing EIS223_RATE_LIMIT_SECRET")
    return hmac.new(secret.encode(), (kind + ":" + identifier).encode(), hashlib.sha256).hexdigest()


def consume(db, kind, identifier, maximum, window_seconds):
    key = bucket_key(kind, identifier)
    db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
    row = db.execute(
        "SELECT attempts,started_at FROM account_throttle WHERE bucket_key=%s FOR UPDATE",
        (key,),
    ).fetchone()
    now = datetime.now(timezone.utc)
    if row and row[1] > now - timedelta(seconds=window_seconds):
        attempts = row[0] + 1
        db.execute("UPDATE account_throttle SET attempts=%s WHERE bucket_key=%s", (attempts, key))
    else:
        attempts = 1
        db.execute(
            """INSERT INTO account_throttle(bucket_key,attempts,started_at)
               VALUES (%s,1,%s) ON CONFLICT(bucket_key)
               DO UPDATE SET attempts=1,started_at=EXCLUDED.started_at""",
            (key, now),
        )
    return attempts <= maximum


def request_allowed(db, request, email, operation):
    address = request.client.host if request.client else "unknown"
    if operation == "register":
        rules = (("register-ip", address, 8, 3600), ("register-email", email, 3, 86400))
    else:
        rules = (("login-ip", address, 30, 900), ("login-email", email, 8, 900))
    results = [consume(db, kind, identity, limit, window) for kind, identity, limit, window in rules]
    return all(results)
