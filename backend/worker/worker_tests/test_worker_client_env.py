"""Client mode building blocks: the env-file parser, a run worker's environment, and the
startup cleanup of old run workspaces."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

import worker

PARSE_CASES = [
    ("A=1", {"A": "1"}),
    ("export A=1", {"A": "1"}),
    ("  A = 1  ", {"A": "1"}),
    ("# comment\n\n   \nA=1 # trailing\n", {"A": "1"}),
    ("A=x#y", {"A": "x#y"}),
    ("A=", {"A": ""}),
    ("A", {"A": None}),
    ("export A # unset it", {"A": None}),
    ("A='a b # c'", {"A": "a b # c"}),
    ("A='it\\'s \\\\ \\n'", {"A": "it's \\ \\n"}),
    ('A="x\\ny \\"q\\" \\\\"', {"A": 'x\ny "q" \\'}),
    ('A="a" # comment', {"A": "a"}),
    ("A='line1\nline2'\nB=2", {"A": "line1\nline2", "B": "2"}),
    ("A=1\r\nB=2\r\n", {"A": "1", "B": "2"}),
    ("A=1\nA=2", {"A": "2"}),
    (
        "GIT_CONFIG_KEY_0='url.file:///C:/repos/origin.git.insteadOf'",
        {"GIT_CONFIG_KEY_0": "url.file:///C:/repos/origin.git.insteadOf"},
    ),
]


@pytest.mark.parametrize(("text", "expected"), PARSE_CASES)
def test_parse_env(text: str, expected: dict[str, str | None]) -> None:
    assert worker.parse_env(text) == expected


@pytest.mark.parametrize("text", ["A='open", 'A="open', "A='x' junk", "=1", "A=1\n'B'=2"])
def test_parse_env_rejects(text: str) -> None:
    with pytest.raises(worker.EnvFileError):
        worker.parse_env(text)


BASE = {"PATH": "p", "agentos_lower": "x", "AGENTOS_CLIENT_TOKEN": "c", "DROP": "d"}
DEFAULTS = {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "AGENTOS_TOKEN": "tok"}

CHILD_ENV_CASES = [
    ({}, {"PATH": "p", "DROP": "d", **DEFAULTS}),
    ({"DROP": None, "GIT_X": "g"}, {"PATH": "p", "GIT_X": "g", **DEFAULTS}),
    ({"AGENTOS_ECHO": "0"}, {"PATH": "p", "DROP": "d", "AGENTOS_ECHO": "0", **DEFAULTS}),
    ({"AGENTOS_TOKEN": "other", "PYTHONUNBUFFERED": "0"}, {"PATH": "p", "DROP": "d", **DEFAULTS}),
]


@pytest.mark.parametrize(("env_file", "expected"), CHILD_ENV_CASES)
def test_child_env(env_file: dict[str, str | None], expected: dict[str, str]) -> None:
    assert worker.child_env("tok", env_file, BASE) == expected


def test_cleanup_deletes_only_old_run_workspaces_and_logs_of_the_client(tmp_path: Path) -> None:
    now = time.time()
    old = now - worker.WORKSPACE_MAX_AGE_S - 60
    runs = tmp_path / "runs"
    (runs / "1" / ".git").mkdir(parents=True)
    pack = runs / "1" / ".git" / "pack"
    pack.write_bytes(b"x")
    pack.chmod(stat.S_IREAD)  # git packs are read-only
    (runs / "1.log").write_bytes(b"log")
    (runs / "2").mkdir()
    (runs / "2.log").write_bytes(b"log")
    (runs / "notes").mkdir()
    (runs / "3.txt").write_bytes(b"x")
    (tmp_path / "4").mkdir()  # not the client's: an unrelated all-digit folder in the root
    (tmp_path / "4.log").write_bytes(b"x")
    (runs / "5-1-ab.lock").write_bytes(b"")  # kept until its `gone` is acknowledged
    for path in (runs / "1", runs / "1.log", runs / "notes", runs / "3.txt", runs / "5-1-ab.lock"):
        os.utime(path, (old, old))
    for path in (tmp_path / "4", tmp_path / "4.log"):
        os.utime(path, (old, old))

    removed = worker.clean_workdir_root(tmp_path, now)

    assert sorted(p.name for p in removed) == ["1", "1.log"]
    names = sorted(p.name for p in runs.iterdir())
    assert names == ["2", "2.log", "3.txt", "5-1-ab.lock", "notes"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["4", "4.log", "runs"]
