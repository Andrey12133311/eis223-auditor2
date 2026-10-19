"""Publication periods survive manager assignment failures and real-batch retries."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import v370_manager_campaigns as campaigns
from scripts.account_migrations_v1 import initialize_schema
import v342_thematic_queue as thematic

MANAGER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
EMPLOYEE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def payload(**updates):
    return {
        "assignee_id": EMPLOYEE, "title": "Проверка сроков публикации",
        "categories": ["dates"], "legal_forms": ["ООО"],
        "target_violations": 5, **updates,
    }


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class IdentityStore:
    """PG boundary double; real campaign insertion uses temporary SQLite."""
    def __init__(self):
        self.goals = {}
        self.owners = set()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args=()):
        if "SELECT COUNT(*) FROM account_thematic_batches" in " ".join(sql.split()):
            owner, active_ids = args
            count = sum(str(who) == str(owner) and batch in active_ids for who, batch in self.owners)
            return Result([(count,)])
        if "SELECT manager_id FROM account_manager_teams" in sql:
            return Result([(MANAGER,)])
        if "INSERT INTO account_manager_campaign_targets" in sql:
            columns = re.search(r"\(([^)]+)\)\s*VALUES", sql).group(1)
            record = dict(zip((x.strip() for x in columns.split(",")), args))
            for field in ("categories", "legal_forms"):
                record[field] = json.loads(record[field])
            record.update(status="preparing", submitted_at=None, completed_at=None,
                          created_at=datetime.now(timezone.utc), email="worker@example.test")
            self.goals[record["id"]] = record
            return Result()
        if "INSERT INTO account_thematic_batches" in sql:
            self.owners.add(tuple(args))
            return Result()
        if "UPDATE account_manager_campaign_targets SET status=" in sql:
            status = re.search(r"SET status='([^']+)'", sql).group(1)
            self.goals[args[0]]["status"] = status
            return Result()
        if "FROM account_manager_campaign_targets" in sql and sql.lstrip().startswith("SELECT"):
            fields = sql.split("SELECT", 1)[1].split("FROM", 1)[0]
            fields = [x.strip().split(".")[-1] for x in fields.split(",")]
            records = list(self.goals.values())
            if "WHERE id=%s" in sql:
                records = [x for x in records if x["id"] == args[0]]
            if "WHERE t.manager_id=%s" in sql:
                records = [x for x in records if x["manager_id"] == args[0]]
            return Result([tuple(row.get(field) for field in fields) for row in records])
        raise AssertionError("Unexpected identity query: " + sql)


class CampaignPeriodTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "thematic.sqlite"
        self.ns = {"conn": lambda: sqlite3.connect(self.file)}
        thematic.init(self.ns)
        self.request = SimpleNamespace(app=SimpleNamespace(
            state=SimpleNamespace(eis223_manual_ns=self.ns)))
        self.identity = IdentityStore()
        env = patch.dict("os.environ", {"EIS223_AUTH_ENFORCE": "true", "EIS223_ACCOUNTS_MOUNT": "true"})
        env.start()
        self.addCleanup(env.stop)
        identity_connection = patch("scripts.account_api_v1.connect", lambda: self.identity)
        identity_connection.start()
        self.addCleanup(identity_connection.stop)
        for name, value in (
            ("connect", lambda: self.identity),
            ("require_origin", lambda request: None),
            ("supervisor", lambda request: (MANAGER, "manager")),
            ("_member", lambda db, actor, role, employee: employee),
            ("_audit", lambda *args, **kwargs: None),
        ):
            patcher = patch.object(campaigns, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_exact_iso_and_order_validation(self):
        for bounds in (
            {"date_from": "2026-10-20", "date_to": "2026-10-19"},
            {"date_from": "2026-02-30"}, {"date_to": "19.10.2026"},
            {"date_from": "2026-10-01T00:00:00Z"},
            {"date_from": "2026-1-01"}, {"date_to": True},
        ):
            with self.subTest(bounds=bounds), self.assertRaises(ValidationError):
                campaigns.CampaignCreate(**payload(**bounds))
        same_day = campaigns.CampaignCreate(**payload(
            date_from="2026-10-19", date_to="2026-10-19"))
        self.assertEqual((same_day.date_from, same_day.date_to),
                         ("2026-10-19", "2026-10-19"))

    def test_optional_and_open_bounds_are_normalized(self):
        for bounds, expected in (
            ({}, ("", "")), ({"date_from": None, "date_to": None}, ("", "")),
            ({"date_from": "2026-10-01"}, ("2026-10-01", "")),
            ({"date_to": "2026-10-19"}, ("", "2026-10-19")),
        ):
            with self.subTest(bounds=bounds):
                model = campaigns.CampaignCreate(**payload(**bounds))
                self.assertEqual((model.date_from, model.date_to), expected)

    def test_successful_create_returns_and_persists_real_scope(self):
        result = campaigns.create(campaigns.CampaignCreate(**payload(
            date_from="2026-10-01", date_to="2026-10-19")), self.request)
        self.assertEqual((result["date_from"], result["date_to"]),
                         ("2026-10-01", "2026-10-19"))
        with sqlite3.connect(self.file) as db:
            row = db.execute("SELECT date_from,date_to FROM thematic_batches WHERE id=?",
                             (result["batch_id"],)).fetchone()
        self.assertEqual(row, ("2026-10-01", "2026-10-19"))
        self.assertIn((EMPLOYEE, result["batch_id"]), self.identity.owners)

    def test_failed_setup_retry_preserves_period_and_owner(self):
        for suffix in "abc":
            campaigns._insert_real_batch(self.ns, suffix * 24, ["docs"], ["ООО"], owner_id=EMPLOYEE)
            self.identity.owners.add((EMPLOYEE, suffix * 24))
        with self.assertRaises(HTTPException) as failure:
            campaigns.create(campaigns.CampaignCreate(**payload(
                date_from="2026-10-01", date_to="2026-10-19")), self.request)
        self.assertEqual(failure.exception.status_code, 429)
        record = next(iter(self.identity.goals.values()))
        self.assertEqual(record["status"], "setup_failed")
        self.assertEqual((record["date_from"], record["date_to"]),
                         ("2026-10-01", "2026-10-19"))
        rows = campaigns._campaign_rows(self.identity, "WHERE t.manager_id=%s", (MANAGER,))
        visible = campaigns._format(rows[0])
        self.assertEqual((visible["date_from"], visible["date_to"]),
                         ("2026-10-01", "2026-10-19"))
        with self.assertRaises(HTTPException) as still_full:
            campaigns.retry(str(record["id"]), self.request)
        self.assertEqual(still_full.exception.status_code, 429)
        with sqlite3.connect(self.file) as db:
            db.execute("UPDATE thematic_batches SET status='stopped' WHERE id=?", ("a" * 24,))
        result = campaigns.retry(str(record["id"]), self.request)
        self.assertEqual(record["status"], "active")
        self.assertEqual((result["date_from"], result["date_to"]),
                         ("2026-10-01", "2026-10-19"))
        with sqlite3.connect(self.file) as db:
            stored = db.execute("SELECT date_from,date_to FROM thematic_batches WHERE id=?",
                                (record["batch_id"],)).fetchone()
        self.assertEqual(stored, ("2026-10-01", "2026-10-19"))
        self.assertIn((EMPLOYEE, record["batch_id"]), self.identity.owners)

    def test_manager_campaign_uses_assignee_capacity_not_foreign_active_batches(self):
        for suffix in "abc":
            campaigns._insert_real_batch(self.ns, suffix * 24, ["docs"], ["ООО"], owner_id=MANAGER)
            self.identity.owners.add((MANAGER, suffix * 24))
        response = campaigns.create(campaigns.CampaignCreate(**payload()), self.request)
        self.assertIn((EMPLOYEE, response["batch_id"]), self.identity.owners)
        self.assertNotIn((MANAGER, response["batch_id"]), self.identity.owners)
        with sqlite3.connect(self.file) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM thematic_batches WHERE status='active'").fetchone()[0], 4)

    def test_repeated_insert_keeps_existing_scope(self):
        identifier = "d" * 24
        for _ in range(2):
            campaigns._insert_real_batch(self.ns, identifier, ["dates"], ["ООО"],
                                         "2026-10-01", "2026-10-19", owner_id=EMPLOYEE)
        with sqlite3.connect(self.file) as db:
            rows = db.execute("SELECT date_from,date_to FROM thematic_batches").fetchall()
        self.assertEqual(rows, [("2026-10-01", "2026-10-19")])

    def test_list_uses_engine_scope_and_old_rows_remain_unbounded(self):
        now = datetime.now(timezone.utc)
        old = [MANAGER, MANAGER, EMPLOYEE, "worker@example.test", "e" * 24,
               "Проверка сроков", ["dates"], ["ООО"], 5, None, "", "active", now, None, None]
        self.assertEqual((campaigns._format(old)["date_from"],
                          campaigns._format(old)["date_to"]), ("", ""))
        current = campaigns._format(old + ["", ""],
            {"date_from": "2026-10-01", "date_to": "2026-10-19", "violations": 2})
        self.assertEqual((current["date_from"], current["date_to"]),
                         ("2026-10-01", "2026-10-19"))
        self.assertEqual(current["actual_violations"], 2)


class MigrationPeriodTests(unittest.TestCase):
    def test_repeated_identity_startup_uses_additive_idempotent_columns(self):
        class Replay:
            def __init__(self):
                self.statements = []

            def execute(self, sql):
                self.statements.append(sql)
                return self

            def fetchone(self):
                return ("CHECK ('manager' IS NOT NULL AND 'assignment' IS NOT NULL)",)

        db = Replay()
        initialize_schema(db)
        initialize_schema(db)
        additions = [sql for sql in db.statements
                     if sql.startswith("ALTER TABLE account_manager_campaign_targets")]
        self.assertEqual(len(additions), 4)
        for field in ("date_from", "date_to"):
            expected = ("ALTER TABLE account_manager_campaign_targets ADD COLUMN IF NOT EXISTS "
                        + field + " TEXT NOT NULL DEFAULT ''")
            self.assertEqual(additions.count(expected), 2)


if __name__ == "__main__":
    unittest.main()
