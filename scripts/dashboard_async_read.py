"""Bounded, read-only dashboard snapshots under ongoing EIS SQLite writes.

No procurement, finding, audit completion, or document state is mutated.
Actual results are computed by the existing reconciler; caching only reuses
a labelled, time-bounded successful result for the exact same filter query.
"""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import threading
import time
import sqlite3


class DashboardWarming(RuntimeError):
    """The exact dashboard snapshot is still being calculated."""


class SQLiteDataVersion:
    """Read-only persistent SQLite observer: detects edits, not just row counts.

    A single dedicated connection is essential: PRAGMA data_version must be
    measured from the SAME connection on subsequent requests. Never use a
    new connection's initial counter as a false freshness signal.
    """
    def __init__(self, connect):
        self.connect = connect
        self._db = None
        self._guard = threading.Lock()

    def __call__(self):
        with self._guard:
            if self._db is None:
                db = self.connect()
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA busy_timeout=500")
                self._db = db
            try:
                return int(self._db.execute("PRAGMA data_version").fetchone()[0])
            except sqlite3.Error:
                try:
                    self._db.close()
                finally:
                    self._db = None
                raise


class DashboardReadCoordinator:
    def __init__(self, *, ttl=20.0, max_stale=90.0, wait=0.75,
                 max_active=2, max_entries=40, clock=time.monotonic,
                 version_getter=None):
        self.ttl = ttl
        self.max_stale = max_stale
        self.wait = wait
        self.max_active = max_active
        self.max_entries = max_entries
        self.clock = clock
        self.version_getter = version_getter or (lambda: 0)
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._running = {}
        self._retry = {}

    def _response(self, snapshot, age):
        completed, value, wall_time = snapshot
        # Never mutate the cached response, or the reconciled finding list.
        return {**value,
                "dashboard_snapshot_as_of": wall_time,
                "dashboard_snapshot_age_seconds": round(age, 2),
                "dashboard_stale": age >= self.ttl,
                "dashboard_count_basis": "verified_saved_exact_snapshot"}

    def _start_locked(self, key, compute):
        future = Future()
        self._running[key] = future
        thread = threading.Thread(target=self._run, args=(key, compute, future),
                                  name="eis-dashboard-read", daemon=True)
        thread.start()
        return future

    def _run(self, key, compute, future):
        try:
            result = compute()
            if not isinstance(result, dict) or "stats" not in result or "purchases" not in result:
                raise ValueError("Invalid dashboard result")
            snapshot = (self.clock(), result, datetime.now(timezone.utc).isoformat())
            with self._lock:
                self._entries[key] = snapshot
                self._entries.move_to_end(key)
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
                self._retry.pop(key, None)
                self._running.pop(key, None)
            future.set_result(True)
        except Exception as error:
            # Keep an existing valid snapshot; never turn an error into an
            # empty list or a false zero-violation result.
            with self._lock:
                self._retry[key] = self.clock() + 5.0
                self._running.pop(key, None)
            future.set_exception(error)

    def prime(self, key, compute):
        """Begin one default-filter read at startup without delaying readiness."""
        if len(key) > 1024:
            return False
        key = (key, self.version_getter())
        with self._lock:
            if key in self._running or len(self._running) >= self.max_active:
                return False
            if key in self._entries and self.clock() - self._entries[key][0] < self.ttl:
                return False
            if self.clock() < self._retry.get(key, 0):
                return False
            self._start_locked(key, compute)
            return True

    def get(self, key, compute):
        if len(key) > 1024:
            raise ValueError("dashboard_filter_too_long")
        # A new SQLite commit (including in-place evidence edits and manual
        # ownership changes) invalidates old snapshots before display.
        key = (key, self.version_getter())
        with self._lock:
            now = self.clock()
            snapshot = self._entries.get(key)
            if snapshot is not None:
                age = max(0.0, now - snapshot[0])
                if age <= self.max_stale:
                    self._entries.move_to_end(key)
                    if age < self.ttl:
                        return self._response(snapshot, age)
                else:
                    snapshot = None
            future = self._running.get(key)
            if future is None and len(self._running) < self.max_active and now >= self._retry.get(key, 0):
                future = self._start_locked(key, compute)
            # Stale-while-refresh is always explicitly labelled, never
            # presented as a fresh live audit.
            if snapshot is not None:
                return self._response(snapshot, max(0.0, now - snapshot[0]))
        if future is not None:
            try:
                future.result(timeout=self.wait)
            except FutureTimeout as exc:
                raise DashboardWarming("dashboard_refresh_pending") from exc
            with self._lock:
                snapshot = self._entries.get(key)
                if snapshot is not None:
                    return self._response(snapshot, max(0.0, self.clock() - snapshot[0]))
        raise DashboardWarming("dashboard_refresh_pending")
