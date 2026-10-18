"""Bounded process-local PostgreSQL connections for the identity database.

Only transport connections are reused. Account/session decisions are still
queried by callers for every request. The context manager keeps psycopg's
commit-on-success / rollback-on-error contract without closing the connection.
"""
import atexit
from contextlib import contextmanager
import os
import threading


_lock = threading.Lock()
_pool = None
_pool_key = None
# A forked child must never operate on its parent's libpq sockets or workers.
# Keep inherited pools alive, but inaccessible, for the child's lifetime.
_inherited_pools = []
_CHECKOUT_TIMEOUT = 5.0


def _after_fork():
    global _lock, _pool, _pool_key
    if _pool is not None:
        _inherited_pools.append(_pool)
    _pool = None
    _pool_key = None
    _lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _get_pool():
    global _pool, _pool_key
    # Import only when identity access is used, as with the previous connect().
    from psycopg_pool import ConnectionPool

    dsn = os.getenv("EIS223_IDENTITY_DATABASE_URL")
    if not dsn:
        raise RuntimeError("Identity database is not configured")
    key = (os.getpid(), dsn)
    with _lock:
        if _pool is not None and _pool_key == key and not _pool.closed:
            return _pool
        previous, previous_key = _pool, _pool_key
        _pool = None
        _pool_key = None
        if previous is not None:
            if previous_key[0] == key[0]:
                # In-use connections finish their transactions and are then
                # closed by psycopg_pool; idle connections close immediately.
                previous.close(timeout=0)
            else:
                _inherited_pools.append(previous)
        pool = ConnectionPool(
            conninfo=dsn,
            kwargs={"connect_timeout": 5},
            min_size=1,
            max_size=8,
            timeout=_CHECKOUT_TIMEOUT,
            max_waiting=64,
            max_idle=300,
            check=ConnectionPool.check_connection,
            name="eis223-identity",
            open=False,
        )
        try:
            pool.open(wait=False)
        except BaseException:
            pool.close(timeout=0)
            raise
        _pool, _pool_key = pool, key
        return pool


@contextmanager
def connect():
    """Borrow one connection and return it after committing or rolling back."""
    with _get_pool().connection(timeout=_CHECKOUT_TIMEOUT) as db:
        yield db


def close_pool():
    """Release this process's pool during ASGI shutdown or process exit."""
    global _pool, _pool_key
    with _lock:
        pool, key = _pool, _pool_key
        _pool = None
        _pool_key = None
    if pool is not None and key[0] == os.getpid():
        pool.close(timeout=5)


atexit.register(close_pool)
