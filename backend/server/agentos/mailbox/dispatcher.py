"""Mailbox dispatcher (`design/mailbox.md` §3.2): replaces `runs.dispatcher.tick` when
AGENTOS_LAUNCHER=mailbox.

The runner can't reach the server, so the server claims each queued run itself (the same guarded
`dispatched → running` claim a worker makes, with a throwaway worker token), sends the manifest
through the mailbox and stores the Actions run URL. Neither token is kept in plaintext.
"""

from __future__ import annotations

import logging
import secrets

from agentos.context import AppContext
from agentos.errors import ApiError
from agentos.launchers.mailbox import Mailbox, MailboxError, run_folder
from agentos.mailbox import service
from agentos.runs import service as runs
from agentos.worker_api import service as worker_api

log = logging.getLogger(__name__)

INTERVAL_S = 2.0


def tick(ctx: AppContext, mailbox: Mailbox) -> list[int]:
    """Launch every queued mailbox run. Returns the run ids taken from the queue (including ones
    whose claim or send failed: those runs are failed)."""
    launched: list[int] = []
    while True:
        dispatch_token = secrets.token_urlsafe(32)
        dispatch_hash = runs.token_hash(dispatch_token)
        run_id = runs.mark_next_dispatched(ctx.db, service.LAUNCHER, None, dispatch_hash)
        if run_id is None:
            return launched
        launched.append(run_id)
        try:
            manifest = worker_api.claim(
                ctx.db,
                ctx.runtimes,
                ctx.space,
                dispatch_token,
                secrets.token_urlsafe(32),
            )
        except ApiError as exc:  # a manifest build failure (the run is failed) or a cancel: 410
            log.warning("run %s: not launched: %s", run_id, exc.message)
            continue
        folder = run_folder(run_id, dispatch_hash)
        # Outside any transaction: uploads and a GitHub call (bounded by the plugins' timeouts).
        try:
            ref = mailbox.send(run_id, folder, manifest.model_dump_json().encode("utf-8"))
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
