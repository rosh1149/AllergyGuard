"""Limits that protect the server: photo processing is CPU-heavy, so only a few jobs run at once."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from fastapi import HTTPException, status

from app.core.config import get_settings

_slots: threading.BoundedSemaphore | None = None
_lock = threading.Lock()
_stats = {"jobs": 0, "rejected_busy": 0, "running": 0}


def _semaphore() -> threading.BoundedSemaphore:
    global _slots
    with _lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(get_settings().max_concurrent_image_jobs)
        return _slots


@contextmanager
def image_job():
    """Wait for a free photo-processing slot; answer 503 if the server stays busy too long."""
    sem = _semaphore()
    if not sem.acquire(timeout=get_settings().image_job_wait_seconds):
        with _lock:
            _stats["rejected_busy"] += 1
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The server is busy processing photos. Try again shortly.",
                            headers={"Retry-After": "10"})
    with _lock:
        _stats["jobs"] += 1
        _stats["running"] += 1
    try:
        yield
    finally:
        with _lock:
            _stats["running"] -= 1
        sem.release()


def image_job_stats() -> dict:
    with _lock:
        return {**_stats, "max_concurrent": get_settings().max_concurrent_image_jobs}
