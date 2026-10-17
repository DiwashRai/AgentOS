"""Loading the job package (protocol 2): the shared fixture, the protocol and the loop shape."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, make_dataclass
from datetime import UTC, datetime
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
    assert (p.run_id, p.ticket.key) == (42, "AOS-42")
    assert p.job.not_after == datetime(2026, 10, 15, 16, 30, tzinfo=UTC)
    assert p.workspace.branch == "agent/AOS-42"
    assert p.workspace.protect == (".claude/skills/context-aos-42",)
    assert p.workspace.blocks == ("AOS-42",)
    assert [(f.kind, f.mode, f.block) for f in p.files if f.mode == "block"] == [
        ("instructions", "block", "AOS-42"),
        ("instructions", "block", "AOS-42"),
    ]
    assert [f.name for f in p.notes] == ["learnings", "swarm"]
    assert all(f.access == "write" for f in p.files)
    assert p.env["AGENTOS_SYNC_BACK"] == p.sync_back.dir == ".agentos/sync_back"
    implement, lint, review = p.stages
    assert isinstance(implement, worker.AgentStage)
    assert isinstance(lint, worker.ShellStage)
    assert isinstance(review, worker.AgentStage)
    assert implement.argv[0] == "claude"
    assert lint.command == "ruff format . && ruff check --fix ."
    assert p.limits == worker.Limits(max_file_bytes=1048576, max_line_bytes=8192)

    # The loop block: implement (maker) -> lint (shell check) -> review (agent check, file).
    assert p.loops == (worker.Loop("implement", "review", 2),)
    assert (p.start_stage, p.warnings) == ("implement", ())
    assert (implement.check, lint.check) == (None, True)
    assert review.check is not None
    assert (review.check.verdict_from, review.check.verdict_repairs) == ("file", 2)
    [later] = implement.rounds
    assert (later.prompt_arg, later.tail) == (None, "")
    assert later.stdin is not None
    assert later.stdin.startswith("Round 2 of 2.")
    assert (len(review.rounds), len(review.check.repairs)) == (1, 2)
    for call in (*review.rounds, *review.check.repairs):
        assert call.prompt_arg is not None
        argv, stdin = call.build("missing key: notes")
        assert stdin is None
        expected = list(call.argv)
        expected[call.prompt_arg] += "missing key: notes" + call.tail
        assert argv == expected


def test_invocation_build_appends_text_then_tail() -> None:
    on_stdin = worker.Invocation(("claude", "-p"), "Base. ", None, " Tail.")
    assert on_stdin.build("Notes.") == (["claude", "-p"], "Base. Notes. Tail.")
    as_arg = worker.Invocation(("copilot", "-p", "Base. ", "--x"), None, 2, " Tail.")
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


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("job.budget_s", "7200"),
        ("job.not_after", "2026-10-15T16:30:00"),
        ("job.not_after", "soon"),
        ("journal.first_seq", True),
        ("report.mode", "post"),
        ("ticket.title", None),
        ("ticket.extra", 1),
        ("workspace.mode", "git"),
        ("workspace.protect", ".claude"),
        ("workspace.blocks.0", 1),
        ("context.0.content", None),
        ("notes.0.content", 1),
        ("sync_back.max_bytes", 1.5),
        ("env.AGENTOS_SKILL_DIR", 1),
        ("limits.max_line_bytes", "8192"),
        ("warnings", ["ok", 1]),
        ("stages.0.timeout_s", None),
        ("stages.0.rounds.0.prompt_arg", "0"),
        ("stages.0.argv", None),
        ("stages.0.command", "make"),
        ("stages.1.argv", ["sh", "-c", "make"]),
        ("stages.1.command", None),
        ("stages.2.verdict_from", "stdout"),
    ],
)
def test_refuses_what_does_not_fit_its_type(path: str, value: Any) -> None:
    data = copy.deepcopy(fixture())
    _set(path, value)(data)
    section = path.split(".")[0]
    with pytest.raises(worker.PackageError, match=f"malformed package: {section}"):
        worker.load_package(data)


@pytest.mark.parametrize(
    ("path", "value", "where"),
    [
        ("stages.0.argv", [], "stages[0].argv"),
        ("stages.0.command", "make", "stages[0].command"),
        ("stages.0.repairs", [REPAIR], "stages[0].repairs"),
        ("stages.0.rounds.0.argv", [], "stages[0].rounds[0].argv"),
        ("stages.1.command", None, "stages[1].command"),
        ("stages.2.verdict_from", None, "stages[2].verdict_from"),
        ("stages.2.rounds.0.prompt_arg", 999, "stages[2].rounds[0].prompt_arg"),
        ("stages.2.repairs.1.prompt_arg", -1, "stages[2].repairs[1].prompt_arg"),
    ],
)
def test_refuses_fields_that_break_a_stage_rule(path: str, value: Any, where: str) -> None:
    data = copy.deepcopy(fixture())
    _set(path, value)(data)
    with pytest.raises(worker.PackageError, match=re.escape(f"malformed package: {where}: ")):
        worker.load_package(data)


def test_a_misfit_names_its_full_path() -> None:
    data = fixture()
    data["stages"][0]["rounds"][0]["prompt_arg"] = "0"
    assert worker.parse(worker.Package, data) == worker.Unfit(
        "stages[0].rounds[0].prompt_arg: expected an integer, got string"
    )


# ---------------------------------------------------------------- parse itself


@dataclass(frozen=True)
class Inner:
    n: int


@dataclass(frozen=True)
class Outer:
    name: str | None
    inner: Inner | None = None
    tags: tuple[str, ...] = ()


def test_parse_reads_optional_values_either_way_round() -> None:
    assert worker.parse(Outer, {"name": None}) == Outer(None)
    assert worker.parse(Outer, {"name": "a", "inner": None}) == Outer("a")
    assert worker.parse(Outer, {"name": "a", "inner": {"n": 1}, "tags": ["x"]}) == Outer(
        "a", Inner(1), ("x",)
    )
    assert worker.parse(Outer, {"inner": None}) == worker.Unfit("missing key: name")
    flipped = make_dataclass("Flipped", [("x", None | str)], frozen=True)
    assert worker.parse(flipped, {"x": None}) == flipped(None)


def test_parse_refuses_unknown_keys_unless_told_to_ignore_them() -> None:
    data = {"name": "a", "inner": {"n": 1, "new": True}, "more": 1}
    assert worker.parse(Outer, data) == worker.Unfit("unexpected key: more")
    assert worker.parse(Outer, data, extra="ignore") == Outer("a", Inner(1))


@dataclass(frozen=True)
class Floats:
    x: float


@dataclass(frozen=True)
class Either:
    x: str | int


@dataclass(frozen=True)
class Pair:
    x: tuple[str, int]


@pytest.mark.parametrize(
    ("tp", "form"), [(Floats, "float"), (Either, "str | int"), (Pair, "tuple")]
)
def test_parse_refuses_an_annotation_it_cannot_read(tp: type[object], form: str) -> None:
    with pytest.raises(TypeError, match=re.escape(form)):
        worker.parse(tp, {"x": 1})


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
