"""Stage timing for pipelines."""

from __future__ import annotations

import time
from contextlib import contextmanager


class StageTimer:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.ms: dict[str, int] = {}

    @contextmanager
    def __call__(self, name: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = self.ms.get(name, 0) + round((time.perf_counter() - t) * 1000)

    def apply(self, steps) -> None:
        for s in steps:
            if s.duration_ms is None and s.name in self.ms:
                s.duration_ms = self.ms[s.name]

    def stages(self, steps):
        from app.engine.types import StageRecord

        self.apply(steps)
        return [StageRecord(name=s.name, status=s.status, duration_ms=s.duration_ms) for s in steps]
