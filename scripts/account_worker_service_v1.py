"""Opt-in background runner for the isolated personal account database.

Start as a separate worker process, never inside the web app. The audit
adapter must be explicitly configured and must persist its evidence/results
before returning True. No adapter is shipped or enabled by default.
"""
import importlib
import logging
import os
import time

from scripts.account_api_v1 import connect
from scripts.account_thematic_worker_v1 import process_one, requeue_stale

log = logging.getLogger(__name__)


def load_adapter(spec):
    if not spec or ":" not in spec:
        raise ValueError("Set EIS223_AUDIT_ADAPTER=module:function")
    module, name = spec.split(":", 1)
    if not module.startswith("scripts.") or not name.isidentifier():
        raise ValueError("Audit adapter must be a scripts module function")
    callback = getattr(importlib.import_module(module), name)
    if not callable(callback):
        raise TypeError("Audit adapter is not callable")
    return callback


def run_forever(connect_fn, audit_callback, interval=10):
    if not callable(audit_callback):
        raise TypeError("Audit adapter is required")
    if interval < 1:
        raise ValueError("Polling interval must be positive")
    while True:
        try:
            with connect_fn() as db:
                requeue_stale(db)
            result = process_one(connect_fn, audit_callback)
            if result is None:
                time.sleep(interval)
            elif result["status"] != "completed":
                log.warning("Thematic job ended with status %s", result["status"])
        except Exception:
            log.exception("Worker iteration failed")
            time.sleep(interval)


def main():
    if os.getenv("EIS223_WORKER_ENABLED") != "true":
        raise RuntimeError("Worker is disabled; set EIS223_WORKER_ENABLED=true")
    if not os.getenv("EIS223_IDENTITY_DATABASE_URL"):
        raise RuntimeError("Identity database is required")
    callback = load_adapter(os.getenv("EIS223_AUDIT_ADAPTER", ""))
    logging.basicConfig(level=logging.INFO)
    run_forever(connect, callback)


if __name__ == "__main__":
    main()
