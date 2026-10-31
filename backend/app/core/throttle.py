"""Failed-login throttling (in-process, per email address + client address)."""

from __future__ import annotations

import threading
import time
from collections import deque

from app.core.config import get_settings

_failures: dict[tuple[str, str], deque[float]] = {}
_lock = threading.Lock()


def _window() -> float:
    return get_settings().login_lockout_minutes * 60


def _recent(key, now) -> deque[float]:
    q = _failures.setdefault(key, deque())
    while q and q[0] < now - _window():
        q.popleft()
    return q


def locked_for(email: str, client: str) -> int:
    """Seconds until another attempt is allowed (0 = allowed now)."""
    now = time.monotonic()
    with _lock:
        q = _recent((email, client), now)
        if len(q) < get_settings().login_max_failures:
            return 0
        return max(1, int(q[0] + _window() - now))


def record_failure(email: str, client: str) -> None:
    now = time.monotonic()
    with _lock:
        _recent((email, client), now).append(now)
        if len(_failures) > 50_000:  # bound memory
            for k in [k for k, q in _failures.items() if not q][:10_000]:
                del _failures[k]


def clear(email: str, client: str) -> None:
    with _lock:
        _failures.pop((email, client), None)


def reset_all() -> None:
    with _lock:
        _failures.clear()
