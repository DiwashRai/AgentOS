"""Windows-only: does a launched worker survive Ctrl+C sent to the server's console?

test_launcher_local runs this in a new hidden console (so pytest never sees the Ctrl+C), playing
the part of `uvicorn --reload`. It starts a control child the plain way plus a worker through
LocalProcessLauncher, sends CTRL_C_EVENT to its own console, and writes
{"control_exit": int | None, "worker_alive": bool} as JSON to argv[1].

Usage: ctrl_c_harness.py OUT_JSON FAKE_WORKER TMP_DIR
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType

from agentos.launchers.local import LocalProcessLauncher

CONTROL = (
    "import pathlib, sys, time\npathlib.Path(sys.argv[1]).write_text('ready')\ntime.sleep(60)\n"
)


def _wait_for(path: Path, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path} never appeared")
        time.sleep(0.05)


def _ignore(signum: int, frame: FrameType | None) -> None:
    del signum, frame


def main() -> None:
    out, fake_worker, tmp = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    signal.signal(signal.SIGINT, _ignore)  # the harness itself must outlive its Ctrl+C

    control_ready = tmp / "control.ready"
    control = subprocess.Popen([sys.executable, "-c", CONTROL, str(control_ready)])

    worker_ready = tmp / "worker.json"
    os.environ["FAKE_WORKER_OUT"] = str(worker_ready)
    os.environ["FAKE_WORKER_SLEEP"] = "60"
    launcher = LocalProcessLauncher(
        worker_path=fake_worker,
        workspaces_dir=tmp / "workspaces",
        logs_dir=tmp / "logs",
        worker_env_file=None,
        max_concurrency=None,
    )
    launcher.launch(1, "http://127.0.0.1:9", "tok")
    worker = launcher._children[-1]  # the launcher never exposes its children; fine for a test

    try:
        _wait_for(control_ready, 30)
        _wait_for(worker_ready, 30)
        os.kill(0, signal.CTRL_C_EVENT)  # what uvicorn --reload does to its console
        try:
            control_exit: int | None = control.wait(timeout=10)
        except subprocess.TimeoutExpired:
            control_exit = None
        time.sleep(0.5)
        result = {"control_exit": control_exit, "worker_alive": worker.poll() is None}
        out.write_text(json.dumps(result), encoding="utf-8")
    finally:
        for child in (control, worker):
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
