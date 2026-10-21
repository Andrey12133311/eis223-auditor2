"""Regression checks for bounded dashboard reads; no production DB required."""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dashboard_async_read import DashboardReadCoordinator, DashboardWarming


RESULT = {"stats": {"purchases": 2, "violations": 1},
          "purchases": [{"reg_number": "32600000001"}]}


class DashboardReadTests(unittest.TestCase):
    def test_single_flight_and_reuse(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        cache = DashboardReadCoordinator(wait=.01)

        def slow_read():
            calls.append(1)
            started.set()
            self.assertTrue(release.wait(2))
            return RESULT

        with self.assertRaises(DashboardWarming):
            cache.get("?limit=100", slow_read)
        self.assertTrue(started.wait(.5))
        for _ in range(3):
            with self.assertRaises(DashboardWarming):
                cache.get("?limit=100", slow_read)
        self.assertEqual(len(calls), 1)
        release.set()
        for _ in range(60):
            try:
                result = cache.get("?limit=100", slow_read)
                break
            except DashboardWarming:
                time.sleep(.01)
        else:
            self.fail("Dashboard did not finish after source release")
        self.assertEqual(result["stats"]["violations"], 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["dashboard_stale"])
        self.assertIn("dashboard_snapshot_as_of", result)
        self.assertIsNot(result, RESULT)

    def test_stale_is_explicit_and_eventually_replaced(self):
        moment = [100.0]
        cache = DashboardReadCoordinator(ttl=10, max_stale=25, wait=.1,
                                         clock=lambda: moment[0])
        self.assertEqual(cache.get("default", lambda: RESULT)["stats"]["purchases"], 2)
        moment[0] += 11
        hold = threading.Event()
        started = threading.Event()

        def refresh():
            started.set()
            if not hold.wait(2):
                raise RuntimeError("refresh blocked")
            return {"stats": {"purchases": 3, "violations": 2}, "purchases": []}

        stale = cache.get("default", refresh)
        self.assertTrue(stale["dashboard_stale"])
        self.assertEqual(stale["stats"]["purchases"], 2)
        self.assertTrue(started.wait(.5))
        hold.set()
        # Another response can reuse old data while the refresh finishes.
        for _ in range(100):
            fresh = cache.get("default", refresh)
            if not fresh["dashboard_stale"]:
                break
            time.sleep(.01)
        self.assertEqual(fresh["stats"]["purchases"], 3)

    def test_errors_never_become_zero_statistics(self):
        cache = DashboardReadCoordinator(wait=.2)
        def failing():
            raise RuntimeError("sqlite busy")
        with self.assertRaises(RuntimeError):
            cache.get("failed", failing)
        with self.assertRaises(DashboardWarming):
            cache.get("failed", failing)
        self.assertEqual(cache._entries, {})

    def test_new_filters_do_not_serve_another_filters_results(self):
        cache = DashboardReadCoordinator(wait=.1)
        a = cache.get("inn=1234567890", lambda: RESULT)
        b = cache.get("inn=9876543210", lambda:
                      {"stats": {"purchases": 0, "violations": 0}, "purchases": []})
        self.assertEqual(a["stats"]["purchases"], 2)
        self.assertEqual(b["stats"]["purchases"], 0)

    def test_query_length_budget(self):
        cache = DashboardReadCoordinator(wait=.01)
        with self.assertRaises(ValueError):
            cache.get("x" * 1025, lambda: RESULT)


if __name__ == "__main__":
    unittest.main()
