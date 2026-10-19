"""Attempt-local fencing for source writes made by installed legal wrappers.

ContextVars propagate through asyncio.to_thread. No process-global namespace is
patched, and ordinary intake/manual writes keep their existing authorization.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

_GUARD = ContextVar('legal_audit_write_guard', default=None)


@contextmanager
def background_write_guard(sql_guard=None, outer_guard=None):
    """sql_guard(connection) validates a lease under the same writer lock.

    Optional outer_guard() acquires an intake file lease before opening SQLite's
    transaction; it must not hash or read source bytes while the transaction is
    open. Both callbacks raise on a superseded attempt.
    """
    token = _GUARD.set((sql_guard, outer_guard))
    try:
        yield
    finally:
        _GUARD.reset(token)


@contextmanager
def guarded_transaction(connection):
    """Guard source mutation atomically, never just check before a later write."""
    sql_guard, outer_guard = _GUARD.get() or (None, None)
    with outer_guard() if callable(outer_guard) else nullcontext():
        try:
            connection.execute('BEGIN IMMEDIATE')
            if callable(sql_guard):
                sql_guard(connection)
            yield
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
