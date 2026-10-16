"""Provider plugin interface: what starts, watches and stops a push job's worker.

Plugins are adapters: they receive everything as arguments and never touch the DB, so a fork can
add one (e.g. an in-network provider at work) without knowing the schema. The provider queues
jobs itself; the server sets no concurrency limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

State = Literal["pending", "running", "ended", "unknown"]


class ProviderError(RuntimeError):
    """The provider refused or couldn't be reached. Safe to store and log (no credentials)."""


@dataclass(frozen=True, slots=True)
class JobStart:
    """A live job to start: its worker dials `server_url` with `job_token` (never logged)."""

    job_id: str
    run_id: int
    server_url: str
    job_token: str


@dataclass(frozen=True, slots=True)
class JobStatus:
    """The state of the job that runs the worker. `since`: when it got a machine; `detail`: the
    conclusion once ended, or why the state is unknown."""

    state: State
    since: datetime | None = None
    detail: str | None = None


class Provider(Protocol):
    def start(self, job: JobStart) -> str:
        """Ask the provider to start a worker for `job`. Returns the ref. Raises ProviderError."""
        ...

    def status(self, ref: str) -> JobStatus:
        """The worker's job (never the whole workflow run): `unknown` for a ref it can't resolve.
        Raises ProviderError when the provider can't be asked."""
        ...

    def cancel(self, ref: str) -> None:
        """Best effort, idempotent; already finished is success. Raises ProviderError."""
        ...

    def cleanup(self, ref: str) -> None:
        """Delete what the provider keeps for the job; missing is fine. Raises ProviderError."""
        ...
