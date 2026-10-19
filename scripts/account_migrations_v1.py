"""Initialize the isolated identity schema, never the production EIS database."""
from scripts.account_schema_v1 import SCHEMA


def initialize_schema(db):
    # psycopg 3 does not accept multiple prepared statements in one execute.
    # Split the known, static schema into individual DDL statements.
    # Strip whole-line SQL comments BEFORE splitting DDL statements.
    # A comment such as "-- append-only; preserve history" must never be
    # split into a standalone executable SQL fragment. Preserve originals.
    ddl = "\n".join(
        line for line in SCHEMA.splitlines()
        if not line.lstrip().startswith("--")
    )
    statements = [statement.strip() for statement in ddl.split(";") if statement.strip()]
    if any(not statement.upper().startswith(("CREATE ", "ALTER "))
           for statement in statements):
        raise RuntimeError("Unsafe SQL fragment in account migration")
    for statement in statements:
        db.execute(statement)
    db.execute("ALTER TABLE account_finding_submissions ADD COLUMN IF NOT EXISTS "
               "source_batch TEXT NOT NULL DEFAULT ''")
    # Preserve publication bounds even if the SQLite batch cannot start yet.
    # Existing campaigns remain unbounded; retries must use their saved period.
    for column in ("date_from", "date_to"):
        db.execute(
            "ALTER TABLE account_manager_campaign_targets ADD COLUMN IF NOT EXISTS "
            + column + " TEXT NOT NULL DEFAULT ''"
        )
    # Safe additive migration for existing accounts. Never guess names from email.
    for column in ("last_name", "first_name", "patronymic"):
        db.execute(
            "ALTER TABLE account_users ADD COLUMN IF NOT EXISTS "
            + column + " TEXT NOT NULL DEFAULT ''"
        )
    # Older installations already have a two-role CHECK. Amend only when
    # needed, inside the existing PostgreSQL transaction.
    existing = db.execute(
        """SELECT pg_get_constraintdef(oid)
           FROM pg_constraint
           WHERE conrelid='account_users'::regclass
             AND conname='account_users_role_check'"""
    ).fetchone()
    if not existing or "'manager'" not in str(existing[0]):
        db.execute(
            "ALTER TABLE account_users DROP CONSTRAINT IF EXISTS account_users_role_check"
        )
        db.execute(
            """ALTER TABLE account_users ADD CONSTRAINT account_users_role_check
               CHECK(role IN ('user','manager','admin'))"""
        )
    alert_constraint = db.execute(
        """SELECT pg_get_constraintdef(oid)
           FROM pg_constraint
           WHERE conrelid='account_alert_events'::regclass
             AND conname='account_alert_events_kind_check'"""
    ).fetchone()
    if alert_constraint and "'assignment'" not in str(alert_constraint[0]):
        db.execute(
            "ALTER TABLE account_alert_events DROP CONSTRAINT account_alert_events_kind_check"
        )
        db.execute(
            """ALTER TABLE account_alert_events
               ADD CONSTRAINT account_alert_events_kind_check
               CHECK(kind IN ('documents_checked','new_violation','thematic_finished',
                              'review_completed','assignment','review_feedback','role_change'))"""
        )
    return len(statements)
