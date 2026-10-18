"""Windows-only: does a run worker the client started survive Ctrl+C sent to the client's console?

test_worker_process runs this in a new hidden console (so pytest never sees the Ctrl+C), playing
the part of `worker.py --client` in a terminal. It starts a control child the plain way plus a
child through `process.spawn_detached`, sends CTRL_C_EVENT to its own console, and writes
{"control_exit": int | None, "detached_alive": bool} as JSON to argv[1].

Usage: ctrl_c_harness.py OUT_JSON TMP_DIR (with backend/worker on PYTHONPATH)
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType

from agentos_worker import process

CHILD = "import pathlib, sys, time\npathlib.Path(sys.argv[1]).write_text('ready')\ntime.sleep(60)\n"


def _wait_for(path: Path, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path} never appeared")
        time.sleep(0.05)


def _ignore(signum: int, frame: FrameType | None) -> None:
    del signum, frame


def main() -> None:
    out, tmp = Path(sys.argv[1]), Path(sys.argv[2])

    # A CREATE_NEW_PROCESS_GROUP ancestor (e.g. a shell AgentOS launched) disables Ctrl+C for
    # all its descendants; re-enable it so the control child, which inherits the flag, gets it.
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    signal.signal(signal.SIGINT, _ignore)  # the harness itself must outlive its Ctrl+C

    control_ready, detached_ready = tmp / "control.ready", tmp / "detached.ready"
    control = subprocess.Popen([sys.executable, "-c", CHILD, str(control_ready)])
    argv = [sys.executable, "-c", CHILD, str(detached_ready)]
    detached = process.spawn_detached(argv, tmp, dict(os.environ), tmp / "detached.log")

    try:
        _wait_for(control_ready, 30)
        _wait_for(detached_ready, 30)
        os.kill(0, signal.CTRL_C_EVENT)  # what a Ctrl+C in the client's terminal does
        try:
            control_exit: int | None = control.wait(timeout=10)
        except subprocess.TimeoutExpired:
            control_exit = None
        time.sleep(0.5)
        result = {"control_exit": control_exit, "detached_alive": detached.poll() is None}
        out.write_text(json.dumps(result), encoding="utf-8")
    finally:
        for child in (control, detached):
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
