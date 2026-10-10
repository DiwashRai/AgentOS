"""Manifest validation (protocol v1, by hand) and the SPEC §6 path rules mirrored in the worker."""

from __future__ import annotations

import copy
import json
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
    assert m.sync_dirs == (".claude/skills/context-aos-42-fix-login-bug",)
    assert [s.kind for s in m.stages] == ["agent", "shell"]
    assert m.stages[0].argv is not None
    assert m.stages[0].argv[0] == "claude"
    assert m.stages[1].command == "ruff format . && ruff check --fix ."
    assert (m.max_file_bytes, m.max_line_bytes) == (1048576, 8192)


def test_protocol_and_optional_stage_fields_may_be_omitted() -> None:
    data = fixture()
    del data["protocol"]
    for stage in data["stages"]:
        for key in ("argv", "stdin", "command"):
            if stage[key] is None:
                del stage[key]
    assert worker.parse_manifest(data).run_id == 42


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
    last = keys[-1]
    if value is _DELETE:
        del target[last]
    elif isinstance(target, list):
        target[int(last)] = value
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


@pytest.mark.parametrize("raw", [None, [], "manifest", 42])
def test_rejects_non_objects(raw: object) -> None:
    with pytest.raises(worker.ManifestError):
        worker.parse_manifest(raw)


# ---------------------------------------------------------------- path rules


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        ".claude/skills/context-aos-42-fix-login-bug/TASKS.md",
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
