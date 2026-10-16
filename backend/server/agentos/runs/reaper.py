"""Reaper loop: no job stays dispatched or running forever. One rule table, keyed on each job's
stored route (start and report), never on the server's current settings.

Each tick (every INTERVAL_S), job by job, the first rule that applies wins:
0. Sleep grace: the previous tick was more than SLEEP_GRACE ago (startup, or the laptop slept):
   every running run counts as just seen, and the tick does nothing else.
1. Start interrupted: a push job dispatched START_GRACE ago still has no ref → failed.
2. Never started: no `started` within the start timeout (push: the Space's `claim_timeout_s`,
   unless the provider says running; pull: PULL_START_TIMEOUT after the lease) → failed "never
   started"; push: stopped `never_started` and cancelled at the provider.
3. Lost: no post for HEARTBEAT_TIMEOUT → failed "worker lost"; push: cancelled at the provider.
4. Over budget: past `started_at + budget_s + BUDGET_GRACE` → stopped `budget`, so the worker
   sees `cancel: true` and publishes before it stops.
5. Stop grace: no result STOP_GRACE after a stop → ended as `service.STOPPED` says; push:
   cancelled at the provider.
A user-cancelled push job is cancelled at its provider before that. Each provider cancel is asked
once per job per process, best effort. File-report jobs get rule 1 only (the mailbox reaper
watches them), and queued runs are never touched. A push job's status is polled at most every
STATUS_EVERY.

`judge` is pure; `Reaper(ctx).tick(now)` is synchronous with an injected clock, and the lifespan
runs it in a thread. A job whose check raises is logged and the next job is checked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from agentos.clock import from_db, from_db_opt
from agentos.context import AppContext
from agentos.db import Row
from agentos.launchers.base import JobStatus, ProviderError
from agentos.models import ReportMode, RunStatus, StartMode, StopReason
from agentos.runs import service
from agentos.runs.service import BUDGET_GRACE, NEVER_STARTED, STOPPED

log = logging.getLogger(__name__)

INTERVAL_S = 15.0
SLEEP_GRACE = timedelta(seconds=60)
START_GRACE = timedelta(minutes=15)
PULL_START_TIMEOUT = timedelta(minutes=2)
HEARTBEAT_TIMEOUT = timedelta(seconds=90)
STOP_GRACE = timedelta(minutes=2)
STATUS_EVERY = timedelta(seconds=60)
START_INTERRUPTED = "start interrupted"
WORKER_LOST = "worker lost"


@dataclass(frozen=True, slots=True)
class JobFacts:
    """An active run's job as the reaper sees it. `seen_at`: its last post (else its start)."""

    run_id: int
    job_id: str
    status: RunStatus
    start: StartMode
    provider: str | None
    report: ReportMode
    ref: str | None
    dispatched_at: datetime
    started_at: datetime
    seen_at: datetime
    budget_s: int
    stop_requested_at: datetime | None
    stop_reason: StopReason | None
    start_timeout: timedelta


@dataclass(frozen=True, slots=True)
class Action:
    """A verdict: stop the run (`stop`), end it (`end`, with `error`), and ask its provider to
    cancel the job (`cancel`)."""

    end: RunStatus | None = None
    error: str | None = None
    stop: StopReason | None = None
    cancel: bool = False


@dataclass(frozen=True, slots=True)
class Reaped:
    run_id: int
    status: RunStatus
    reason: str


def judge(job: JobFacts, now: datetime, status: JobStatus | None) -> Action | None:
    """Rules 1-5 for one job, in order; None leaves it. `status` is its provider's answer (push
    only; None when there is none)."""
    push = job.start == "push"
    if job.status == "dispatched" and push and job.ref is None:
        late = now > job.dispatched_at + START_GRACE
        return Action("failed", START_INTERRUPTED) if late else None
    if job.report == "file":
        return None

    if job.status == "dispatched":
        running = status is not None and status.state == "running"
        if running or now <= job.dispatched_at + job.start_timeout:
            return None
        return Action("failed", NEVER_STARTED, "never_started" if push else None, cancel=push)

    if now > job.seen_at + HEARTBEAT_TIMEOUT:
        return Action("failed", WORKER_LOST, cancel=push)
    if job.stop_requested_at is None:
        over = now > job.started_at + timedelta(seconds=job.budget_s) + BUDGET_GRACE
        return Action(stop="budget") if over else None
    if now > job.stop_requested_at + STOP_GRACE:
        end, error = STOPPED[job.stop_reason]
        return Action(end, error, cancel=push)
    return Action(cancel=True) if push and job.stop_reason == "cancel" else None


def job_facts(row: Row, start_timeout: timedelta) -> JobFacts:
    """`service.active_jobs` row → facts; a pull job's start timeout is PULL_START_TIMEOUT."""
    dispatched = from_db(row["dispatched_at"])
    started = from_db_opt(row["started_at"]) or dispatched
    return JobFacts(
        run_id=row["run_id"],
        job_id=row["job_id"],
        status=row["status"],
        start=row["start"],
        provider=row["provider"],
        report=row["report"],
        ref=row["launch_ref"],
        dispatched_at=dispatched,
        started_at=started,
        seen_at=from_db_opt(row["last_seen_at"]) or started,
        budget_s=row["budget_s"],
        stop_requested_at=from_db_opt(row["stop_requested_at"]),
        stop_reason=row["stop_reason"],
        start_timeout=start_timeout if row["start"] == "push" else PULL_START_TIMEOUT,
    )


class Reaper:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.previous: datetime | None = None
        self.polled: dict[str, tuple[datetime, JobStatus | None]] = {}  # by job id
        self.cancelled: set[str] = set()  # job ids whose provider was asked to cancel

    def tick(self, now: datetime) -> list[Reaped]:
        previous, self.previous = self.previous, now
        if previous is None or now - previous > SLEEP_GRACE:
            touched = service.touch_running(self.ctx.db, now)
            if touched:
                log.info("reaper: grace tick, refreshed %s running run(s)", touched)
            return []

        start_timeout = timedelta(seconds=self.ctx.space.claim_timeout_s)
        jobs = [job_facts(row, start_timeout) for row in service.active_jobs(self.ctx.db)]
        active = {job.job_id for job in jobs}
        self.polled = {k: v for k, v in self.polled.items() if k in active}
        self.cancelled &= active

        reaped: list[Reaped] = []
        for job in jobs:
            try:
                action = judge(job, now, self._status(job, now))
                if action is not None and (ended := self._apply(job, action, now)):
                    reaped.append(ended)
            except Exception:
                log.exception("reaper: run %s: check failed", job.run_id)
        return reaped

    def _status(self, job: JobFacts, now: datetime) -> JobStatus | None:
        """The provider's status of a live push job, asked at most every STATUS_EVERY."""
        provider = self.ctx.providers.get(job.provider or "")
        if job.report == "file" or job.ref is None or provider is None:
            return None
        polled = self.polled.get(job.job_id)
        if polled is None or now - polled[0] >= STATUS_EVERY:
            try:
                status: JobStatus | None = provider.status(job.ref)
            except ProviderError as exc:
                log.warning("reaper: run %s: no provider status: %s", job.run_id, exc)
                status = None
            polled = self.polled[job.job_id] = (now, status)
        return polled[1]

    def _apply(self, job: JobFacts, action: Action, now: datetime) -> Reaped | None:
        """Write the verdict, then ask the provider to cancel (once per job). Returns the run's
        end, if it ended."""
        ended = None
        verdict = (action.stop, action.end, action.error)
        if service.reap(self.ctx.db, job.run_id, job.status, now, *verdict):
            ended = Reaped(job.run_id, action.end or "failed", action.error or "stop unanswered")
            log.warning("reaper: run %s %s: %s", ended.run_id, ended.status, ended.reason)
        elif action.stop is not None:
            log.info("reaper: run %s stopped: %s", job.run_id, action.stop)

        provider = self.ctx.providers.get(job.provider or "")
        if action.cancel and job.ref and provider and job.job_id not in self.cancelled:
            try:
                provider.cancel(job.ref)
            except ProviderError as exc:
                log.warning("reaper: run %s: provider cancel failed: %s", job.run_id, exc)
            else:
                self.cancelled.add(job.job_id)
        return ended
