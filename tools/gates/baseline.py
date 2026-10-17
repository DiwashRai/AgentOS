"""The baseline file (`quality-baseline.json`): loading (checked against its schema) and saving,
raise detection against a base revision, the ratchet, `--init` and `--rebaseline-cyclomatic`."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

from common import ROOT
from defaults import DEFAULT_LAYERS, DEFAULT_LIMITS, DEFAULT_UNCAPPED, EXCEPTION_GATES
from schema import problems

if TYPE_CHECKING:
    from measures import Measures

BASELINE = ROOT / "quality-baseline.json"


class BaselineError(ValueError):
    """The baseline file doesn't match its schema; `problems` says where."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{BASELINE.name} is invalid")
        self.problems = problems


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill what a baseline leaves out with the strictest value (a missing cap is 0). A base
    revision's older baseline may hold one suppression total: it compares as no per-file caps."""
    allowed = raw.get("allowed", {})
    exceptions = raw.get("exceptions", {})
    suppressions = allowed.get("suppressions", {})
    return {
        "version": raw.get("version", 1),
        "layers": raw.get("layers", DEFAULT_LAYERS),
        "limits": DEFAULT_LIMITS | raw.get("limits", {}),
        "allowed": {
            "uncapped": allowed.get("uncapped", {}),
            "defensive": allowed.get("defensive", {}),
            "suppressions": suppressions if isinstance(suppressions, dict) else {},
            "raw_tags": allowed.get("raw_tags", {}),
        },
        "exceptions": {gate: exceptions.get(gate, {}) for gate in EXCEPTION_GATES},
        "raises": raw.get("raises", []),
    }


def load() -> dict[str, Any]:
    """The baseline, checked and normalised; with no file, the strictest one."""
    if not BASELINE.exists():
        return normalise({})
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    if found := problems(raw):
        raise BaselineError(found)
    return normalise(raw)


def save(baseline: dict[str, Any]) -> None:
    BASELINE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")


def flatten(baseline: dict[str, Any]) -> dict[str, int]:
    flat = {f"limits.{k}": v for k, v in baseline["limits"].items() if isinstance(v, int)}
    allowed = baseline["allowed"]
    for layer, uncapped in allowed["uncapped"].items():  # a kind uncapped counts as 1: a raise
        flat |= {f"allowed.uncapped.{layer}.{kind}": 1 for kind in uncapped}
    for path, kinds in allowed["defensive"].items():
        flat |= {f"allowed.defensive.{path}.{kind}": n for kind, n in kinds.items()}
    flat |= {f"allowed.suppressions.{p}": n for p, n in allowed["suppressions"].items()}
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


def new_raises(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """The `raises` entries `new` adds to `old`'s: the ones the owner still has to approve."""
    kept = len(old["raises"])
    entries: list[dict[str, Any]] = new["raises"]
    return entries[kept:] if entries[:kept] == old["raises"] else entries


def git(*args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else None


def at_revision(rev: str) -> dict[str, Any] | None:
    """The baseline committed at `rev`, normalised (not schema-checked: it may be older)."""
    committed = git("show", f"{rev}:{BASELINE.name}")
    return None if committed is None else normalise(json.loads(committed))


def _ratchet_counts(caps: dict[str, Any], now: dict[str, int], where: str) -> list[str]:
    """Lower each cap to its count; a cap that reaches 0 goes."""
    changes: list[str] = []
    for key, cap in list(caps.items()):
        if now.get(key, 0) < cap:
            changes.append(f"{where}.{key}: {cap} -> {now.get(key, 0)}")
            caps[key] = now.get(key, 0)
        if not caps[key]:
            del caps[key]
    return changes


def ratchet(baseline: dict[str, Any], m: Measures) -> list[str]:
    """Lower every cap and named score the code now beats. Never raises one."""
    allowed = baseline["allowed"]
    counts = m.counts(allowed["uncapped"])
    changes: list[str] = []
    for path, kinds in list(allowed["defensive"].items()):
        changes += _ratchet_counts(kinds, counts.get(path, {}), f"allowed.defensive.{path}")
        if not kinds:
            del allowed["defensive"][path]
    suppressions = m.suppression_counts()
    changes += _ratchet_counts(allowed["suppressions"], suppressions, "allowed.suppressions")
    changes += _ratchet_counts(allowed["raw_tags"], m.raw_tags, "allowed.raw_tags")
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
            "suppressions": dict(sorted(m.suppression_counts().items())),
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
