"""Manifest validation (protocol v1, by hand) and the SPEC §6 path rules mirrored in the worker."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import worker

FIXTURE = Path(__file__).resolve().parents[2] / "server" / "tests" / "fixtures" / "manifest_v1.json"


def fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def test_accepts_the_shared_contract_fixture() -> None:
    m = worker.parse_manifest(fixture())
    assert m.run_id == 42
    assert m.key == "AOS-42"
    assert m.mode == "repo"
    assert m.branch == "agent/AOS-42"
    assert [f.mode for f in m.files].count("block") == 2
    assert [f.path for f in m.files if not f.sync] == [".claude/skills/review-checklist/SKILL.md"]
    assert m.sync_dirs == (".claude/skills/context-aos-42",)
    assert [s.kind for s in m.stages] == ["agent", "shell", "agent"]
    implement, lint, review = m.stages
    assert implement.argv is not None
    assert implement.argv[0] == "claude"
    assert lint.command == "ruff format . && ruff check --fix ."
    assert (m.max_file_bytes, m.max_line_bytes) == (1048576, 8192)

    # The loop block: implement (maker) -> lint (shell check) -> review (agent check, file).
    assert m.loops == (worker.ManifestLoop("implement", "review", 2),)
    assert (m.start_stage, m.warnings) == ("implement", ())
    assert [(s.check, s.verdict_from, s.verdict_repairs) for s in m.stages] == [
        (False, None, 0),
        (True, None, 0),
        (True, "file", 2),
    ]
    [later] = implement.rounds
    assert (later.argv[-1], later.prompt_arg, later.tail) == (
        "0b8a4c1e-2f4d-4b7a-9c3e-1d2f3a4b5c6d",
        None,
        "",
    )
    assert later.stdin is not None
    assert later.stdin.startswith("Round 2 of 2.")
    assert (implement.repairs, lint.rounds, lint.repairs) == ((), (), ())
    assert len(review.rounds) == 1
    assert len(review.repairs) == 2
    for call in (*review.rounds, *review.repairs):
        assert call.stdin is None
        assert call.prompt_arg is not None
        argv, stdin = call.build("missing key: notes")
        assert stdin is None
        expected = list(call.argv)
        expected[call.prompt_arg] += "missing key: notes" + call.tail
        assert argv == expected


def test_protocol_and_optional_fields_may_be_omitted() -> None:
    data = fixture()
    for key in ("protocol", "loops", "start_stage", "warnings"):
        del data[key]
    for stage in data["stages"]:
        for key in ("argv", "stdin", "command", "verdict_from"):
            if stage[key] is None:
                del stage[key]
        for key in ("check", "rounds", "repairs", "verdict_repairs"):
            del stage[key]
    m = worker.parse_manifest(data)
    assert m.run_id == 42
    assert (m.loops, m.start_stage, m.warnings) == ((), None, ())
    assert all(not s.check and not s.rounds and not s.repairs for s in m.stages)


def test_invocation_build_appends_text_then_tail() -> None:
    on_stdin = worker.Invocation(("claude", "-p"), "Base. ", None, " Tail.")
    assert on_stdin.build("Notes.") == (["claude", "-p"], "Base. Notes. Tail.")
    as_arg = worker.Invocation(("copilot", "-p", "Base. ", "--x"), None, 2, " Tail.")
    assert as_arg.build("Notes.") == (["copilot", "-p", "Base. Notes. Tail.", "--x"], None)


def test_warnings_and_a_start_stage_inside_the_first_block() -> None:
    data = fixture()
    data["warnings"] = ["loop ignored"]
    data["start_stage"] = "review"
    m = worker.parse_manifest(data)
    assert (m.warnings, m.start_stage) == (("loop ignored",), "review")


def test_no_repo_workspace() -> None:
    data = fixture()
    data["workspace"] = {"mode": "none", "repo_url": None, "base_branch": None, "branch": None}
    assert worker.parse_manifest(data).mode == "none"


def _mutate(path: str, value: Any) -> dict[str, Any]:
    data = copy.deepcopy(fixture())
    target: Any = data
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    last: Any = int(keys[-1]) if isinstance(target, list) else keys[-1]
    if value is _DELETE:
        del target[last]
    else:
        target[last] = value
    return data


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("protocol", 2),
        ("protocol", True),
        ("run_id", "42"),
        ("run_id", True),
        ("extra_top_level", 1),
        ("ticket.extra", "x"),
        ("ticket.key", "AOS 42"),
        ("workspace.mode", "worktree"),
        ("workspace.repo_url", None),
        ("workspace.repo_url", "--upload-pack=evil"),
        ("workspace.branch", "-x"),
        ("workspace.branch", "a..b"),
        ("workspace.base_branch", None),
        ("files.0.mode", "append"),
        ("files.0.sync", "yes"),
        ("files.0.content", None),
        ("files.0.extra", 1),
        ("files.0.path", "../escape.md"),
        ("files.0.path", "/abs.md"),
        ("files", {}),
        ("sync_dirs.0", ".git/hooks"),
        ("stages.0.kind", "human"),
        ("stages.0.argv", []),
        ("stages.0.argv", ["claude", 1]),
        ("stages.0.timeout_s", 0),
        ("stages.0.timeout_s", 1.5),
        ("stages.1.command", ""),
        ("stages.1.extra", None),
        ("limits.max_line_bytes", _DELETE),
        ("limits.max_file_bytes", -1),
        ("ticket", _DELETE),
    ],
)
def test_rejects_malformed_manifests(path: str, value: Any) -> None:
    with pytest.raises(worker.ManifestError):
        worker.parse_manifest(_mutate(path, value))


REPAIR = {"argv": ["claude", "-p"], "stdin": "Fix it: ", "prompt_arg": None, "tail": ""}


@pytest.mark.parametrize(
    ("path", "value", "why"),
    [
        # invocations
        ("stages.0.rounds", [], "stages[0].rounds: expected 1 (max_rounds - 1), got 0"),
        ("stages.2.rounds", [REPAIR, REPAIR], "stages[2].rounds: expected 1"),
        ("stages.0.rounds.0.argv", [], "stages[0].rounds[0].argv: must not be empty"),
        ("stages.0.rounds.0.argv", ["claude", 1], "stages[0].rounds[0].argv[1]: expected a str"),
        ("stages.0.rounds.0.tail", None, "stages[0].rounds[0].tail: expected a string"),
        ("stages.0.rounds.0.tail", _DELETE, "stages[0].rounds[0]: missing tail"),
        ("stages.0.rounds.0.extra", 1, "stages[0].rounds[0]: unexpected extra"),
        ("stages.0.rounds.0.prompt_arg", 0, "exactly one of stdin and prompt_arg"),
        ("stages.0.rounds.0.stdin", None, "exactly one of stdin and prompt_arg"),
        ("stages.2.rounds.0.stdin", "x", "exactly one of stdin and prompt_arg"),
        ("stages.2.rounds.0.prompt_arg", None, "exactly one of stdin and prompt_arg"),
        ("stages.2.rounds.0.prompt_arg", 8, "stages[2].rounds[0].prompt_arg: 8 is past the end"),
        ("stages.2.rounds.0.prompt_arg", -1, "stages[2].rounds[0].prompt_arg: must be >= 0"),
        ("stages.2.rounds.0.prompt_arg", True, "prompt_arg: expected an integer"),
        ("stages.2.repairs.0.prompt_arg", 9, "stages[2].repairs[0].prompt_arg: 9 is past"),
        # check fields
        ("stages.2.verdict_from", "tool", "verdict_from: expected structured_output or file"),
        ("stages.2.verdict_from", None, "stages[2].verdict_from: an agent check needs"),
        ("stages.2.repairs.1", _DELETE, "stages[2].repairs: expected 0 or 2 (max_rounds), got 1"),
        ("stages.2.verdict_repairs", -1, "stages[2].verdict_repairs: must be >= 0"),
        ("stages.2.check", "yes", "stages[2].check: expected a boolean"),
        ("stages.0.repairs", [REPAIR], "stages[0].repairs: only an agent check is repaired"),
        ("stages.1.rounds", [REPAIR], "stages[1]: a shell stage takes no rounds or repairs"),
        ("stages.1.id", "implement", "stages[1].id: duplicate stage id 'implement'"),
        # loops
        ("loops.0.max_rounds", 0, "loops[0].max_rounds: must be >= 1"),
        ("loops.0.max_rounds", True, "loops[0].max_rounds: expected an integer"),
        ("loops.0.start", "nope", "loops[0].start: no stage 'nope'"),
        ("loops.0.end", "nope", "loops[0].end: no stage 'nope'"),
        ("loops.0.extra", 1, "loops[0]: unexpected extra"),
        ("loops.0.start", "review", "loops[0]: start 'review' comes after end 'lint'"),
        ("loops", {}, "loops: expected a list"),
        ("loops", [], "stages[0]: check, rounds and repairs need a loop block"),
        # start_stage, warnings
        ("start_stage", "nope", "start_stage: no stage 'nope'"),
        ("start_stage", 2, "start_stage: expected a string"),
        ("warnings", "careful", "warnings: expected a list"),
        ("warnings", ["ok", 3], "warnings[1]: expected a string"),
    ],
)
def test_rejects_malformed_loops(path: str, value: Any, why: str) -> None:
    data = _mutate(path, value)
    if path == "loops.0.start" and value == "review":
        data["loops"][0]["end"] = "lint"
    with pytest.raises(worker.ManifestError, match=re.escape(why)):
        worker.parse_manifest(data)


def plain() -> dict[str, Any]:
    """The fixture without its loop block."""
    data = fixture()
    data["loops"] = []
    for stage in data["stages"]:
        stage.update(check=False, rounds=[], repairs=[], verdict_from=None, verdict_repairs=0)
    return data


@pytest.mark.parametrize(
    ("loops", "why"),
    [
        (
            [("implement", "lint"), ("lint", "review")],
            "loops[1]: overlaps or comes before the previous loop",
        ),
        (
            [("review", "review"), ("implement", "lint")],
            "loops[1]: overlaps or comes before the previous loop",
        ),
    ],
)
def test_rejects_overlapping_or_unordered_loops(loops: list[tuple[str, str]], why: str) -> None:
    data = plain()
    data["loops"] = [{"start": a, "end": b, "max_rounds": 1} for a, b in loops]
    with pytest.raises(worker.ManifestError, match=re.escape(why)):
        worker.parse_manifest(data)


def test_one_round_blocks_and_back_to_back_blocks_parse() -> None:
    data = plain()
    data["loops"] = [
        {"start": "implement", "end": "implement", "max_rounds": 1},
        {"start": "lint", "end": "review", "max_rounds": 1},
    ]
    data["stages"][2].update(check=True, verdict_from="structured_output")
    m = worker.parse_manifest(data)
    assert [(lp.start, lp.end) for lp in m.loops] == [
        ("implement", "implement"),
        ("lint", "review"),
    ]


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda d: d["stages"][1].update(check=True), "stages[1]: check, rounds and repairs need"),
        (lambda d: d["stages"][2].update(repairs=[REPAIR]), "stages[2]: check, rounds and repairs"),
        (lambda d: d.update(start_stage="lint"), "start_stage: must be the first stage, or in a"),
    ],
)
def test_rejects_loop_extras_without_a_block(
    mutate: Callable[[dict[str, Any]], object], why: str
) -> None:
    data = plain()
    mutate(data)
    with pytest.raises(worker.ManifestError, match=re.escape(why)):
        worker.parse_manifest(data)


def test_start_stage_must_be_in_a_block_that_starts_at_the_first_stage() -> None:
    data = plain()
    data["loops"] = [{"start": "lint", "end": "review", "max_rounds": 1}]
    data["stages"][2].update(check=True, verdict_from="file")
    data["start_stage"] = "review"
    with pytest.raises(worker.ManifestError, match="start_stage: must be the first stage"):
        worker.parse_manifest(data)
    data["start_stage"] = "implement"
    assert worker.parse_manifest(data).start_stage == "implement"


@pytest.mark.parametrize("raw", [None, [], "manifest", 42])
def test_rejects_non_objects(raw: object) -> None:
    with pytest.raises(worker.ManifestError):
        worker.parse_manifest(raw)


# ---------------------------------------------------------------- path rules


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        ".claude/skills/context-aos-42/TASKS.md",
        "out/report v2.md",
        "a/b@c+d-e_f.txt",
        ".hidden",
        "x" * 255,
    ],
)
def test_valid_paths(path: str) -> None:
    assert worker.path_error(path) is None


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "a//b",
        "a/",
        "..",
        "a/../b",
        ".",
        "a/./b",
        ".git",
        ".GIT/config",
        "sub/.Git/hooks/pre-commit",
        "C:/x.md",
        "a:b.md",
        "a\\b.md",
        "CON",
        "con.txt",
        "dir/Nul.md",
        "COM1",
        "lpt9.log",
        "aux.tar.gz",
        "trailing.",
        "trailing ",
        "dir./x",
        "tab\there",
        "caf\u00e9.md",
        "e\u0301.md",  # not NFC (and outside the segment charset anyway)
        "x" * 256,
    ],
)
def test_invalid_paths(path: str) -> None:
    assert worker.path_error(path) is not None


@pytest.mark.parametrize("name", ["COM10", "console", "CONx", "nul-device", "LPT"])
def test_reserved_name_lookalikes_are_fine(name: str) -> None:
    assert worker.path_error(name) is None


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
