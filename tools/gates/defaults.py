"""The baseline's defaults: what `--init` writes, and what a baseline that leaves something out
gets (limits; everything else defaults to the strictest value)."""

from __future__ import annotations

from typing import Any

from common import AGENTOS

# `cyclomatic_python_tool` picks the Python cyclomatic counter: "lizard" (and/or, ternaries and
# comprehensions count; what the reference ranges were measured with) or "ruff" (C901, McCabe:
# they don't). The named cyclomatic exceptions are that tool's scores, so a switch goes with
# `--rebaseline-cyclomatic` and a `raises` entry naming both changes.
DEFAULT_LIMITS: dict[str, Any] = {
    "cognitive": 15,
    "cyclomatic": 10,
    "cyclomatic_python_tool": "lizard",
    "file_lines": 1000,
    "fan_out": 5,
    "optional_fields": 1,
    "any": 0,
}
PY_CYCLOMATIC_TOOLS = ("lizard", "ruff")
# fnmatch patterns ("*" also matches "/"), relative to the repo root. First match wins.
# core: domain types and rules on parsed values. boundary: everything touching the outside (HTTP,
# db rows, config, plugins). shell: the feature orchestration between them.
DEFAULT_LAYERS = {
    "core": [
        f"{AGENTOS}/models.py",
        f"{AGENTOS}/errors.py",
        f"{AGENTOS}/pipeline/*",
        f"{AGENTOS}/*/board.py",
        f"{AGENTOS}/*/rules.py",
    ],
    "boundary": [
        f"{AGENTOS}/validate.py",  # pure, but it parses raw input
        f"{AGENTOS}/*/routes.py",
        f"{AGENTOS}/*/deps.py",
        f"{AGENTOS}/launchers/*",
        f"{AGENTOS}/sources/*",
        f"{AGENTOS}/stores/*",
        f"{AGENTOS}/db.py",
        f"{AGENTOS}/config.py",
        f"{AGENTOS}/clock.py",
        f"{AGENTOS}/space_store.py",
        f"{AGENTOS}/http_*.py",
        f"{AGENTOS}/csrf.py",
        f"{AGENTOS}/meta.py",
        f"{AGENTOS}/spa.py",
        f"{AGENTOS}/openapi_export.py",
        f"{AGENTOS}/worker_archive.py",
    ],
    "shell": [
        f"{AGENTOS}/*/service.py",
        f"{AGENTOS}/*/loops.py",
        f"{AGENTOS}/*/reaper.py",
        f"{AGENTOS}/*/dispatcher.py",
        f"{AGENTOS}/main.py",
        f"{AGENTOS}/context.py",
        f"{AGENTOS}/ratelimit.py",
        f"{AGENTOS}/ttlcache.py",
        f"{AGENTOS}/__init__.py",
        f"{AGENTOS}/*/__init__.py",
    ],
    "worker": ["backend/worker/worker.py", "backend/worker/agentos_worker/*"],
}
# What `--init` leaves uncapped per layer (the baseline's `allowed.uncapped` is what applies): the
# boundary is where chaotic input gets checked; `cast` is capped in the core only.
DEFAULT_UNCAPPED = {
    "boundary": ["isinstance", "try", "cast"],
    "shell": ["cast"],
    "worker": ["cast"],
}
EXCEPTION_GATES = ("cognitive", "cyclomatic", "file_lines", "fan_out", "optional_fields", "any")
