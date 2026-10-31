"""The adapter interface, result type and per-adapter health counters."""

from __future__ import annotations

import enum
import logging
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

    @property
    def ok(self) -> bool:
        return self.status == AdapterStatus.ok


@dataclass
class _Health:
    calls: int = 0
    failures: int = 0
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
        h.by_status[result.status.value] = h.by_status.get(result.status.value, 0) + 1
        h.last_status = result.status.value
        if result.status.failed:
            h.failures += 1
            h.last_failure = result.detail[:300]
            h.last_failure_at = result.retrieved_at


def adapter_health() -> dict[str, dict]:
    """Per-adapter counters since the process started (no query contents are kept)."""
    with _health_lock:
        return {
            name: {
                "calls": h.calls, "failures": h.failures, "by_status": dict(h.by_status), "last_status": h.last_status,
                "last_failure": h.last_failure, "last_failure_at": h.last_failure_at,
                "average_latency_ms": round(h.total_latency_ms / h.calls) if h.calls else 0,
            }
            for name, h in _health.items()
        }


def reset_health() -> None:
    with _health_lock:
        _health.clear()


class SourceAdapter:
    """Base class. Subclasses set `name`, `source_type` and `description`, and implement fetch methods
    that call `self.run(...)` so timing, failure handling and health counters are uniform."""

    name: ClassVar[str] = ""
    source_type: ClassVar[SourceType]
    description: ClassVar[str] = ""

    def run(self, fn: Callable[[], AdapterResult | tuple[AdapterStatus, list[EvidenceSource], Any, str]]) -> AdapterResult:
        started = time.monotonic()
        at = utcnow()
        try:
            out = fn()
            result = out if isinstance(out, AdapterResult) else AdapterResult(self.name, out[0], out[1], out[2], out[3])
        except AdapterError as e:
            result = AdapterResult(self.name, e.status, detail=e.detail or str(e))
        except Exception as e:  # an adapter bug must not break the analysis: treat it as a failed source
            log.exception("Source adapter %s failed", self.name)
            result = AdapterResult(self.name, AdapterStatus.unavailable, detail=f"{type(e).__name__}: {e}")
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
