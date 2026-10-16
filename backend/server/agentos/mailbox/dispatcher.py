"""Mailbox dispatcher (`design/mailbox.md` §3.2): replaces `runs.dispatcher.tick` when
AGENTOS_LAUNCHER=mailbox.

The runner can't reach the server, so the server dispatches each queued run with a file-report
job, starts it itself (`dispatched → running`, no worker binds to it), sends the job's package
through the mailbox and stores the Actions run URL. The job token is never kept in plaintext.
"""

from __future__ import annotations

import logging

from agentos.context import AppContext
from agentos.launchers.mailbox import Mailbox, MailboxError, run_folder
from agentos.mailbox import service
from agentos.models import ManifestReport
from agentos.runs import service as runs

log = logging.getLogger(__name__)

INTERVAL_S = 2.0
_REPORT = ManifestReport(mode="file", url=None)


def tick(ctx: AppContext, mailbox: Mailbox) -> list[int]:
    """Launch every queued mailbox run. Returns the run ids taken from the queue (including ones
    whose build or send failed: those runs are failed)."""
    launched: list[int] = []
    while True:
        dispatched = runs.mark_next_dispatched(ctx.db, service.ROUTE, None, _REPORT)
        if dispatched is None:
            return launched
        run_id, built = dispatched
        launched.append(run_id)
        # Not built (the run is failed), or cancelled meanwhile.
        if built is None or (package := runs.start(ctx.db, run_id)) is None:
            log.warning("run %s: not launched", run_id)
            continue
        folder = run_folder(run_id, built[0][-16:])  # the job id's random hex
        # Outside any transaction: uploads and a GitHub call (bounded by the plugins' timeouts).
        try:
            ref = mailbox.send(run_id, folder, package.encode("utf-8"))
        except Exception as exc:
            log.warning("run %s: launch failed: %s", run_id, exc)
            service.end(ctx.db, run_id, "failed", f"launch failed: {exc}")
            _delete(mailbox, run_id, folder)
            continue
        runs.set_launch_ref(ctx.db, run_id, ref)
        log.info("run %s: launched (mailbox %s, %s)", run_id, folder, ref)
        if not service.is_running(ctx.db, run_id):  # cancelled or reaped meanwhile
            try:
                mailbox.cancel(ref)
            except MailboxError as exc:
                log.warning("run %s: cannot cancel the GitHub run: %s", run_id, exc)


def _delete(mailbox: Mailbox, run_id: int, folder: str) -> None:
    try:
        mailbox.delete_run(folder)
    except MailboxError as exc:
        log.warning("run %s: cannot delete the mailbox folder: %s", run_id, exc)
