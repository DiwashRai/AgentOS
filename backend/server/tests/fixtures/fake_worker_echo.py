"""Stands in for worker.py in test_launcher_local: records how it was started, then exits.

Writes {argv, cwd, env} as JSON to $FAKE_WORKER_OUT (atomically, after printing to stdout and
stderr). With $FAKE_WORKER_SLEEP set, sleeps that many seconds afterwards.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    print("fake worker stdout: héllo ✓")
    print("fake worker stderr", file=sys.stderr)
    out = Path(os.environ["FAKE_WORKER_OUT"])
    record = {"argv": sys.argv[1:], "cwd": str(Path.cwd()), "env": dict(os.environ)}
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    tmp.replace(out)
    if seconds := os.environ.get("FAKE_WORKER_SLEEP"):
        time.sleep(float(seconds))


if __name__ == "__main__":
    main()
