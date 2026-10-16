"""Mailbox reaper (`design/mailbox.md` §4, §5 as amended by D3-D6, D9): watches running
file-report runs (whatever the launcher setting) through GitHub and the store, replays results,
and ends runs that can't finish.

Per running file-report run, each tick, in this order (each run on its own: an error is logged
and the next run is checked):
1. A cancel was requested → cancel the Actions run, once per run per process.
2. Ask GitHub for the work job's status (`JobStatus`; unknown also for no run URL or a
   MailboxError).
3. The outbox is in the store → replay it (an unexpected error fails the run).
4. The job has ended (every job of the Actions run completed) and there is no outbox →
   cancelled (if requested) or failed.
5. The work job is still pending past the start timeout → cancel + failed "never started".
6. Past its job's `not_after` → cancel + failed "time budget exceeded".
7. The status is unknown and a cancel was requested over CANCEL_GRACE ago → cancelled.
8. No launch_ref START_GRACE after the start (the server died mid-launch) → failed.
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
from agentos.launchers.base import JobStatus
from agentos.launchers.mailbox import (
    RUN_FOLDER_PATTERN,
    Mailbox,
    MailboxError,
    MailboxTooLarge,
    run_folder,
)
from agentos.mailbox import service
from agentos.models import RunStatus
from agentos.runs.reaper import START_GRACE, START_INTERRUPTED
from agentos.runs.service import NEVER_STARTED, OVER_BUDGET

log = logging.getLogger(__name__)

INTERVAL_S = 20.0
CANCEL_GRACE = timedelta(minutes=15)  # only while GitHub can't tell us the run's status (D5)
SWEEP_EVERY = timedelta(hours=1)
TOO_LARGE = "the run's results are over 512 MiB"
_FOLDER = re.compile(RUN_FOLDER_PATTERN)

# What a tick did to a run (for logs and tests).
Outcome = Literal["replayed", "ended", "gone"]


class MailboxReaper:
    def __init__(self, ctx: AppContext, mailbox: Mailbox) -> None:
        self.ctx = ctx
        self.mailbox = mailbox
        self.cancelled: set[int] = set()  # runs whose Actions run this process asked to cancel
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
        if self.last_sweep is None or now - self.last_sweep >= SWEEP_EVERY:
            self.last_sweep = now
            self.sweep()
        return outcomes

    # ------------------------------------------------------------------ one run

    def check(self, run: service.MailboxRun, now: datetime) -> Outcome | None:
        folder = run_folder(run.id, run.job_hex)
        ref = run.launch_ref
        cancel_requested = run.stop_requested_at is not None
        # 1. a requested cancel reaches GitHub once
        if cancel_requested:
            self._cancel(run.id, ref)
        # 2. GitHub's view (read before the outbox: publish uploads before the run completes)
        status: JobStatus | None = None  # None: unknown
        if ref is not None:
            try:
                status = self.mailbox.status(ref)
            except MailboxError as exc:
                log.warning("mailbox reaper: run %s: no GitHub status: %s", run.id, exc)
            status = None if status is None or status.state == "unknown" else status
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
        start_timeout = timedelta(seconds=self.ctx.space.claim_timeout_s)
        if outbox_known and status is not None:
            # 4. the job ended without results
            if status.state == "ended":
                if cancel_requested:
                    return self._end(run.id, folder, "cancelled", None)
                ended = status.detail or "no conclusion"
                error = f"GitHub job ended ({ended}) without sending results; see the GitHub run"
                return self._end(run.id, folder, "failed", error)
            # 5. never picked up (a finished work job whose publish job waits is running)
            if status.state == "pending" and now > run.started_at + start_timeout:
                self._cancel(run.id, ref)
                return self._end(run.id, folder, "failed", NEVER_STARTED)
        # 6. over budget: the worker publishes nothing after its not_after
        if now > run.not_after:
            self._cancel(run.id, ref)
            return self._end(run.id, folder, "failed", OVER_BUDGET)
        # 7. a cancel GitHub can't confirm
        stop = run.stop_requested_at
        if stop is not None and status is None and now > stop + CANCEL_GRACE:
            return self._end(run.id, folder, "cancelled", None)
        # 8. the server died between the start and storing the launch_ref
        if outbox_known and ref is None and now > run.started_at + START_GRACE:
            return self._end(run.id, folder, "failed", START_INTERRUPTED)
        return None

    def _replay(self, run_id: int, folder: str, outbox: Path) -> Outcome:
        try:
            result = service.replay(self.ctx.db, run_id, outbox)
        except Exception as exc:
            log.exception("mailbox reaper: run %s: replay failed", run_id)
            return self._end(
                run_id, folder, "failed", f"could not replay the results: {type(exc).__name__}"
            )
        log.info("mailbox reaper: run %s: replayed (%s)", run_id, result)
        self._delete(run_id, folder)
        return "gone" if result == "gone" else ("replayed" if result == "completed" else "ended")

    def _end(self, run_id: int, folder: str, status: RunStatus, error: str | None) -> Outcome:
        ended = service.end(self.ctx.db, run_id, status, error)
        self._delete(run_id, folder)
        return "ended" if ended else "gone"

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
        deleted: list[str] = []
        for name in folders:
            if not _FOLDER.fullmatch(name) or int(name.split("-", 1)[0]) in active:
                continue  # not a run folder, or an active run's id: never touch it (D9)
            try:
                self.mailbox.delete_run(name)
            except MailboxError as exc:
                log.warning("mailbox reaper: sweep: cannot delete %s: %s", name, exc)
                continue
            deleted.append(name)
        if deleted:
            log.info("mailbox reaper: swept %s old run folder(s)", len(deleted))
        return deleted
