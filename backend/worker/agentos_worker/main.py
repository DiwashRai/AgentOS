"""The entry point: `main` picks the mode; `run_live` binds a job from the server,
`run_mailbox` runs one from a file.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

from agentos_worker.archive import this_worker
from agentos_worker.cli import ClientMode, FileMode, LiveMode, parse_args
from agentos_worker.client import run_client
from agentos_worker.console import say
from agentos_worker.events import EventSink, now
from agentos_worker.outbox import Recorder
from agentos_worker.package import PROTOCOL, PackageError
from agentos_worker.parse import load_package
from agentos_worker.process import spawn_detached
from agentos_worker.run import Run
from agentos_worker.workdir_root import hold_lock, release_lock
from agentos_worker.worker_api import (
    AlreadyClaimed,
    Client,
    HttpError,
    HttpReporter,
    RunGone,
    Unreachable,
)


def main(argv: list[str] | None = None) -> int:
    mode = parse_args(sys.argv[1:] if argv is None else argv)
    os.environ.pop("AGENTOS_TOKEN", None)  # never handed to git or to stages
    client_token = os.environ.pop("AGENTOS_CLIENT_TOKEN", "").strip()  # nor to run workers
    match mode:
        case ClientMode():
            return run_client(mode, client_token, spawn_detached)
        case FileMode(manifest=manifest, outbox=outbox, workdir=workdir):
            return run_mailbox(manifest, outbox, workdir)
        case LiveMode(lock_file=Path() as lock_file):
            # A client's run worker: its client lists the job as alive while this lock is held.
            try:
                lock = hold_lock(lock_file)
            except OSError as exc:
                say(f"cannot lock {lock_file}: {exc}")
                return 1
            try:
                return run_live(mode)
            finally:
                release_lock(lock)
        case LiveMode():
            return run_live(mode)


def run_live(mode: LiveMode) -> int:
    """Live mode: fetch the job, then bind it to a new instance id by posting `started` and
    waiting for its ack, all before the workdir is touched."""
    client = Client(mode.server, mode.token, secrets.token_urlsafe(32))
    reporter = HttpReporter(client)
    say(f"fetching the job from {mode.server}")
    try:
        package = load_package(client.get("/api/worker/job"))
        sha256 = this_worker().sha256
        started = {"seq": package.journal.first_seq, "ts": now(), "job": package.job.id}
        data = {"protocol": PROTOCOL, "worker_sha256": sha256}
        reporter.send([{**started, "type": "started", "stage_id": None, "data": data}])
    except AlreadyClaimed as exc:
        say(f"nothing to do: {exc}")
        return 0
    except HttpError as exc:
        say(f"cannot fetch the job: {exc}")
        return 0 if exc.status == 409 else 1
    except (PackageError, RunGone, Unreachable) as exc:
        say(f"cannot start the job: {exc}")
        return 1

    say(f"bound to job {package.job.id} ({package.ticket.key}, {len(package.stages)} stage(s))")
    events = EventSink(reporter, package.job.id, package.journal.first_seq + 1)
    events.start()
    workdir = Path(f"agentos-run-{package.run_id}") if mode.workdir is None else mode.workdir
    return Run(events, package, workdir.resolve(), client).execute()


def run_mailbox(manifest_path: Path, outbox: Path, workdir: Path) -> int:
    """Mailbox mode: the package is a file and the journal goes to the outbox. A package
    that can't be used exits 1 before the outbox exists (the server then fails the run)."""
    try:
        package = load_package(json.loads(manifest_path.read_bytes()))
    except (OSError, ValueError) as exc:  # ValueError: bad JSON, bad UTF-8 or a PackageError
        say(f"invalid package {manifest_path}: {exc}")
        return 1
    try:
        recorder = Recorder(outbox, package.run_id)
    except FileExistsError:
        say(f"the outbox {outbox} already exists: refusing to overwrite it")
        return 1
    except OSError as exc:
        say(f"cannot create the outbox {outbox}: {exc}")
        return 1
    say(f"mailbox run {package.run_id} ({package.ticket.key}, {len(package.stages)} stage(s))")
    events = EventSink(recorder, package.job.id, package.journal.first_seq + 1)
    events.start()
    try:
        return Run(events, package, workdir.resolve(), None).execute()
    finally:
        recorder.close()
