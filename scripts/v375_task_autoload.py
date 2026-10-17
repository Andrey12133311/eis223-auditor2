"""Background enqueue for manager-assigned EIS checks.

Never creates account_manual_checks. Assignment work and employee-initiated
checks stay separated while sharing the real idempotent EIS search queue.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)
_once = threading.Lock()
_started = False


def queue_missing(app, limit=3):
    ns = getattr(app.state, "eis223_manual_ns", None)
    if not ns:
        return 0
    from scripts.account_api_v1 import connect
    from v301_search_jobs import _enqueue
    with connect() as db:
        pending = db.execute(
            """SELECT t.id,t.reg_number FROM account_manager_tasks t
               LEFT JOIN account_manager_task_eis_jobs j ON j.task_id=t.id
               WHERE j.task_id IS NULL
                 AND t.status IN ('assigned','returned','in_progress')
               ORDER BY t.created_at ASC LIMIT %s""",
            (max(1,min(4,int(limit))),),
        ).fetchall()
    queued = 0
    for task_id,reg in pending:
        try:
            job,_created = _enqueue(ns, "reg", reg, 1, 1, "", True)
            with connect() as db:
                db.execute(
                    """INSERT INTO account_manager_task_eis_jobs(task_id,job_id)
                       VALUES(%s,%s) ON CONFLICT(task_id) DO NOTHING""",
                    (task_id, job["id"]),
                )
            queued += 1
        except Exception as exc:
            log.warning("Manager EIS task enqueue deferred: %s", type(exc).__name__)
    return queued


def _run(app):
    from scripts.account_api_v1 import connect
    del connect
    stop = threading.Event()
    while not stop.is_set():
        try:
            queue_missing(app, 3)
        except Exception as exc:
            # Transient database/EIS errors must never crash web serving.
            log.warning("Manager EIS task dispatcher unavailable: %s",
                        type(exc).__name__)
        stop.wait(60)


def start(app):
    global _started
    with _once:
        if _started:
            return
        worker = threading.Thread(
            target=_run, args=(app,), name="eis223-manager-task-queue", daemon=True
        )
        worker.start()
        _started = True
