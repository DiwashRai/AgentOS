"""The `any` gate's measure: mypy on the core files with every `Any` an error."""

from __future__ import annotations

import re
import subprocess
import sys

from common import ROOT

MYPY_ERROR = re.compile(r"^(?P<path>.+?\.py):(?P<line>\d+): error: (?P<message>.*)$")


def start_any(paths: list[str]) -> subprocess.Popen[str]:
    """mypy on the core files with every `Any` an error, beside the normal `just check` mypy (its
    own cache, so neither invalidates the other's). Imported modules' errors are not reported."""
    command = [sys.executable, "-m", "mypy", "--disallow-any-explicit", "--disallow-any-expr"]
    command += ["--follow-imports=silent", "--cache-dir=.mypy_cache/gates-any"]
    command += ["--no-error-summary", "--no-color-output"]
    command += [p.removeprefix("backend/") for p in paths]
    return subprocess.Popen(
        command, cwd=ROOT / "backend", stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )


def finish_any(run: subprocess.Popen[str]) -> dict[str, list[tuple[int, str]]]:
    """Per core file, its `Any` errors as (line, message)."""
    out, _ = run.communicate()
    if run.returncode not in (0, 1):
        raise RuntimeError(f"mypy exited with {run.returncode}")
    errors: dict[str, list[tuple[int, str]]] = {}
    for line in out.splitlines():
        if hit := MYPY_ERROR.match(line):
            path = "backend/" + hit["path"].replace("\\", "/")
            errors.setdefault(path, []).append((int(hit["line"]), hit["message"]))
    return errors
