"""Launcher plugin interface (SPEC §12)."""

from __future__ import annotations

from typing import Protocol


class Launcher(Protocol):
    name: str

    def capacity(self) -> int | None:
        """Free slots right now; None means unlimited."""
        ...

    def launch(self, run_id: int, server_url: str, dispatch_token: str) -> str:
        """Start a worker for the run. Returns an informational launch_ref."""
        ...
