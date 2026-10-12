"""Store plugin interface (mailbox mode, `design/mailbox.md`).

A store is a folder of files addressed by relative POSIX paths (`runs/42-ab12.../manifest.json`).
Plugins are adapters: no db, services or HTTP layer; everything comes in as arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class StoreError(RuntimeError):
    """The store couldn't be reached or refused a request. The message never holds credentials."""


class StoreTooLarge(StoreError):
    """A file being fetched is over the caller's size cap (nothing usable was written)."""


class Store(Protocol):
    def put(self, path: str, data: bytes) -> None:
        """Create or replace the file at `path`."""
        ...

    def get(self, path: str, dest: Path, *, max_bytes: int) -> bool:
        """Stream the file at `path` to `dest`. False if it doesn't exist. Raises StoreTooLarge
        past `max_bytes` (dest is then removed)."""
        ...

    def delete(self, path: str) -> None:
        """Delete a file or a folder (recursively). A missing path is fine."""
        ...

    def list_dir(self, path: str) -> list[str]:
        """The names of the direct children of folder `path` ([] if it doesn't exist)."""
        ...
