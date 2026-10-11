"""A small in-memory cache whose entries expire `ttl_s` seconds after they were put.

Pure and thread-safe (sync routes run in a thread pool). The clock is injectable for tests. State
lives in the process: a restart forgets it. Keys must be few (e.g. configured query ids): expired
entries are dropped on `put`, nothing else bounds the size.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TtlCache(Generic[K, V]):
    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self._clock = clock
        self._entries: dict[K, tuple[float, V]] = {}
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        """The value put under `key` less than `ttl_s` ago, else None."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or self._clock() - entry[0] >= self.ttl_s:
                return None
            return entry[1]

    def put(self, key: K, value: V) -> None:
        with self._lock:
            now = self._clock()
            expired = [k for k, (at, _) in self._entries.items() if now - at >= self.ttl_s]
            for k in expired:
                del self._entries[k]
            self._entries[key] = (now, value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
