"""Worker delivery deadlines do not falsely acknowledge a VPS report."""
import asyncio
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eis_server_worker as worker


class WorkerDeadlineTests(unittest.TestCase):
    def test_fast_result_is_unchanged(self):
        async def run():
            return await worker._database_call("report", lambda: {"received": True})
        self.assertEqual(asyncio.run(run()), {"received": True})

    def test_slow_report_returns_retryable_503_without_fabricating_ack(self):
        completed = threading.Event()

        def slow_report():
            time.sleep(0.08)
            completed.set()
            return {"received": True}

        async def run():
            with self.assertRaises(HTTPException) as raised:
                await worker._database_call("report", slow_report)
            return raised.exception

        with patch.dict(worker._RESPONSE_BUDGET_SECONDS, {"report": 0.015}):
            with patch.object(worker, "_log_telemetry") as log:
                error = asyncio.run(run())
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.headers["Retry-After"], "300")
        self.assertNotIn("received", str(error.detail))
        log.assert_called_once_with("report_deadline", retry_after_seconds=300)
        # The durable operation is not cancelled mid-transaction. Its later
        # result is resolved by the existing token-fenced replay path.
        self.assertTrue(completed.wait(1))

    def test_sqlite_busy_is_retryable_without_ack(self):
        def busy_report():
            raise sqlite3.OperationalError("database is locked")

        async def run():
            with self.assertRaises(HTTPException) as raised:
                await worker._database_call("report", busy_report)
            return raised.exception

        with patch.object(worker, "_log_database_busy"):
            error = asyncio.run(run())
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.headers["Retry-After"], "300")


if __name__ == "__main__":
    unittest.main()
