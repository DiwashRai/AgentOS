"""An in-memory sliding-window rate limiter (SPEC §11: 10 login attempts per minute per key).

Pure and thread-safe (sync routes run in a thread pool). State lives in the process: a restart
forgets it, which is fine for a login throttle.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Hashable

MAX_KEYS = 10_000  # past this, keys whose attempts have all expired are dropped


class RateLimiter:
    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[Hashable, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: Hashable) -> bool:
        """Record an attempt. False (and not recorded) when `key` already used up its window."""
        with self._lock:
            now = self._clock()
            if len(self._hits) > MAX_KEYS:
                self._prune(now)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
