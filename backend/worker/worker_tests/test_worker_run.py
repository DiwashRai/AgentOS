"""Whole runs through worker.main(): fake server + temp bare repo as origin + fake agent.

Covers the commit procedure (context files and notes never committed, repo edits committed), the
pre-push guard, abort (410), server loss, cancel, push rejection, no-repo mode and the early exits.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import worker
from worker_tests.worker_support import (
    AGENTS_BLOCK,
    DISPATCH_TOKEN,
    KEY,
    SKILL_DIR,
    FakeServer,
    Origin,
    agent_stage,
    error,
    git,
    make_package,
    python_shell,
    run_worker,
    shell_stage,
)

BRANCH = f"agent/{KEY}"
LINT = (
    "import pathlib; pathlib.Path('src').mkdir(exist_ok=True); "
    "pathlib.Path('src/lint.txt').write_text('linted')"
)


def trip_on(kind: str, answer: tuple[int, Any]) -> Any:
    """A hook that starts answering `answer` to everything once an event of `kind` is posted."""
    tripped = False

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        nonlocal tripped
        if path == "/api/worker/events" and any(e["type"] == kind for e in body["events"]):
            tripped = True
        return answer if tripped else None

    return hook


def stage_events(server: FakeServer, kind: str) -> list[dict[str, Any]]:
    return [e for e in server.events() if e["type"] == kind]


def synced(server: FakeServer, stage_id: str) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for body in server.bodies("/api/worker/sync"):
        if body["stage_id"] == stage_id:
            files.update({f["path"]: (f["content"], f["mode"]) for f in body["files"]})
    return files


def test_repo_run_commits_code_and_never_context(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_shell(monkeypatch)
    server.manifest = make_package(
        [agent_stage("implement", "edit", "env"), shell_stage("lint", LINT)], repo_url=origin.url
    )
    workdir = tmp_path / "ws"
    assert run_worker(server, workdir) == 0

    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert not workdir.exists(), "the workdir is deleted on completion"

    # Code reached the branch; context files and the block never did.
    tree = origin.tree(BRANCH)
    assert {"src/app.py", "src/lint.txt", "README.md", "AGENTS.md", ".gitignore"} <= set(tree)
    assert "CLAUDE.md" not in tree
    assert not [p for p in tree if p.startswith(".claude/")]
    assert origin.show(f"{BRANCH}:AGENTS.md") == "# Repo agents\nBe nice.\n"
    assert "agentos:begin" not in origin.log_patch(BRANCH)
    subjects = git(origin.bare, "log", "--format=%s", f"main..{BRANCH}").splitlines()
    assert subjects == [f"{KEY} [lint] wip", f"{KEY} [implement] wip"]
    author = git(origin.bare, "log", "-1", "--format=%an <%ae>", BRANCH).strip()
    assert author == "AgentOS <agentos@localhost>"

    # Context went to the server instead.
    files = synced(server, "implement")
    assert files[f"{SKILL_DIR}/TASKS.md"] == (
        "# Tasks\n\n- [x] Fix expiry\n- [ ] Add test\n",
        "replace",
    )
    assert files[f"{SKILL_DIR}/learnings.log"] == ("expiry is computed in minutes\n", "replace")
    assert files[f"{SKILL_DIR}/notes.md"] == ("# Notes\n", "replace")
    assert files["AGENTS.md"] == (AGENTS_BLOCK, "block")
    assert files["CLAUDE.md"] == ("@AGENTS.md", "block")
    assert f"{SKILL_DIR}/notes.md" not in synced(server, "lint")  # unchanged since

    # Events: stage lifecycle, seqs, clone output as run-level system logs.
    started = [(e["stage_id"], e["data"]) for e in stage_events(server, "stage_started")]
    finished = [(e["stage_id"], e["data"]) for e in stage_events(server, "stage_finished")]
    assert started == [("implement", {"round": 1}), ("lint", {"round": 1})]
    assert finished == [
        ("implement", {"status": "succeeded", "exit_code": 0, "reason": None, "round": 1}),
        ("lint", {"status": "succeeded", "exit_code": 0, "reason": None, "round": 1}),
    ]
    events = server.events()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(
        e["type"] == "log" and e["stage_id"] is None and e["data"]["stream"] == "system"
        for e in events
    )
    assert server.warnings() == []

    # Tokens: the dispatch token claims; everything else uses the worker's own token.
    claim, *rest = server.requests
    assert claim.path == "/api/worker/claim"
    assert claim.auth == f"Bearer {DISPATCH_TOKEN}"
    worker_token = claim.body["worker_token"]
    assert len(worker_token) >= 32
    assert {r.auth for r in rest} == {f"Bearer {worker_token}"}

    # Stage env.
    env = json.loads(next(t for t in server.logs("stdout") if t.startswith("{")))
    assert env["AGENTOS_RUN_ID"] == "42"
    assert env["AGENTOS_TICKET_KEY"] == KEY
    assert env["AGENTOS_SKILL_DIR"] == SKILL_DIR
    assert env["AGENTOS_SYNC_BACK"] == ".agentos/sync_back"
    assert "AGENTOS_TOKEN" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_a_second_run_continues_the_branch(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws1") == 0
    server.manifest = make_package(
        [agent_stage("review", "write=src/review.md:ok")], repo_url=origin.url, run_id=43
    )
    assert run_worker(server, tmp_path / "ws2") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert {"src/app.py", "src/review.md"} <= set(origin.tree(BRANCH))


def test_build_caches_are_not_committed(server: FakeServer, origin: Origin, tmp_path: Path) -> None:
    # origin's .gitignore says nothing about caches: the worker's defaults keep them out.
    caches = ["src/__pycache__/app.cpython-311.pyc", "node_modules/x/index.js", ".DS_Store"]
    writes = [f"write={p}:junk" for p in caches]
    server.manifest = make_package(
        [agent_stage("implement", "write=src/app.py:code", *writes)], repo_url=origin.url
    )
    assert run_worker(server, tmp_path / "ws") == 0
    tree = set(origin.tree(BRANCH))
    assert "src/app.py" in tree
    assert not tree & set(caches)


def test_nothing_to_commit_pushes_nothing(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    server.manifest = make_package([agent_stage("plan", "print=thinking")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert not origin.has_branch(BRANCH)


@pytest.mark.parametrize(
    ("action", "why"),
    [
        ("selfcommit", "context files: CLAUDE.md"),
        ("copyblock", "contain the agentos context block"),
    ],
)
def test_pre_push_guard_blocks_agent_commits_of_context(
    server: FakeServer, origin: Origin, tmp_path: Path, action: str, why: str
) -> None:
    server.manifest = make_package([agent_stage("implement", "edit", action)], repo_url=origin.url)
    workdir = tmp_path / "ws"
    assert run_worker(server, workdir) == 0
    done = server.complete()
    assert done is not None
    assert done["status"] == "failed"
    assert done["failed_stage"] == "implement"
    assert "pre-push guard" in done["error"]
    assert why in done["error"]
    assert not origin.has_branch(BRANCH), "nothing was pushed"
    assert workdir.exists(), "the workdir is kept on failure"
    [finished] = stage_events(server, "stage_finished")
    assert finished["data"]["status"] == "failed"
    assert finished["data"]["exit_code"] == 0
    assert synced(server, "implement"), "context is still synced"


def test_failed_stage_still_syncs_commits_and_pushes(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    server.manifest = make_package(
        [agent_stage("implement", "edit", "exit=3"), agent_stage("never", "print=no")],
        repo_url=origin.url,
    )
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "implement",
        "error": "exited with code 3",
    }
    assert "src/app.py" in origin.tree(BRANCH)
    assert [e["stage_id"] for e in stage_events(server, "stage_started")] == ["implement"]
    [finished] = stage_events(server, "stage_finished")
    assert finished["data"] == {
        "status": "failed",
        "exit_code": 3,
        "reason": "exited with code 3",
        "round": 1,
    }


def test_run_gone_after_claim_means_no_commit_and_no_push(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    server.hook = trip_on("stage_started", error(410, "run_gone"))
    server.manifest = make_package(
        [agent_stage("implement", "edit", "sleep=30")], repo_url=origin.url
    )
    started = time.monotonic()
    assert run_worker(server, tmp_path / "ws") == 1
    assert time.monotonic() - started < 15, "the stage tree was killed"
    assert server.complete() is None
    assert server.bodies("/api/worker/sync") == []
    assert not origin.has_branch(BRANCH)
    assert git(tmp_path / "ws", "log", "--format=%s", "-1").strip() == "init", "nothing committed"


def test_unreachable_server_saves_the_work_and_exits_1(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "RETRY_BUDGET_S", 0.5)
    server.hook = trip_on("stage_started", error(503, "internal_error"))
    server.manifest = make_package(
        [agent_stage("implement", "edit", "sleep=30")], repo_url=origin.url
    )
    assert run_worker(server, tmp_path / "ws") == 1
    assert server.complete() is None
    tree = origin.tree(BRANCH)
    assert "src/app.py" in tree
    assert "CLAUDE.md" not in tree


def test_cancel_kills_the_stage_but_still_syncs_and_pushes(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    def hook(path: str, body: Any) -> None:
        if path == "/api/worker/events" and any(
            e["type"] == "log" and e["data"]["text"] == "started" for e in body["events"]
        ):
            server.cancel = True

    server.hook = hook
    server.manifest = make_package(
        [agent_stage("implement", "edit", "sleep=30"), agent_stage("never", "print=no")],
        repo_url=origin.url,
    )
    workdir = tmp_path / "ws"
    started = time.monotonic()
    assert run_worker(server, workdir) == 0
    assert time.monotonic() - started < 15
    assert server.complete() == {"status": "cancelled", "failed_stage": "implement", "error": None}
    [finished] = stage_events(server, "stage_finished")
    assert finished["data"] == {
        "status": "cancelled",
        "exit_code": None,
        "reason": "cancelled",
        "round": 1,
    }
    assert synced(server, "implement")[f"{SKILL_DIR}/notes.md"] == ("# Notes\n", "replace")
    assert "src/app.py" in origin.tree(BRANCH)
    assert not workdir.exists(), "the workdir is deleted on cancel"


def test_rejected_push_fails_the_run(server: FakeServer, origin: Origin, tmp_path: Path) -> None:
    hook = origin.bare / "hooks" / "pre-receive"
    hook.write_bytes(b"#!/bin/sh\necho 'no pushes today' >&2\nexit 1\n")
    hook.chmod(0o755)
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "implement",
        "error": "push rejected",
    }


def test_no_repo_mode(server: FakeServer, tmp_path: Path) -> None:
    server.manifest = make_package([agent_stage("plan", "edit")])
    workdir = tmp_path / "ws"
    assert run_worker(server, workdir) == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    files = synced(server, "plan")
    assert files[f"{SKILL_DIR}/TASKS.md"][0].startswith("# Tasks\n\n- [x]")
    assert files["AGENTS.md"] == (AGENTS_BLOCK, "block")
    assert not workdir.exists()


@pytest.mark.parametrize("status", [409, 410])
def test_claim_conflict_or_gone_exits_0(server: FakeServer, tmp_path: Path, status: int) -> None:
    server.hook = lambda path, body: error(status, "already_claimed")
    assert run_worker(server, tmp_path / "ws") == 0
    assert [r.path for r in server.requests] == ["/api/worker/claim"]
    assert not (tmp_path / "ws").exists()


def test_claim_with_a_bad_dispatch_token_exits_1(server: FakeServer, tmp_path: Path) -> None:
    server.hook = lambda path, body: error(401, "unauthorized")
    assert run_worker(server, tmp_path / "ws") == 1


def test_non_empty_workdir_fails_the_run(server: FakeServer, tmp_path: Path) -> None:
    workdir = tmp_path / "ws"
    workdir.mkdir()
    (workdir / "keep.txt").write_bytes(b"mine")
    server.manifest = make_package([agent_stage("plan", "print=x")])
    assert run_worker(server, workdir) == 0
    done = server.complete()
    assert done is not None
    assert (done["status"], done["failed_stage"]) == ("failed", None)
    assert "not empty" in done["error"]
    assert (workdir / "keep.txt").read_bytes() == b"mine"


def test_clone_failure_fails_the_run(server: FakeServer, tmp_path: Path) -> None:
    missing = (tmp_path / "nope.git").as_uri()
    server.manifest = make_package([agent_stage("plan", "print=x")], repo_url=missing)
    assert run_worker(server, tmp_path / "ws") == 0
    done = server.complete()
    assert done is not None
    assert (done["status"], done["failed_stage"]) == ("failed", None)
    assert done["error"].startswith("git clone failed")
    assert stage_events(server, "stage_started") == []


@pytest.mark.parametrize("protocol", [1, 3, None])
def test_another_protocol_fails_the_run_without_touching_git(
    server: FakeServer, origin: Origin, tmp_path: Path, protocol: int | None
) -> None:
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    server.manifest["protocol"] = protocol
    assert run_worker(server, tmp_path / "ws") == 0
    done = server.complete()
    assert done is not None
    assert done["status"] == "failed"
    assert "protocol" in done["error"]
    assert not (tmp_path / "ws").exists(), "no workspace, no clone"
    assert not origin.has_branch(BRANCH)


@pytest.mark.parametrize(
    ("field", "value"),
    [("repo_url", "--upload-pack=evil"), ("branch", "-x"), ("base_branch", "a..b")],
)
def test_a_bad_workspace_fails_setup_before_the_clone(
    server: FakeServer, origin: Origin, tmp_path: Path, field: str, value: str
) -> None:
    server.manifest = make_package([agent_stage("plan", "print=x")], repo_url=origin.url)
    server.manifest["workspace"][field] = value
    assert run_worker(server, tmp_path / "ws") == 0
    done = server.complete()
    assert done is not None
    assert (done["status"], done["failed_stage"]) == ("failed", None)
    assert field in done["error"]
    assert not (tmp_path / "ws" / ".git").exists()


def test_token_from_env_never_reaches_stages(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTOS_TOKEN", DISPATCH_TOKEN)
    server.manifest = make_package([agent_stage("plan", "env")])
    assert worker.main(["--server", server.url, "--workdir", str(tmp_path / "ws")]) == 0
    assert server.requests[0].auth == f"Bearer {DISPATCH_TOKEN}"
    env = json.loads(next(t for t in server.logs("stdout") if t.startswith("{")))
    assert "AGENTOS_TOKEN" not in env


def test_missing_program_fails_the_stage(server: FakeServer, tmp_path: Path) -> None:
    stage = agent_stage("plan")
    stage["argv"] = ["agentos-no-such-program-xyz", "-p"]
    server.manifest = make_package([stage])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "plan",
        "error": "program not found: agentos-no-such-program-xyz",
    }


def test_stage_timeout_fails_the_stage(server: FakeServer, tmp_path: Path) -> None:
    stage = agent_stage("plan", "sleep=30")
    stage["timeout_s"] = 1
    server.manifest = make_package([stage])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "plan",
        "error": "timed out after 1s",
    }


@pytest.mark.skipif(sys.platform != "win32", reason=".cmd shims only exist on Windows")
def test_cmd_shim_with_an_unsafe_prompt_argument_is_refused(
    server: FakeServer, tmp_path: Path
) -> None:
    shim = tmp_path / "agent.cmd"
    shim.write_bytes(b"@echo ran %*\r\n")
    stage = agent_stage("plan")
    stage["argv"] = [str(shim), "-p", "fix it & del everything"]
    server.manifest = make_package([stage])
    assert run_worker(server, tmp_path / "ws") == 0
    done = server.complete()
    assert done is not None
    assert done["error"] == "use prompt_via=stdin or a native binary"
    assert not any("ran" in t for t in server.logs())
