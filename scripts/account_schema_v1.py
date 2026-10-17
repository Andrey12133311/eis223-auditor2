"""Identity schema for an isolated PostgreSQL database.

No schema changes to the production EIS SQLite database.
Run against a separately provisioned database after review.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS account_users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','manager','admin')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','blocked')),
    email_verified_at TIMESTAMPTZ,
    approved_by UUID,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS account_users_email_unique
    ON account_users (lower(email));
CREATE TABLE IF NOT EXISTS account_email_challenges (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES account_users(id),
    token_digest TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS account_email_challenges_user ON account_email_challenges(user_id);
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
CREATE TABLE IF NOT EXISTS account_manual_checks (
    user_id UUID NOT NULL REFERENCES account_users(id),
    reg_number TEXT NOT NULL CHECK (reg_number ~ '^3[0-9]{10}$'),
    job_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, reg_number)
);
CREATE INDEX IF NOT EXISTS account_manual_checks_owner_recent
    ON account_manual_checks (user_id, last_started_at DESC);
CREATE TABLE IF NOT EXISTS account_thematic_batches (
    user_id UUID NOT NULL REFERENCES account_users(id),
    batch_id TEXT NOT NULL,
    PRIMARY KEY(user_id, batch_id)
);
CREATE TABLE IF NOT EXISTS account_thematic_procurements (
    user_id UUID NOT NULL REFERENCES account_users(id),
    batch_id TEXT NOT NULL,
    reg_number TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, batch_id, reg_number),
    FOREIGN KEY(user_id, batch_id) REFERENCES account_thematic_batches(user_id, batch_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS account_thematic_jobs (
    user_id UUID NOT NULL,
    batch_id TEXT NOT NULL,
    reg_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    PRIMARY KEY(user_id, batch_id, reg_number),
    FOREIGN KEY(user_id, batch_id, reg_number)
        REFERENCES account_thematic_procurements(user_id, batch_id, reg_number)
        ON DELETE CASCADE,
    CHECK(status IN ('queued', 'running', 'completed', 'failed'))
);
CREATE TABLE IF NOT EXISTS account_thematic_findings (
    user_id UUID NOT NULL,
    batch_id TEXT NOT NULL,
    reg_number TEXT NOT NULL,
    finding_key TEXT NOT NULL,
    legal_basis TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, batch_id, reg_number, finding_key),
    FOREIGN KEY(user_id, batch_id, reg_number)
        REFERENCES account_thematic_jobs(user_id, batch_id, reg_number)
        ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS account_memos (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES account_users(id),
    reg_number TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS account_memos_owner ON account_memos(user_id);
CREATE TABLE IF NOT EXISTS account_favorites (
    user_id UUID NOT NULL REFERENCES account_users(id),
    kind TEXT NOT NULL CHECK(kind IN ('procurement','customer')),
    entity_key TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id,kind,entity_key)
);
CREATE INDEX IF NOT EXISTS account_favorites_owner
    ON account_favorites(user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS account_alert_snapshots (
    user_id UUID NOT NULL REFERENCES account_users(id),
    source_key TEXT NOT NULL,
    status TEXT NOT NULL,
    violation_count INTEGER NOT NULL DEFAULT 0,
    documents_checked INTEGER NOT NULL DEFAULT 0,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id,source_key)
);
CREATE TABLE IF NOT EXISTS account_alert_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES account_users(id),
    source_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('documents_checked','new_violation','thematic_finished','review_completed')),
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS account_alert_events_owner
    ON account_alert_events(user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS account_memo_blobs (
    memo_id UUID PRIMARY KEY REFERENCES account_memos(id) ON DELETE CASCADE,
    odt BYTEA NOT NULL,
    pdf BYTEA,
    pdf_created_at TIMESTAMPTZ
);
-- Supervisor access is independently granted by an administrator.
-- Each staff member belongs to at most one explicit managerial team.
CREATE TABLE IF NOT EXISTS account_manager_teams (
    user_id UUID PRIMARY KEY REFERENCES account_users(id),
    manager_id UUID NOT NULL REFERENCES account_users(id),
    assigned_by UUID NOT NULL REFERENCES account_users(id),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (user_id <> manager_id)
);
CREATE INDEX IF NOT EXISTS account_manager_teams_manager
    ON account_manager_teams(manager_id,assigned_at DESC);
CREATE TABLE IF NOT EXISTS account_manager_tasks (
    id UUID PRIMARY KEY,
    manager_id UUID NOT NULL REFERENCES account_users(id),
    user_id UUID NOT NULL REFERENCES account_users(id),
    reg_number TEXT NOT NULL CHECK(reg_number ~ '^3[0-9]{10}$'),
    title TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    due_date DATE,
    status TEXT NOT NULL DEFAULT 'assigned'
        CHECK(status IN ('assigned','in_progress','submitted','returned','completed','cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS account_manager_tasks_manager
    ON account_manager_tasks(manager_id,created_at DESC);
CREATE INDEX IF NOT EXISTS account_manager_tasks_user
    ON account_manager_tasks(user_id,status,due_date);
CREATE UNIQUE INDEX IF NOT EXISTS account_manager_tasks_one_active
    ON account_manager_tasks(manager_id,user_id,reg_number)
    WHERE status NOT IN ('completed','cancelled');
CREATE TABLE IF NOT EXISTS account_manager_finding_reviews (
    user_id UUID NOT NULL REFERENCES account_users(id),
    reg_number TEXT NOT NULL,
    finding_key TEXT NOT NULL,
    title TEXT NOT NULL,
    legal_basis TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    reviewer_id UUID NOT NULL REFERENCES account_users(id),
    decision TEXT NOT NULL CHECK(decision IN ('approved','returned','rejected')),
    comment TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id,reg_number,finding_key)
);
CREATE INDEX IF NOT EXISTS account_manager_finding_reviewer
    ON account_manager_finding_reviews(reviewer_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS account_manager_memo_reviews (
    memo_id UUID PRIMARY KEY REFERENCES account_memos(id) ON DELETE CASCADE,
    owner_id UUID NOT NULL REFERENCES account_users(id),
    reviewer_id UUID REFERENCES account_users(id),
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK(status IN ('submitted','approved','returned','rejected')),
    comment TEXT NOT NULL DEFAULT '',
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS account_manager_memo_pending
    ON account_manager_memo_reviews(owner_id,status,submitted_at DESC);
CREATE TABLE IF NOT EXISTS account_manager_actions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id UUID NOT NULL REFERENCES account_users(id),
    target_user_id UUID REFERENCES account_users(id),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS account_manager_actions_recent
    ON account_manager_actions(actor_id,created_at DESC);
-- Personal evidence annotations; append-only to preserve employee history.
CREATE TABLE IF NOT EXISTS account_case_notes (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES account_users(id),
    reg_number TEXT NOT NULL CHECK(reg_number ~ '^3[0-9]{10}$'),
    finding_key TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL CHECK (length(body) BETWEEN 4 AND 3000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS account_case_notes_owner
    ON account_case_notes(user_id,reg_number,created_at DESC);
CREATE TABLE IF NOT EXISTS account_throttle (
    bucket_key TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS account_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id UUID,
    subject_id UUID,
    action TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
