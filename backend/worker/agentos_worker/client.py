"""Client mode (`--client`, on a user's own machine): long-polls the server for the runs
its user dispatched to "My client" and starts this same worker as a detached process for each, so
a Ctrl+C in the client's console never kills a run. Each lease's outcome is a value (`Outcome`);
the root it runs in is checked and held first (`ClientRoot`).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from agentos_worker import tunables
from agentos_worker.archive import this_worker
from agentos_worker.children import JOB_ID_RE, Child, Children
from agentos_worker.cli import ClientMode
from agentos_worker.console import say
from agentos_worker.env_file import EnvFileError, parse_env
from agentos_worker.fit import Unfit
from agentos_worker.package import PROTOCOL
from agentos_worker.parse import parse
from agentos_worker.workdir_root import (
    CLIENT_LOCK,
    CLIENT_RUNS_DIR,
    clean_workdir_root,
    release_lock,
    take_lock,
)
from agentos_worker.worker_api import Client, HttpError, Unreachable

# Lease answers worth retrying; any other 4xx (a revoked token, a newer protocol) stops the client.
CLIENT_TRANSIENT_4XX = frozenset({408, 425, 429})


_ENV_PREFIX = "AGENTOS_"


def _env_set(env: dict[str, str], key: str, value: str | None) -> None:
    """Set (or with None, remove) `key`; Windows env names are case-insensitive."""
    if sys.platform == "win32":
        for existing in [k for k in env if k.upper() == key.upper()]:
            del env[existing]
    else:
        env.pop(key, None)
    if value is not None:
        env[key] = value


def child_env(
    job_token: str, env_file: Mapping[str, str | None], base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """A run worker's environment: `base` (default: ours) minus every AGENTOS_* var, then the env
    file's values (None removes the key), then unbuffered UTF-8 output and AGENTOS_TOKEN. The
    token is never on the command line."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.upper().startswith(_ENV_PREFIX)}
    for key, value in env_file.items():
        _env_set(env, key, value)
    _env_set(env, "PYTHONUNBUFFERED", "1")  # live log
    _env_set(env, "PYTHONIOENCODING", "utf-8")
    _env_set(env, "AGENTOS_TOKEN", job_token)
    return env


Spawn = Callable[[list[str], Path, dict[str, str], Path], Child]  # (argv, cwd, env, log file)


@dataclass(frozen=True)
class LeasedJob:
    job_id: str  # names its lock file: checked against JOB_ID_RE
    run_id: int
    job_token: str


@dataclass(frozen=True)
class Speaks:
    """The protocol a lease answer speaks, read before the rest (another protocol may shape the
    rest differently): any value but PROTOCOL itself is another protocol."""

    protocol: object


@dataclass(frozen=True)
class LeaseAnswer:
    """A lease answered 200, read with unknown keys ignored: servers add fields."""

    job: LeasedJob | None
    worker_sha256: str | None = None


@dataclass(frozen=True)
class Retry:
    """No usable answer (unreachable, 5xx, a transient 4xx, malformed): back off, lease again."""

    problem: str


@dataclass(frozen=True)
class Stop:
    """The client exits with `code` (a refusing server, another protocol, a root it can't use)."""

    code: int


Outcome = LeasedJob | None | Retry | Stop  # what one lease came to (None: no run to start)


def read_env_file(path: Path) -> dict[str, str | None]:
    """parse_env of a UTF-8 file (a leading BOM is ignored). Raises OSError or EnvFileError."""
    return parse_env(path.read_text(encoding="utf-8-sig"))


def git_repo_containing(path: Path) -> Path | None:
    """The nearest existing ancestor of `path` (itself included) that holds a `.git` entry."""
    for candidate in (path, *path.parents):
        if candidate.exists() and (candidate / ".git").exists():
            return candidate
    return None


@dataclass(frozen=True)
class ClientRoot:
    """A client's root, checked and held: its `runs` folder, the env file's values and the held
    CLIENT_LOCK."""

    runs: Path
    env: dict[str, str | None]
    lock: IO[bytes]


def _open_root(root: Path, env_file: Path | None) -> ClientRoot | Stop:
    """Refuse a root inside a git repo, an unreadable env file (2), a `runs` that can't be made
    (1) and a root another client holds (2), in that order; then delete its old workspaces."""
    repo = git_repo_containing(root)
    if repo is not None:
        say(f"--workdir-root {root} is inside the git repo {repo}; choose a directory outside it")
        return Stop(2)
    try:
        env = read_env_file(env_file) if env_file is not None else {}
    except (OSError, EnvFileError) as exc:
        say(f"cannot read the env file {env_file}: {exc}")
        return Stop(2)
    runs = root / CLIENT_RUNS_DIR
    try:
        runs.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        say(f"cannot create {runs}: {exc}")
        return Stop(1)
    # One client per root, for its whole life: another would misread this one's workers' locks.
    lock = (runs / CLIENT_LOCK).open("a+b")
    if not take_lock(lock, wait=False):
        lock.close()
        say(f"another client already runs on {root}; stop it or choose another --workdir-root")
        return Stop(2)
    removed = clean_workdir_root(root, time.time())
    if removed:
        say(f"deleted {len(removed)} run workspace(s) and log(s) older than 7 days")
    return ClientRoot(runs, env, lock)


def run_client(mode: ClientMode, token: str, spawn: Spawn) -> int:
    """Client mode: lease this user's jobs and start a detached worker for each, at most
    `max_runs` at once (`Children`). Every lease lists the running ones as `alive` (those it
    started, and the survivors of an earlier client, which count against `max_runs`) and the
    ones it saw end as `gone`; only `gone` ends a job, and a worker that exits while a lease waits
    is reported at once by a no-take lease. The first lease goes out at once, with the survivors
    and the lock files freed while no client watched. With every slot busy
    it sends a no-take lease every CLIENT_HEARTBEAT_S, so the server still sees it online. Network
    errors, 5xx, CLIENT_TRANSIENT_4XX and malformed answers back off (up to CLIENT_BACKOFF_CAP_S)
    forever; any other 4xx or another protocol exits 1. Started workers outlive the client.

    Everything it writes goes under `<root>/runs` (CLIENT_RUNS_DIR): each worker runs there with
    `--workdir <root>/runs/<run_id>` and `--lock-file <root>/runs/<job id>.lock`, logs to
    `<run_id>.log` and gets its job token in child_env. It holds `<root>/runs/client.lock`
    (CLIENT_LOCK) for its whole life; a second client on that root exits 2."""
    match _open_root(mode.root, mode.env_file):
        case Stop(code=code):
            return code
        case ClientRoot() as root:
            try:
                return ClientLoop(mode, token, root, spawn).run()
            except KeyboardInterrupt:
                say("client stopped; started workers keep running")
                return 130
            finally:
                release_lock(root.lock)


class ClientLoop:
    """Lease after lease on an open root, each one's outcome a value: `_lease` says what it came
    to, `run` acts on it, `_start` starts a leased job's worker."""

    def __init__(self, mode: ClientMode, token: str, root: ClientRoot, spawn: Spawn) -> None:
        self.mode, self.root, self.spawn = mode, root, spawn
        self.client = Client(mode.server, token)
        self.me = this_worker()  # what runs, and its sha256 to compare with the server's
        self.said_differs = False  # said once
        self.answered: float | None = None  # when the server last answered (None: lease at once)
        self.children = Children(root.runs, lambda alive, gone: self._post(False, alive, gone))

    def run(self) -> int:
        mode, runs = self.mode, self.root.runs
        self.children.start()
        delay = tunables.BACKOFF_START_S
        say(f"client: leasing jobs from {mode.server}, at most {mode.max_runs} at once, in {runs}")
        while True:
            with self.children.waiting() as (alive, gone):
                busy = len(alive) >= mode.max_runs
                last = self.answered
                quiet = last is not None and time.monotonic() - last < tunables.CLIENT_HEARTBEAT_S
                if busy and not gone and quiet:
                    time.sleep(tunables.CLIENT_REAP_S)
                    continue
                outcome = self._lease(busy, alive, gone)
            match outcome:
                case Stop(code=code):
                    return code
                case Retry(problem=problem):
                    say(f"lease failed ({problem}); leasing again in {delay:.1f}s")
                    time.sleep(delay)
                    delay = min(delay * 2, tunables.CLIENT_BACKOFF_CAP_S)
                case LeasedJob() as job:
                    delay = tunables.BACKOFF_START_S
                    self._start(job)
                case None:
                    delay = tunables.BACKOFF_START_S

    def _post(
        self, take: bool, alive: list[str], gone: list[str], timeout_s: float | None = None
    ) -> Any:
        """A lease: one that takes waits for a run; a no-take one (heartbeat, report) doesn't."""
        wait_s = tunables.CLIENT_POLL_WAIT_S if take else 0
        body = {"wait_s": wait_s, "take": take, "alive": alive, "gone": gone}
        return self.client.post("/api/worker/lease", body, timeout_s=timeout_s, retry_budget_s=0)

    def _lease(self, busy: bool, alive: list[str], gone: list[str]) -> Outcome:
        """One lease (a heartbeat when `busy`); its answer acknowledges `gone`, then is read."""
        try:
            resp = self._post(not busy, alive, gone, tunables.CLIENT_POLL_TIMEOUT_S)
        except HttpError as exc:
            if exc.status not in CLIENT_TRANSIENT_4XX:
                say(f"the server refused this client ({exc}); stopping")
                return Stop(1)
            return Retry(str(exc))
        except (Unreachable, ValueError) as exc:
            return Retry(str(exc))
        self.answered = time.monotonic()
        self.children.acked(gone)
        return self._read(resp)

    def _read(self, resp: object) -> Outcome:
        """An answer: its protocol first, then the rest; another worker.py is said once."""
        match parse(Speaks, resp, extra="ignore"):
            case Unfit(why=why):
                return Retry(f"a malformed answer ({why})")
            case Speaks(protocol=protocol) if protocol != PROTOCOL:
                say(f"the server speaks another protocol than {PROTOCOL}; update worker.py")
                return Stop(1)
        match parse(LeaseAnswer, resp, extra="ignore"):
            case Unfit(why=why):
                return Retry(f"a malformed answer ({why})")
            case LeaseAnswer(job=LeasedJob(job_id=job_id)) if not JOB_ID_RE.fullmatch(job_id):
                return Retry(f"a job id that isn't one: {job_id!r}")
            case LeaseAnswer(job=job, worker_sha256=sha256):
                if sha256 != self.me.sha256 and not self.said_differs:
                    url = f"{self.mode.server}/api/worker/worker.py"
                    say(f"this worker.py differs from the server's ({url})")
                    self.said_differs = True
                return job

    def _start(self, job: LeasedJob) -> None:
        runs, server = self.root.runs, self.mode.server
        workdir = runs / str(job.run_id)
        argv = [sys.executable, str(self.me.path), "--server", server, "--workdir", str(workdir)]
        argv += ["--lock-file", str(runs / f"{job.job_id}.lock")]
        log = runs / f"{job.run_id}.log"
        try:
            child = self.spawn(argv, runs, child_env(job.job_token, self.root.env), log)
        except OSError as exc:
            say(f"run {job.run_id}: cannot start its worker: {exc}")
            return
        self.children.add(job.job_id, child)
        say(f"run {job.run_id}: started its worker (pid {child.pid}), log {log}")
