"""Launcher plugin interface (SPEC §12).

Plugins are adapters: they receive everything as arguments and never touch the DB, so a fork can
add one (e.g. an in-network launcher at work) without knowing the schema.
"""

from __future__ import annotations

from typing import Protocol


class Launcher(Protocol):
    name: str
    # Max concurrent active runs for this launcher; None = unlimited (e.g. GitHub queues them).
    # The dispatcher counts active runs itself.
    max_concurrency: int | None

    def launch(self, run_id: int, server_url: str, dispatch_token: str) -> str:
        """Start a worker for the run. Returns an informational launch_ref."""
        ...
