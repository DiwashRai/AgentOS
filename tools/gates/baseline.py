"""The baseline file (`quality-baseline.json`): its defaults, loading and saving, raise detection,
the ratchet, `--init` and `--rebaseline-cyclomatic`."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from common import AGENTOS, ROOT
from measures import Measures

BASELINE = ROOT / "quality-baseline.json"
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
    "worker": ["backend/worker/worker.py"],
}
# What `--init` leaves uncapped per layer (the baseline's `allowed.uncapped` is what applies): the
# boundary is where chaotic input gets checked.
DEFAULT_UNCAPPED = {"boundary": ["isinstance", "try"]}
EXCEPTION_GATES = ("cognitive", "cyclomatic", "file_lines", "fan_out", "optional_fields", "any")


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill what a baseline leaves out with the strictest value (a missing cap is 0)."""
    allowed = raw.get("allowed", {})
    exceptions = raw.get("exceptions", {})
    return {
        "version": raw.get("version", 1),
        "layers": raw.get("layers", DEFAULT_LAYERS),
        "limits": DEFAULT_LIMITS | raw.get("limits", {}),
        "allowed": {
            "uncapped": allowed.get("uncapped", {}),
            "defensive": allowed.get("defensive", {}),
            "suppressions": allowed.get("suppressions", 0),
            "raw_tags": allowed.get("raw_tags", {}),
        },
        "exceptions": {gate: exceptions.get(gate, {}) for gate in EXCEPTION_GATES},
        "raises": raw.get("raises", []),
    }


def load() -> dict[str, Any]:
    """The baseline, normalised; with no file, the strictest one."""
    exists = BASELINE.exists()
    return normalise(json.loads(BASELINE.read_text(encoding="utf-8")) if exists else {})


def save(baseline: dict[str, Any]) -> None:
    BASELINE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")


def flatten(baseline: dict[str, Any]) -> dict[str, int]:
    flat = {f"limits.{k}": v for k, v in baseline["limits"].items() if isinstance(v, int)}
    allowed = baseline["allowed"]
    for layer, uncapped in allowed["uncapped"].items():  # a kind uncapped counts as 1: a raise
        flat |= {f"allowed.uncapped.{layer}.{kind}": 1 for kind in uncapped}
    for path, kinds in allowed["defensive"].items():
        flat |= {f"allowed.defensive.{path}.{kind}": n for kind, n in kinds.items()}
    flat["allowed.suppressions"] = allowed["suppressions"]
    flat |= {f"allowed.raw_tags.{f}": n for f, n in allowed["raw_tags"].items()}
    for gate, named in baseline["exceptions"].items():
        flat |= {f"exceptions.{gate}.{name}": n for name, n in named.items()}
    return flat


def unexplained_raises(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What went up from `old` to `new` without a new `raises` entry (with a reason) naming it."""
    kept = len(old["raises"])
    if new["raises"][:kept] != old["raises"]:
        return ["raises: earlier entries were changed or removed"]
    named = [e.get("what", "") for e in new["raises"][kept:] if str(e.get("reason", "")).strip()]
    before = flatten(old)
    raised = [k for k, v in flatten(new).items() if v > before.get(k, 0)]
    if new["layers"] != old["layers"]:
        raised.append("layers")
    tool = "cyclomatic_python_tool"
    if new["limits"][tool] != old["limits"][tool]:
        raised.append(f"limits.{tool}")
    return [
        f"{key}: {before.get(key, 'none')} -> {flatten(new).get(key, 'changed')}"
        for key in sorted(raised)
        if not any(key == what or key.startswith(f"{what}.") for what in named)
    ]


def git_show(path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def _ratchet_defensive(allowed: dict[str, Any], m: Measures) -> list[str]:
    """Lower each file's caps to its counts; a cap that reaches 0 (and then its file) goes."""
    changes: list[str] = []
    caps: dict[str, dict[str, int]] = allowed["defensive"]
    counts = m.counts(allowed["uncapped"])
    for path, kinds in list(caps.items()):
        for kind, cap in list(kinds.items()):
            now = counts.get(path, {}).get(kind, 0)
            if now < cap:
                kinds[kind] = now
                changes.append(f"allowed.defensive.{path}.{kind}: {cap} -> {now}")
            if not kinds[kind]:
                del kinds[kind]
        if not kinds:
            del caps[path]
    return changes


def ratchet(baseline: dict[str, Any], m: Measures) -> list[str]:
    """Lower every cap and named score the code now beats. Never raises one."""
    allowed = baseline["allowed"]
    changes = _ratchet_defensive(allowed, m)
    if len(m.suppressions) < allowed["suppressions"]:
        changes.append(f"allowed.suppressions: {allowed['suppressions']} -> {len(m.suppressions)}")
        allowed["suppressions"] = len(m.suppressions)
    for feature, cap in list(allowed["raw_tags"].items()):
        now = m.raw_tags.get(feature, 0)
        if now < cap:
            allowed["raw_tags"][feature] = now
            changes.append(f"allowed.raw_tags.{feature}: {cap} -> {now}")
    for gate in EXCEPTION_GATES:
        current, limit = m.scores(gate), baseline["limits"][gate]
        named: dict[str, int] = baseline["exceptions"][gate]
        for name, cap in list(named.items()):
            now = current.get(name, 0)
            if now <= limit:
                del named[name]
                changes.append(f"exceptions.{gate}.{name}: removed (now {now})")
            elif now < cap:
                named[name] = now
                changes.append(f"exceptions.{gate}.{name}: {cap} -> {now}")
    return changes


def initial_baseline(m: Measures) -> dict[str, Any]:
    limits = dict(DEFAULT_LIMITS)
    return {
        "version": 2,
        "layers": DEFAULT_LAYERS,
        "limits": limits,
        "allowed": {
            "uncapped": DEFAULT_UNCAPPED,
            "defensive": dict(sorted(m.counts(DEFAULT_UNCAPPED).items())),
            "suppressions": len(m.suppressions),
            "raw_tags": dict(sorted(m.raw_tags.items())),
        },
        "exceptions": {
            gate: {k: v for k, v in sorted(m.scores(gate).items()) if v > limits[gate]}
            for gate in EXCEPTION_GATES
        },
        "raises": [],
    }


def rebaseline_cyclomatic(baseline: dict[str, Any], m: Measures) -> None:
    """Name every function over the cyclomatic limit at its score (after a tool switch)."""
    limit = baseline["limits"]["cyclomatic"]
    scores = sorted(m.scores("cyclomatic").items())
    baseline["exceptions"]["cyclomatic"] = {k: v for k, v in scores if v > limit}
