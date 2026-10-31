"""Work that need not delay the customer's answer (queues, bookkeeping) runs on a small thread pool.

Each job opens its own database session. With BACKGROUND_INLINE=true jobs run immediately instead
(tests and debugging). Failures are logged and counted, never raised to the customer."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_stats = {"submitted": 0, "completed": 0, "failed": 0}


def _run(name: str, job: Callable[[Session], None]) -> None:
    try:
        with SessionLocal() as db:
            job(db)
        ok = True
    except Exception:
        log.exception("Background job %s failed", name)
        ok = False
    with _lock:
        _stats["completed" if ok else "failed"] += 1


def submit(name: str, job: Callable[[Session], None]) -> None:
    global _executor
    with _lock:
        _stats["submitted"] += 1
    if get_settings().background_inline:
        _run(name, job)
        return
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=get_settings().background_workers, thread_name_prefix="bg")
        ex = _executor
    ex.submit(_run, name, job)


def shutdown() -> None:
    """Finish queued jobs (called when the server stops)."""
    global _executor
    with _lock:
        ex, _executor = _executor, None
    if ex is not None:
        ex.shutdown(wait=True)


def stats() -> dict:
    with _lock:
        out = dict(_stats)
    out["pending"] = out["submitted"] - out["completed"] - out["failed"]
    out["mode"] = "inline" if get_settings().background_inline else "thread_pool"
    return out
