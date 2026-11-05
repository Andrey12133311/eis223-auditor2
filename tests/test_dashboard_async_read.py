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

    def test_continuous_commits_keep_one_read_and_publish_a_labelled_snapshot(self):
        version = [1]
        entered, release, refreshed = threading.Event(), threading.Event(), threading.Event()
        calls = []
        cache = DashboardReadCoordinator(wait=.01, version_getter=lambda: version[0])

        def read():
            calls.append(1)
            entered.set()
            gate = release if len(calls) == 1 else refreshed
            if not gate.wait(3):
                raise RuntimeError("fixture read did not release")
            return RESULT

        try:
            with self.assertRaises(DashboardWarming):
                cache.get("default", read)
            self.assertTrue(entered.wait(1))
            first = next(iter(cache._running.values()))
            for change in range(2, 8):
                version[0] = change
                with self.assertRaises(DashboardWarming):
                    cache.get("default", read)
            self.assertEqual(len(calls), 1)
            release.set()
            first.result(1)
            version[0] += 1
            old = cache.get("default", read)
            self.assertEqual(old["stats"], RESULT["stats"])
            self.assertTrue(old["dashboard_stale"])
            self.assertTrue(old["dashboard_source_changed"])
            second = next(iter(cache._running.values()))
            for change in range(9, 14):
                version[0] = change
                self.assertTrue(cache.get("default", read)["dashboard_stale"])
            self.assertEqual(len(calls), 2)
        finally:
            release.set(); refreshed.set()
            for future in list(cache._running.values()):
                future.result(1)

    def test_fast_revision_refresh_returns_edited_result_immediately(self):
        version = [1]
        cache = DashboardReadCoordinator(wait=.2, version_getter=lambda: version[0])
        cache.get("default", lambda: RESULT)
        version[0] += 1
        updated = {"stats": {"purchases": 2, "violations": 0}, "purchases": []}
        current = cache.get("default", lambda: updated)
        self.assertEqual(current["stats"]["violations"], 0)
        self.assertFalse(current["dashboard_stale"])
        self.assertFalse(current["dashboard_source_changed"])

    def test_visibility_guard_failure_never_returns_previously_cached_rows(self):
        import sqlite3
        fail=[False]
        def guard():
            if fail[0]:raise sqlite3.OperationalError('database is locked')
            return 'shared-scope'
        cache=DashboardReadCoordinator(wait=.2,stale_guard_getter=guard)
        self.assertEqual(cache.get('default',lambda:RESULT)['purchases'],RESULT['purchases'])
        fail[0]=True
        with self.assertRaises(sqlite3.OperationalError):
            cache.get('default',lambda:RESULT)

    def test_long_read_age_starts_before_computation_not_after_completion(self):
        moment, version = [100.0], [1]
        cache = DashboardReadCoordinator(clock=lambda: moment[0], wait=.2,
                                         version_getter=lambda: version[0])
        def long_read():
            moment[0] += 56
            version[0] += 1
            return RESULT
        result = cache.get("default", long_read)
        self.assertEqual(result["dashboard_snapshot_age_seconds"], 56)
        self.assertTrue(result["dashboard_stale"])
        self.assertTrue(result["dashboard_source_changed"])

    def test_changed_visibility_guard_blocks_old_rows_until_refresh_completes(self):
        guard = ["public-before"]
        cache = DashboardReadCoordinator(wait=.01, stale_guard_getter=lambda: guard[0])
        cache.get("default", lambda: RESULT)
        hold = threading.Event()
        safe = {"stats": {"purchases": 0, "violations": 0}, "purchases": []}
        def refresh():
            if not hold.wait(3):
                raise RuntimeError("fixture refresh did not release")
            return safe
        try:
            # A visibility fence is independent of the ordinary data version.
            guard[0] = "private-after"
            with self.assertRaises(DashboardWarming):
                cache.get("default", refresh)
            pending = next(iter(cache._running.values()))
            hold.set(); pending.result(1)
            self.assertEqual(cache.get("default", refresh)["purchases"], [])
        finally:
            hold.set()

    def test_guard_change_during_read_cannot_be_hidden_by_returning_to_old_guard(self):
        guard = ["before"]
        cache = DashboardReadCoordinator(wait=.01, stale_guard_getter=lambda: guard[0])
        first_release, next_release = threading.Event(), threading.Event()
        calls = []
        def read():
            calls.append(1)
            gate = first_release if len(calls) == 1 else next_release
            if not gate.wait(3):
                raise RuntimeError("fixture guard read did not release")
            return RESULT
        try:
            with self.assertRaises(DashboardWarming): cache.get("default", read)
            first = next(iter(cache._running.values()))
            guard[0] = "changed-during-read"
            first_release.set(); first.result(1)
            guard[0] = "before"
            with self.assertRaises(DashboardWarming): cache.get("default", read)
            self.assertEqual(len(calls), 2)
        finally:
            first_release.set(); next_release.set()
            for future in list(cache._running.values()): future.result(1)

    def test_expired_snapshot_is_not_returned_during_source_churn(self):
        moment, version = [0.0], [1]
        cache = DashboardReadCoordinator(clock=lambda: moment[0], wait=.01,
                                         max_stale=25,
                                         version_getter=lambda: version[0])
        cache.get("default", lambda: RESULT)
        moment[0], version[0] = 26, 2
        hold = threading.Event()
        def read():
            if not hold.wait(3): raise RuntimeError("fixture expired read did not release")
            return RESULT
        try:
            with self.assertRaises(DashboardWarming): cache.get("default", read)
        finally:
            hold.set()
            for future in list(cache._running.values()): future.result(1)

    def test_failed_refresh_retains_guarded_snapshot_without_inventing_zero(self):
        version = [1]
        cache = DashboardReadCoordinator(wait=.2, version_getter=lambda: version[0])
        cache.get("default", lambda: RESULT)
        version[0] += 1
        def fail(): raise RuntimeError("fixture unavailable")
        result = cache.get("default", fail)
        self.assertEqual(result["stats"]["purchases"], 2)
        self.assertTrue(result["dashboard_stale"])
        self.assertTrue(result["dashboard_source_changed"])

    def test_revision_churn_does_not_bypass_cooldown_or_grow_retry_map(self):
        version, calls = [1], []
        cache = DashboardReadCoordinator(wait=.2, max_entries=3,
                                         version_getter=lambda: version[0])
        def fail():
            calls.append(1)
            raise RuntimeError("fixture unavailable")
        with self.assertRaises(RuntimeError): cache.get("failed", fail)
        for change in range(2, 8):
            version[0] = change
            with self.assertRaises(DashboardWarming): cache.get("failed", fail)
        self.assertEqual(len(calls), 1)
        for index in range(8):
            with self.assertRaises(RuntimeError): cache.get("other-"+str(index), fail)
        self.assertLessEqual(len(cache._retry), 3)

    def test_prime_and_request_share_one_physical_read_across_revisions(self):
        version = [1]
        entered, release = threading.Event(), threading.Event()
        calls = []
        cache = DashboardReadCoordinator(wait=.01, version_getter=lambda: version[0])
        def read():
            calls.append(1); entered.set()
            if not release.wait(3): raise RuntimeError("fixture prime did not release")
            return RESULT
        try:
            self.assertTrue(cache.prime("default", read))
            self.assertTrue(entered.wait(1))
            version[0] += 1
            self.assertFalse(cache.prime("default", read))
            with self.assertRaises(DashboardWarming): cache.get("default", read)
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            for future in list(cache._running.values()): future.result(1)


if __name__ == "__main__":
    unittest.main()
