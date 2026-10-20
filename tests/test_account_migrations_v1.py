"""DDL and additive-upgrade contracts using a DB-API compatible recorder."""
import unittest
from scripts.account_migrations_v1 import initialize_schema


class RecordingConnection:
    def __init__(self, role="CHECK (role IN ('user','manager','admin'))",
                 alerts="CHECK (kind IN ('assignment','review_feedback','role_change'))"):
        self.statements = []
        self.role = role
        self.alerts = alerts
        self.row = None

    def execute(self, statement):
        self.statements.append(statement)
        self.row = None
        if statement.lstrip().startswith("SELECT"):
            value = self.role if "account_users_role_check" in statement else self.alerts
            self.row = (value,) if value is not None else None
        return self

    def fetchone(self):
        return self.row


class MigrationTests(unittest.TestCase):
    def test_schema_runs_one_statement_at_a_time(self):
        db = RecordingConnection()
        count = initialize_schema(db)
        # Return value counts static schema DDL; additive upgrades and catalog
        # reads are additional statements, not failed or skipped migrations.
        self.assertGreaterEqual(count, 8)
        self.assertGreater(len(db.statements), count)
        self.assertTrue(all(stmt.lstrip().upper().startswith(("CREATE ", "ALTER "))
                            for stmt in db.statements[:count]))
        self.assertTrue(all(";" not in stmt for stmt in db.statements))
        self.assertTrue(all(stmt.lstrip().upper().startswith(("CREATE ", "ALTER ", "SELECT "))
                            for stmt in db.statements))
        self.assertTrue(any("account_users" in stmt for stmt in db.statements))
        self.assertTrue(any("account_sessions" in stmt for stmt in db.statements))
        self.assertEqual(sum(stmt.lstrip().startswith("SELECT") for stmt in db.statements), 2)
        self.assertFalse(any("DROP CONSTRAINT" in stmt for stmt in db.statements))

    def test_existing_old_constraints_are_upgraded_without_deleting_rows(self):
        db = RecordingConnection("CHECK (role IN ('user','admin'))",
                                 "CHECK (kind IN ('new_violation'))")
        initialize_schema(db)
        upgrades = [stmt for stmt in db.statements if "ADD CONSTRAINT" in stmt]
        self.assertEqual(len(upgrades), 2)
        self.assertTrue(any("'manager'" in stmt for stmt in upgrades))
        self.assertTrue(any("'assignment'" in stmt for stmt in upgrades))
        self.assertFalse(any(stmt.lstrip().upper().startswith(("DELETE ", "UPDATE ", "TRUNCATE ", "DROP TABLE "))
                             for stmt in db.statements))


if __name__ == "__main__":
    unittest.main()
