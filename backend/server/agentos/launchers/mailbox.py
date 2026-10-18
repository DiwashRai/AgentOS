"""MailboxLauncher (`design/mailbox.md`): runs for runners that can't reach the AgentOS server.

The server uploads the run's manifest and worker.py to a store (Artifactory) under
`runs/<folder>/`, dispatches `agentos-mailbox.yml`, polls the Actions run, and later fetches
`runs/<folder>/outbox.jsonl` (the worker's recorded API calls) to replay. `<folder>` is
`run_folder(run_id, <the random hex of the run's job id>)`, unique per run even across servers
and db resets.

The mailbox dispatcher and reaper (`agentos.mailbox`) drive it. Every method raises MailboxError
(a safe message, no credentials) when GitHub or the store fails.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Protocol

from agentos.launchers.base import JobStatus, ProviderError
from agentos.launchers.github import GitHubActionsProvider
from agentos.stores.base import Store, StoreError, StoreTooLarge

# A folder name in the store: "<run id>-<16 hex>". The workflow checks the same pattern.
RUN_FOLDER_PATTERN = r"^[0-9]+-[0-9a-f]{16}$"
MAX_OUTBOX_BYTES = 512 * 1024 * 1024

_FOLDER = re.compile(RUN_FOLDER_PATTERN)
log = logging.getLogger(__name__)


def run_folder(run_id: int, job_hex: str) -> str:
    return f"{run_id}-{job_hex[:16]}"


class MailboxError(RuntimeError):
    """GitHub or the store failed. Safe to store and log."""


class MailboxTooLarge(MailboxError):
    """The outbox is over MAX_OUTBOX_BYTES."""


class Mailbox(Protocol):
    """What `agentos.mailbox` needs (tests pass a fake)."""

    def send(self, run_id: int, folder: str, manifest_json: bytes) -> str:
        """Upload worker.py and the manifest to `runs/<folder>/`, then dispatch the workflow with
        inputs {run: folder, worker_sha256, manifest_sha256}. Returns the launch_ref: the Actions
        run's html_url, or the workflow's runs page when GitHub returns no run (logged as a
        warning: liveness rules that need the run's status can't apply then)."""
        ...

    def status(self, launch_ref: str) -> JobStatus:
        """The work job's status (`GitHubActionsProvider.status`): unknown when `launch_ref`
        isn't a run of the current target repo (e.g. a runs page). Raises MailboxError when
        GitHub can't be asked."""
        ...

    def cancel(self, launch_ref: str) -> None:
        """Ask GitHub to cancel the run (202 and 409 both count as done). No-op when
        `launch_ref` isn't a run of the current target repo."""
        ...

    def fetch_outbox(self, folder: str, dest: Path) -> bool:
        """Download `runs/<folder>/outbox.jsonl` to `dest`. False if it isn't there (yet).
        Raises MailboxTooLarge past MAX_OUTBOX_BYTES."""
        ...

    def delete_run(self, folder: str) -> None:
        """Delete `runs/<folder>/` (missing is fine)."""
        ...

    def run_folders(self) -> list[str]:
        """The folder names under `runs/` (for the sweep)."""
        ...


class MailboxLauncher:
    """Mailbox over a GitHubActionsProvider (dispatch, status, cancel) and a Store."""

    def __init__(self, *, github: GitHubActionsProvider, store: Store, worker: bytes) -> None:
        """`worker`: the bytes uploaded as each run's worker.py."""
        self.github = github
        self.store = store
        self.worker = worker

    def send(self, run_id: int, folder: str, manifest_json: bytes) -> str:
        _check_folder(folder)
        if not folder.startswith(f"{run_id}-"):
            raise MailboxError(f"run folder {folder!r} isn't run {run_id}'s")
        try:
            self.store.put(f"runs/{folder}/worker.py", self.worker)
            self.store.put(f"runs/{folder}/manifest.json", manifest_json)
            ref = self.github.dispatch(
                {
                    "run": folder,
                    "worker_sha256": hashlib.sha256(self.worker).hexdigest(),
                    "manifest_sha256": hashlib.sha256(manifest_json).hexdigest(),
                }
            )
        except (StoreError, ProviderError) as exc:
            raise MailboxError(str(exc)) from None
        if self.github.run_id_of(ref) is None:
            log.warning(
                "run %s: GitHub returned no workflow run (launch_ref %s); the liveness rules "
                "that need the run's status won't apply to it",
                run_id,
                ref,
            )
        return ref

    def status(self, launch_ref: str) -> JobStatus:
        try:
            return self.github.status(launch_ref)
        except ProviderError as exc:
            raise MailboxError(str(exc)) from None

    def cancel(self, launch_ref: str) -> None:
        try:
            self.github.cancel(launch_ref)
        except ProviderError as exc:
            raise MailboxError(str(exc)) from None

    def fetch_outbox(self, folder: str, dest: Path) -> bool:
        _check_folder(folder)
        try:
            return self.store.get(f"runs/{folder}/outbox.jsonl", dest, max_bytes=MAX_OUTBOX_BYTES)
        except StoreTooLarge as exc:
            raise MailboxTooLarge(str(exc)) from None
        except StoreError as exc:
            raise MailboxError(str(exc)) from None

    def delete_run(self, folder: str) -> None:
        # Any single name (not only RUN_FOLDER_PATTERN): the sweep also clears junk folders.
        if not folder or "/" in folder or "\\" in folder or folder in (".", ".."):
            raise MailboxError(f"bad run folder {folder!r}")
        try:
            self.store.delete(f"runs/{folder}")
        except (StoreError, ValueError) as exc:  # ValueError: a name the store can't address
            raise MailboxError(str(exc)) from None

    def run_folders(self) -> list[str]:
        try:
            return self.store.list_dir("runs")
        except StoreError as exc:
            raise MailboxError(str(exc)) from None


def _check_folder(folder: str) -> None:
    if not _FOLDER.fullmatch(folder):
        raise MailboxError(f"bad run folder {folder!r}")
