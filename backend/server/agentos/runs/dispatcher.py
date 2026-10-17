"""Dispatcher loop: start a worker for each queued push + live run, oldest first, through the
provider its route stores (`ctx.providers`). The provider queues; the server sets no limit.

`tick(ctx)` is synchronous and unit-testable; the lifespan runs it every INTERVAL_S in a thread.
Each run's job is built as it is dispatched; the job token exists in plaintext only here and in
the provider call; never log it.
"""

from __future__ import annotations

import logging

from agentos.context import AppContext
from agentos.launchers.base import JobStart
from agentos.models import ManifestReport
from agentos.runs import service

log = logging.getLogger(__name__)

INTERVAL_S = 2.0


def tick(ctx: AppContext) -> list[int]:
    """Dispatch queued runs until none of a configured provider is left. Returns the run ids
    taken from the queue (including ones whose build or start failed: those runs are failed).
    With no mailbox store, the queued file-report runs get their `queue_reason`."""
    url = ctx.settings.effective_worker_url
    report = ManifestReport(mode="live", url=url)
    launched: list[int] = []
    while (dispatched := service.mark_next_dispatched(ctx.db, ctx.providers, report)) is not None:
        run_id, name, built = dispatched
        launched.append(run_id)
        if built is None:
            log.warning("run %s: not started: its job could not be built", run_id)
            continue

        # Outside any transaction: a start may take a while (a GitHub API call).
        job_id, token = built
        try:
            ref = ctx.providers[name].start(JobStart(job_id, run_id, url, token))
        except Exception as exc:  # noqa: BLE001 any provider failure fails this run; the loop goes on
            log.warning("run %s: start failed: %s", run_id, exc)
            service.fail_start(ctx.db, run_id, job_id, f"launch failed: {exc}")
            continue
        service.set_launch_ref(ctx.db, run_id, ref)
        log.info("run %s: started (%s %s)", run_id, name, ref)

    if ctx.mailbox is None:  # nothing takes file-report runs: they only get their reason
        service.mark_next_dispatched(ctx.db, (), ManifestReport(mode="file", url=None))
    return launched
