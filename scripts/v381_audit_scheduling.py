"""Bounded local audit resources and lossless in-process evidence notifications.

No network I/O or persistence. A timed-out extraction keeps its permit until
its real executor thread exits, so retries cannot silently exceed capacity.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import threading
import weakref

_BACKGROUND = ContextVar("eis_background_audit", default=False)
_GATES = weakref.WeakKeyDictionary()
_SIGNALS_LOCK = threading.Lock()


@contextmanager
def background_audit(enabled=True):
    token = _BACKGROUND.set(bool(enabled))
    try:
        yield
    finally:
        _BACKGROUND.reset(token)


class PriorityGate:
    """FIFO within each class, with reserved foreground capacity.

    Foreground requests win queued permits, but after three foreground grants
    an eligible background waiter gets a turn. This never consumes the reserved
    slot and does not interrupt a job that already owns a permit.
    """
    def __init__(self, limit=6, background_limit=5):
        if not 1 <= background_limit <= limit:
            raise ValueError("Invalid audit resource capacity")
        self.limit = limit
        self.background_limit = background_limit
        self.active = 0
        self.background_active = 0
        self._foreground = deque()
        self._background = deque()
        self._foreground_streak = 0
        self._waiting = {}

    def promote(self, task):
        """Promote a queued background request after an explicit manual check."""
        future = self._waiting.get(task)
        if future is None or future.done():
            return False
        try:
            self._background.remove(future)
        except ValueError:
            return False
        self._foreground.append(future)
        self._drain()
        return True

    def _drain(self):
        for queue in (self._foreground, self._background):
            while queue and queue[0].cancelled():
                queue.popleft()
        while self.active < self.limit:
            bg_ready = bool(self._background and
                            self.background_active < self.background_limit)
            background = bool(bg_ready and
                              (not self._foreground or self._foreground_streak >= 3))
            queue = self._background if background else self._foreground
            if not queue:
                break
            future = queue.popleft()
            if future.cancelled():
                continue
            self.active += 1
            self.background_active += int(background)
            self._foreground_streak = 0 if background else self._foreground_streak + 1
            future.set_result(background)

    async def acquire(self, background=None):
        background = _BACKGROUND.get() if background is None else bool(background)
        future = asyncio.get_running_loop().create_future()
        task = asyncio.current_task()
        self._waiting[task] = future
        (self._background if background else self._foreground).append(future)
        self._drain()
        try:
            # Shield distinguishes a cancelled waiter from a permit that was
            # already granted immediately before cancellation arrived.
            return await asyncio.shield(future)
        except BaseException:
            if future.done() and not future.cancelled():
                self.release(future.result())
            else:
                future.cancel()
                self._drain()
            raise
        finally:
            self._waiting.pop(task, None)

    def release(self, background):
        self.active -= 1
        self.background_active -= int(background)
        self._drain()


def _gate(name="files"):
    loop = asyncio.get_running_loop()
    gates = _GATES.setdefault(loop, {})
    if name not in gates:
        gates[name] = PriorityGate() if name == "files" else PriorityGate(1, 1)
    return gates[name]


@asynccontextmanager
async def review_slot(background=False):
    gate = _gate("review")
    permit = await gate.acquire(background)
    try:
        with background_audit(permit):
            yield
    finally:
        gate.release(permit)


def promote_review(task):
    return _gate("review").promote(task)


def is_manual_review(ns, reg):
    """`force` is used by automatic collectors too; only manual rows qualify."""
    connector = ns.get("conn")
    if not callable(connector):
        return False
    connection = connector()
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                    "AND name='v319_manual_inputs'").fetchone()
        return bool(exists and connection.execute(
            "SELECT 1 FROM v319_manual_inputs WHERE reg=?", (reg,)).fetchone())
    finally:
        connection.close()


async def run_file_io(function, *args, timeout=None):
    """Run blocking file/extraction work with a physical (not awaiter) cap."""
    gate = _gate()
    permit = await gate.acquire()
    task = asyncio.create_task(asyncio.to_thread(function, *args))

    def finished(done):
        gate.release(permit)
        # A caller may already have timed out; always retrieve worker failures.
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    awaited = asyncio.shield(task)
    return await awaited if timeout is None else await asyncio.wait_for(awaited, timeout)


async def ordered_map(function, values, limit=12):
    """Rolling bounded workers, stable output order, no per-batch barrier."""
    values = list(values)
    results = [None] * len(values)
    pending = iter(enumerate(values))

    async def worker():
        for index, value in pending:
            results[index] = await function(value)

    tasks = [asyncio.create_task(worker()) for _ in range(min(limit, len(values)))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return results


class AuditSignals:
    def __init__(self):
        self._lock = threading.Lock()
        self._versions = {}
        self._waiters = {}

    def version(self, topic):
        with self._lock:
            return self._versions.get(topic, 0)

    def notify(self, *topics):
        with self._lock:
            waiters = []
            for topic in topics:
                self._versions[topic] = self._versions.get(topic, 0) + 1
                waiters.extend(self._waiters.get(topic, ()))
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass  # The owning application loop has already shut down.

    async def wait(self, topic, version, timeout):
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        waiter = (loop, event)
        with self._lock:
            if self._versions.get(topic, 0) != version:
                return
            self._waiters.setdefault(topic, set()).add(waiter)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            with self._lock:
                self._waiters[topic].discard(waiter)


def audit_signals(ns):
    with _SIGNALS_LOCK:
        signals = ns.get("_v381_audit_signals")
        if signals is None:
            signals = ns["_v381_audit_signals"] = AuditSignals()
    return signals


def notify_evidence(ns):
    """Call only after originals/document rows are durably available."""
    audit_signals(ns).notify("evidence", "general", "thematic")
