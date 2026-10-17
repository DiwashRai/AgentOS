"""Loop blocks: rounds, checks and the verdict channel, through worker.main() with a fake agent.

A check's pass/fail is decided by code: a shell check's exit code, or an agent check's JSON verdict
(from `.agentos/verdict.json` or the CLI's result line), validated, repaired by resuming at most
`verdict_repairs` times, and never a pass when it is invalid. Plus the pure verdict helpers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

import worker
from worker_tests.worker_support import (
    JOURNAL,
    KEY,
    FakeServer,
    Origin,
    agent_stage,
    git,
    invocation,
    make_package,
    python_shell,
    run_worker,
    shell_stage,
    verdict_file,
)

BRANCH = f"agent/{KEY}"
COMPLETED = {"status": "completed", "failed_stage": None, "error": None}
RESULT = {"type": "result", "subtype": "success", "is_error": False}


def block(start: str, end: str, max_rounds: int) -> dict[str, Any]:
    return {"start": start, "end": end, "max_rounds": max_rounds}


def manifest(
    stages: list[dict[str, Any]], loops: list[dict[str, Any]], **extra: Any
) -> dict[str, Any]:
    data = make_package(stages, repo_url=extra.pop("repo_url", None))
    data.update(loops=loops, **extra)
    return data


def checked(
    stage: dict[str, Any],
    *,
    rounds: list[dict[str, Any]] | None = None,
    repairs: list[dict[str, Any]] | None = None,
    verdict_from: str = "file",
    verdict_repairs: int = 2,
) -> dict[str, Any]:
    """`stage` as an agent check."""
    stage.update(
        check=True,
        rounds=rounds or [],
        repairs=repairs or [],
        verdict_from=verdict_from,
        verdict_repairs=verdict_repairs,
    )
    return stage


def maker(stage_id: str, *actions: str, rounds: list[dict[str, Any]]) -> dict[str, Any]:
    stage = agent_stage(stage_id, *actions)
    stage["rounds"] = rounds
    return stage


def events(server: FakeServer, kind: str) -> list[tuple[str | None, dict[str, Any]]]:
    return [(e["stage_id"], e["data"]) for e in server.events() if e["type"] == kind]


def rounds_of(server: FakeServer, kind: str) -> list[tuple[str | None, int]]:
    return [(stage_id, data["round"]) for stage_id, data in events(server, kind)]


def finished(server: FakeServer, stage_id: str) -> list[dict[str, Any]]:
    return [data for sid, data in events(server, "stage_finished") if sid == stage_id]


def verdict(round_: int, value: str, notes: str, source: str = "file", repairs: int = 0) -> Any:
    return {"round": round_, "verdict": value, "notes": notes, "source": source, "repairs": repairs}


# ---------------------------------------------------------------- rounds


def test_implement_and_review_until_the_review_passes(server: FakeServer, tmp_path: Path) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    implement = maker(
        "implement",
        "write=src/app.py:v1",
        rounds=[
            invocation(
                f"savein={saved / 'implement-2.txt'}",
                "write=src/app.py:v2",
                stdin="Round 2 of 2. Fix these:\n\n",
                tail="\n\nThen stop.",
            )
        ],
    )
    review = checked(
        agent_stage("review", verdict_file("fail", "Add a test.\n"), stdin="Review it."),
        rounds=[
            invocation(
                f"savein={saved / 'review-2.txt'}",
                verdict_file("pass", ""),
                stdin="Review it.\n## Previous round\n",
                tail="\n## Verdict\nWrite it.",
            )
        ],
    )
    server.manifest = manifest([implement, review], [block("implement", "review", 2)])
    assert run_worker(server, tmp_path / "ws") == 0

    assert server.complete() == COMPLETED
    order = [("implement", 1), ("review", 1), ("implement", 2), ("review", 2)]
    assert rounds_of(server, "stage_started") == order
    assert rounds_of(server, "stage_finished") == order
    assert events(server, "verdict") == [
        ("review", verdict(1, "fail", "Add a test.\n")),
        ("review", verdict(2, "pass", "")),
    ]
    assert finished(server, "review") == [
        {
            "status": "succeeded",
            "exit_code": 0,
            "reason": "verdict: fail (round 1 of 2)",
            "round": 1,
        },
        {"status": "succeeded", "exit_code": 0, "reason": None, "round": 2},
    ]
    # Each verdict comes right before its stage_finished.
    kinds = [(e["type"], e["stage_id"]) for e in server.events() if e["type"] != "log"]
    assert kinds[kinds.index(("verdict", "review")) + 1] == ("stage_finished", "review")

    feedback = "### Review\nAdd a test."
    assert (saved / "implement-2.txt").read_bytes().decode() == (
        "Round 2 of 2. Fix these:\n\n" + feedback + "\n\nThen stop."
    )
    assert (saved / "review-2.txt").read_bytes().decode() == (
        "Review it.\n## Previous round\n" + feedback + "\n## Verdict\nWrite it."
    )
    assert server.warnings() == []


def test_checks_still_failing_after_the_last_round_fail_the_run(
    server: FakeServer, tmp_path: Path
) -> None:
    implement = maker("implement", "print=work", rounds=[invocation("print=more work")])
    review = checked(
        agent_stage("review", verdict_file("fail", "Still wrong.")),
        rounds=[invocation(verdict_file("fail", "Still wrong."))],
    )
    server.manifest = manifest([implement, review], [block("implement", "review", 2)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "review",
        "error": "Checks still failing after 2 rounds",
    }
    assert finished(server, "review") == [
        {
            "status": "succeeded",
            "exit_code": 0,
            "reason": "verdict: fail (round 1 of 2)",
            "round": 1,
        },
        {
            "status": "failed",
            "exit_code": 0,
            "reason": "verdict: fail in the last round (2 of 2)",
            "round": 2,
        },
    ]


def test_every_check_runs_each_round_and_only_failing_notes_go_back(
    server: FakeServer, tmp_path: Path
) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    implement = maker(
        "implement", "print=work", rounds=[invocation(f"savein={saved / 'implement-2.txt'}")]
    )
    correctness = checked(
        agent_stage("correctness", verdict_file("fail", "Off by one.")),
        rounds=[invocation(verdict_file("fail", "Still off by one."))],
    )
    simplicity = checked(
        agent_stage("simplicity", verdict_file("pass", "Looks simple.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    server.manifest = manifest(
        [implement, correctness, simplicity], [block("implement", "simplicity", 2)]
    )
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "correctness",
        "error": "Checks still failing after 2 rounds",
    }
    assert rounds_of(server, "stage_started") == [
        ("implement", 1),
        ("correctness", 1),
        ("simplicity", 1),
        ("implement", 2),
        ("correctness", 2),
        ("simplicity", 2),
    ]
    assert (saved / "implement-2.txt").read_bytes().decode() == "### Correctness\nOff by one."
    assert [d["status"] for d in finished(server, "correctness")] == ["succeeded", "failed"]
    assert [d["status"] for d in finished(server, "simplicity")] == ["succeeded", "succeeded"]


def test_a_shell_check_fails_with_its_output_and_notes_are_combined(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_shell(monkeypatch)
    saved = tmp_path / "saved"
    saved.mkdir()
    implement = maker(
        "implement",
        "print=work",
        rounds=[invocation(f"savein={saved / 'implement-2.txt'}", "write=fixed.txt:yes")],
    )
    lint = shell_stage(
        "lint",
        "import os, sys; print('checking'); sys.exit(0 if os.path.exists('fixed.txt') else 1)",
    )
    lint["check"] = True
    review = checked(
        agent_stage("review", verdict_file("fail", "Name it better.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    server.manifest = manifest([implement, lint, review], [block("implement", "review", 2)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == COMPLETED
    lint_notes = "Exited with code 1. The last lines of its output:\nchecking"
    assert events(server, "verdict") == [
        ("lint", verdict(1, "fail", lint_notes, source="exit_code")),
        ("review", verdict(1, "fail", "Name it better.")),
        ("lint", verdict(2, "pass", "", source="exit_code")),
        ("review", verdict(2, "pass", "")),
    ]
    assert finished(server, "lint")[0] == {
        "status": "succeeded",
        "exit_code": 1,
        "reason": "verdict: fail (round 1 of 2)",
        "round": 1,
    }
    assert (saved / "implement-2.txt").read_bytes().decode() == (
        f"### Lint\n{lint_notes}\n\n### Review\nName it better."
    )


def test_a_run_started_inside_a_block_goes_back_to_its_start(
    server: FakeServer, tmp_path: Path
) -> None:
    implement = maker("implement", "print=never in round 1", rounds=[invocation("print=fixing")])
    review = checked(
        agent_stage("review", verdict_file("fail", "Fix it.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    server.manifest = manifest(
        [implement, review], [block("implement", "review", 2)], start_stage="review"
    )
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == COMPLETED
    assert rounds_of(server, "stage_started") == [("review", 1), ("implement", 2), ("review", 2)]
    assert "never in round 1" not in server.logs()
    assert "fixing" in server.logs()


def test_stages_after_a_block_run_once(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_shell(monkeypatch)
    implement = maker("implement", "print=work", rounds=[invocation("print=more")])
    review = checked(
        agent_stage("review", verdict_file("fail", "Fix it.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    stages = [agent_stage("plan", "print=plan"), implement, review, shell_stage("ship", "pass")]
    server.manifest = manifest(stages, [block("implement", "review", 2)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == COMPLETED
    assert rounds_of(server, "stage_started") == [
        ("plan", 1),
        ("implement", 1),
        ("review", 1),
        ("implement", 2),
        ("review", 2),
        ("ship", 1),
    ]


def test_an_argument_mode_prompt_gets_the_feedback(server: FakeServer, tmp_path: Path) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    later = invocation(
        f"savein={saved / 'stdin.txt'}",
        f"saveargs={saved / 'args.json'}",
        "Round 2: ",
        prompt_in_argv=True,
        tail=" Go.",
    )
    implement = maker("implement", "print=work", rounds=[later])
    review = checked(
        agent_stage("review", verdict_file("fail", "Fix it.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    server.manifest = manifest([implement, review], [block("implement", "review", 2)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == COMPLETED
    assert json.loads((saved / "args.json").read_bytes()) == ["Round 2: ### Review\nFix it. Go."]
    assert (saved / "stdin.txt").read_bytes() == b"", "no prompt on stdin"


def test_cancel_in_a_later_round(server: FakeServer, tmp_path: Path) -> None:
    def hook(path: str, body: Any) -> None:
        if path == JOURNAL and any(
            r["type"] == "log" and r["data"]["text"] == "started" for r in body["records"]
        ):
            server.cancel = True

    server.hook = hook
    implement = maker("implement", "print=work", rounds=[invocation("sleep=30")])
    review = checked(
        agent_stage("review", verdict_file("fail", "Fix it.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    server.manifest = manifest([implement, review], [block("implement", "review", 2)])
    started = time.monotonic()
    assert run_worker(server, tmp_path / "ws") == 0
    assert time.monotonic() - started < 15
    assert server.complete() == {"status": "cancelled", "failed_stage": "implement", "error": None}
    assert events(server, "stage_finished")[-1] == (
        "implement",
        {"status": "cancelled", "exit_code": None, "reason": "cancelled", "round": 2},
    )


def test_manifest_warnings_become_warning_events(server: FakeServer, tmp_path: Path) -> None:
    server.manifest = make_package([agent_stage("plan", "print=x")])
    server.manifest["warnings"] = ["The loop over implement..review is ignored: it has no check."]
    assert run_worker(server, tmp_path / "ws") == 0
    kinds = [(e["type"], e["stage_id"]) for e in server.events() if e["type"] != "log"]
    assert kinds[0] == ("warning", None)
    assert server.warnings() == ["The loop over implement..review is ignored: it has no check."]


# ---------------------------------------------------------------- verdicts and repairs


def test_an_invalid_verdict_is_repaired_by_resuming_the_check(
    server: FakeServer, tmp_path: Path
) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    review = checked(
        agent_stage("review", verdict_file("approved", "")),
        repairs=[
            invocation(
                f"savein={saved / 'repair.txt'}",
                verdict_file("pass", ""),
                stdin="Your verdict was invalid: ",
                tail=".\nOnly the verdict.",
            )
        ],
    )
    implement = agent_stage("implement", "print=work")
    server.manifest = manifest([implement, review], [block("implement", "review", 1)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == COMPLETED
    error = 'verdict: expected "pass" or "fail", got "approved"'
    assert events(server, "verdict_repair") == [
        ("review", {"round": 1, "attempt": 1, "error": error})
    ]
    assert events(server, "verdict") == [("review", verdict(1, "pass", "", repairs=1))]
    assert rounds_of(server, "stage_started") == [("implement", 1), ("review", 1)]
    assert (saved / "repair.txt").read_bytes().decode() == (
        f"Your verdict was invalid: {error}.\nOnly the verdict."
    )


@pytest.mark.parametrize(
    ("repairs", "verdict_repairs", "after"),
    [
        (1, 2, " after 2 repairs"),
        (1, 1, " after 1 repair"),
        (0, 2, ""),
        (1, 0, ""),
    ],
)
def test_no_valid_verdict_after_the_repairs_fails_the_run(
    server: FakeServer, tmp_path: Path, repairs: int, verdict_repairs: int, after: str
) -> None:
    review = checked(
        agent_stage("review", "print=reviewed"),
        repairs=[invocation("print=repairing")] * repairs,
        verdict_repairs=verdict_repairs,
    )
    implement = agent_stage("implement", "print=work")
    server.manifest = manifest([implement, review], [block("implement", "review", 1)])
    assert run_worker(server, tmp_path / "ws") == 0
    error = f"Review gave no valid verdict{after}: .agentos/verdict.json was not written"
    assert server.complete() == {"status": "failed", "failed_stage": "review", "error": error}
    attempts = verdict_repairs if repairs else 0
    assert [d["attempt"] for _, d in events(server, "verdict_repair")] == list(
        range(1, attempts + 1)
    )
    assert server.logs("stdout").count("repairing") == attempts
    assert events(server, "verdict") == []
    assert finished(server, "review") == [
        {"status": "failed", "exit_code": 0, "reason": error, "round": 1}
    ]


def test_a_stale_verdict_file_is_deleted_before_the_check(
    server: FakeServer, tmp_path: Path
) -> None:
    implement = agent_stage("implement", verdict_file("pass", ""))  # a maker writes a "verdict"
    review = checked(agent_stage("review", "print=reviewed"))
    server.manifest = manifest([implement, review], [block("implement", "review", 1)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "review",
        "error": "Review gave no valid verdict: .agentos/verdict.json was not written",
    }
    assert events(server, "verdict") == []


def test_a_check_that_cannot_start_is_not_repaired(server: FakeServer, tmp_path: Path) -> None:
    review = checked(agent_stage("review"), repairs=[invocation(verdict_file("pass", ""))])
    review["argv"] = ["agentos-no-such-program-xyz", "-p"]
    implement = agent_stage("implement", "print=work")
    server.manifest = manifest([implement, review], [block("implement", "review", 1)])
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == {
        "status": "failed",
        "failed_stage": "review",
        "error": "program not found: agentos-no-such-program-xyz",
    }
    assert events(server, "verdict_repair") == []


def result_line(**fields: Any) -> str:
    return "print=" + json.dumps({**RESULT, **fields})


@pytest.mark.parametrize(
    ("actions", "outcome"),
    [
        ([result_line(structured_output={"verdict": "pass", "notes": ""})], ("pass", "")),
        (
            [
                result_line(structured_output={"verdict": "pass", "notes": ""}),
                result_line(structured_output={"verdict": "fail", "notes": "Fix it."}),
            ],
            ("fail", "Fix it."),
        ),
        (
            [result_line(subtype="error_max_turns", is_error=True)],
            "the CLI's result has no structured_output (subtype: error_max_turns)",
        ),
        (
            [result_line(structured_output=None)],
            "the CLI's result has no structured_output (subtype: success)",
        ),
        (['print={"type": "result"}'], "the CLI's result has no structured_output"),
        (["print=all done"], "the CLI printed no result line"),
        (
            [result_line(structured_output={"verdict": "pass", "notes": ""}), "exit=3"],
            "exited with code 3",
        ),
        ([result_line(structured_output={"verdict": "pass"})], "missing key: notes"),
    ],
)
def test_the_structured_output_channel(
    server: FakeServer, tmp_path: Path, actions: list[str], outcome: Any
) -> None:
    # The check also writes a passing verdict file: this channel never reads it.
    review = checked(
        agent_stage("review", verdict_file("pass", ""), *actions),
        verdict_from="structured_output",
    )
    server.manifest = manifest([review], [block("review", "review", 1)])
    assert run_worker(server, tmp_path / "ws") == 0
    done = server.complete()
    if isinstance(outcome, str):
        assert done == {
            "status": "failed",
            "failed_stage": "review",
            "error": f"Review gave no valid verdict: {outcome}",
        }
        assert events(server, "verdict") == []
        return
    value, notes = outcome
    assert events(server, "verdict") == [
        ("review", verdict(1, value, notes, source="structured_output"))
    ]
    assert done == (
        COMPLETED
        if value == "pass"
        else {
            "status": "failed",
            "failed_stage": "review",
            "error": "Checks still failing after 1 round",
        }
    )


# ---------------------------------------------------------------- repo mode


def test_agentos_dir_never_reaches_the_branch(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    implement = maker(
        "implement",
        "edit",
        "write=.agentos/notes.md:mine",
        rounds=[invocation("write=src/fix.py:fixed")],
    )
    review = checked(
        agent_stage("review", verdict_file("fail", "Fix it.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    server.manifest = manifest(
        [implement, review], [block("implement", "review", 2)], repo_url=origin.url
    )
    assert run_worker(server, tmp_path / "ws") == 0
    assert server.complete() == COMPLETED
    tree = origin.tree(BRANCH)
    assert {"src/app.py", "src/fix.py"} <= set(tree)
    assert not [p for p in tree if p.startswith(".agentos")]
    assert ".agentos" not in origin.log_patch(BRANCH)
    subjects = git(origin.bare, "log", "--format=%s", f"main..{BRANCH}").splitlines()
    assert subjects == [f"{KEY} [implement] wip", f"{KEY} [implement] wip"]


def test_the_pre_push_guard_refuses_commits_of_agentos(
    server: FakeServer, origin: Origin, tmp_path: Path
) -> None:
    server.manifest = make_package(
        [agent_stage("implement", "write=.agentos/verdict.json:{}", "forcecommit=.agentos")],
        repo_url=origin.url,
    )
    assert run_worker(server, tmp_path / "ws") == 0
    done = server.complete()
    assert done is not None
    assert done["error"] == "pre-push guard: commits touch context files: .agentos/verdict.json"
    assert not origin.has_branch(BRANCH)


# ---------------------------------------------------------------- mailbox mode


def test_a_mailbox_run_records_rounds_and_verdicts(tmp_path: Path) -> None:
    implement = maker("implement", "print=work", rounds=[invocation("print=more")])
    review = checked(
        agent_stage("review", verdict_file("fail", "Fix it.")),
        rounds=[invocation(verdict_file("pass", ""))],
    )
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(manifest([implement, review], [block("implement", "review", 2)])),
        encoding="utf-8",
    )
    outbox = tmp_path / "outbox.jsonl"
    argv = ["--manifest", str(path), "--outbox", str(outbox), "--workdir", str(tmp_path / "ws")]
    assert worker.main(argv) == 0
    calls = [json.loads(line) for line in outbox.read_bytes().splitlines()[1:]]
    recorded = [e for c in calls if c["path"] == "/api/worker/events" for e in c["body"]["events"]]
    assert [e["data"]["verdict"] for e in recorded if e["type"] == "verdict"] == ["fail", "pass"]
    assert calls[-1]["body"] == COMPLETED


# ---------------------------------------------------------------- the pure helpers


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ([], "expected a JSON object, got array"),
        ("pass", "expected a JSON object, got string"),
        (None, "expected a JSON object, got null"),
        (3, "expected a JSON object, got number"),
        (True, "expected a JSON object, got boolean"),
        ({"verdict": "pass"}, "missing key: notes"),
        ({"notes": ""}, "missing key: verdict"),
        ({"verdict": "pass", "notes": "", "score": 1}, "unexpected key: score"),
        (
            {"verdict": "approved", "notes": ""},
            'verdict: expected "pass" or "fail", got "approved"',
        ),
        ({"verdict": "PASS", "notes": ""}, 'verdict: expected "pass" or "fail", got "PASS"'),
        ({"verdict": True, "notes": ""}, 'verdict: expected "pass" or "fail", got true'),
        ({"verdict": ["pass"], "notes": ""}, 'verdict: expected "pass" or "fail", got ["pass"]'),
        ({"verdict": "pass", "notes": None}, "notes: expected a string, got null"),
        ({"verdict": "pass", "notes": 1}, "notes: expected a string, got number"),
        ({"verdict": "pass", "notes": False}, "notes: expected a string, got boolean"),
        ({"verdict": "pass", "notes": ["x"]}, "notes: expected a string, got array"),
        ({"verdict": "pass", "notes": "x" * 20_001}, "notes: longer than 20000 characters"),
        ({"verdict": "fail", "notes": ""}, 'notes: must not be empty when the verdict is "fail"'),
        (
            {"verdict": "fail", "notes": " \n\t"},
            'notes: must not be empty when the verdict is "fail"',
        ),
        ({"verdict": "pass", "notes": ""}, None),
        ({"verdict": "pass", "notes": "x" * 20_000}, None),
        ({"verdict": "fail", "notes": "Fix it."}, None),
    ],
)
def test_verdict_error(value: object, error: str | None) -> None:
    match worker.checked_verdict(value):
        case worker.Unfit(why=why):
            assert why == error
        case worker.Verdict():
            assert error is None


def test_file_verdict(tmp_path: Path) -> None:
    folder = tmp_path / ".agentos"
    target = folder / "verdict.json"
    assert worker.file_verdict(tmp_path) == (None, ".agentos/verdict.json was not written")
    folder.mkdir()
    for data, expected in [
        (b'{"verdict": "pass", "notes": ""}', ({"verdict": "pass", "notes": ""}, None)),
        (b'\xef\xbb\xbf{"verdict": "pass", "notes": ""}', ({"verdict": "pass", "notes": ""}, None)),
        (b"\xff", (None, ".agentos/verdict.json is not valid UTF-8")),
        (b" " * (256 * 1024 + 1), (None, ".agentos/verdict.json: is over 262144 bytes")),
    ]:
        target.write_bytes(data)
        assert worker.file_verdict(tmp_path) == expected
    target.write_bytes(b"{nope")
    value, error = worker.file_verdict(tmp_path)
    assert value is None
    assert error is not None
    assert error.startswith(".agentos/verdict.json is not valid JSON: ")
    target.write_bytes(b"[" * 100_000)
    assert worker.file_verdict(tmp_path)[1] is not None, "deep nesting is invalid, not a crash"
    target.unlink()
    target.mkdir()
    assert worker.file_verdict(tmp_path) == (None, ".agentos/verdict.json: is not a regular file")
    target.rmdir()
    folder.rmdir()
    folder.write_bytes(b"")
    assert worker.file_verdict(tmp_path) == (
        None,
        ".agentos/verdict.json: .agentos is not a directory",
    )


def test_shell_verdict() -> None:
    assert worker.shell_verdict(0, ["ok"]) == worker.Verdict("pass", "")
    assert worker.shell_verdict(2, []) == worker.Verdict("fail", "Exited with code 2 (no output).")
    assert worker.shell_verdict(2, ["", " "]).notes == "Exited with code 2 (no output)."
    head = "Exited with code 1. The last lines of its output:\n"
    lines = [f"line {i}" for i in range(250)]
    assert worker.shell_verdict(1, lines).notes == head + "\n".join(lines[50:])
    long = worker.shell_verdict(1, ["a" * 15_000, "b" * 15_000]).notes
    assert len(long) == 20_000
    assert long.startswith(head + "a")
    assert long.endswith("\n" + "b" * 15_000)


def test_combine_feedback() -> None:
    assert worker.combine_feedback([("Lint", "Exited.\n"), ("Review", "\nFix it.")]) == (
        "### Lint\nExited.\n\n### Review\nFix it."
    )
    big = worker.combine_feedback([("A", "x" * 30_000), ("B", "y" * 30_000)])
    assert len(big) == 40_000
    assert big.startswith("### A\nxxx")
    assert big.endswith("y\n[truncated]")
