"""Display-only bounded membership reuse; never a legal/audit completion cache.

One slow restored intake scan must not be duplicated by every browser refresh.
No source or database writes, persistent counters, file removal or queue changes.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from itertools import islice
import json
import os
import sqlite3
import threading
import time

TTL_SECONDS = 30.0
MAX_AGE_SECONDS = 300.0
COLD_WAIT_SECONDS = 3.0
RETRY_SECONDS = 10.0
MAX_MEMBERS = 20000


def _emit(message):
    # A daemon source read may finish during process shutdown. Avoid Python
    # buffered stdout locks; the fixed aggregate line contains no private data.
    try:
        os.write(1, (message + "\n").encode("utf-8"))
    except OSError:
        pass  # No active stdout (e.g. container shutdown); never alter data.


class MembershipUnavailable(RuntimeError):
    pass


class MembershipCache:
    """Per-app, immutable last successful membership with one physical scan.

    Only the dashboard consumes this value. Core verification and request
    freezing continue to use the real source directly. Never interpret a
    missing/failed source as an empty successful list. An overdue refresh can
    serve the last real snapshot for at most MAX_AGE_SECONDS, with its age.
    """
    def __init__(self, getter, *, clock=time.monotonic, ttl=TTL_SECONDS,
                 max_age=MAX_AGE_SECONDS, retry=RETRY_SECONDS):
        self.getter = getter
        self.clock = clock
        self.ttl, self.max_age, self.retry = ttl, max_age, retry
        self._lock = threading.Lock()
        self._future = None
        self._value = None
        self._captured = None
        self._refreshed_at = None
        self._next_retry = 0.0

    def refresh(self):
        with self._lock:
            now = self.clock()
            if self._future is not None:
                return self._future
            if now < self._next_retry:
                return None
            if self._value is not None and now - self._captured < self.ttl:
                return None
            future = Future()
            self._future = future
            threading.Thread(target=self._load, args=(future,),
                             name="eis-dashboard-membership", daemon=True).start()
            return future

    def _load(self, future):
        started = self.clock()
        try:
            values = self.getter()
            if values is None or isinstance(values, (str, bytes, dict)):
                raise MembershipUnavailable("invalid_membership_source")
            rows = list(islice(iter(values), MAX_MEMBERS + 1))
            if len(rows) > MAX_MEMBERS:
                raise MembershipUnavailable("membership_budget")
            # Use exactly the same string identity as the existing dashboard.
            value = frozenset(str(row) for row in rows)
            with self._lock:
                self._value = value
                self._captured = self.clock()
                self._refreshed_at = datetime.now(timezone.utc).isoformat()
                self._next_retry = 0.0
                self._future = None
            future.set_result(True)
            _emit("DASHBOARD_MEMBERSHIP_READY " + json.dumps({
                "members": len(value), "elapsed_ms": round((self.clock()-started)*1000, 1),
                "basis": "display_membership_only_not_audit_completion"}))
        except Exception as exc:
            with self._lock:
                self._next_retry = self.clock() + self.retry
                self._future = None
            # Retrieve failure only as a fixed outcome; do not leak private
            # source paths, SQL, account data or credentials into the response.
            future.set_result(False)
            _emit("DASHBOARD_MEMBERSHIP_DEFERRED type=" + type(exc).__name__)

    def snapshot(self, *, wait_seconds=COLD_WAIT_SECONDS):
        future = self.refresh()
        with self._lock:
            usable = self._value is not None and self.clock()-self._captured <= self.max_age
        if not usable and future is not None:
            try:
                future.result(timeout=max(0.0, wait_seconds))
            except FutureTimeout:
                # The real scan keeps its single-flight slot after this waiter
                # times out. A retry cannot create another scan/thread.
                pass
        with self._lock:
            age = None if self._captured is None else max(0.0, self.clock()-self._captured)
            if self._value is None or age > self.max_age:
                raise MembershipUnavailable("membership_refresh_pending")
            return self._value, {
                "membership_refreshed_at": self._refreshed_at,
                "membership_age_seconds": round(age, 3),
                "membership_refreshing": self._future is not None,
                "membership_stale": age >= self.ttl,
                "membership_basis": "last_successful_display_snapshot",
            }


def ensure_read_indexes(connect):
    """Small additive indexes, once at startup; no data rewrite/deletion.

    Partial indexes avoid walking the many ordinary checklist rows to count
    saved violations. Failures remain observable, without blocking startup
    indefinitely or pretending that an index was created.
    """
    db = None
    deadline = time.monotonic() + 5.0
    try:
        db = connect()
        db.execute("PRAGMA busy_timeout=1000")
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, predicate in (("findings", "kind='violation'"), ("checks", "status='violation'")):
            if table in tables:
                db.execute("CREATE INDEX IF NOT EXISTS dashboard_" + table +
                           "_violations_reg ON " + table + "(reg_number) WHERE " + predicate)
        if "purchases" in tables:
            db.execute("CREATE INDEX IF NOT EXISTS dashboard_purchase_order "
                       "ON purchases(last_seen DESC,reg_number)")
        db.commit()
        print("DASHBOARD_READ_INDEXES_READY", flush=True)
        return True
    except sqlite3.Error as exc:
        if db is not None:
            db.rollback()
        print("DASHBOARD_READ_INDEXES_DEFERRED type=" + type(exc).__name__, flush=True)
        return False
    finally:
        if db is not None:
            db.set_progress_handler(None, 0)
            db.close()
