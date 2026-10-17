"""The `any` gate's measure: mypy on the core files with every `Any` an error, read as JSON so a
config change can't make its output unreadable, and failing closed when it can't be read."""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from typing import Any

from common import ROOT

PYPROJECT = ROOT / "backend" / "pyproject.toml"
# What `--disallow-any-expr` and `--disallow-any-explicit` report; anything else is a type error.
ANY_EXPR = ('Expression has type "Any"', 'Expression type contains "Any"')


@dataclass
class AnyRun:
    errors: dict[str, list[tuple[int, str]]] = field(default_factory=dict)  # path -> Any errors
    problems: list[str] = field(default_factory=list[str])  # fail the gate on their own


def start_any(paths: list[str]) -> subprocess.Popen[str]:
    """mypy on the core files with every `Any` an error, beside the normal `just check` mypy (its
    own cache, so neither invalidates the other's). Imported modules' errors are not reported."""
    command = [sys.executable, "-m", "mypy", "--disallow-any-explicit", "--disallow-any-expr"]
    command += ["--follow-imports=silent", "--cache-dir=.mypy_cache/gates-any"]
    command += ["--output", "json", "--no-error-summary", "--no-color-output"]
    command += [p.removeprefix("backend/") for p in paths]
    return subprocess.Popen(
        command,
        cwd=ROOT / "backend",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _is_any(diagnostic: dict[str, Any]) -> bool:
    code, message = diagnostic.get("code"), str(diagnostic.get("message", ""))
    return code == "explicit-any" or (code == "misc" and message.startswith(ANY_EXPR))


def parse_mypy(returncode: int, out: str, err: str) -> AnyRun:
    """mypy's JSON lines: Any errors per file; every other error, an unreadable line, a crash, or
    a failure with nothing parsed is a problem."""
    run = AnyRun()
    if returncode not in (0, 1):
        tail = err.strip().splitlines()[-1:] or ["no output"]
        run.problems.append(f"mypy crashed (exit {returncode}): {tail[0]}")
    for line in filter(str.strip, out.splitlines()):
        try:
            diagnostic = json.loads(line)
        except json.JSONDecodeError:
            run.problems.append(f"unreadable mypy output: {line}")
            continue
        if diagnostic.get("severity") != "error":
            continue
        path = "backend/" + str(diagnostic["file"]).replace("\\", "/")
        where = f"{path}:{diagnostic['line']}"
        if _is_any(diagnostic):
            run.errors.setdefault(path, []).append((int(diagnostic["line"]), diagnostic["message"]))
        else:
            run.problems.append(f"{where}  [{diagnostic.get('code')}] {diagnostic['message']}")
    if returncode == 1 and not run.errors and not run.problems:
        run.problems.append("mypy failed, but none of its output could be read")
    return run


def finish_any(run: subprocess.Popen[str]) -> AnyRun:
    out, err = run.communicate()
    return parse_mypy(run.returncode, out, err)


def _matches(pattern: str, module: str) -> bool:
    """mypy's override patterns: `a.b` exactly, `a.b.*` for a.b and below, `*` anywhere."""
    if pattern.endswith(".*") and module == pattern[:-2]:
        return True
    return fnmatch.fnmatchcase(module, pattern)


def core_overrides(modules: list[str], pyproject: str | None = None) -> list[str]:
    """Each `[[tool.mypy.overrides]]` that names a core module: it would switch the gate off."""
    text = PYPROJECT.read_text(encoding="utf-8") if pyproject is None else pyproject
    overrides = tomllib.loads(text).get("tool", {}).get("mypy", {}).get("overrides", [])
    found: list[str] = []
    for override in overrides:
        patterns = override.get("module", [])
        for pattern in [patterns] if isinstance(patterns, str) else patterns:
            hit = [m for m in modules if _matches(pattern, m)]
            if hit:
                found.append(
                    f"backend/pyproject.toml  [[tool.mypy.overrides]] {pattern!r}: {hit[0]}"
                )
    return found
