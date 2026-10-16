"""Stage processes: background children don't hang the worker, timeouts and cancel kill the tree,
line caps, stdin, the Windows .cmd guard, the shell prefix, and the client's detached run workers
surviving Ctrl+C."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import worker
from worker_tests.worker_support import FAKE_STAGE

Line = tuple[str, str, bool]


def run(
    tmp_path: Path,
    *actions: str,
    stdin: str | None = None,
    timeout_s: float = 30,
    caps: tuple[int, int] = (1000, 1000),
    interrupt: str | None = None,
) -> tuple[worker.ProcResult, list[Line]]:
    lines: list[Line] = []
    result = worker.run_process(
        [sys.executable, str(FAKE_STAGE), *actions],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=stdin,
        timeout_s=timeout_s,
        caps=caps,
        on_line=lambda stream, text, truncated: lines.append((stream, text, truncated)),
        interrupt=lambda: interrupt,
    )
    return result, lines


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_output_lines_reach_on_line(tmp_path: Path) -> None:
    result, lines = run(tmp_path, "print=hello", "print=world")
    assert result == worker.ProcResult(0)
    assert lines == [("stdout", "hello", False), ("stdout", "world", False)]


def test_exit_code_is_reported(tmp_path: Path) -> None:
    result, _ = run(tmp_path, "exit=3")
    assert result.exit_code == 3
    assert result.stopped is None


def test_stdin_is_written_after_readers_start(tmp_path: Path) -> None:
    prompt = 'line 1\nline 2 with "quotes" & <angles> %PATH% !bang!\n' * 2000  # > pipe buffers
    result, lines = run(tmp_path, "stdin", stdin=prompt)
    assert result.exit_code == 0
    assert [text for _, text, _ in lines] == prompt.splitlines()


def test_background_child_holding_the_pipe_does_not_hang(tmp_path: Path) -> None:
    pidfile = tmp_path / "bg.pid"
    started = time.monotonic()
    result, lines = run(tmp_path, f"bgchild={pidfile}")
    elapsed = time.monotonic() - started
    assert result.exit_code == 0
    assert ("stdout", "parent done", False) in lines
    assert elapsed < 10, f"took {elapsed:.1f}s: the orphan's pipe blocked the worker"
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid), "the background child survived the stage"


def test_timeout_kills_the_tree(tmp_path: Path) -> None:
    started = time.monotonic()
    result, lines = run(tmp_path, "sleep=30", timeout_s=0.5)
    assert result.stopped == "timeout"
    assert time.monotonic() - started < 10
    assert ("stdout", "started", False) in lines


@pytest.mark.parametrize("reason", ["cancel", "abort"])
def test_interrupt_kills_the_tree(tmp_path: Path, reason: str) -> None:
    started = time.monotonic()
    result, _ = run(tmp_path, "sleep=30", interrupt=reason)
    assert result.stopped == reason
    assert time.monotonic() - started < 10


@pytest.mark.parametrize(("length", "truncated"), [(10, False), (11, True), (100, True)])
def test_long_lines_are_cut_and_flagged(tmp_path: Path, length: int, truncated: bool) -> None:
    result, lines = run(tmp_path, f"long={length}", caps=(10, 10))
    assert result.exit_code == 0
    assert lines == [  # the cut-off rest is dropped, not emitted as further lines
        ("stdout", "x" * min(length, 10), truncated),
        ("stdout", "after", False),
    ]


def test_missing_program_is_an_error(tmp_path: Path) -> None:
    result = worker.run_process(
        [str(tmp_path / "does-not-exist")],
        cwd=tmp_path,
        env=dict(os.environ),
        stdin=None,
        timeout_s=5,
        caps=(100, 100),
        on_line=lambda *_: None,
        interrupt=lambda: None,
    )
    assert result.error is not None
    assert result.exit_code is None


# Characters cmd.exe interprets in a .cmd/.bat argument instead of passing them through literally.
@pytest.mark.parametrize("char", ["&", "|", "<", ">", "^", "%", "!", '"', "\r", "\n"])
def test_cmd_guard_rejects_unsafe_characters(char: str) -> None:
    error = worker.cmd_guard_error(r"C:\tools\claude.CMD", ["claude", "-p", f"a{char}b"])
    assert error == "use prompt_via=stdin or a native binary"


def test_cmd_guard_allows_native_binaries_and_safe_args() -> None:
    assert worker.cmd_guard_error(r"C:\tools\claude.exe", ["claude", "a&b\n"]) is None
    safe = ["claude", "-p", "--model=claude-haiku-5-5", r"C:\work dir\a.md", "it's (fine) 100$"]
    assert worker.cmd_guard_error(r"C:\tools\claude.bat", safe) is None


def test_shell_prefix_defaults_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    default = worker.shell_prefix()
    if os.name == "nt":
        assert default == ["pwsh", "-NoProfile", "-Command"]
        monkeypatch.setenv("AGENTOS_SHELL", r'"C:\Program Files\Git\bin\bash.exe" -lc')
        assert worker.shell_prefix() == [r"C:\Program Files\Git\bin\bash.exe", "-lc"]
    else:
        assert default == ["bash", "-lc"]
        monkeypatch.setenv("AGENTOS_SHELL", "'/opt/my shell/sh' -c")
        assert worker.shell_prefix() == ["/opt/my shell/sh", "-c"]


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+C console semantics are Windows-only")
def test_a_detached_child_survives_ctrl_c_to_the_client_console(tmp_path: Path) -> None:
    """A Ctrl+C in the client's terminal must not kill the run workers it started.

    The harness runs in its own hidden console, so the Ctrl+C never reaches pytest. A control child
    started without `spawn_detached` must die, proving the event was actually delivered.
    """
    out = tmp_path / "result.json"
    env = dict(os.environ)
    worker_dir = str(Path(worker.__file__).resolve().parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [worker_dir, env.get("PYTHONPATH")]))
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    harness = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "ctrl_c_harness.py"), str(out), str(tmp_path)],
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startupinfo,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert harness.returncode == 0, harness.stderr
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["control_exit"] is not None, "control child ignored Ctrl+C; test proves nothing"
    assert result["detached_alive"] is True
