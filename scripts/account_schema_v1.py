"""Identity schema for an isolated PostgreSQL database.

No schema changes to the production EIS SQLite database.
Run against a separately provisioned database after review.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS account_users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','blocked')),
    email_verified_at TIMESTAMPTZ,
    approved_by UUID,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS account_users_email_unique
    ON account_users (lower(email));
CREATE TABLE IF NOT EXISTS account_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES account_users(id),
    token_digest TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS account_sessions_user ON account_sessions(user_id);
CREATE TABLE IF NOT EXISTS account_procurements (
    user_id UUID NOT NULL REFERENCES account_users(id),
    reg_number TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, reg_number)
);
CREATE TABLE IF NOT EXISTS account_thematic_batches (
    user_id UUID NOT NULL REFERENCES account_users(id),
    batch_id TEXT NOT NULL,
    PRIMARY KEY(user_id, batch_id)
);
CREATE TABLE IF NOT EXISTS account_memos (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES account_users(id),
    reg_number TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS account_memos_owner ON account_memos(user_id);
CREATE TABLE IF NOT EXISTS account_throttle (\n    bucket_key TEXT PRIMARY KEY,\n    attempts INTEGER NOT NULL,\n    started_at TIMESTAMPTZ NOT NULL\n);\nCREATE TABLE IF NOT EXISTS account_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id UUID,
    subject_id UUID,
    action TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
