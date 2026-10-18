"""The worker's progress lines on stderr (`say`), which the live echo shares
(STDERR_LOCK).
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime

STDERR_LOCK = threading.Lock()  # say() and the output readers' echo (Echo) share stderr


def say(message: str) -> None:
    """Worker progress on stderr (the launcher redirects it to a log file). Never pass tokens."""
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    with STDERR_LOCK:
        print(f"[worker {stamp}] {message}", file=sys.stderr, flush=True)
