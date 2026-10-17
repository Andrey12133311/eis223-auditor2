"""Smoke tests for live thematic ownership + redesigned application pages.

No network connection, production DB writes or EIS data modifications.
Run in Docker build to fail before release on accidental routing/UI regression.
"""
from __future__ import annotations
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import v342_thematic_queue as theme
import v360_thematic_ownership as owners


def check_thematic_sql():
    with tempfile.TemporaryDirectory() as name:
        db_path = Path(name) / "thematic_test.sqlite"
        ns = {"conn": lambda: sqlite3.connect(str(db_path))}
        theme.init(ns)
        one, two = "a" * 24, "b" * 24
        with ns["conn"]() as db:
            for index, batch in enumerate((one, two)):
                db.execute(
                    "INSERT INTO thematic_batches(id,categories,created_at,status) VALUES(?,?,?,?)",
                    (batch, '["docs"]', f"2026-10-17T10:0{index}:00+00:00", "active"))
                db.execute(
                    "INSERT INTO thematic_items(batch_id,reg,status,updated_at) "
                    "VALUES(?,?,?,?)",
                    (batch, f"326164811{index:02d}", "completed", "2026-10-17T10:00:00+00:00"))
        assert theme.thematic_history_records(ns, []) == {"batches": []}
        left = theme.thematic_history_records(ns, [one])["batches"]
        right = theme.thematic_history_records(ns, [two])["batches"]
        all_batches = theme.thematic_history_records(ns, [one, two])["batches"]
        assert len(left) == len(right) == 1 and len(all_batches) == 2
        assert left[0]["id"] == one and right[0]["id"] == two
        assert left[0]["purchases"] == 1 and left[0]["violations"] == 0


def check_scope():
    from scripts.account_production_v1 import thematic_user_route, add_account_navigation
    from scripts.account_site_ui_v1 import site_header
    from scripts.account_pages_v1 import THEMATIC

    allowed = (
        ("/thematic", "GET"),
        ("/api/thematic/start", "POST"),
        ("/api/thematic/history", "GET"),
        ("/api/thematic/" + "a" * 24, "GET"),
        ("/api/thematic/" + "a" * 24, "DELETE"),
        ("/api/thematic/" + "a" * 24 + "/stop", "POST"),
        ("/thematic/card/" + "a" * 24 + "/32616481101", "GET"),
    )
    assert all(thematic_user_route(*part) for part in allowed)
    assert not thematic_user_route("/api/thematic/history", "DELETE")
    assert not thematic_user_route("/api/thematic/start", "GET")
    assert not thematic_user_route("/api/thematic/" + "a" * 24 + "/stop", "GET")
    assert not thematic_user_route("/api/thematic/" + "a" * 24, "POST")
    assert not thematic_user_route("/api/thematic/bad", "DELETE")
    assert b"eis223-new-batch" not in site_header()
    assert b"'/api/thematic/history'" in site_header()
    assert "/api/thematic/history" in THEMATIC
    assert "/me/thematic-batches" not in THEMATIC
    assert "create-personal" not in THEMATIC
    assert "Идентификатор тематической очереди" not in THEMATIC

    page = b'<html><head></head><body><main><div class="stats"></div></main></body></html>'
    output = add_account_navigation(page)
    assert b"eis361-home-script" in output
    assert b"eis361-home-style" in output
    assert b"eis223-toggle-cabinet" in output
    assert output.count(b"eis361-home-script") == 1
    assert add_account_navigation(output) == output


def check_owner_fail_closed():
    class Request:
        def __init__(self, owner=None):
            self.scope = {} if owner is None else {"eis223_account_id": owner}

    with patch.dict(os.environ, {
        "EIS223_ACCOUNTS_MOUNT": "true", "EIS223_AUTH_ENFORCE": "true"
    }):
        from fastapi import HTTPException
        try:
            owners.account_id(Request())
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("Anonymous thematic access was granted")
        assert owners.account_id(Request("user-1")) == "user-1"


if __name__ == "__main__":
    check_thematic_sql()
    check_scope()
    check_owner_fail_closed()
    print("V360_REAL_THEMATIC_SELFTEST_OK owner_scoped=True "
          "real_queue_history=True no_placeholder_creation=True "
          "modern_main=True no_data_mutation=True", flush=True)
