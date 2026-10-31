"""Limits that protect the server: photo processing is CPU-heavy, so only a few jobs run at once.

Jobs run in the API process's thread pool. That is enough because the heavy parts do not hold
Python's global lock: Tesseract runs as a separate process, and Pillow and the barcode reader
release the lock while they work. Measured on 4 cores, 4 concurrent jobs process 2.4 photos/s
against 0.9 photos/s for one (docs/PERFORMANCE.md), so a separate photo-worker service is not
needed at this scale.
"""

from __future__ import annotations

import os
import resource
import threading
import time
from collections import deque
from contextlib import contextmanager

from fastapi import HTTPException, status

from app.core.config import get_settings

_slots: threading.BoundedSemaphore | None = None
_lock = threading.Lock()
_stats = {"jobs": 0, "rejected_busy": 0, "running": 0}
_durations: deque[float] = deque(maxlen=500)  # seconds, most recent jobs
_waits: deque[float] = deque(maxlen=500)


def max_concurrent() -> int:
    n = get_settings().max_concurrent_image_jobs
    return n if n > 0 else (os.cpu_count() or 2)


def _semaphore() -> threading.BoundedSemaphore:
    global _slots
    with _lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max_concurrent())
        return _slots


@contextmanager
def image_job():
    """Wait for a free photo-processing slot; answer 503 if the server stays busy too long."""
    sem = _semaphore()
    t0 = time.perf_counter()
    if not sem.acquire(timeout=get_settings().image_job_wait_seconds):
        with _lock:
            _stats["rejected_busy"] += 1
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The server is busy processing photos. Try again shortly.",
                            headers={"Retry-After": "10"})
    t1 = time.perf_counter()
    with _lock:
        _stats["jobs"] += 1
        _stats["running"] += 1
        _waits.append(t1 - t0)
    try:
        yield
    finally:
        with _lock:
            _stats["running"] -= 1
            _durations.append(time.perf_counter() - t1)
        sem.release()


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))] * 1000)


def image_job_stats() -> dict:
    with _lock:
        d, w = list(_durations), list(_waits)
        return {**_stats, "max_concurrent": max_concurrent(),
                "duration_ms": {"p50": _pct(d, 0.5), "p95": _pct(d, 0.95), "recent": len(d)},
                "wait_ms": {"p50": _pct(w, 0.5), "p95": _pct(w, 0.95)}}


_started = time.time()


def _rss_mb() -> float | None:
    try:  # Linux: current resident memory
        with open("/proc/self/statm") as f:
            return round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)
    except (OSError, ValueError, IndexError):
        return None


def process_stats() -> dict:
    """CPU and memory of this server process (and of the OCR processes it has run)."""
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    uptime = time.time() - _started
    cpu = me.ru_utime + me.ru_stime
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    return {
        "pid": os.getpid(),
        "uptime_s": round(uptime),
        "cpu_count": os.cpu_count(),
        "cpu_seconds": round(cpu, 1),
        "cpu_seconds_ocr_processes": round(kids.ru_utime + kids.ru_stime, 1),
        "cpu_average_share": round(cpu / uptime, 3) if uptime > 0 else None,  # 1.0 = one core busy
        "load_average": load,
        "memory_rss_mb": _rss_mb(),
        "memory_peak_rss_mb": round(me.ru_maxrss / 1024, 1),  # ru_maxrss is in KiB on Linux
        "threads": threading.active_count(),
    }
