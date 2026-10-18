"""Worker test support: a stdlib fake AgentOS server, temp git remotes, manifests, fast timings.

The fixtures are re-exported by conftest.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from agentos_worker import main, tunables

FAKE_STAGE = Path(__file__).resolve().parent / "worker_fake_stage.py"
KEY = "AOS-42"
SKILL_DIR = ".claude/skills/context-aos-42-fix-login-bug"
JOB_TOKEN = "j" * 43
JOB = "/api/worker/job"
JOURNAL = "/api/worker/journal"
DISPLAY = frozenset(
    {"stage_started", "stage_finished", "log", "warning", "verdict", "verdict_repair"}
)

# (path, body) -> (status, body) to override the default answer, or None to keep it.
Hook = Callable[[str, Any], tuple[int, Any] | None]


@dataclass
class Request:
    path: str
    auth: str | None
    instance: str | None  # X-AgentOS-Instance
    body: Any


@dataclass
class FakeServer:
    """The worker API, in memory. Records every request; `hook` can override any answer. The
    journal is applied as the server does (`ingest`), per job, into `applied`."""

    manifest: dict[str, Any] = field(default_factory=dict)
    cancel: bool = False
    hook: Hook | None = None
    requests: list[Request] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    acks: dict[str, int] = field(default_factory=dict)  # job id -> the highest seq applied
    reject: set[str] = field(default_factory=set)  # record types consumed as invalid
    lock: threading.Lock = field(default_factory=threading.Lock)
    url: str = ""

    def answer(self, path: str, body: Any) -> tuple[int, Any]:
        if self.hook is not None:
            override = self.hook(path, body)
            if override is not None:
                return override
        if path == JOB:
            return 200, self.manifest
        if path == JOURNAL:
            return 200, self.ingest(body["records"])
        if path.startswith("/api/worker/jobs/") and "/sync-back?" in path:
            return 204, None
        return 404, {"error": {"code": "not_found", "message": path}}

    def ingest(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply records as the server does for the manifest's job: in strict seq order from its
        `journal.first_seq`; a seq held already is skipped, and a gap stops the batch. A record
        of a type in `reject` is consumed and listed in `rejected`, never applied."""
        job = self.manifest["job"]["id"]
        rejected: list[int] = []
        with self.lock:
            ack = self.acks.get(job, self.manifest["journal"]["first_seq"] - 1)
            for record in records:
                if record["seq"] > ack + 1:
                    break
                if record["seq"] == ack + 1:
                    if record["type"] in self.reject:
                        rejected.append(record["seq"])
                    else:
                        self.applied.append(record)
                    ack += 1
            self.acks[job] = ack
        return {"ack": ack, "cancel": self.cancel, "rejected": rejected}

    # ------------------------------------------------------------ views for assertions

    def bodies(self, path: str) -> list[Any]:
        with self.lock:
            return [r.body for r in self.requests if r.path == path]

    def uploads(self) -> list[tuple[dict[str, str], bytes]]:
        """Every sync_back PUT: (its query, its raw body)."""
        with self.lock:
            return [
                (dict(parse_qsl(urlsplit(r.path).query)), r.body)
                for r in self.requests
                if "/sync-back?" in r.path
            ]

    def records(self, type_: str | None = None) -> list[dict[str, Any]]:
        """The applied journal (each seq once, in order), or its records of one type."""
        with self.lock:
            return [r for r in self.applied if type_ is None or r["type"] == type_]

    def events(self) -> list[dict[str, Any]]:
        """The applied display records."""
        return [r for r in self.records() if r["type"] in DISPLAY]

    def complete(self) -> dict[str, Any] | None:
        """The last `result` record's data, leaving out a null `pr_url`."""
        done = self.records("result")
        if not done:
            return None
        return {k: v for k, v in done[-1]["data"].items() if k != "pr_url" or v is not None}

    def logs(self, stream: str | None = None) -> list[str]:
        return [
            e["data"]["text"]
            for e in self.events()
            if e["type"] == "log" and (stream is None or e["data"]["stream"] == stream)
        ]

    def warnings(self) -> list[str]:
        return [e["data"]["message"] for e in self.events() if e["type"] == "warning"]


def error(status: int, code: str) -> tuple[int, Any]:
    return status, {"error": {"code": code, "message": code}}


def eventually(condition: Callable[[], bool], what: str, timeout_s: float = 10.0) -> None:
    """Poll until `condition()` holds; fail after `timeout_s`. For waits on background threads."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout_s}s waiting for {what}")
        time.sleep(0.01)


def _handler(server: FakeServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.do_POST(raw=True)

        def do_PUT(self) -> None:
            self.do_POST(raw=True)

        def do_POST(self, raw: bool = False) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length)
            body = data if raw else json.loads(data or b"null")
            auth, instance = (
                self.headers.get("Authorization"),
                self.headers.get("X-AgentOS-Instance"),
            )
            with server.lock:
                server.requests.append(Request(self.path, auth, instance, body))
            status, answer = server.answer(self.path, body)
            payload = b"" if answer is None else json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


@pytest.fixture(scope="session")
def user_git_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("git") / "config"
    path.write_bytes(b"[user]\n\tname = Test User\n\temail = test@example.com\n")
    return path


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch, user_git_config: Path) -> None:
    """Shrink every worker timing constant so the suite runs in seconds, keep the worker off
    GitHub API publication even when the suite runs inside an Actions job, and give the
    workers' commits a test user's global git config instead of the developer's."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(user_git_config))
    for name, value in {
        "HTTP_TIMEOUT_S": 5.0,
        "RETRY_BUDGET_S": 1.0,
        "BACKOFF_START_S": 0.01,
        "BACKOFF_CAP_S": 0.05,
        "FLUSH_INTERVAL_S": 0.05,
        "HEARTBEAT_S": 0.2,
        "POLL_S": 0.02,
        "READER_JOIN_S": 0.5,
        "GIT_TIMEOUT_S": 60.0,
        "CLIENT_POLL_TIMEOUT_S": 5.0,
        "CLIENT_BACKOFF_CAP_S": 0.05,
        "CLIENT_REAP_S": 0.01,
    }.items():
        monkeypatch.setattr(tunables, name, value)
    monkeypatch.delenv("AGENTOS_SHELL", raising=False)
    monkeypatch.delenv("AGENTOS_TOKEN", raising=False)
    monkeypatch.delenv("AGENTOS_CLIENT_TOKEN", raising=False)
    for name in (
        "GITHUB_ACTIONS",
        "GH_TOKEN",
        "GITHUB_REPOSITORY",
        "GITHUB_API_URL",
        "GITHUB_SERVER_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_pr_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker tests never call a real `gh`; PR lookup tests delenv this and fake PR_LOOKUP."""
    monkeypatch.setenv("AGENTOS_PR_LOOKUP", "0")


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    httpd.daemon_threads = True
    fake.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    # A short poll interval: shutdown() waits up to one interval (the default 0.5 s per test).
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield fake
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def no_leaked_events_thread() -> Iterator[None]:
    """Fail a test that leaves a run's events thread running (it would post into later tests)."""
    before = set(threading.enumerate())
    yield
    leaked = [t for t in threading.enumerate() if t.name == "agentos-events" and t not in before]
    assert not leaked, "the run left its events thread running"


# ---------------------------------------------------------------- git helpers


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    p = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )
    if p.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr.decode(errors='replace')}")
    return p.stdout.decode("utf-8", "replace")


@dataclass
class Origin:
    """A temp bare repo used as `origin`."""

    bare: Path

    @property
    def url(self) -> str:
        return self.bare.as_uri()

    def has_branch(self, branch: str) -> bool:
        return bool(git(self.bare, "branch", "--list", branch).strip())

    def show(self, rev: str) -> str:
        return git(self.bare, "show", rev)

    def tree(self, branch: str) -> list[str]:
        return git(self.bare, "ls-tree", "-r", "--name-only", branch).splitlines()

    def log_patch(self, branch: str) -> str:
        return git(self.bare, "log", "-p", branch)


@pytest.fixture(scope="session")
def origin_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The bare repo `origin` copies, built once per session (and per xdist worker)."""
    root = tmp_path_factory.mktemp("origin-template")
    bare = root / "origin.git"
    seed = root / "seed"
    git(root, "init", "-q", "--bare", "-b", "main", str(bare))
    git(root, "init", "-q", "-b", "main", str(seed))

    (seed / "README.md").write_bytes(b"# App\n")
    (seed / "AGENTS.md").write_bytes(b"# Repo agents\nBe nice.\n")
    (seed / ".gitignore").write_bytes(b"*.log\n!*.md\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")

    git(seed, "remote", "add", "origin", bare.as_uri())
    git(seed, "push", "-q", "origin", "main")
    return bare


@pytest.fixture
def origin(tmp_path: Path, origin_template: Path) -> Origin:
    """main has README.md, a tracked AGENTS.md and a .gitignore that re-includes every *.md.

    A per-test copy of the session template: tests push to it and add hooks."""
    bare = tmp_path / "origin.git"
    shutil.copytree(origin_template, bare)
    return Origin(bare)


# ---------------------------------------------------------------- manifests


def agent_stage(stage_id: str, *actions: str, stdin: str | None = None) -> dict[str, Any]:
    return {
        "id": stage_id,
        "name": stage_id.title(),
        "kind": "agent",
        "timeout_s": 60,
        "argv": [sys.executable, str(FAKE_STAGE), *actions],
        "stdin": stdin,
        "command": None,
    }


def shell_stage(stage_id: str, command: str) -> dict[str, Any]:
    return {
        "id": stage_id,
        "name": stage_id.title(),
        "kind": "shell",
        "timeout_s": 60,
        "argv": None,
        "stdin": None,
        "command": command,
    }


def invocation(
    *actions: str, stdin: str = "", tail: str = "", prompt_in_argv: bool = False
) -> dict[str, Any]:
    """A later-round or repair call of the fake agent (a manifest Invocation). The prompt is
    `stdin`; with `prompt_in_argv` it is the last action instead (put `saveargs=P` before it)."""
    argv = [sys.executable, str(FAKE_STAGE), *actions]
    return {
        "argv": argv,
        "stdin": None if prompt_in_argv else stdin,
        "prompt_arg": len(argv) - 1 if prompt_in_argv else None,
        "tail": tail,
    }


def verdict_file(verdict: Any, notes: Any) -> str:
    """A fake-agent action that writes this verdict to .agentos/verdict.json."""
    return "write=.agentos/verdict.json:" + json.dumps({"verdict": verdict, "notes": notes})


def python_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make shell stages run `python -c <command>` (portable, no pwsh/bash needed)."""
    monkeypatch.setenv("AGENTOS_SHELL", f'"{sys.executable}" -c')


AGENTS_BLOCK = (
    "Working on AOS-42. Read .claude/skills/context-aos-42-fix-login-bug/SKILL.md first.\r\n"
    "Do not run git commit or push."
)


def context(path: str, content: str, kind: str, name: str, **extra: Any) -> dict[str, Any]:
    """A package context entry (write access, replace mode unless `extra` says otherwise)."""
    entry = {"path": path, "kind": kind, "name": name, "mode": "replace", "block": None}
    return {**entry, "access": "write", "content": content, **extra}


def make_package(
    stages: list[dict[str, Any]], *, repo_url: str | None = None, run_id: int = 42
) -> dict[str, Any]:
    """A protocol 2 job package shaped like the server's (fixtures/job_v2.json)."""
    block = {"mode": "block", "block": KEY}
    repo = repo_url is not None
    return {
        "protocol": 2,
        "job": {
            "id": f"{run_id}-1-3f9c0a1be47d2c55",
            "run_id": run_id,
            "segment": 1,
            "segments": 1,
            "budget_s": 3600,
            "not_after": "2100-01-01T00:00:00Z",
        },
        "journal": {"first_seq": 1},
        "report": {"mode": "live", "url": None},
        "run_id": run_id,
        "ticket": {"key": KEY, "title": "Fix login bug"},
        "workspace": {
            "mode": "repo" if repo else "none",
            "repo_url": repo_url,
            "base_branch": "main" if repo else None,
            "branch": f"agent/{KEY}" if repo else None,
            "protect": [SKILL_DIR],
            "blocks": [KEY],
        },
        "context": [
            context(
                ".claude/skills/review-checklist/SKILL.md",
                "# Review checklist\n",
                "skill",
                "review-checklist",
                access="read",
            ),
            context(f"{SKILL_DIR}/SKILL.md", "# AOS-42\n", "skill", SKILL_DIR.split("/")[-1]),
            context(
                f"{SKILL_DIR}/TASKS.md",
                "# Tasks\n\n- [ ] Fix expiry\n- [ ] Add test\n",
                "plan",
                "TASKS",
            ),
            context("AGENTS.md", AGENTS_BLOCK, "instructions", KEY, **block),
            context("CLAUDE.md", "@AGENTS.md", "instructions", KEY, **block),
        ],
        "notes": [{"path": f"{SKILL_DIR}/learnings.log", "name": "learnings", "content": ""}],
        "sync_back": {"dir": ".agentos/sync_back", "max_bytes": 20 * 1024 * 1024},
        "env": {"AGENTOS_SKILL_DIR": SKILL_DIR, "AGENTOS_SYNC_BACK": ".agentos/sync_back"},
        "stages": stages,
        "limits": {"max_file_bytes": 1048576, "max_line_bytes": 262144},
        "loops": [],
        "start_stage": None,
        "warnings": [],
    }


def run_worker(server: FakeServer, workdir: Path) -> int:
    return main.main(["--server", server.url, "--token", JOB_TOKEN, "--workdir", str(workdir)])
