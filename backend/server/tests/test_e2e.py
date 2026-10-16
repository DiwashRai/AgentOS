"""End to end, no network and no LLM (SPEC §14): the real server (uvicorn on a free loopback port,
dispatcher and reaper loops on), the real LocalProcessLauncher, the real worker.py and a temp bare
git repo as the remote, driven only through the HTTP API. Agent stages run `fake_agent.py`.

The remote is the catalog repo `remote`. Its `https://` URL (repo_url_problem accepts no file://)
is what the worker's git rewrites to the bare repo's file:// URL with `url.<file-url>.insteadOf`,
set via GIT_CONFIG_* in the worker env file (AGENTOS_WORKER_ENV_FILE). No server escape hatch.

Skip with `pytest -m "not e2e"`.
"""

from __future__ import annotations

import ctypes
import io
import os
import re
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx
import pytest
import uvicorn

from agentos.config import Settings
from agentos.main import create_app
from agentos.models import (
    FileContent,
    FileInfo,
    Me,
    RunDetail,
    RunStatus,
    RunSummary,
    TaskProgress,
    TicketDetail,
)
from agentos.pipeline.runtime import NamedRuntime
from agentos.pipeline.space import RepoSpec, SpaceConfig
from agentos.runs import dispatcher
from tests.fake_agent import BLOCK_LINE, SYNC_BACK_FILE
from tests.settings_support import seed_test_settings

pytestmark = pytest.mark.e2e

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"
REPO_URL = "https://git.agentos-e2e.invalid/acme/remote.git"
CSRF = {"X-AgentOS": "1"}
DEADLINE_S = 60.0
POLL_S = 0.1
TERMINAL: frozenset[RunStatus] = frozenset({"succeeded", "gated", "failed", "cancelled"})
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}

T = TypeVar("T")


# ------------------------------------------------------------------ the remote


def git(*args: str, cwd: Path | None = None) -> str:
    """Run git and return stdout; fail the test with stderr on a non-zero exit."""
    p = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        capture_output=True,
        check=False,
        env={**os.environ, **GIT_ENV},
    )
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr.decode('utf-8', 'replace')}"
    return p.stdout.decode("utf-8", "replace")


# The nasty cases from SPEC §14: a tracked AGENTS.md (a block-mode context file) and a
# .gitignore whose `!*.md` overrides the worker's info/exclude for CLAUDE.md. The repo's own agent
# is what a ticket shipping the same path clashes with.
REPO_AGENT = ".claude/agents/repo-reviewer.md"
SEED = {
    "AGENTS.md": "# Repo rules\n\nKeep it simple.\n",
    ".gitignore": "*.tmp\n!*.md\n",
    "README.md": "# remote\n",
    REPO_AGENT: "# The repo's reviewer\n",
}


def make_remote(root: Path) -> Path:
    """A bare repo whose `main` holds SEED in one commit."""
    seed, bare = root / "seed", root / "remote.git"
    git("-c", "init.defaultBranch=main", "init", "-q", str(seed))
    for path, content in SEED.items():
        (seed / path).parent.mkdir(parents=True, exist_ok=True)
        (seed / path).write_bytes(content.encode())
    git("add", "-A", cwd=seed)
    git(
        "-c", "user.name=Seed", "-c", "user.email=seed@localhost", "-c", "commit.gpgsign=false",
        "commit", "-q", "--no-verify", "-m", "initial", cwd=seed,
    )  # fmt: skip
    git("-c", "init.defaultBranch=main", "init", "-q", "--bare", str(bare))
    git("push", "-q", str(bare), "main:main", cwd=seed)
    return bare


# ------------------------------------------------------------------ processes


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = k32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie (exited, not yet reaped by its parent) is dead for our purposes
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0]
    except OSError:
        return True
    return state != "Z"


# ------------------------------------------------------------------ the server + API driver


@dataclass
class E2E:
    http: httpx.Client
    settings: Settings
    remote: Path
    me: Me
    run_ids: list[int] = field(default_factory=list)

    # ---------------------------------------------------------------- diagnostics

    def diag(self, run_id: int | None) -> str:
        """The worker log and the run's last events, for assertion messages."""
        if run_id is None:
            return ""
        log = self.settings.logs_dir / f"worker-{run_id}.log"
        text = log.read_text("utf-8", "replace") if log.exists() else "(no worker log)"
        try:
            events = self.events(run_id)[-40:]
            lines = [f"{e['seq']} {e['type']} {e.get('stage_id')} {e['data']}" for e in events]
        except AssertionError as exc:
            lines = [f"(events unavailable: {exc})"]
        return (
            f"\n--- worker-{run_id}.log ---\n{text}"
            f"\n--- run {run_id} events (last 40) ---\n" + "\n".join(lines)
        )

    # ---------------------------------------------------------------- API

    def get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params=params)
        assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text}"
        return r.json()

    def send(self, method: str, path: str, body: Any, status: int = 200) -> Any:
        r = self.http.request(method, path, json=body)
        assert r.status_code == status, f"{method} {path}: {r.status_code} {r.text}"
        return r.json() if r.content else None

    def ticket(self, key: str) -> TicketDetail:
        return TicketDetail.model_validate(self.get(f"/api/tickets/{key}"))

    def create_ticket(self, title: str) -> TicketDetail:
        body = {
            "title": title,
            "description": "Say hello from a module.",
            "repo": "remote",  # REPO_URL in the catalog; base branch = its default (main)
            "owner_id": self.me.id,
        }
        return TicketDetail.model_validate(self.send("POST", "/api/tickets", body, 201))

    def configure(self, key: str, modes: dict[str, str]) -> None:
        """Give each stage in `modes` the fake runtime and a `fake-agent: <mode>` prompt line;
        disable open-pr (no network) and leave lint disabled."""
        t = self.ticket(key)
        pipeline = t.pipeline.model_dump()
        for stage in pipeline["stages"]:
            if stage["id"] == "open-pr":
                stage["enabled"] = False
            if stage["id"] in modes:
                base = stage["prompt"].split("\n\nfake-agent:")[0]
                stage["prompt"] = f"{base}\n\nfake-agent: {modes[stage['id']]}"
                stage["runtime"] = "fake"
        body = {"pipeline": pipeline, "base_version": t.pipeline_version}
        self.send("PUT", f"/api/tickets/{key}/pipeline", body)

    def dispatch(self, key: str, from_stage: str | None = None) -> RunSummary:
        body = {} if from_stage is None else {"from_stage": from_stage}
        run = RunSummary.model_validate(self.send("POST", f"/api/tickets/{key}/runs", body, 201))
        self.run_ids.append(run.id)
        return run

    def run(self, run_id: int) -> RunDetail:
        return RunDetail.model_validate(self.get(f"/api/runs/{run_id}"))

    def events(self, run_id: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = self.get(f"/api/runs/{run_id}/events", limit=1000)
        return events

    def files(self, key: str) -> dict[str, FileInfo]:
        infos = [FileInfo.model_validate(f) for f in self.get(f"/api/tickets/{key}/files")]
        return {f.path: f for f in infos}

    def file(self, key: str, path: str) -> FileContent:
        return FileContent.model_validate(self.get(f"/api/tickets/{key}/files/{path}"))

    # ---------------------------------------------------------------- waiting

    def wait(self, what: str, probe: Callable[[], T | None], run_id: int | None = None) -> T:
        deadline = time.monotonic() + DEADLINE_S
        while True:
            value = probe()
            if value is not None and value is not False:
                return value
            if time.monotonic() > deadline:
                pytest.fail(f"timed out after {DEADLINE_S}s waiting for {what}{self.diag(run_id)}")
            time.sleep(POLL_S)

    def wait_run(self, run_id: int, status: RunStatus) -> RunDetail:
        """Poll until the run is terminal; fail at once (with the worker log) if it ended in any
        other status than `status`."""

        def probe() -> RunDetail | None:
            run = self.run(run_id)
            return run if run.status in TERMINAL else None

        run = self.wait(f"run {run_id} to finish", probe, run_id)
        assert run.status == status, (
            f"run {run_id} ended {run.status} (error={run.error!r}), expected {status}"
            + self.diag(run_id)
        )
        return run


def _free_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


@pytest.fixture(scope="module")
def e2e(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2E]:
    root = tmp_path_factory.mktemp("e2e")
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.upper().startswith("AGENTOS_"):
                mp.delenv(name)
        mp.setattr(dispatcher, "INTERVAL_S", 0.1)

        remote = make_remote(root)
        worker_env = root / "worker.env"
        worker_env.write_bytes(
            (
                "GIT_CONFIG_COUNT=1\n"
                f"GIT_CONFIG_KEY_0='url.{remote.as_uri()}.insteadOf'\n"
                f"GIT_CONFIG_VALUE_0='{REPO_URL}'\n"
                "AGENTOS_PR_LOOKUP=0\n"  # never ask a real `gh` about the fake remote
            ).encode()
        )

        sock = _free_socket()
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        settings = Settings(
            _env_file=None,
            data_dir=root / "data",
            workspaces_dir=root / "workspaces",
            dev_auth=True,
            public_url=url,
            worker_env_file=worker_env,
            local_max_concurrency=None,
        )
        seed_test_settings(
            settings,
            space=SpaceConfig(repos=(RepoSpec(name="remote", url=REPO_URL),)),
            runtimes=[
                NamedRuntime(
                    name="fake", argv=[sys.executable, str(FAKE_AGENT)], prompt_via="stdin"
                )
            ],
        )

        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), log_config=None, access_log=False)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, name="e2e-uvicorn", daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            assert thread.is_alive(), "the server failed to start (see the captured log)"
            assert time.monotonic() < deadline, "the server did not start within 20 s"
            time.sleep(0.05)

        try:
            with httpx.Client(base_url=url, headers=CSRF, timeout=15) as http:
                r = http.post("/api/auth/login", json={"username": "e2e"})
                assert r.status_code == 200, r.text
                env = E2E(http, settings, remote, Me.model_validate(r.json()))
                try:
                    yield env
                finally:
                    _settle(env)
        finally:
            server.should_exit = True
            thread.join(30)
            sock.close()


def _settle(env: E2E) -> None:
    """Best effort after a failed test: cancel unfinished runs so no worker outlives the server."""
    for run_id in env.run_ids:
        try:
            if env.run(run_id).status not in TERMINAL:
                env.http.post(f"/api/runs/{run_id}/cancel")
                deadline = time.monotonic() + 15
                while env.run(run_id).status not in TERMINAL and time.monotonic() < deadline:
                    time.sleep(0.2)
        except (AssertionError, httpx.HTTPError):
            continue


# ------------------------------------------------------------------ helpers for assertions


def rail(t: TicketDetail) -> dict[str, str]:
    """Rail states of the enabled stages (lint and open-pr are `skipped` throughout)."""
    return {step.stage_id: step.state for step in t.rail if step.state != "skipped"}


def gates(t: TicketDetail) -> dict[str, str]:
    return {step.stage_id: step.gate.state for step in t.rail if step.gate is not None}


def track(t: TicketDetail) -> dict[str, str]:
    return {step.phase: step.state for step in t.phase_track}


def branch_diff(remote: Path, branch: str) -> list[str]:
    out = git("--git-dir", str(remote), "diff", "--name-only", "--no-renames", f"main..{branch}")
    return out.splitlines()


def assert_clean_branch(remote: Path, key: str) -> None:
    """SPEC principle 5 on the remote: the branch has code commits and no context at all."""
    branch = f"agent/{key}"
    git("--git-dir", str(remote), "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    changed = branch_diff(remote, branch)
    assert changed == ["src/hello.py"], changed
    leaked = [
        p
        for p in changed
        if p.startswith(".claude/") or p in ("AGENTS.md", "CLAUDE.md", "TASKS.md")
    ]
    assert leaked == []
    patch = git("--git-dir", str(remote), "log", "-p", "--no-color", f"main..{branch}")
    assert "agentos:begin" not in patch
    assert "agentos:end" not in patch
    assert "Note from the fake agent" not in patch  # the AGENTS.md edit outside the block
    show = git("--git-dir", str(remote), "show", f"{branch}:src/hello.py")
    assert show == f'GREETING = "hello from {key}"\n'
    # The tracked AGENTS.md is untouched on the branch (skip-worktree; SPEC §17 notes the cost).
    agents = git("--git-dir", str(remote), "show", f"{branch}:AGENTS.md")
    assert agents == SEED["AGENTS.md"]


# ------------------------------------------------------------------ the tests


def test_gate_edit_continue_succeeds(e2e: E2E) -> None:
    t = e2e.create_ticket("Say hello")
    key = t.key
    tasks_md = f".claude/skills/{t.skill_name}/TASKS.md"
    learnings = tasks_md.replace("TASKS.md", "learnings.log")
    review_md = tasks_md.replace("TASKS.md", "REVIEW.md")
    e2e.configure(key, {"plan": "plan", "implement": "implement", "review": "review"})
    assert e2e.ticket(key).next_stage == "plan"

    # 1. Dispatch: plan runs alone (it gates) and stops at the gate.
    run1 = e2e.dispatch(key)
    assert run1.stage_ids == ["plan"]
    detail = e2e.wait_run(run1.id, "gated")
    assert [(s.stage_id, s.status, s.exit_code) for s in detail.stage_runs] == [
        ("plan", "succeeded", 0)
    ], e2e.diag(run1.id)
    assert detail.sync_back is None  # plan leaves the sync_back folder empty

    t = e2e.ticket(key)
    assert t.attention == "needs_you"
    assert t.phase == "plan"
    assert rail(t) == {"plan": "current", "implement": "pending", "review": "pending"}
    assert gates(t) == {"plan": "you"}
    assert track(t) == {
        "inbox": "done",
        "plan": "current",
        "build": "pending",
        "verify": "skipped",  # lint is disabled
        "review": "pending",
        "done": "pending",
    }
    assert t.next_stage == "implement"
    assert (t.now.actor, t.now.run_id) == (None, run1.id)
    assert t.tasks == TaskProgress(done=0, total=3), e2e.diag(run1.id)
    files = e2e.files(key)
    assert files[tasks_md].author_run_id == run1.id, e2e.diag(run1.id)
    assert files[learnings].author_run_id == run1.id
    assert files["AGENTS.md"].author_run_id is None  # the block didn't change
    assert review_md not in files
    events = e2e.events(run1.id)
    stdout = [e["data"]["text"] for e in events if e["type"] == "log"]
    assert any('"subtype": "init"' in line for line in stdout), e2e.diag(run1.id)
    stderr = [e for e in events if e["type"] == "log" and e["data"]["stream"] == "stderr"]
    assert any("fake-agent plan" in e["data"]["text"] for e in stderr), e2e.diag(run1.id)
    # Nothing outside the skill dir changed, so nothing was committed or pushed.
    branches = git("--git-dir", str(e2e.remote), "branch", "--list", f"agent/{key}")
    assert branches.strip() == ""

    # 2. A human edits the plan at the gate: drop one item.
    current = e2e.file(key, tasks_md)
    edited = current.content.replace("- [ ] add a greeting\n", "")
    assert edited != current.content
    e2e.send(
        "PUT",
        f"/api/tickets/{key}/files/{tasks_md}",
        {"content": edited, "mode": "replace", "base_version": current.version},
    )
    assert e2e.ticket(key).tasks == TaskProgress(done=0, total=2)

    # 3. Continue: implement → review run to the end (lint and open-pr are disabled).
    run2 = e2e.dispatch(key)
    assert run2.from_stage == "implement"
    assert run2.stage_ids == ["implement", "review"]
    detail = e2e.wait_run(run2.id, "succeeded")
    assert [(s.stage_id, s.status) for s in detail.stage_runs] == [
        ("implement", "succeeded"),
        ("review", "succeeded"),
    ]

    t = e2e.ticket(key)
    assert t.attention == "needs_you"
    assert rail(t) == {"plan": "done", "implement": "done", "review": "done"}
    assert gates(t) == {"plan": "passed"}
    assert t.phase == "review"
    assert track(t) == {
        "inbox": "done",
        "plan": "done",
        "build": "done",
        "verify": "skipped",
        "review": "done",
        "done": "pending",
    }
    assert t.next_stage is None
    assert (t.now.actor, t.now.run_id) == (None, run2.id)
    assert t.tasks == TaskProgress(done=2, total=2), e2e.diag(run2.id)
    files = e2e.files(key)
    for path in (tasks_md, learnings, "AGENTS.md"):
        assert files[path].author_run_id == run2.id, (path, e2e.diag(run2.id))
    assert BLOCK_LINE in e2e.file(key, "AGENTS.md").content
    assert review_md not in files  # undeclared: not kept, and the run log names it
    warnings = [e["data"]["message"] for e in e2e.events(run2.id) if e["type"] == "warning"]
    assert any(w.startswith("not kept") and review_md in w for w in warnings), warnings
    tasks = e2e.file(key, tasks_md).content
    assert "add a greeting" not in tasks  # the human edit reached the agent
    assert tasks.count("- [x]") == 2
    log = e2e.file(key, learnings).content
    assert f"run {run1.id}: planned" in log
    assert f"run {run2.id}: implemented" in log

    # 4. The sync_back folder came back as one zip, served as an attachment.
    assert detail.sync_back is not None, e2e.diag(run2.id)
    assert detail.sync_back.files == 1
    r = e2e.http.get(f"/api/runs/{run2.id}/sync-back")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    assert len(r.content) == detail.sync_back.bytes
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        assert archive.namelist() == [SYNC_BACK_FILE]
        assert archive.read(SYNC_BACK_FILE) == f"run {run2.id} implemented {key}\n".encode()

    # 5. The remote: code commit(s) on agent/KEY and no manifest files or block text.
    assert_clean_branch(e2e.remote, key)
    subjects = git("--git-dir", str(e2e.remote), "log", "--format=%s", f"main..agent/{key}")
    assert subjects.splitlines() == [f"{key} [implement] wip"]


def test_failed_stage_then_retry_succeeds(e2e: E2E) -> None:
    key = e2e.create_ticket("Fail once").key
    e2e.configure(key, {"plan": "plan", "implement": "fail", "review": "review"})

    run1 = e2e.dispatch(key, from_stage="implement")
    assert run1.stage_ids == ["implement", "review"]
    detail = e2e.wait_run(run1.id, "failed")
    assert [(s.stage_id, s.status, s.exit_code) for s in detail.stage_runs] == [
        ("implement", "failed", 1),
        ("review", "cancelled", None),
    ], e2e.diag(run1.id)

    t = e2e.ticket(key)
    assert t.attention == "blocked"
    assert rail(t) == {"plan": "pending", "implement": "failed", "review": "pending"}
    assert t.phase == "build"
    assert track(t)["build"] == "failed"
    assert t.next_stage == "implement"
    assert t.now.run_id == run1.id

    # Fix the stage and retry: the default from_stage is the failed one.
    e2e.configure(key, {"implement": "implement"})
    run2 = e2e.dispatch(key)
    assert run2.from_stage == "implement"
    e2e.wait_run(run2.id, "succeeded")
    t = e2e.ticket(key)
    assert t.attention == "needs_you"
    assert rail(t) == {"plan": "pending", "implement": "done", "review": "done"}
    assert t.next_stage is None
    assert_clean_branch(e2e.remote, key)


def test_cancel_mid_stage(e2e: E2E) -> None:
    key = e2e.create_ticket("Cancel me").key
    e2e.configure(key, {"implement": "slow"})
    run = e2e.dispatch(key, from_stage="implement")

    def stage_running() -> RunDetail | None:
        detail = e2e.run(run.id)
        assert detail.status not in TERMINAL, f"run ended {detail.status}" + e2e.diag(run.id)
        running = detail.status == "running" and detail.stage_runs[0].status == "running"
        return detail if running else None

    detail = e2e.wait("the implement stage to run", stage_running, run.id)
    assert detail.launch_ref is not None
    worker_pid = int(detail.launch_ref)

    def agent_pid() -> int | None:
        for e in e2e.events(run.id):
            text = e["data"].get("text", "") if e["type"] == "log" else ""
            if m := re.search(r'"subtype": "pid", "pid": (\d+)', text):
                return int(m.group(1))
        return None

    pid = e2e.wait("the fake agent's pid", agent_pid, run.id)

    r = e2e.send("POST", f"/api/runs/{run.id}/cancel", None)
    assert RunSummary.model_validate(r).stop_requested_at is not None
    detail = e2e.wait_run(run.id, "cancelled")
    assert detail.stage_runs[0].status == "cancelled", e2e.diag(run.id)
    assert detail.stage_runs[0].started_at is not None

    t = e2e.ticket(key)
    assert t.attention == "blocked"
    assert rail(t)["implement"] == "failed"
    assert t.next_stage == "implement"
    assert t.now.run_id == run.id

    # No process left behind: the stage tree is killed and the worker exits after completing.
    e2e.wait("the fake agent to exit", lambda: not pid_alive(pid), run.id)
    e2e.wait("the worker to exit", lambda: not pid_alive(worker_pid), run.id)


def test_shipped_agent_the_repo_already_has_fails_setup(e2e: E2E) -> None:
    key = e2e.create_ticket("Clash with the repo's agent").key
    e2e.configure(key, {"plan": "plan"})
    body = {"content": "# The ticket's reviewer\n", "mode": "replace", "base_version": 0}
    e2e.send("PUT", f"/api/tickets/{key}/files/{REPO_AGENT}", body)

    run = e2e.dispatch(key)
    detail = e2e.wait_run(run.id, "failed")

    assert REPO_AGENT in (detail.error or ""), e2e.diag(run.id)
    assert [s.status for s in detail.stage_runs] == ["cancelled"], e2e.diag(run.id)
    assert e2e.files(key)[REPO_AGENT].author_run_id is None
    branches = git("--git-dir", str(e2e.remote), "branch", "--list", f"agent/{key}")
    assert branches.strip() == ""
