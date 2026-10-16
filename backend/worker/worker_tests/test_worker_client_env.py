"""Client mode building blocks: the env-file parser, a run worker's environment, and the
startup cleanup of old run workspaces."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path
from typing import IO

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
    (runs / "5-1-ab.lock").write_bytes(b"")  # its worker is gone
    held = worker.hold_lock(runs / "6-1-cd.lock")  # its worker still runs
    locks = (runs / "5-1-ab.lock", runs / "6-1-cd.lock")
    for path in (runs / "1", runs / "1.log", runs / "notes", runs / "3.txt", *locks):
        os.utime(path, (old, old))
    for path in (tmp_path / "4", tmp_path / "4.log"):
        os.utime(path, (old, old))

    try:
        removed = worker.clean_workdir_root(tmp_path, now)
    finally:
        worker.release_lock(held)

    assert sorted(p.name for p in removed) == ["1", "1.log", "5-1-ab.lock"]
    names = sorted(p.name for p in runs.iterdir())
    assert names == ["2", "2.log", "3.txt", "6-1-cd.lock", "notes"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["4", "4.log", "runs"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows can't delete an open file")
def test_a_worker_whose_lock_file_is_deleted_before_it_locks_starts_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "5-1-ab.lock"
    take = worker._take_lock
    calls: list[int] = []

    def deleted_first(lock: IO[bytes], wait: bool) -> bool:
        calls.append(1)
        if len(calls) == 1:
            path.unlink()  # a cleanup removed the file this worker had just opened
        return take(lock, wait)

    monkeypatch.setattr(worker, "_take_lock", deleted_first)
    lock = worker.hold_lock(path)
    try:
        assert len(calls) == 2
        assert os.fstat(lock.fileno()).st_ino == path.stat().st_ino
        assert worker.lock_held(path)
    finally:
        worker.release_lock(lock)


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX unlinks while holding the lock")
def test_a_cleanup_never_deletes_a_lock_file_a_worker_opened_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "5-1-ab.lock"
    path.write_bytes(b"")
    release = worker.release_lock
    opened: list[IO[bytes]] = []

    def release_then_a_worker_opens(lock: IO[bytes]) -> None:
        release(lock)
        opened.append(path.open("a+b"))  # a worker starting between the release and the unlink

    monkeypatch.setattr(worker, "release_lock", release_then_a_worker_opens)
    try:
        with pytest.raises(PermissionError):  # Windows: the file is open
            worker.remove_free_lock(path)
        assert path.exists()
    finally:
        for f in opened:
            f.close()
