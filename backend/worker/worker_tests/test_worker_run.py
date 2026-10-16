"""Whole runs through worker.main(): fake server + temp bare repo as origin + fake agent.

Covers binding and the journal (every step a record, in seq order, resent from the ack), the
commit procedure (context files and notes never committed, repo edits committed), the pre-push
guard, the pre-push hook, abort (410), not_after, server loss, cancel, the time budget, push
rejection, no-repo mode and the early exits.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import worker
from worker_tests.worker_support import (
    JOB,
    JOB_TOKEN,
    JOURNAL,
    KEY,
    SKILL_DIR,
    FakeServer,
    Origin,
    agent_stage,
    error,
    git,
    invocation,
    make_package,
    python_shell,
    run_worker,
    shell_stage,
    verdict_file,
)

BRANCH = f"agent/{KEY}"
LINT = (
    "import pathlib; pathlib.Path('src').mkdir(exist_ok=True); "
    "pathlib.Path('src/lint.txt').write_text('linted')"
)
JOURNAL_V2 = Path(__file__).resolve().parents[2] / "server/tests/fixtures/journal_v2.jsonl"


def trip_on(kind: str, answer: tuple[int, Any]) -> Any:
    """A hook that starts answering `answer` to everything once a record of `kind` is posted."""
    tripped = False

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        nonlocal tripped
        if path == JOURNAL and any(r["type"] == kind for r in body["records"]):
            tripped = True
        return answer if tripped else None

    return hook


def stage_events(server: FakeServer, kind: str) -> list[dict[str, Any]]:
    return [e for e in server.events() if e["type"] == kind]


def synced(server: FakeServer, stage_id: str) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for record in server.records("sync"):
        if record["stage_id"] == stage_id:
            files.update({f["path"]: (f["content"], f["mode"]) for f in record["data"]["files"]})
    return files


def test_a_live_run_journals_every_step_in_seq_order(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    implement = agent_stage(
        "implement", "edit", f"write={SKILL_DIR}/notes.md:x", "write=.agentos/sync_back/r.md:ok"
    )
    review = agent_stage("review", verdict_file("approved", ""))
    repair = [invocation(verdict_file("pass", ""))]
    review.update(check=True, repairs=repair, verdict_from="file", verdict_repairs=1)
    server.manifest = make_package([implement, review], repo_url=origin.url)
    server.manifest["loops"] = [{"start": "implement", "end": "review", "max_rounds": 1}]
    assert run_worker(server, tmp_path / "ws") == 0

    # Bound first, then one record per step, numbered from the package's first_seq with no gap.
    records = server.records()
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    assert {r["job"] for r in records} == {server.manifest["job"]["id"]}
    steps = [r["type"] for r in records if r["type"] not in ("log", "warning")]
    assert steps == [
        "started",
        *("stage_started", "sync", "pushed", "stage_finished"),
        *("stage_started", "verdict_repair", "verdict", "sync", "stage_finished"),
        *("sync_back", "result"),
    ]
    sha256 = hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest()
    assert records[0]["data"] == {"protocol": 2, "worker_sha256": sha256}
    [pushed] = server.records("pushed")
    head = git(origin.bare, "rev-parse", BRANCH).strip()
    assert pushed["data"] == {"branch": BRANCH, "head": head, "commits": 1, "method": "git"}
    [(query, data)] = server.uploads()
    [sync_back] = server.records("sync_back")
    assert sync_back["data"] == {"sha256": query["sha256"], "bytes": len(data), "files": 1}
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}

    # Every request carries the job token and one instance id; the package is fetched first.
    assert server.requests[0].path == JOB
    assert {r.auth for r in server.requests} == {f"Bearer {JOB_TOKEN}"}
    assert len({r.instance for r in server.requests}) == 1
    assert server.requests[0].instance

    # Each record type has the shared fixture's keys.
    shapes = {r["type"]: r for r in map(json.loads, JOURNAL_V2.read_text("utf-8").splitlines())}
    assert {r["type"] for r in records} == set(shapes)
    for record in records:
        shape = shapes[record["type"]]
        assert (list(record), set(record["data"])) == (list(shape), set(shape["data"]))


def test_a_short_ack_or_a_lost_answer_resends_from_ack_plus_1(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "FLUSH_INTERVAL_S", 0.3)  # a few records per batch
    tripped: list[list[int]] = []

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        records = body["records"] if path == JOURNAL else []
        if len(tripped) == 2 or not records or records[0]["type"] == "started":
            return None
        if not tripped and len(records) < 2:
            return None

        tripped.append([r["seq"] for r in records])
        if len(tripped) == 1:  # the server applies only the first record
            return 200, server.ingest(records[:1])
        server.ingest(records)  # applied, but the answer is lost
        return error(503, "internal_error")

    server.hook = hook
    server.manifest = make_package([agent_stage("plan", "print=a", "print=b", "sleep=0.5")])
    assert run_worker(server, tmp_path / "ws") == 0

    short, lost = tripped
    posts = [r.body["records"] for r in server.requests if r.path == JOURNAL]
    batches = [[record["seq"] for record in post] for post in posts if post]
    at = batches.index(short)
    assert lost[0] == short[0] + 1
    assert batches[at + 1 : at + 3] == [lost, lost], "resent from the ack, then retried as is"
    assert all(b[0] == lost[-1] + 1 for b in batches[at + 3 : at + 4])
    seqs = [r["seq"] for r in server.records()]
    assert seqs == list(range(1, len(seqs) + 1))
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert len({r.instance for r in server.requests if r.path == JOURNAL}) == 1


def test_repo_run_commits_code_and_never_context(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_shell(monkeypatch)
    notes = f"{SKILL_DIR}/notes.md"
    server.manifest = make_package(
        [agent_stage("implement", "edit", f"write={notes}:x", "env"), shell_stage("lint", LINT)],
        repo_url=origin.url,
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
    assert set(files) == {f"{SKILL_DIR}/TASKS.md", f"{SKILL_DIR}/learnings.log"}  # changed only
    assert synced(server, "lint") == {}  # unchanged since

    # The new file in the skill folder is neither synced nor committed, and named each stage.
    warnings = server.warnings()
    assert len(warnings) == 2
    assert all(w.startswith("not kept") and notes in w for w in warnings)

    # Events: stage lifecycle, clone output as run-level system logs.
    started = [(e["stage_id"], e["data"]) for e in stage_events(server, "stage_started")]
    finished = [(e["stage_id"], e["data"]) for e in stage_events(server, "stage_finished")]
    assert started == [("implement", {"round": 1}), ("lint", {"round": 1})]
    assert finished == [
        ("implement", {"status": "succeeded", "exit_code": 0, "reason": None, "round": 1}),
        ("lint", {"status": "succeeded", "exit_code": 0, "reason": None, "round": 1}),
    ]
    assert any(
        e["type"] == "log" and e["stage_id"] is None and e["data"]["stream"] == "system"
        for e in server.events()
    )

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


@pytest.mark.parametrize(("status", "code"), [(410, "run_gone"), (409, "already_claimed")])
def test_run_gone_after_binding_means_no_commit_and_no_push(
    server: FakeServer, origin: Origin, tmp_path: Path, status: int, code: str
) -> None:
    server.hook = trip_on("stage_started", error(status, code))
    server.manifest = make_package(
        [agent_stage("implement", "edit", "sleep=30")], repo_url=origin.url
    )
    started = time.monotonic()
    assert run_worker(server, tmp_path / "ws") == 1
    assert time.monotonic() - started < 15, "the stage tree was killed"
    assert server.complete() is None
    assert server.records("sync") == []
    assert not origin.has_branch(BRANCH)
    assert git(tmp_path / "ws", "log", "--format=%s", "-1").strip() == "init", "nothing committed"


@pytest.mark.parametrize("mode", ["live", "file"])
def test_nothing_is_published_after_the_jobs_not_after(
    server: FakeServer, origin: Origin, tmp_path: Path, mode: str
) -> None:
    package = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    package["job"]["not_after"] = "2000-01-01T00:00:00Z"
    package["report"]["mode"] = mode
    if mode == "live":
        server.manifest = package
        assert run_worker(server, tmp_path / "ws") == 1
        assert server.complete() is None
    else:
        manifest = tmp_path / "job.json"
        manifest.write_text(json.dumps(package), encoding="utf-8", newline="\n")
        outbox, workdir = tmp_path / "outbox.jsonl", tmp_path / "ws"
        args = ["--manifest", str(manifest), "--outbox", str(outbox), "--workdir", str(workdir)]
        assert worker.main(args) == 1
    assert not origin.has_branch(BRANCH)


def test_over_budget_kills_the_stage_still_publishes_then_fails(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = time.monotonic

    def hook(path: str, body: Any) -> None:  # the budget runs out once the stage is running
        if path == JOURNAL and any(
            r["type"] == "log" and r["data"]["text"] == "started" for r in body["records"]
        ):
            monkeypatch.setattr(time, "monotonic", lambda: real() + 3600)

    server.hook = hook
    server.manifest = make_package(
        [agent_stage("implement", "edit", "sleep=60"), agent_stage("never", "print=no")],
        repo_url=origin.url,
    )
    started = real()
    assert run_worker(server, tmp_path / "ws") == 0
    assert real() - started < 45, "the stage tree was killed"
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "implement",
        "error": worker.OVER_BUDGET,
    }
    assert [e["stage_id"] for e in stage_events(server, "stage_started")] == ["implement"]
    assert synced(server, "implement")
    assert "src/app.py" in origin.tree(BRANCH)


def test_the_pre_push_hook_refuses_an_agent_push_but_not_the_workers(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    stage = agent_stage("implement", "edit", "forcecommit=src/app.py", "push")
    server.manifest = make_package([stage], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert "push exit 1" in server.logs("stdout")
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    [pushed] = server.records("pushed")
    assert pushed["data"]["head"] == git(origin.bare, "rev-parse", BRANCH).strip()


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
        if path == JOURNAL and any(
            r["type"] == "log" and r["data"]["text"] == "started" for r in body["records"]
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
    assert synced(server, "implement")[f"{SKILL_DIR}/TASKS.md"][0].startswith("# Tasks\n\n- [x]")
    assert "src/app.py" in origin.tree(BRANCH)
    assert not workdir.exists(), "the workdir is deleted on cancel"


def test_rejected_push_fails_the_run(server: FakeServer, origin: Origin, tmp_path: Path) -> None:
    hook = origin.bare / "hooks" / "pre-receive"
    hook.write_bytes(b"#!/bin/sh\necho 'no pushes today' >&2\nexit 1\n")
    hook.chmod(0o755)
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    done = server.complete()
    assert done is not None
    assert (done["status"], done["failed_stage"]) == ("failed", "implement")
    assert "did the agent push?" in done["error"]


def test_no_repo_mode(server: FakeServer, tmp_path: Path) -> None:
    server.manifest = make_package([agent_stage("plan", "edit")])
    workdir = tmp_path / "ws"
    assert run_worker(server, workdir) == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    files = synced(server, "plan")
    assert files[f"{SKILL_DIR}/TASKS.md"][0].startswith("# Tasks\n\n- [x]")
    assert not workdir.exists()


def test_a_rejected_result_is_a_failure_to_complete(server: FakeServer, tmp_path: Path) -> None:
    """The run hasn't ended, so the worker must not act as if it had: exit 1, workdir kept."""
    server.reject = {"result"}
    server.manifest = make_package([agent_stage("plan", "edit")])
    workdir = tmp_path / "ws"

    assert run_worker(server, workdir) == 1

    assert server.complete() is None
    assert workdir.exists()
    last = [r for r in server.requests if r.path == JOURNAL][-1]
    assert last.body["records"][-1]["type"] == "result"  # nothing reported after it


def test_deleted_files_go_as_null_and_rewritten_notes_warn_once(
    server: FakeServer, tmp_path: Path
) -> None:
    log = f"{SKILL_DIR}/learnings.log"
    server.manifest = make_package(
        [
            agent_stage("plan", f"rm={SKILL_DIR}/TASKS.md", f"write={log}:new"),
            agent_stage("build", f"write={log}:newer"),
        ]
    )
    server.manifest["notes"][0]["content"] = "shipped\n"
    assert run_worker(server, tmp_path / "ws") == 0
    assert synced(server, "plan") == {
        f"{SKILL_DIR}/TASKS.md": (None, "replace"),
        log: ("new", "replace"),
    }
    assert synced(server, "build") == {log: ("newer", "replace")}
    assert len([w for w in server.warnings() if log in w and "rewritten" in w]) == 1


@pytest.mark.parametrize(
    ("path", "status", "exit_code"),
    [
        (JOB, 409, 0),
        (JOURNAL, 409, 0),
        (JOB, 401, 1),
        (JOURNAL, 401, 1),
        (JOB, 410, 1),
        (JOURNAL, 410, 1),
    ],
)
def test_binding_comes_first_and_a_refusal_leaves_the_workdir_untouched(
    server: FakeServer, tmp_path: Path, path: str, status: int, exit_code: int
) -> None:
    server.hook = lambda p, body: error(status, "already_claimed") if p == path else None
    server.manifest = make_package([agent_stage("plan", "print=x")])
    assert run_worker(server, tmp_path / "ws") == exit_code
    calls = [r.path for r in server.requests]
    assert calls == [JOB, JOURNAL][: calls.index(path) + 1]
    assert not (tmp_path / "ws").exists()


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


AGENT = ".claude/agents/x.md"


def ship_agent(origin: Origin) -> dict[str, Any]:
    package = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    package["context"].append(
        {
            "path": AGENT,
            "kind": "agent",
            "name": "x",
            "mode": "replace",
            "block": None,
            "access": "write",
            "content": "# shipped\n",
        }
    )
    return package


@pytest.mark.parametrize("theirs", [AGENT, ".claude/agents/X.md", ".Claude/Agents/x.md"])
def test_a_shipped_agent_the_repo_already_has_fails_setup(
    server: FakeServer, origin: Origin, tmp_path: Path, theirs: str
) -> None:
    """Matched ignoring case, as a case-sensitive runner would otherwise miss it."""
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", origin.url, str(seed))
    (seed / theirs).parent.mkdir(parents=True)
    (seed / theirs).write_bytes(b"# theirs\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "agent")
    git(seed, "push", "-q", "origin", "main")
    server.manifest = ship_agent(origin)

    assert run_worker(server, tmp_path / "ws") == 0

    done = server.complete()
    assert done is not None
    assert (done["status"], done["failed_stage"]) == ("failed", None)
    assert theirs in done["error"]
    assert git(tmp_path / "ws", "status", "--porcelain", "--untracked-files=all") == ""
    assert not origin.has_branch(BRANCH)


def test_a_dangling_symlink_counts_as_an_existing_entry(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    try:
        (tmp_path / ".claude" / "agents" / "X.md").symlink_to(tmp_path / "missing.md")
    except OSError:
        pytest.skip("symlinks need a privilege here")
    assert worker.existing_entry(tmp_path, AGENT) == ".claude/agents/X.md"
    assert worker.existing_entry(tmp_path, ".claude/agents/y.md") is None


def test_a_shipped_agent_is_written_and_never_committed(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_shell(monkeypatch)
    seen = tmp_path / "seen.md"
    server.manifest = ship_agent(origin)
    copy = f"import shutil; shutil.copy({AGENT!r}, {str(seen)!r})"
    server.manifest["stages"].append(shell_stage("look", copy))

    assert run_worker(server, tmp_path / "ws") == 0

    done = server.complete()
    assert done is not None
    assert done["status"] == "completed"
    assert seen.read_bytes() == b"# shipped\n"
    assert origin.has_branch(BRANCH)
    assert AGENT not in origin.tree(BRANCH)


@pytest.mark.parametrize("protocol", [1, 3, None])
def test_another_protocol_exits_1_before_binding(
    server: FakeServer, origin: Origin, tmp_path: Path, protocol: int | None
) -> None:
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    server.manifest["protocol"] = protocol
    assert run_worker(server, tmp_path / "ws") == 1
    assert [r.path for r in server.requests] == [JOB]
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
    monkeypatch.setenv("AGENTOS_TOKEN", JOB_TOKEN)
    server.manifest = make_package([agent_stage("plan", "env")])
    assert worker.main(["--server", server.url, "--workdir", str(tmp_path / "ws")]) == 0
    assert server.requests[0].auth == f"Bearer {JOB_TOKEN}"
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
