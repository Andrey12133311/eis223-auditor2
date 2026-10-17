import unittest
from scripts.account_migrations_v1 import initialize_schema


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)


class MigrationTests(unittest.TestCase):
    def test_schema_runs_one_statement_at_a_time(self):
        db = RecordingConnection()
        count = initialize_schema(db)
        self.assertEqual(count, len(db.statements))
        self.assertGreaterEqual(count, 8)
        self.assertTrue(all(not stmt.endswith(";") for stmt in db.statements))
        self.assertTrue(any("account_users" in stmt for stmt in db.statements))
        self.assertTrue(any("account_sessions" in stmt for stmt in db.statements))


if __name__ == "__main__":
    unittest.main()
