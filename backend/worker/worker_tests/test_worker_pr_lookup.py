"""End-of-run PR lookup: the parsing of captured `gh` output as a table, and whole runs through
worker.main() with a fake `gh` (PR_LOOKUP) for the lookup's preconditions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import worker
from worker_tests.worker_support import (
    KEY,
    FakeServer,
    Origin,
    agent_stage,
    make_package,
    run_worker,
)

PR = "https://github.com/o/r/pull/7"
# argv: RECORD BRANCH EXIT STDOUT... -- records the branch, prints each STDOUT line, exits EXIT.
FAKE_GH = (
    "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); "
    "[print(line) for line in sys.argv[4:]]; "
    "sys.stderr.write('gh: no pull requests found\\n'); sys.exit(int(sys.argv[3]))"
)
NO_PR = "gh: no pull requests found"
DONE = worker.ProcResult(exit_code=0)


def fake_gh(
    monkeypatch: pytest.MonkeyPatch, record: Path, *output: str, exit_code: int = 0
) -> None:
    monkeypatch.delenv("AGENTOS_PR_LOOKUP")
    monkeypatch.setattr(
        worker,
        "PR_LOOKUP",
        (sys.executable, "-c", FAKE_GH, str(record), "{branch}", str(exit_code), *output),
    )


def system_logs(server: FakeServer) -> list[str]:
    return [
        e["data"]["text"]
        for e in server.events()
        if e["type"] == "log" and e["stage_id"] is None and e["data"]["stream"] == "system"
    ]


@pytest.mark.parametrize(
    ("result", "out", "err", "why"),
    [
        (worker.ProcResult(exit_code=1), [], [NO_PR], "exited with code 1: gh: no pull requests"),
        (DONE, ["http://github.com/o/r/pull/7"], [], "not an https:// URL"),
        (DONE, ["https://user:pw@github.com/o/r/pull/7"], [], "credentials"),
        (DONE, [PR, PR], [], "got 2 lines"),
        (DONE, [], [], "got 0 lines"),
        (worker.ProcResult(exit_code=None, stopped="timeout"), [PR], [], "timed out"),
        (worker.ProcResult(exit_code=None, stopped="abort"), [PR], [], "server went away"),
        (worker.ProcResult(exit_code=None, error="cannot start gh: denied"), [], [], "denied"),
    ],
    ids=[
        "gh-failed",
        "http",
        "credentials",
        "two-lines",
        "no-lines",
        "timeout",
        "stopped",
        "spawn",
    ],
)
def test_no_pr_url_says_why(
    result: worker.ProcResult, out: list[str], err: list[str], why: str
) -> None:
    url, reason = worker.pr_lookup_result("gh", result, out, err)
    assert url is None
    assert why in reason


def test_a_failed_lookup_quotes_its_last_stderr_line_clipped() -> None:
    err = ["earlier", "x" * 300]
    _, reason = worker.pr_lookup_result("gh", worker.ProcResult(exit_code=1), [], err)
    assert reason.endswith(": " + "x" * 197 + "...")


def test_one_https_line_is_the_pr_url() -> None:
    assert worker.pr_lookup_result("gh", DONE, [PR], [NO_PR]) == (PR, "")


def test_a_found_pr_is_reported_even_when_the_run_fails(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "gh-branch.txt"
    fake_gh(monkeypatch, record, PR)
    server.manifest = make_package(
        [agent_stage("implement", "edit", "exit=3")], repo_url=origin.url
    )
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "implement",
        "error": "exited with code 3",
        "pr_url": PR,
    }
    assert record.read_text() == f"agent/{KEY}"
    assert f"PR: {PR}" in system_logs(server)
    assert server.warnings() == []


def test_missing_gh_is_not_an_error(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTOS_PR_LOOKUP")
    monkeypatch.setattr(worker, "PR_LOOKUP", ("agentos-no-such-gh-xyz", "pr", "view", "{branch}"))
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert "no PR URL: agentos-no-such-gh-xyz not found" in system_logs(server)


def test_no_repo_mode_does_no_lookup(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "gh-branch.txt"
    fake_gh(monkeypatch, record, PR)
    server.manifest = make_package([agent_stage("plan", "edit")])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert not record.exists()


def test_opt_out_does_no_lookup(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "gh-branch.txt"
    fake_gh(monkeypatch, record, PR)
    monkeypatch.setenv("AGENTOS_PR_LOOKUP", "0")
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert not record.exists()
