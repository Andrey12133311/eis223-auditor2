"""One-time administrator bootstrap for a separate identity database.

Usage (in a secured operator terminal):
  EIS223_IDENTITY_DATABASE_URL=... python -m scripts.account_bootstrap_v1

Reads admin email and password from terminal. Never prints the password.
Never upgrades a preexisting user to admin and never creates a second admin.
"""
import getpass
import os
import sys
from uuid import uuid4

from scripts.account_passwords_v1 import hash_password
from scripts.account_security_v1 import normalize_email


def create_first_admin(db, email, password_hash):
    # Transaction-level advisory lock prevents concurrent first-admin creation.
    db.execute("SELECT pg_advisory_xact_lock(2230001)")
    existing = db.execute(
        "SELECT 1 FROM account_users WHERE role='admin' LIMIT 1"
    ).fetchone()
    if existing:
        raise RuntimeError("An administrator already exists")
    user_id = uuid4()
    row = db.execute(
        """INSERT INTO account_users(id,email,password_hash,status,role,approved_at)
           VALUES (%s,%s,%s,'approved','admin',CURRENT_TIMESTAMP)
           ON CONFLICT DO NOTHING RETURNING id""",
        (user_id, email, password_hash)
    ).fetchone()
    if not row:
        raise RuntimeError("Email is already registered; use a different admin email")
    db.execute(
        "INSERT INTO account_audit(actor_id,subject_id,action) VALUES (%s,%s,'bootstrap_admin')",
        (user_id, user_id)
    )
    return user_id


def main():
    dsn = os.environ.get("EIS223_IDENTITY_DATABASE_URL")
    if not dsn:
        raise SystemExit("EIS223_IDENTITY_DATABASE_URL is required")
    if not sys.stdin.isatty():
        raise SystemExit("Run interactively in a trusted operator terminal")
    email = normalize_email(input("Administrator email: "))
    password = getpass.getpass("Administrator password (12+ chars): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    encoded = hash_password(password)
    import psycopg
    with psycopg.connect(dsn) as db:
        admin_id = create_first_admin(db, email, encoded)
    print("Administrator created:", admin_id)


if __name__ == "__main__":
    main()
