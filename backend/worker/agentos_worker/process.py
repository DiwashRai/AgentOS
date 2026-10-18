"""Running programs: a stage in its own process group (`run_process`, killed as a tree at
the end; on Windows also through a job object), a detached run worker (`spawn_detached`), the
shell for shell stages and the cmd.exe guard.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from agentos_worker import tunables
from agentos_worker.console import say

# Characters cmd.exe interprets; a .cmd/.bat program must not receive them in argv (SPEC §9.3).
CMD_UNSAFE = frozenset('\r\n"&|<>^%!')


@dataclass(frozen=True)
class ProcResult:
    exit_code: int | None
    stopped: str | None = None  # "timeout" | "cancel" | "budget" | "abort"
    error: str | None = None  # the program could not be started


OnLine = Callable[[str, str, bool], None]  # (stream, text, truncated)


if sys.platform == "win32":

    class _BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        )

    class _IoCounters(ctypes.Structure):
        _fields_ = tuple(
            (name, ctypes.c_uint64)
            for name in ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes", "OtherBytes")
        )

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    _K32: ctypes.WinDLL | None = None

    def _kernel32() -> ctypes.WinDLL:
        global _K32  # one kernel32 handle per process, prototypes set once
        if _K32 is None:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = ctypes.c_void_p
            k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
            k32.SetInformationJobObject.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(_ExtendedLimits),
                ctypes.c_uint32,
            )
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
            k32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            k32.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            k32.CloseHandle.argtypes = (ctypes.c_void_p,)
            _K32 = k32
        return _K32

    def _job_for(pid: int) -> int | None:
        """A job object (killed when closed) holding `pid`, or None if that isn't possible."""
        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        process = k32.OpenProcess(0x0100 | 0x0001, 0, pid)  # PROCESS_SET_QUOTA | _TERMINATE
        assigned = bool(process) and bool(k32.AssignProcessToJobObject(job, process))
        if process:
            k32.CloseHandle(process)
        if not (ok and assigned):
            k32.CloseHandle(job)
            return None
        return int(job)


class _Job:
    """Windows: a job object holding the stage tree, so orphaned children die with it (taskkill
    /T can't find a child whose parent already exited). A no-op elsewhere (killpg covers it)."""

    def __init__(self, pid: int) -> None:
        self._handle: int | None = None
        if sys.platform == "win32":
            try:
                self._handle = _job_for(pid)
            except OSError:
                self._handle = None

    def kill(self) -> None:
        if sys.platform == "win32" and self._handle is not None:
            _kernel32().TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if sys.platform == "win32" and self._handle is not None:
            _kernel32().CloseHandle(self._handle)
        self._handle = None


def _spawn(
    argv: list[str], cwd: Path, env: dict[str, str], with_stdin: bool
) -> subprocess.Popen[bytes]:
    stdin = subprocess.PIPE if with_stdin else subprocess.DEVNULL
    pipe = subprocess.PIPE
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=stdin, stdout=pipe, stderr=pipe, creationflags=flags
        )
    return subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=stdin, stdout=pipe, stderr=pipe, start_new_session=True
    )


def kill_tree(proc: subprocess.Popen[bytes], job: _Job) -> None:
    """Kill the stage and everything it started (its process group / job)."""
    if sys.platform == "win32":
        if proc.poll() is None:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=tunables.KILL_TIMEOUT_S,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        job.kill()
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)


def _pump(pipe: IO[bytes], stream: str, cap: int, on_line: OnLine) -> None:
    """Emit each line of `pipe`; lines longer than `cap` bytes are cut and flagged."""
    skipping = False  # inside the remainder of a cut line
    while True:
        try:
            chunk = pipe.readline(cap + 2)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        complete = chunk.endswith(b"\n")
        if skipping:
            skipping = not complete
            continue
        line = chunk
        if complete:
            line = line[:-1]
            if line.endswith(b"\r"):
                line = line[:-1]
        truncated = len(line) > cap
        if truncated:
            line = line[:cap]
            skipping = not complete
        on_line(stream, line.decode("utf-8", "replace"), truncated)


def _feed(pipe: IO[bytes], data: bytes) -> None:
    with contextlib.suppress(OSError, ValueError):
        pipe.write(data)
    with contextlib.suppress(OSError, ValueError):
        pipe.close()


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: str | None,
    timeout_s: float,
    caps: tuple[int, int],
    on_line: OnLine,
    interrupt: Callable[[], str | None],
) -> ProcResult:
    """Run one stage program in its own process group and stream its output.

    `caps` are the (stdout, stderr) line caps in bytes. `interrupt` is polled every POLL_S and
    returns "cancel"/"abort" to stop the stage. Whatever happens, the whole tree is killed at the
    end (background children included) and output readers are abandoned after READER_JOIN_S.
    """
    try:
        proc = _spawn(argv, cwd, env, stdin is not None)
    except OSError as exc:
        return ProcResult(None, error=f"cannot start {argv[0]}: {exc}")
    job = _Job(proc.pid)
    readers: list[threading.Thread] = []
    stopped: str | None = None
    try:
        for pipe, stream, cap in (
            (proc.stdout, "stdout", caps[0]),
            (proc.stderr, "stderr", caps[1]),
        ):
            if pipe is not None:
                reader = threading.Thread(
                    target=_pump, args=(pipe, stream, cap, on_line), daemon=True
                )
                reader.start()
                readers.append(reader)
        # Readers first, then stdin: a child that fills its stdout before reading stdin can't
        # deadlock us. The writer is a thread so a child that never reads stdin can't either.
        if stdin is not None and proc.stdin is not None:
            threading.Thread(
                target=_feed, args=(proc.stdin, stdin.encode("utf-8")), daemon=True
            ).start()
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                proc.wait(timeout=tunables.POLL_S)
                break
            except subprocess.TimeoutExpired:
                pass
            stopped = interrupt()
            if stopped is None and time.monotonic() >= deadline:
                stopped = "timeout"
            if stopped is not None:
                break
    finally:
        kill_tree(proc, job)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=tunables.KILL_TIMEOUT_S)
        join_deadline = time.monotonic() + tunables.READER_JOIN_S
        for reader in readers:
            reader.join(max(0.0, join_deadline - time.monotonic()))
        job.close()
    abandoned = sum(r.is_alive() for r in readers)
    if abandoned:
        say(f"abandoned {abandoned} output reader(s) still blocked on an orphan-held pipe")
    return ProcResult(proc.returncode, stopped=stopped)


def cmd_guard_error(program: str, argv: list[str]) -> str | None:
    """SPEC §9.3 Windows guard: cmd.exe would mangle (or execute) these characters."""
    if program.lower().endswith((".cmd", ".bat")) and any(
        ch in CMD_UNSAFE for arg in argv for ch in arg
    ):
        return "use prompt_via=stdin or a native binary"
    return None


def shell_prefix() -> list[str]:
    """The shell argv for shell stages; AGENTOS_SHELL overrides (e.g. `bash -c`)."""
    override = os.environ.get("AGENTOS_SHELL", "").strip()
    if override:
        if os.name == "nt":
            parts = shlex.split(override, posix=False)
            return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
        return shlex.split(override)
    if os.name == "nt":
        return ["pwsh", "-NoProfile", "-Command"]
    return ["bash", "-lc"]


def spawn_detached(
    argv: list[str], cwd: Path, env: dict[str, str], log: Path
) -> subprocess.Popen[bytes]:
    """Start `argv` in its own process group (Windows: also without a console) with stdout and
    stderr appended to `log`, so it outlives this process and a Ctrl+C in its console."""
    with log.open("ab") as out:
        # The child gets its own copy of the log handle; ours closes when this block exits.
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            return subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
