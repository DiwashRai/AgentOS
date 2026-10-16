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
Within the stop grace a running job is left alone, so its worker can still publish. Each provider
cancel is asked once per job per process, best effort. File-report jobs get rule 1 only (the
mailbox reaper watches them); with no mailbox configured, one past its `not_after` is failed
UNWATCHED. Queued runs are never touched. A push job's status is polled at most every STATUS_EVERY.

An ended run's job gets `ended_at` once `job_ended` (lifting the fence for its ticket's next
run); a push run cancelled while queued or dispatched is cancelled at its provider first.

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
from agentos.runs.service import BUDGET_GRACE, NEVER_STARTED, PULL_START_TIMEOUT, STOPPED, TERMINAL

log = logging.getLogger(__name__)

INTERVAL_S = 15.0
SLEEP_GRACE = timedelta(seconds=60)
START_GRACE = timedelta(minutes=15)
HEARTBEAT_TIMEOUT = timedelta(seconds=90)  # also how long a pull job's client may be offline
STOP_GRACE = timedelta(minutes=2)
STATUS_EVERY = timedelta(seconds=60)
START_INTERRUPTED = "start interrupted"
WORKER_LOST = "worker lost"
UNWATCHED = "its runner is no longer configured"


@dataclass(frozen=True, slots=True)
class JobFacts:
    """A job as the reaper sees it. `seen_at`: its last post (else its start); pull: `alive_at`,
    the last lease that listed it, and `client_seen_at`, its client's last lease."""

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
    not_after: datetime
    finished_at: datetime | None
    alive_at: datetime | None
    client_seen_at: datetime | None


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


def judge(job: JobFacts, now: datetime, status: JobStatus | None, mailbox: bool) -> Action | None:
    """Rules 1-5 for one job, in order; None leaves it. `status` is its provider's answer (push
    only; None when there is none); `mailbox`: a mailbox store watches the file-report jobs."""
    push = job.start == "push"
    if job.status == "dispatched" and push and job.ref is None:
        late = now > job.dispatched_at + START_GRACE
        return Action("failed", START_INTERRUPTED) if late else None
    if job.report == "file":
        return Action("failed", UNWATCHED) if not mailbox and now > job.not_after else None

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
    if now <= job.stop_requested_at + STOP_GRACE:
        return None  # the worker sees `cancel: true` and still publishes
    end, error = STOPPED[job.stop_reason]
    return Action(end, error, cancel=push)


def job_ended(job: JobFacts, now: datetime, status: JobStatus | None) -> bool:
    """Whether an ended run's job can no longer publish: past its `not_after` (any route); push:
    its provider says `ended` (`status`); pull: a later lease by its client didn't list it, or
    that client has been offline HEARTBEAT_TIMEOUT since the result."""
    if now > job.not_after:
        return True
    if job.start == "push":
        return status is not None and status.state == "ended"
    seen, finished = job.client_seen_at, job.finished_at or job.dispatched_at
    if seen is not None and seen > (job.alive_at or job.dispatched_at):
        return True
    return now > max(seen or finished, finished) + HEARTBEAT_TIMEOUT


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
        not_after=from_db(row["not_after"]),
        finished_at=from_db_opt(row["finished_at"]),
        alive_at=from_db_opt(row["alive_at"]),
        client_seen_at=from_db_opt(row["client_seen_at"]),
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
                if job.status in TERMINAL:
                    if job.status == "cancelled" and job.start == "push":
                        self._cancel(job)
                    if job_ended(job, now, self._status(job, now)):
                        service.end_job(self.ctx.db, job.run_id, job.job_id, now)
                    continue
                action = judge(job, now, self._status(job, now), self.ctx.mailbox is not None)
                if action is not None and (ended := self._apply(job, action, now)):
                    reaped.append(ended)
            except Exception:
                log.exception("reaper: run %s: check failed", job.run_id)
        return reaped

    def _status(self, job: JobFacts, now: datetime) -> JobStatus | None:
        """A push job's provider status (file: once ended), asked at most every STATUS_EVERY."""
        provider = self.ctx.providers.get(job.provider or "")
        live = job.report == "live" or job.status in TERMINAL
        if not live or job.ref is None or provider is None:
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

        if action.cancel:
            self._cancel(job)
        return ended

    def _cancel(self, job: JobFacts) -> None:
        """Ask the job's provider to cancel it, once per job (best effort)."""
        provider = self.ctx.providers.get(job.provider or "")
        if job.ref and provider and job.job_id not in self.cancelled:
            try:
                provider.cancel(job.ref)
            except ProviderError as exc:
                log.warning("reaper: run %s: provider cancel failed: %s", job.run_id, exc)
            else:
                self.cancelled.add(job.job_id)
