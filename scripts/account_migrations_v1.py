"""Initialize the isolated identity schema, never the production EIS database."""
from scripts.account_schema_v1 import SCHEMA


def initialize_schema(db):
    # psycopg 3 does not accept multiple prepared statements in one execute.
    # Split the known, static schema into individual DDL statements.
    statements = [statement.strip() for statement in SCHEMA.split(";") if statement.strip()]
    for statement in statements:
        db.execute(statement)
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
    return len(statements)
