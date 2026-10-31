"""The adapter interface, result type, per-adapter health counters, and failure handling for remote
sources: retries with exponential backoff and a circuit breaker.

Retry: a remote source that is *unavailable* (network error, timeout, 5xx) is tried again, up to
`SOURCE_MAX_ATTEMPTS` times, waiting `SOURCE_BACKOFF_SECONDS` x 2^n plus jitter between attempts.
Rate limiting, malformed answers and "not found" are not retried: retrying would not help, or would
make the rate limiting worse.

Circuit breaker, per adapter: after `SOURCE_BREAKER_THRESHOLD` consecutive failed calls the circuit
*opens* and calls return at once as unavailable ("skipped: source failing") for
`SOURCE_BREAKER_COOLDOWN_SECONDS`. Then one trial call is let through (*half-open*): success closes
the circuit, failure opens it again. A source being down therefore costs the analysis nothing but
its evidence: the outcome becomes Unknown where that evidence was needed, never "not listed".
"""

from __future__ import annotations

import enum
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, ClassVar

from app.engine.types import EvidenceSource, SourceType
from app.models.common import utcnow

log = logging.getLogger(__name__)


class AdapterStatus(str, enum.Enum):
    ok = "ok"  # data returned
    not_found = "not_found"  # the source answered, but has nothing for this query
    unavailable = "unavailable"  # network error, timeout or server error
    rate_limited = "rate_limited"  # the source asked us to slow down
    malformed = "malformed"  # the source answered with data we could not understand
    skipped = "skipped"  # not configured or not applicable

    @property
    def failed(self) -> bool:
        return self in (AdapterStatus.unavailable, AdapterStatus.rate_limited, AdapterStatus.malformed)


class AdapterError(Exception):
    """Raised inside an adapter to report a failure with a specific status."""

    def __init__(self, status: AdapterStatus, detail: str = ""):
        super().__init__(detail or status.value)
        self.status = status
        self.detail = detail


@dataclass
class AdapterResult:
    adapter: str
    status: AdapterStatus
    sources: list[EvidenceSource] = field(default_factory=list)
    payload: Any = None  # adapter-specific data the pipeline may need (e.g. the product record)
    detail: str = ""
    retrieved_at: datetime | None = None
    latency_ms: int = 0
    attempts: int = 1
    short_circuited: bool = False  # not called: the source's circuit breaker is open

    @property
    def ok(self) -> bool:
        return self.status == AdapterStatus.ok


@dataclass
class _Health:
    calls: int = 0
    failures: int = 0
    retries: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    last_status: str = ""
    last_failure: str = ""
    last_failure_at: datetime | None = None
    total_latency_ms: int = 0


_health: dict[str, _Health] = {}
_health_lock = threading.Lock()


def _record(result: AdapterResult) -> None:
    with _health_lock:
        h = _health.setdefault(result.adapter, _Health())
        h.calls += 1
        h.total_latency_ms += result.latency_ms
        h.retries += max(0, result.attempts - 1)
        h.by_status[result.status.value] = h.by_status.get(result.status.value, 0) + 1
        h.last_status = result.status.value
        if result.status.failed:
            h.failures += 1
            h.last_failure = result.detail[:300]
            h.last_failure_at = result.retrieved_at


def adapter_health() -> dict[str, dict]:
    """Per-adapter counters since the process started (no query contents are kept)."""
    breakers = breaker_status()
    with _health_lock:
        return {
            name: {
                "calls": h.calls, "failures": h.failures, "retries": h.retries, "by_status": dict(h.by_status),
                "last_status": h.last_status, "circuit": breakers.get(name, {"state": "closed"}),
                "last_failure": h.last_failure, "last_failure_at": h.last_failure_at,
                "average_latency_ms": round(h.total_latency_ms / h.calls) if h.calls else 0,
            }
            for name, h in _health.items()
        }


def reset_health() -> None:
    with _health_lock:
        _health.clear()
    reset_breakers()


# ---- circuit breaker ----


@dataclass
class _Breaker:
    consecutive_failures: int = 0
    opened_at: float | None = None  # monotonic time the circuit opened; None = closed
    trial_running: bool = False  # half-open: one call is testing the source
    times_opened: int = 0
    short_circuited: int = 0  # calls answered without contacting the source


_breakers: dict[str, _Breaker] = {}
_breaker_lock = threading.Lock()


def _cooldown() -> float:
    from app.core.config import get_settings
    return float(get_settings().source_breaker_cooldown_seconds)


def _state(b: _Breaker, now: float) -> str:
    if b.opened_at is None:
        return "closed"
    return "open" if now - b.opened_at < _cooldown() else "half_open"


def _admit(name: str) -> tuple[bool, str]:
    """May a call to this source go ahead? Returns (allowed, reason if not)."""
    now = time.monotonic()
    with _breaker_lock:
        b = _breakers.setdefault(name, _Breaker())
        st = _state(b, now)
        if st == "closed":
            return True, ""
        if st == "half_open" and not b.trial_running:
            b.trial_running = True
            return True, ""
        b.short_circuited += 1
        wait = max(0, round(_cooldown() - (now - b.opened_at)))
        return False, (f"Skipped: this source failed {b.consecutive_failures} times in a row; "
                       f"it will be tried again in about {wait} s.")


def _settle(name: str, failed: bool) -> None:
    from app.core.config import get_settings
    with _breaker_lock:
        b = _breakers.setdefault(name, _Breaker())
        was_trial, b.trial_running = b.trial_running, False
        if not failed:
            b.consecutive_failures, b.opened_at = 0, None
            return
        b.consecutive_failures += 1
        if was_trial or (b.opened_at is None and b.consecutive_failures >= get_settings().source_breaker_threshold):
            b.opened_at = time.monotonic()
            b.times_opened += 1
            log.warning("Source %s: circuit opened after %d consecutive failures", name, b.consecutive_failures)


def breaker_status() -> dict[str, dict]:
    now = time.monotonic()
    with _breaker_lock:
        return {name: {"state": _state(b, now), "consecutive_failures": b.consecutive_failures,
                       "times_opened": b.times_opened, "short_circuited": b.short_circuited}
                for name, b in _breakers.items()}


def reset_breakers() -> None:
    with _breaker_lock:
        _breakers.clear()


class SourceAdapter:
    """Base class. Subclasses set `name`, `source_type` and `description`, and implement fetch methods
    that call `self.run(...)` so timing, failure handling and health counters are uniform."""

    name: ClassVar[str] = ""
    source_type: ClassVar[SourceType]
    description: ClassVar[str] = ""
    remote: ClassVar[bool] = False  # calls a remote service: retried and protected by a circuit breaker

    def _attempt(self, fn) -> AdapterResult:
        try:
            out = fn()
            return out if isinstance(out, AdapterResult) else AdapterResult(self.name, out[0], out[1], out[2], out[3])
        except AdapterError as e:
            return AdapterResult(self.name, e.status, detail=e.detail or str(e))
        except Exception as e:  # an adapter bug must not break the analysis: treat it as a failed source
            log.exception("Source adapter %s failed", self.name)
            return AdapterResult(self.name, AdapterStatus.unavailable, detail=f"{type(e).__name__}: {e}")

    def run(self, fn: Callable[[], AdapterResult | tuple[AdapterStatus, list[EvidenceSource], Any, str]]) -> AdapterResult:
        from app.core.config import get_settings

        started = time.monotonic()
        at = utcnow()
        attempts = 0
        if self.remote:
            allowed, why = _admit(self.name)
            if not allowed:
                result = AdapterResult(self.name, AdapterStatus.unavailable, detail=why)
                result.retrieved_at, result.attempts, result.short_circuited = at, 0, True
                _record(result)
                return result
            s = get_settings()
            while True:
                attempts += 1
                result = self._attempt(fn)
                if result.status != AdapterStatus.unavailable or attempts >= max(1, s.source_max_attempts):
                    break
                delay = s.source_backoff_seconds * (2 ** (attempts - 1))
                time.sleep(delay + random.uniform(0, delay / 2))
            # Only a skipped call says nothing about the source's health.
            if result.status != AdapterStatus.skipped:
                _settle(self.name, result.status.failed)
        else:
            attempts = 1
            result = self._attempt(fn)
        result.attempts = attempts
        if attempts > 1:
            result.detail = f"{result.detail} (after {attempts} attempts)".strip()
        result.retrieved_at = at
        result.latency_ms = int((time.monotonic() - started) * 1000)
        for src in result.sources:  # every source records when it was collected and by which adapter
            if not src.note:
                src.note = f"Collected by the {self.name} adapter."
        _record(result)
        return result


# Registry of adapter classes, for documentation and the admin health view.
ADAPTERS: dict[str, type[SourceAdapter]] = {}


def register(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    ADAPTERS[cls.name] = cls
    return cls
