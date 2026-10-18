"""The baseline's defaults: what `--init` writes, and what a baseline that leaves something out
gets (limits; everything else defaults to the strictest value)."""

from __future__ import annotations

from typing import Any

from common import AGENTOS

WORKER = "backend/worker/agentos_worker"

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
        f"{WORKER}/package.py",
        f"{WORKER}/verdicts.py",
        f"{WORKER}/publication.py",
        f"{WORKER}/git.py",
        f"{WORKER}/paths.py",
        f"{WORKER}/blocks.py",
        f"{WORKER}/env_file.py",
        f"{WORKER}/fit.py",
        f"{WORKER}/text.py",
        f"{WORKER}/tunables.py",
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
        f"{WORKER}/cli.py",
        f"{WORKER}/worker_api.py",
        f"{WORKER}/outbox.py",
        f"{WORKER}/events.py",
        f"{WORKER}/echo.py",
        f"{WORKER}/process.py",
        f"{WORKER}/files.py",
        f"{WORKER}/github.py",
        f"{WORKER}/verdict_reading.py",
        f"{WORKER}/parse.py",
        f"{WORKER}/workdir_root.py",
        f"{WORKER}/archive.py",
        f"{WORKER}/console.py",
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
        "backend/worker/worker.py",
        f"{WORKER}/__init__.py",
        f"{WORKER}/main.py",
        f"{WORKER}/client.py",
        f"{WORKER}/run.py",
        f"{WORKER}/children.py",
        f"{WORKER}/sync.py",
        f"{WORKER}/publishing.py",
    ],
}
# What `--init` leaves uncapped per layer (the baseline's `allowed.uncapped` is what applies): the
# boundary is where chaotic input gets checked; `cast` is capped in the core only.
DEFAULT_UNCAPPED = {
    "boundary": ["isinstance", "try", "cast"],
    "shell": ["cast"],
}
EXCEPTION_GATES = ("cognitive", "cyclomatic", "file_lines", "fan_out", "optional_fields", "any")
