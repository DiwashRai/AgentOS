"""MailboxLauncher (`design/mailbox.md`): runs for runners that can't reach the AgentOS server.

The server uploads the run's manifest and worker.py to a store (Artifactory) under
`runs/<folder>/`, dispatches `agentos-mailbox.yml`, polls the Actions run, and later fetches
`runs/<folder>/outbox.jsonl` (the worker's recorded API calls) to replay. `<folder>` is
`run_folder(run_id, dispatch_token_hash)`, unique per run even across servers and db resets.

The mailbox dispatcher and reaper (`agentos.mailbox`) drive it; `launch()` is never used.
Every method raises MailboxError (a safe message, no credentials) when GitHub or the store fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentos.launchers.github import ActionsRun, GitHubActionsLauncher
from agentos.stores.base import Store

# A folder name in the store: "<run id>-<16 hex>". The workflow checks the same pattern.
RUN_FOLDER_PATTERN = r"^[0-9]+-[0-9a-f]{16}$"
MAX_OUTBOX_BYTES = 512 * 1024 * 1024


def run_folder(run_id: int, dispatch_token_hash: str) -> str:
    return f"{run_id}-{dispatch_token_hash[:16]}"


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

    def status(self, launch_ref: str) -> ActionsRun | None:
        """The Actions run's status. None when `launch_ref` isn't a run of the current target repo
        (e.g. a runs page). Raises MailboxError when GitHub can't be asked."""
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
    """Mailbox over a GitHubActionsLauncher (dispatch, status, cancel) and a Store."""

    name = "mailbox"
    max_concurrency: int | None = None  # GitHub queues jobs

    def __init__(self, *, github: GitHubActionsLauncher, store: Store, worker_path: Path) -> None:
        self.github = github
        self.store = store
        self.worker_path = worker_path

    def launch(self, run_id: int, server_url: str, dispatch_token: str) -> str:
        raise MailboxError("mailbox runs are launched by the mailbox dispatcher")

    def send(self, run_id: int, folder: str, manifest_json: bytes) -> str:
        raise NotImplementedError

    def status(self, launch_ref: str) -> ActionsRun | None:
        raise NotImplementedError

    def cancel(self, launch_ref: str) -> None:
        raise NotImplementedError

    def fetch_outbox(self, folder: str, dest: Path) -> bool:
        raise NotImplementedError

    def delete_run(self, folder: str) -> None:
        raise NotImplementedError

    def run_folders(self) -> list[str]:
        raise NotImplementedError
