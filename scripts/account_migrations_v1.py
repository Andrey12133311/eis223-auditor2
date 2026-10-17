"""Initialize the isolated identity schema, never the production EIS database."""
from scripts.account_schema_v1 import SCHEMA


def initialize_schema(db):
    # psycopg 3 does not accept multiple prepared statements in one execute.
    # Split the known, static schema into individual DDL statements.
    statements = [statement.strip() for statement in SCHEMA.split(";") if statement.strip()]
    for statement in statements:
        db.execute(statement)
    return len(statements)
