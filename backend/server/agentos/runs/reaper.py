"""Reaper loop (SPEC §8): no run stays dispatched or running forever.

Rules, in order, each tick (every INTERVAL_S):
1. The previous tick was more than SLEEP_GRACE ago (startup, or the laptop slept): every running
   run counts as just seen, and the tick does nothing else.
2. dispatched for longer than `claim_timeout_s` → failed "worker never connected".
3. running without a heartbeat for 90 s → failed "worker lost".
4. running past `started_at + time_budget_s + 15 min` → failed "time budget exceeded".
5. a cancel requested over 2 min ago and still running → cancelled.

Rules 2-5 key on the run's own row, whichever provider serves it. No rule touches a `queued`
run: a client run (`start = 'pull'`) waits for its user's client however long it is
offline (cancel still works). Once a client has taken it, it is `dispatched` and rules 2-5 apply
to it exactly as to a push run. Only mailbox runs (`report = 'file'`) skip rules 3-5, and
only while the mailbox reaper watches them (the mailbox is configured).

`Reaper(ctx).tick(now)` is synchronous with an injected clock; the lifespan runs it in a thread.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from agentos.context import AppContext
from agentos.runs import service

log = logging.getLogger(__name__)

INTERVAL_S = 15.0
SLEEP_GRACE = timedelta(seconds=60)


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
        # runs only with the mailbox. Every other run (client runs too) is checked here.
        exempt = "file" if self.ctx.mailbox is not None else None
        reaped = service.reap(self.ctx.db, now, self.ctx.space.claim_timeout_s, exempt)
        for r in reaped:
            log.warning("reaper: run %s %s: %s", r.run_id, r.status, r.reason)
        return reaped
