"""Mailbox reaper (`design/mailbox.md` §4, §5 as amended by D3-D6, D9): watches running mailbox
runs through GitHub and the store, replays results, and ends runs that can't finish.

Per running mailbox run, each tick, in this order (each run on its own: an error is logged and the
next run is checked):
1. A cancel was requested → cancel the Actions run, once per run per process.
2. Ask GitHub for the Actions run's status (unknown: no run URL, or a MailboxError).
3. The outbox is in the store → replay it (an unexpected error fails the run).
4. GitHub says `completed` and there is no outbox → cancelled (if requested) or failed.
5. GitHub says not started for longer than the claim timeout, and the run's jobs say the work job
   never started and none is running (the run is queued/waiting again while publish waits)
   → cancel + failed.
6. Past `started_at + time_budget_s + BUDGET_GRACE + claim_timeout_s` → cancel + failed.
7. The status is unknown and a cancel was requested over CANCEL_GRACE ago → cancelled.
8. No launch_ref LAUNCH_GRACE after the claim (the server died mid-launch) → failed.
A store error while fetching the outbox leaves only the time rules (6, 7) for that tick. The
run's folder is deleted after every replay and every end. Once an hour, folders of runs that
aren't active on this server are deleted (the sweep).

`MailboxReaper(ctx, mailbox).tick(now)` is synchronous with an injected clock.
"""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from agentos.context import AppContext
from agentos.launchers.github import ActionsRun
from agentos.launchers.mailbox import (
    RUN_FOLDER_PATTERN,
    WORK_JOB,
    Mailbox,
    MailboxError,
    MailboxTooLarge,
    run_folder,
)
from agentos.mailbox import service
from agentos.runs.service import BUDGET_GRACE, OVER_BUDGET

log = logging.getLogger(__name__)

INTERVAL_S = 20.0
CANCEL_GRACE = timedelta(minutes=15)  # only while GitHub can't tell us the run's status (D5)
LAUNCH_GRACE = timedelta(minutes=15)  # well above a bounded send() (D4)
SWEEP_EVERY = timedelta(hours=1)
NOT_STARTED = frozenset({"queued", "waiting", "pending", "requested"})
JOB_STARTED = frozenset({"in_progress", "completed"})  # a queued job has a started_at too
NOT_PICKED_UP = "no runner picked up the GitHub job"
LAUNCH_INTERRUPTED = "launch interrupted"
TOO_LARGE = "the run's results are over 512 MiB"
_FOLDER = re.compile(RUN_FOLDER_PATTERN)

# What a tick did to a run (for logs and tests).
Outcome = Literal["replayed", "ended", "gone"]


class MailboxReaper:
    def __init__(self, ctx: AppContext, mailbox: Mailbox) -> None:
        self.ctx = ctx
        self.mailbox = mailbox
        self.cancelled: set[int] = set()  # runs whose Actions run this process asked to cancel
        self.work_started: set[int] = set()  # runs whose work job this process saw started
        self.last_sweep: datetime | None = None

    def tick(self, now: datetime) -> dict[int, Outcome]:
        """Check every running mailbox run, then sweep if due. Returns what happened to the runs
        that were replayed or ended."""
        outcomes: dict[int, Outcome] = {}
        for run in service.running_runs(self.ctx.db):
            try:
                outcome = self.check(run, now)
            except Exception:
                log.exception("mailbox reaper: run %s: check failed", run.id)
                continue
            if outcome is not None:
                outcomes[run.id] = outcome
                self.cancelled.discard(run.id)
                self.work_started.discard(run.id)
        if self.last_sweep is None or now - self.last_sweep >= SWEEP_EVERY:
            self.last_sweep = now
            self.sweep()
        return outcomes

    # ------------------------------------------------------------------ one run

    def check(self, run: service.MailboxRun, now: datetime) -> Outcome | None:
        folder = run_folder(run.id, run.dispatch_token_hash)
        ref = run.launch_ref
        cancel_requested = run.cancel_requested_at is not None
        # 1. a requested cancel reaches GitHub once
        if cancel_requested:
            self._cancel(run.id, ref)
        # 2. GitHub's view (read before the outbox: publish uploads before the run completes)
        status: ActionsRun | None = None
        if ref is not None:
            try:
                status = self.mailbox.status(ref)
            except MailboxError as exc:
                log.warning("mailbox reaper: run %s: no GitHub status: %s", run.id, exc)
        # 3. results
        with tempfile.TemporaryDirectory(prefix="agentos-outbox-") as tmp:
            outbox = Path(tmp) / "outbox.jsonl"
            outbox_known = True
            try:
                if self.mailbox.fetch_outbox(folder, outbox):
                    return self._replay(run.id, folder, outbox)
            except MailboxTooLarge:
                return self._end(run.id, folder, "failed", TOO_LARGE)
            except MailboxError as exc:
                log.warning("mailbox reaper: run %s: cannot fetch the outbox: %s", run.id, exc)
                outbox_known = False
        claim_timeout = timedelta(seconds=self.ctx.space.claim_timeout_s)
        if outbox_known and status is not None:
            # 4. the job ended without results
            if status.status == "completed":
                if cancel_requested:
                    return self._end(run.id, folder, "cancelled", None)
                return self._end(
                    run.id,
                    folder,
                    "failed",
                    f"GitHub job ended ({status.conclusion or 'no conclusion'}) without sending "
                    "results; see the GitHub run",
                )
            # 5. never picked up (`ref` is set whenever `status` is; the check narrows it)
            if (
                status.status in NOT_STARTED
                and run.started_at is not None
                and now - run.started_at > claim_timeout
                and ref is not None
                and self._work_never_picked_up(run.id, ref)
            ):
                self._cancel(run.id, ref)
                return self._end(run.id, folder, "failed", NOT_PICKED_UP)
        # 6. over budget (the claim timeout is the queue allowance: started_at = launch time)
        if run.started_at is not None:
            budget = timedelta(seconds=run.time_budget_s) + BUDGET_GRACE + claim_timeout
            if now > run.started_at + budget:
                self._cancel(run.id, ref)
                return self._end(run.id, folder, "failed", OVER_BUDGET)
        # 7. a cancel GitHub can't confirm
        if (
            run.cancel_requested_at is not None
            and status is None
            and now > run.cancel_requested_at + CANCEL_GRACE
        ):
            return self._end(run.id, folder, "cancelled", None)
        # 8. the server died between the claim and storing the launch_ref
        if (
            outbox_known
            and ref is None
            and run.started_at is not None
            and now > run.started_at + LAUNCH_GRACE
        ):
            return self._end(run.id, folder, "failed", LAUNCH_INTERRUPTED)
        return None

    def _replay(self, run_id: int, folder: str, outbox: Path) -> Outcome:
        try:
            result = service.replay(self.ctx.db, self.ctx.broker, run_id, outbox)
        except Exception as exc:
            log.exception("mailbox reaper: run %s: replay failed", run_id)
            return self._end(
                run_id, folder, "failed", f"could not replay the results: {type(exc).__name__}"
            )
        log.info("mailbox reaper: run %s: replayed (%s)", run_id, result)
        self._delete(run_id, folder)
        return "gone" if result == "gone" else ("replayed" if result == "completed" else "ended")

    def _end(
        self,
        run_id: int,
        folder: str,
        status: Literal["failed", "cancelled"],
        error: str | None,
    ) -> Outcome:
        ended = service.end(self.ctx.db, self.ctx.broker, run_id, status, error)
        self._delete(run_id, folder)
        return "ended" if ended else "gone"

    def _work_never_picked_up(self, run_id: int, ref: str) -> bool:
        """The work job hasn't started and no job is running. Unknown is False (the budget rule
        ends the run). A started work job is remembered, so publish waiting costs one call."""
        if run_id in self.work_started:
            return False
        try:
            jobs = self.mailbox.jobs(ref)
        except MailboxError as exc:
            log.warning("mailbox reaper: run %s: cannot list the GitHub jobs: %s", run_id, exc)
            return False
        if jobs is None:
            return False
        if jobs.get(WORK_JOB) in JOB_STARTED:
            self.work_started.add(run_id)
            return False
        return "in_progress" not in jobs.values()

    def _cancel(self, run_id: int, ref: str | None) -> None:
        """Ask GitHub to cancel the run, once per run per process (best effort)."""
        if ref is None or run_id in self.cancelled:
            return
        try:
            self.mailbox.cancel(ref)
        except MailboxError as exc:
            log.warning("mailbox reaper: run %s: cannot cancel the GitHub run: %s", run_id, exc)
            return
        self.cancelled.add(run_id)

    def _delete(self, run_id: int, folder: str) -> None:
        try:
            self.mailbox.delete_run(folder)
        except MailboxError as exc:
            log.warning("mailbox reaper: run %s: cannot delete its folder: %s", run_id, exc)

    # ------------------------------------------------------------------ sweep

    def sweep(self) -> list[str]:
        """Delete every run folder that belongs to no active (dispatched/running) run of this
        server (late partial outboxes, folders a crash left). Names that aren't run folders are
        left alone. Returns the deleted folders; errors are logged."""
        try:
            folders = self.mailbox.run_folders()
            active = service.active_runs(self.ctx.db)
        except Exception:
            log.exception("mailbox reaper: sweep failed")
            return []
        keep = {run_folder(i, h) for i, h in active.items() if h is not None}
        deleted: list[str] = []
        for name in folders:
            if not _FOLDER.fullmatch(name) or name in keep:
                continue
            if int(name.split("-", 1)[0]) in active:
                continue  # an active run's id: never touch it (D9)
            try:
                self.mailbox.delete_run(name)
            except MailboxError as exc:
                log.warning("mailbox reaper: sweep: cannot delete %s: %s", name, exc)
                continue
            deleted.append(name)
        if deleted:
            log.info("mailbox reaper: swept %s old run folder(s)", len(deleted))
        return deleted
