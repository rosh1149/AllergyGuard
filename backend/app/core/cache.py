"""A small thread-safe TTL cache with hit/miss counters (in-process; one per worker)."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

_MISSING = object()


class TTLCache:
    def __init__(self, name: str, max_items: int = 2048):
        self.name = name
        self.max_items = max_items
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = self.expired = 0

    def get(self, key, default=_MISSING):
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return default
            expires, value = item
            if expires < now:
                del self._data[key]
                self.expired += 1
                self.misses += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key, value, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._data[key] = (time.monotonic() + ttl_seconds, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {"items": len(self._data), "hits": self.hits, "misses": self.misses, "expired": self.expired,
                    "hit_rate": round(self.hits / total, 3) if total else None}


MISSING = _MISSING
