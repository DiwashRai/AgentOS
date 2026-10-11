"""End-of-run PR lookup: whole runs through worker.main() with a fake `gh` (PR_LOOKUP)."""

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
    make_manifest,
    run_worker,
)

PR = "https://github.com/o/r/pull/7"
# argv: RECORD BRANCH EXIT STDOUT... -- records the branch, prints each STDOUT line, exits EXIT.
FAKE_GH = (
    "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); "
    "[print(line) for line in sys.argv[4:]]; "
    "sys.stderr.write('gh: no pull requests found\\n'); sys.exit(int(sys.argv[3]))"
)


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
    ("actions", "expected"),
    [
        (("edit",), {"status": "completed", "failed_stage": None, "error": None}),
        (
            ("edit", "exit=3"),
            {"status": "failed", "failed_stage": "implement", "error": "exited with code 3"},
        ),
    ],
)
def test_a_found_pr_is_reported_on_complete(
    server: FakeServer,
    origin: Origin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actions: tuple[str, ...],
    expected: dict[str, object],
) -> None:
    record = tmp_path / "gh-branch.txt"
    fake_gh(monkeypatch, record, PR)
    server.manifest = make_manifest([agent_stage("implement", *actions)], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {**expected, "pr_url": PR}
    assert record.read_text() == f"agent/{KEY}"
    assert f"PR: {PR}" in system_logs(server)
    assert server.warnings() == []


@pytest.mark.parametrize(
    ("output", "exit_code", "why"),
    [
        ((), 1, "exited with code 1: gh: no pull requests found"),
        (("http://github.com/o/r/pull/7",), 0, "not an https:// URL"),
        (("https://user:pw@github.com/o/r/pull/7",), 0, "credentials"),
        ((PR, PR), 0, "got 2 lines"),
        ((), 0, "got 0 lines"),
    ],
)
def test_no_pr_url_leaves_the_run_alone(
    server: FakeServer,
    origin: Origin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: tuple[str, ...],
    exit_code: int,
    why: str,
) -> None:
    fake_gh(monkeypatch, tmp_path / "gh-branch.txt", *output, exit_code=exit_code)
    server.manifest = make_manifest([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    [line] = [t for t in system_logs(server) if t.startswith("no PR URL")]
    assert why in line
    assert server.warnings() == []


def test_missing_gh_is_not_an_error(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTOS_PR_LOOKUP")
    monkeypatch.setattr(worker, "PR_LOOKUP", ("agentos-no-such-gh-xyz", "pr", "view", "{branch}"))
    server.manifest = make_manifest([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert "no PR URL: agentos-no-such-gh-xyz not found" in system_logs(server)


def test_no_repo_mode_does_no_lookup(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "gh-branch.txt"
    fake_gh(monkeypatch, record, PR)
    server.manifest = make_manifest([agent_stage("plan", "edit")])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert not record.exists()


def test_opt_out_does_no_lookup(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "gh-branch.txt"
    fake_gh(monkeypatch, record, PR)
    monkeypatch.setenv("AGENTOS_PR_LOOKUP", "0")
    server.manifest = make_manifest([agent_stage("implement", "edit")], repo_url=origin.url)
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert not record.exists()
