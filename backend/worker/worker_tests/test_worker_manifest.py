"""Loading the job package (protocol 2): the shared fixture, the protocol and the loop shape."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import worker

FIXTURE = Path(__file__).resolve().parents[2] / "server" / "tests" / "fixtures" / "job_v2.json"


def fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def test_loads_the_shared_contract_fixture() -> None:
    p = worker.load_package(fixture())
    assert (p.run_id, p.key) == (42, "AOS-42")
    assert p.workspace["branch"] == "agent/AOS-42"
    assert p.workspace["protect"] == [".claude/skills/context-aos-42"]
    assert p.workspace["blocks"] == ["AOS-42"]
    assert [(f.kind, f.mode, f.block) for f in p.files if f.mode == "block"] == [
        ("instructions", "block", "AOS-42"),
        ("instructions", "block", "AOS-42"),
    ]
    assert [f.name for f in p.notes] == ["learnings", "swarm"]
    assert all(f.access == "write" for f in p.files)
    assert p.env["AGENTOS_SYNC_BACK"] == p.sync_back["dir"] == ".agentos/sync_back"
    assert [s.kind for s in p.stages] == ["agent", "shell", "agent"]
    implement, lint, review = p.stages
    assert implement.argv is not None
    assert implement.argv[0] == "claude"
    assert lint.command == "ruff format . && ruff check --fix ."
    assert p.limits == {"max_file_bytes": 1048576, "max_line_bytes": 8192}

    # The loop block: implement (maker) -> lint (shell check) -> review (agent check, file).
    assert p.loops == (worker.Loop("implement", "review", 2),)
    assert (p.start_stage, p.warnings) == ("implement", ())
    assert [(s.check, s.verdict_from, s.verdict_repairs) for s in p.stages] == [
        (False, None, 0),
        (True, None, 0),
        (True, "file", 2),
    ]
    [later] = implement.rounds
    assert (later.prompt_arg, later.tail) == (None, "")
    assert later.stdin is not None
    assert later.stdin.startswith("Round 2 of 2.")
    assert (implement.repairs, lint.rounds, lint.repairs) == ((), (), ())
    assert (len(review.rounds), len(review.repairs)) == (1, 2)
    for call in (*review.rounds, *review.repairs):
        assert call.prompt_arg is not None
        argv, stdin = call.build("missing key: notes")
        assert stdin is None
        expected = list(call.argv)
        expected[call.prompt_arg] += "missing key: notes" + call.tail
        assert argv == expected


def test_invocation_build_appends_text_then_tail() -> None:
    on_stdin = worker.Invocation(["claude", "-p"], "Base. ", None, " Tail.")
    assert on_stdin.build("Notes.") == (["claude", "-p"], "Base. Notes. Tail.")
    as_arg = worker.Invocation(["copilot", "-p", "Base. ", "--x"], None, 2, " Tail.")
    assert as_arg.build("Notes.") == (["copilot", "-p", "Base. Notes. Tail.", "--x"], None)


@pytest.mark.parametrize("protocol", [1, 3, "2", True, None])
def test_refuses_another_protocol(protocol: object) -> None:
    data = fixture()
    data["protocol"] = protocol
    with pytest.raises(worker.PackageError, match="protocol"):
        worker.load_package(data)


@pytest.mark.parametrize("raw", [None, [], "package", 42])
def test_refuses_non_objects(raw: object) -> None:
    with pytest.raises(worker.PackageError):
        worker.load_package(raw)


@pytest.mark.parametrize(
    "missing", ["job", "journal", "report", "run_id", "workspace", "stages", "context", "notes"]
)
def test_refuses_a_package_missing_a_section(missing: str) -> None:
    data = fixture()
    del data[missing]
    with pytest.raises(worker.PackageError):
        worker.load_package(data)


REPAIR = {"argv": ["claude", "-p"], "stdin": "Fix it: ", "prompt_arg": None, "tail": ""}


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        target: Any = data
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        target[int(keys[-1]) if isinstance(target, list) else keys[-1]] = value

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        _set("stages.0.rounds", []),
        _set("stages.2.rounds", [REPAIR, REPAIR]),
        _set("stages.2.verdict_from", None),
        _set("stages.2.repairs", [REPAIR]),
        _set("stages.0.repairs", [REPAIR]),
        _set("stages.1.rounds", [REPAIR]),
        _set("stages.1.id", "implement"),
        _set("loops.0.max_rounds", 0),
        _set("loops.0.max_rounds", True),
        _set("loops.0.start", "nope"),
        _set("loops.0.end", "nope"),
        _set("loops.0.extra", 1),
        _set("loops", []),
        _set("start_stage", "nope"),
        lambda d: d["loops"][0].update(start="review", end="lint"),
    ],
)
def test_refuses_loops_the_stage_loop_cannot_run(mutate: Callable[[dict[str, Any]], None]) -> None:
    data = copy.deepcopy(fixture())
    mutate(data)
    with pytest.raises(worker.PackageError):
        worker.load_package(data)


def plain() -> dict[str, Any]:
    """The fixture without its loop block."""
    data = fixture()
    data["loops"] = []
    for stage in data["stages"]:
        stage.update(check=False, rounds=[], repairs=[], verdict_from=None, verdict_repairs=0)
    return data


@pytest.mark.parametrize(
    "loops",
    [
        [("implement", "lint"), ("lint", "review")],
        [("review", "review"), ("implement", "lint")],
    ],
)
def test_refuses_overlapping_or_unordered_loops(loops: list[tuple[str, str]]) -> None:
    data = plain()
    data["loops"] = [{"start": a, "end": b, "max_rounds": 1} for a, b in loops]
    with pytest.raises(worker.PackageError):
        worker.load_package(data)


def test_one_round_blocks_and_back_to_back_blocks_load() -> None:
    data = plain()
    data["loops"] = [
        {"start": "implement", "end": "implement", "max_rounds": 1},
        {"start": "lint", "end": "review", "max_rounds": 1},
    ]
    data["stages"][2].update(check=True, verdict_from="structured_output")
    p = worker.load_package(data)
    assert [(lp.start, lp.end) for lp in p.loops] == [
        ("implement", "implement"),
        ("lint", "review"),
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["stages"][1].update(check=True),
        lambda d: d["stages"][2].update(repairs=[REPAIR]),
        lambda d: d.update(start_stage="lint"),
    ],
)
def test_refuses_loop_extras_without_a_block(mutate: Callable[[dict[str, Any]], object]) -> None:
    data = plain()
    mutate(data)
    with pytest.raises(worker.PackageError):
        worker.load_package(data)


def test_start_stage_must_be_in_a_block_that_starts_at_the_first_stage() -> None:
    data = plain()
    data["loops"] = [{"start": "lint", "end": "review", "max_rounds": 1}]
    data["stages"][2].update(check=True, verdict_from="file")
    data["start_stage"] = "review"
    with pytest.raises(worker.PackageError, match="start_stage"):
        worker.load_package(data)
    data["start_stage"] = "implement"
    assert worker.load_package(data).start_stage == "implement"


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("main", True),
        ("agent/AOS-42", True),
        ("release/1.2", True),
        ("-main", False),
        ("a..b", False),
        ("a//b", False),
        ("a/", False),
        ("a.", False),
        ("a.lock", False),
        ("a b", False),
    ],
)
def test_branch_names(name: str, ok: bool) -> None:
    assert (worker.branch_error(name) is None) is ok
