"""Dispatcher loop (SPEC §8): launch workers for queued runs, oldest first, within the launcher's
`max_concurrency` (None = unlimited; the dispatcher counts active runs, plugins never query).

`tick(ctx)` is synchronous and unit-testable; the lifespan runs it every INTERVAL_S in a thread.
The dispatch token exists in plaintext only here and in the launcher call; never log it.
"""

from __future__ import annotations

import logging
import secrets

from agentos.context import AppContext
from agentos.runs import service

log = logging.getLogger(__name__)

INTERVAL_S = 2.0


def tick(ctx: AppContext) -> list[int]:
    """Dispatch queued runs until none is left or the launcher is full. Returns the run ids
    handed to the launcher (including ones whose launch failed)."""
    launcher = ctx.launcher
    launched: list[int] = []
    while True:
        token = secrets.token_urlsafe(32)
        run_id = service.mark_next_dispatched(
            ctx.db, launcher.name, launcher.max_concurrency, service.token_hash(token)
        )
        if run_id is None:
            return launched
        launched.append(run_id)
        # Outside any transaction: a launch may take a while (a GitHub API call in M1).
        try:
            ref = launcher.launch(run_id, ctx.settings.effective_worker_url, token)
        except Exception as exc:
            log.warning("run %s: launch failed: %s", run_id, exc)
            service.fail_launch(ctx.db, run_id, f"launch failed: {exc}")
            continue
        service.set_launch_ref(ctx.db, run_id, ref)
        log.info("run %s: launched (%s %s)", run_id, launcher.name, ref)
