"""Bounded, read-only dashboard snapshots under ongoing EIS SQLite writes.

No procurement, finding, audit completion, or document state is mutated.
Actual results are computed by the existing reconciler; caching only reuses
a labelled, time-bounded successful result for the exact same filter query.
"""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
import sqlite3
from pathlib import Path


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
                probe = self.connect()
                try:
                    filename = probe.execute("PRAGMA database_list").fetchone()[2]
                finally:
                    probe.close()
                if not filename:
                    raise sqlite3.OperationalError("Dashboard requires a file-backed SQLite database")
                # The observer serves multiple request threads, but never
                # writes and never keeps a read transaction open.
                db = sqlite3.connect(Path(filename).resolve().as_uri()+"?mode=ro",
                                     uri=True, check_same_thread=False,
                                     isolation_level=None, timeout=0.5)
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


@dataclass(frozen=True)
class _Snapshot:
    started: float
    value: dict
    wall_time: str
    version: object
    guard: object
    source_changed: bool
    guard_stable: bool


class DashboardReadCoordinator:
    def __init__(self, *, ttl=20.0, max_stale=90.0, wait=0.75,
                 max_active=2, max_entries=40, clock=time.monotonic,
                 version_getter=None, stale_guard_getter=None):
        self.ttl = ttl
        self.max_stale = max_stale
        self.wait = wait
        self.max_active = max_active
        self.max_entries = max_entries
        self.clock = clock
        self.version_getter = version_getter or (lambda: 0)
        # Unlike ordinary data changes, visibility/ownership changes prohibit
        # reuse of an older snapshot. The caller supplies a read-only fence.
        self.stale_guard_getter = stale_guard_getter or (lambda: 0)
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._running = {}
        self._retry = OrderedDict()

    def _usable(self, snapshot, now, guard):
        return (snapshot is not None and snapshot.guard_stable and
                snapshot.guard == guard and
                max(0.0, now - snapshot.started) <= self.max_stale)

    def _source_changed(self, snapshot, version):
        return snapshot.source_changed or snapshot.version != version

    def _fresh(self, snapshot, now, version, guard):
        return (self._usable(snapshot, now, guard) and
                now - snapshot.started < self.ttl and
                not self._source_changed(snapshot, version))

    def _response(self, snapshot, now, version):
        age = max(0.0, now - snapshot.started)
        source_changed = self._source_changed(snapshot, version)
        # Never mutate the cached response, or the reconciled finding list.
        return {**snapshot.value,
                "dashboard_snapshot_as_of": snapshot.wall_time,
                "dashboard_snapshot_age_seconds": round(age, 2),
                "dashboard_stale": source_changed or age >= self.ttl,
                "dashboard_source_changed": source_changed,
                "dashboard_count_basis": "verified_saved_exact_snapshot"}

    def _start_locked(self, key, compute, version, guard):
        future = Future()
        self._running[key] = future
        thread = threading.Thread(target=self._run, args=(key, compute, future, version, guard),
                                  name="eis-dashboard-read", daemon=True)
        thread.start()
        return future

    def _run(self, key, compute, future, version, guard):
        try:
            started = self.clock()
            wall_time = datetime.now(timezone.utc).isoformat()
            result = compute()
            if not isinstance(result, dict) or "stats" not in result or "purchases" not in result:
                raise ValueError("Invalid dashboard result")
            # A long read is already old when it finishes. Do not label its
            # completion time as the source snapshot time, or make a read
            # spanning SQLite commits appear current.
            source_changed = self.version_getter() != version
            guard_stable = self.stale_guard_getter() == guard
            snapshot = _Snapshot(started, result, wall_time, version, guard,
                                 source_changed, guard_stable)
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
                self._retry.move_to_end(key)
                while len(self._retry) > self.max_entries:
                    self._retry.popitem(last=False)
                self._running.pop(key, None)
            future.set_exception(error)

    def prime(self, key, compute):
        """Begin one default-filter read at startup without delaying readiness."""
        if len(key) > 1024:
            return False
        version, guard = self.version_getter(), self.stale_guard_getter()
        with self._lock:
            if key in self._running or len(self._running) >= self.max_active:
                return False
            if self._fresh(self._entries.get(key), self.clock(), version, guard):
                return False
            if self.clock() < self._retry.get(key, 0):
                return False
            self._start_locked(key, compute, version, guard)
            return True

    def get(self, key, compute):
        if len(key) > 1024:
            raise ValueError("dashboard_filter_too_long")
        # Commit versions invalidate freshness, not the single-flight key.
        # Otherwise continuous collector writes make every finished read
        # unreachable and start another expensive read on each poll.
        version, guard = self.version_getter(), self.stale_guard_getter()
        with self._lock:
            now = self.clock()
            snapshot = self._entries.get(key)
            if not self._usable(snapshot, now, guard):
                snapshot = None
            if snapshot is not None:
                self._entries.move_to_end(key)
                if self._fresh(snapshot, now, version, guard):
                    return self._response(snapshot, now, version)
            future = self._running.get(key)
            if future is None and len(self._running) < self.max_active and now >= self._retry.get(key, 0):
                future = self._start_locked(key, compute, version, guard)
            # A revision change gets a bounded chance to refresh immediately
            # (e.g. a quick evidence edit). If it is slow, return only the
            # correctly labelled, bounded, visibility-safe previous snapshot.
            if snapshot is not None and (future is None or
                                         not self._source_changed(snapshot, version)):
                return self._response(snapshot, now, version)
        if future is not None:
            error = None
            try:
                future.result(timeout=self.wait)
            except FutureTimeout:
                pass
            except Exception as exc:
                error = exc
            version, guard = self.version_getter(), self.stale_guard_getter()
            with self._lock:
                snapshot = self._entries.get(key)
                now = self.clock()
                if self._usable(snapshot, now, guard):
                    return self._response(snapshot, now, version)
            if error is not None:
                raise error
        raise DashboardWarming("dashboard_refresh_pending")
