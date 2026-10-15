"""Reaper loop (SPEC §8): no run stays dispatched or running forever.

Rules, in order, each tick (every INTERVAL_S):
1. The previous tick was more than SLEEP_GRACE ago (startup, or the laptop slept): every running
   run counts as just seen, and the tick does nothing else.
2. dispatched for longer than `claim_timeout_s` → failed "worker never connected".
3. running without a heartbeat for 90 s → failed "worker lost".
4. running past `started_at + time_budget_s + 15 min` → failed "time budget exceeded".
5. a cancel requested over 2 min ago and still running → cancelled.
6. local launcher only: delete workspaces older than 7 days that no active run owns.

Rules 2-5 key on the run's own row, whichever launcher serves it. No rule touches a `queued`
run: a client run (`launcher = 'client'`) waits for its user's client however long it is
offline (cancel still works). Once a client has taken it, it is `dispatched` and rules 2-5 apply
to it exactly as to a local run. Only mailbox runs (`launcher = 'mailbox'`) skip rules 3-5, and
only while the mailbox reaper watches them (the mailbox launcher is configured). Rule 6 cleans
the server's own WORKSPACES_DIR: client runs' workspaces are on the user's machine.

`Reaper(ctx).tick(now)` is synchronous with an injected clock; the lifespan runs it in a thread.
"""

from __future__ import annotations

import logging
import shutil
import stat
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from agentos.context import AppContext
from agentos.runs import service

log = logging.getLogger(__name__)

INTERVAL_S = 15.0
SLEEP_GRACE = timedelta(seconds=60)
WORKSPACE_MAX_AGE = timedelta(days=7)


class Reaper:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.previous: datetime | None = None

    def tick(self, now: datetime) -> list[service.Reaped]:
        previous, self.previous = self.previous, now
        if previous is None or now - previous > SLEEP_GRACE:
            touched = service.touch_running(self.ctx.db, now)
            if touched:
                log.info("reaper: grace tick, refreshed %s running run(s)", touched)
            return []
        # Mailbox runs send no heartbeats: the mailbox reaper (agentos.mailbox) watches them, and
        # runs only with the mailbox launcher. Every other run (client runs too) is checked here.
        exempt = "mailbox" if self.ctx.launcher.name == "mailbox" else None
        reaped = service.reap(self.ctx.db, now, self.ctx.space.claim_timeout_s, exempt)
        for r in reaped:
            log.warning("reaper: run %s %s: %s", r.run_id, r.status, r.reason)
        if self.ctx.launcher.name == "local":
            self.clean_workspaces(now)
        return reaped

    def clean_workspaces(self, now: datetime) -> list[Path]:
        """Delete direct children of WORKSPACES_DIR older than WORKSPACE_MAX_AGE whose name isn't
        an active run id. Returns what was deleted."""
        root = self.ctx.settings.workspaces_dir
        try:
            children = list(root.iterdir())
        except OSError:
            return []
        active = {str(i) for i in service.active_run_ids(self.ctx.db)}
        cutoff = (now - WORKSPACE_MAX_AGE).timestamp()
        removed: list[Path] = []
        for child in children:
            try:
                if child.name in active or child.lstat().st_mtime >= cutoff:
                    continue
                if child.is_dir() and not child.is_symlink():
                    _rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                log.warning("reaper: cannot delete workspace %s: %s", child, exc)
                continue
            removed.append(child)
        if removed:
            log.info("reaper: deleted %s old workspace(s)", len(removed))
        return removed


def _rmtree(path: Path) -> None:
    """shutil.rmtree that also removes read-only files (git packs on Windows)."""

    def retry(func: Callable[..., object], target: str, _exc: object) -> None:
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=retry)
