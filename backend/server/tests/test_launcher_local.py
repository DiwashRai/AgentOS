"""LocalProcessLauncher (SPEC §12): argv, cwd, env scrubbing, logging and detached spawns."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentos.launchers.base import JobStart, Provider
from agentos.launchers.local import LocalProcessLauncher

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_WORKER = FIXTURES / "fake_worker_echo.py"
SERVER_URL = "http://127.0.0.1:8000"
TOKEN = "dispatch-tok-123"  # noqa: S105 - test value


def _launcher(tmp_path: Path, env_file: Path | None = None, **kw: Any) -> LocalProcessLauncher:
    return LocalProcessLauncher(
        worker_path=kw.pop("worker_path", FAKE_WORKER),
        workspaces_dir=tmp_path / "workspaces",
        logs_dir=tmp_path / "logs",
        worker_env_file=env_file,
        **kw,
    )


def job(run_id: int) -> JobStart:
    return JobStart(f"{run_id}-1-0123456789abcdef", run_id, SERVER_URL, TOKEN)


_: Provider = _launcher(Path("unused"))


def test_real_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_WORKER_OUT", str(out))
    monkeypatch.setenv("AGENTOS_DEV_AUTH", "1")
    monkeypatch.setenv("AGENTOS_SECRET_THING", "server-secret")
    monkeypatch.setenv("AGENTOS_TOKEN", "stale-server-token")
    monkeypatch.setenv("SERVER_ONLY", "server")
    monkeypatch.setenv("OVERRIDE_ME", "server")
    monkeypatch.setenv("UNSET_ME", "server")
    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "OVERRIDE_ME=from-file\nAGENTOS_GIT_NAME=Agent Bot\nFILE_ONLY=yes\nUNSET_ME\n",
        encoding="utf-8",
    )
    launcher = _launcher(tmp_path, env_file)

    ref = launcher.start(job(42))

    child = launcher._children[-1]
    assert child.wait(timeout=60) == 0
    assert ref == str(child.pid)
    record = json.loads(out.read_text(encoding="utf-8"))

    workdir = tmp_path / "workspaces" / "42"
    assert record["argv"] == ["--server", SERVER_URL, "--workdir", str(workdir)]
    assert TOKEN not in json.dumps(record["argv"])
    assert Path(record["cwd"]).resolve() == (tmp_path / "workspaces").resolve()
    assert not workdir.exists()  # the worker creates its own workdir

    env: dict[str, str] = record["env"]
    agentos_vars = {k: v for k, v in env.items() if k.upper().startswith("AGENTOS_")}
    assert agentos_vars == {"AGENTOS_TOKEN": TOKEN, "AGENTOS_GIT_NAME": "Agent Bot"}
    assert env["SERVER_ONLY"] == "server"
    assert env["OVERRIDE_ME"] == "from-file"
    assert env["FILE_ONLY"] == "yes"
    assert "UNSET_ME" not in env
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"

    log = (tmp_path / "logs" / "worker-42.log").read_text(encoding="utf-8")
    assert "fake worker stdout: héllo ✓" in log
    assert "fake worker stderr" in log
    assert TOKEN not in log


def test_log_is_appended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_WORKER_OUT", str(tmp_path / "record.json"))
    log = tmp_path / "logs" / "worker-7.log"
    log.parent.mkdir(parents=True)
    log.write_text("earlier attempt\n", encoding="utf-8")
    launcher = _launcher(tmp_path)

    launcher.start(job(7))

    assert launcher._children[-1].wait(timeout=60) == 0
    text = log.read_text(encoding="utf-8")
    assert text.startswith("earlier attempt\n")
    assert "fake worker stdout" in text


class _FakePopen:
    calls: ClassVar[list[tuple[list[str], dict[str, Any]]]] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        _FakePopen.calls.append((argv, kwargs))
        self.pid = 4242
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> type[_FakePopen]:
    _FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)  # local.py calls subprocess.Popen
    return _FakePopen


def test_spawn_is_detached(tmp_path: Path, fake_popen: type[_FakePopen]) -> None:
    launcher = _launcher(tmp_path)

    assert launcher.start(job(5)) == "4242"

    [(argv, kwargs)] = fake_popen.calls
    assert argv[0] == sys.executable
    assert TOKEN not in argv
    assert kwargs["cwd"] == tmp_path / "workspaces"
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["env"]["AGENTOS_TOKEN"] == TOKEN
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        assert kwargs["creationflags"] & flags == flags
        assert "start_new_session" not in kwargs
    else:
        assert kwargs["start_new_session"] is True


def test_spawn_failure_raises(tmp_path: Path) -> None:
    launcher = _launcher(tmp_path, python=str(tmp_path / "no-such-python.exe"))
    with pytest.raises(OSError):  # noqa: PT011 - the OS decides which OSError subclass
        launcher.start(job(9))
    assert launcher._children == []


def test_missing_env_file_raises(tmp_path: Path, fake_popen: type[_FakePopen]) -> None:
    launcher = _launcher(tmp_path, tmp_path / "missing.env")
    with pytest.raises(FileNotFoundError, match="worker env file"):
        launcher.start(job(9))
    assert fake_popen.calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+C console semantics are Windows-only")
def test_worker_survives_ctrl_c_to_server_console(tmp_path: Path) -> None:
    """uvicorn --reload sends Ctrl+C to its console on every edit; workers must not die with it.

    The harness runs in its own hidden console, so the Ctrl+C never reaches pytest. A control child
    started without the launcher's flags must die, proving the event was actually delivered.
    """
    out, tmp = tmp_path / "result.json", str(tmp_path)
    env = dict(os.environ)
    server_dir = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [server_dir, env.get("PYTHONPATH")]))
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    harness = subprocess.run(
        [sys.executable, str(FIXTURES / "ctrl_c_harness.py"), str(out), str(FAKE_WORKER), tmp],
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
    assert result["worker_alive"] is True
