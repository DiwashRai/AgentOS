"""The run workers a client sees: those it started, the survivors of an earlier client and
the ones that ended (`Children`).
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

from agentos_worker.console import say
from agentos_worker.workdir_root import lock_held
from agentos_worker.worker_api import HttpError, Unreachable

LEASE_LIST_MAX = 100  # a lease's `alive` and `gone` hold at most this many (models.LeaseRequest)


JOB_ID_RE = re.compile(r"[0-9]+-[0-9]+-[0-9a-f]+")  # a job id the client puts in a file name


class Child(Protocol):
    """A started run worker, as the client loop sees it."""

    pid: int

    def poll(self) -> int | None: ...

    def wait(self) -> int: ...


class Children:
    """What the client saw of its run workers: those it started (by process handle), the
    survivors whose `<job id>.lock` in `runs` is held (an earlier client started them, or a
    started process exited while its lock stays held: on Windows a venv's python.exe is a stub
    whose child is the real worker), and the jobs it saw end (`gone`: the process exited and the
    lock is free), kept until the server acknowledges them. Only `gone` ends a job on the server;
    `alive` is for display. Each list goes out at most LEASE_LIST_MAX at a time; the rest of
    `gone` goes with the next lease, sent at once.

    A thread per started worker waits on its process: one that exits while the client loop waits
    (`waiting`: a lease in flight, or a busy client's pause) is reported at once by `report`
    (alive, gone), so the server hears it ended without waiting for the next lease."""

    def __init__(self, runs: Path, report: Callable[[list[str], list[str]], object]) -> None:
        self._runs = runs
        self._report = report
        self._lock = threading.Lock()
        self._running: dict[str, Child] = {}
        self._survivors: set[str] = set()
        self._gone: set[str] = set()
        self._waiting = False

    def start(self) -> None:
        """At startup: a held lock is a worker that outlived the last client; a free one, a worker
        that ended while no client watched (or whose `gone` was never acknowledged)."""
        try:
            jobs = sorted(p.stem for p in self._runs.glob("*.lock") if JOB_ID_RE.fullmatch(p.stem))
        except OSError as exc:
            say(f"cannot list {self._runs} for run workers: {exc}")
            return
        for job in jobs:
            if lock_held(self._runs / f"{job}.lock"):
                self._survivors.add(job)
                say(f"job {job}: its worker outlived the last client; it is listed as alive")
            else:
                self._gone.add(job)
                say(f"job {job}: its worker ended while no client watched; reporting it gone")

    def add(self, job_id: str, child: Child) -> None:
        with self._lock:
            self._running[job_id] = child
        threading.Thread(target=self._watch, args=(job_id, child), daemon=True).start()

    @contextlib.contextmanager
    def waiting(self) -> Iterator[tuple[list[str], list[str]]]:
        """Note the ends seen since (a started worker's process, a survivor's lock) and yield
        the alive and the gone job ids; until the block ends, each exit is reported at once."""
        with self._lock:
            survivors = set(self._survivors)
            exited = [job for job, child in self._running.items() if child.poll() is not None]
        freed = {job for job in survivors if not self._held(job)}
        still_held = {job for job in exited if self._held(job)}
        with self._lock:
            for job in exited:
                if self._running.pop(job, None) is not None:
                    (self._survivors if job in still_held else self._gone).add(job)
            self._survivors -= freed
            self._gone |= freed
            self._waiting = True
            listing = self._listing()
        try:
            yield listing
        finally:
            with self._lock:
                self._waiting = False

    def acked(self, gone: list[str]) -> None:
        """The server has ended these jobs: forget them and delete their lock files (until then
        a lock file says "report this job gone", at the next startup too)."""
        with self._lock:
            self._gone -= set(gone)
        for job in gone:
            say(f"job {job}: reported gone")
            try:
                (self._runs / f"{job}.lock").unlink(missing_ok=True)
            except OSError as exc:  # Windows: its process is still being torn down
                say(f"cannot delete {job}.lock ({exc}); the next client reports it again")

    def _held(self, job: str) -> bool:
        return lock_held(self._runs / f"{job}.lock")

    def _listing(self) -> tuple[list[str], list[str]]:
        """(alive, gone), each capped at LEASE_LIST_MAX; the caller holds the lock."""
        alive = [*self._running, *sorted(self._survivors)]
        return alive[:LEASE_LIST_MAX], sorted(self._gone)[:LEASE_LIST_MAX]

    def _watch(self, job_id: str, child: Child) -> None:
        code = child.wait()
        say(f"job {job_id}: its worker (pid {child.pid}) exited with code {code}")
        held = self._held(job_id)
        with self._lock:
            if self._running.pop(job_id, None) is None:
                return  # `waiting` saw it first
            if held:  # its process was a stub: the worker still runs, held by its lock
                self._survivors.add(job_id)
                return
            self._gone.add(job_id)
            if not self._waiting:
                return  # the loop's next lease, sent at once, reports it
            alive, gone = self._listing()
        try:
            self._report(alive, gone)
        except (HttpError, Unreachable, ValueError) as exc:
            say(f"could not report the end of job {job_id} ({exc}); the next lease will")
            return
        self.acked(gone)
