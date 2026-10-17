"""Each gate's verdict on the measures against the baseline: a `Result` with its offenders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from baseline import BASELINE, unexplained_raises
from common import AGENTOS, FAN_OUT_EXEMPT, FEATURES, KINDS, NO_LAYER, Finding
from measures import Measures

HINTS = {
    "isinstance": "parse it at the boundary; in the core, match on a union of your own types",
    "try": "clean up with `with` (contextlib.closing / ExitStack); "
    "a try belongs only inside a context manager",
    "cast": "parse it at the boundary into a typed value; a cast only tells mypy to trust it",
    "optional_fields": "fields that are null together: model the states as separate types",
    "any": "parse it at the boundary into a typed value; the core never sees Any",
    "any_config": "remove the override: the core is checked with every Any an error",
    "suppressions": "fix what the suppression hides; one that stays names its code and why",
    "layers": f"add the file to a layer in {BASELINE.name}",
}


@dataclass
class Result:
    name: str
    ok: bool
    summary: str
    details: list[str] = field(default_factory=list[str])
    hint: str = ""  # how to fix a failure


def _over(name: str, found: list[Finding], baseline: dict[str, Any]) -> list[Finding]:
    """The worst finding per id that is over its named score or the limit, worst first."""
    limit: int = baseline["limits"][name]
    named: dict[str, int] = baseline["exceptions"][name]
    worst: dict[str, Finding] = {}
    for f in found:
        if f.id not in worst or f.score > worst[f.id].score:
            worst[f.id] = f
    over = [f for f in worst.values() if f.score > named.get(f.id, limit)]
    return sorted(over, key=lambda f: (-f.score, f.path, f.line))


def named_gate(name: str, found: list[Finding], baseline: dict[str, Any], what: str) -> Result:
    limit: int = baseline["limits"][name]
    named: dict[str, int] = baseline["exceptions"][name]
    over = _over(name, found, baseline)
    details = [f"{f.path}:{f.line}  {f.name}  {f.score} > {named.get(f.id, limit)}" for f in over]
    top = max((f.score for f in found), default=0)
    summary = f"{len(over)} {what} over the limit {limit} ({len(named)} named), worst {top}"
    return Result(name, not over, summary, details, HINTS.get(name, ""))


def gate_any(m: Measures, baseline: dict[str, Any]) -> Result:
    """mypy's `Any` errors per core file, each offending file's errors listed. Any other mypy
    error, or output that can't be read, fails on its own: it is never budget."""
    result = named_gate("any", m.any, baseline, "core files with Any errors")
    for f in _over("any", m.any, baseline):
        result.details += [f"    {f.path}:{line}  {msg}" for line, msg in m.any_errors[f.path]]
    if m.any_problems:
        result.ok = False
        result.summary += f"; {len(m.any_problems)} other mypy problems (not budget: fix them)"
        result.details += m.any_problems
    return result


def gate_any_config(m: Measures) -> Result:
    count = len(m.any_overrides)
    summary = f"{count} mypy overrides naming a core module (they would switch the Any gate off)"
    return Result("any_config", not count, summary, m.any_overrides, HINTS["any_config"])


def gate_layers(m: Measures) -> Result:
    unmatched = sorted(p for p, layer in m.layer.items() if layer == NO_LAYER)
    details = [f"{p}  matches no layer" for p in unmatched]
    summary = f"{len(unmatched)} Python files in no layer"
    return Result("layers", not unmatched, summary, details, HINTS["layers"])


def gate_defensive(m: Measures, baseline: dict[str, Any]) -> Result:
    """Each file's capped kinds against its own caps; a per-layer table as the summary."""
    caps: dict[str, dict[str, int]] = baseline["allowed"]["defensive"]
    uncapped_kinds: dict[str, list[str]] = baseline["allowed"]["uncapped"]
    rows = [*baseline["layers"], *([NO_LAYER] if NO_LAYER in m.layer.values() else [])]
    table = {layer: {kind: [0, 0] for kind in KINDS} for layer in rows}
    places: list[str] = []
    for path, kinds in sorted(m.defensive.items()):
        layer = m.layer[path]
        for kind, lines in kinds.items():
            cap = caps.get(path, {}).get(kind, 0)
            table[layer][kind][0] += len(lines)
            table[layer][kind][1] += cap
            if len(lines) > cap and kind not in uncapped_kinds.get(layer, []):
                fix = f"  fix: {HINTS[kind]}" if kind in HINTS else ""
                places += [f"{path}  {kind}  {len(lines)} > {cap}{fix}"]
                places += [f"    {path}:{line}" for line in lines]
    header = "".join(f"{kind[:10]:>11}" for kind in KINDS)
    details = [f"{'layer':<9}{header}"]
    for layer, kinds in table.items():
        uncapped = uncapped_kinds.get(layer, [])
        cells = (f"{n}/{'-' if kind in uncapped else cap}" for kind, (n, cap) in kinds.items())
        details.append(f"{layer:<9}" + "".join(cell.rjust(11) for cell in cells))
    failed = sum(not line.startswith(" ") for line in places)
    summary = (
        f"{failed} file/kind counts over their cap (per layer below: count/cap, - is uncapped); "
        f"{len(m.cleanup_try)} try blocks cleaning up in context managers not counted"
    )
    return Result("defensive", not failed, summary, details + places)


def gate_fan_out(m: Measures, baseline: dict[str, Any]) -> Result:
    limit: int = baseline["limits"]["fan_out"]
    named: dict[str, int] = baseline["exceptions"]["fan_out"]
    details: list[str] = []
    for unit, targets in sorted(m.fan_out.items(), key=lambda kv: -len(kv[1])):
        cap = named.get(unit, limit)
        if unit not in FAN_OUT_EXEMPT and len(targets) > cap:
            path = f"{AGENTOS}/{unit}"
            details.append(
                f"{path}  imports  {len(targets)} > {cap}  ({', '.join(sorted(targets))})"
            )
    counts = ", ".join(f"{u} {len(t)}" for u, t in sorted(m.fan_out.items()) if t)
    summary = f"{len(details)} over {limit} other subpackages (main exempt): {counts}"
    return Result("fan_out", not details, summary, details)


def gate_suppressions(m: Measures, baseline: dict[str, Any]) -> Result:
    """Each file's suppressions against its own cap."""
    caps: dict[str, int] = baseline["allowed"]["suppressions"]
    counts = m.suppression_counts()
    details: list[str] = []
    over = [path for path, n in sorted(counts.items()) if n > caps.get(path, 0)]
    for path in over:
        details.append(f"{path}  suppressions  {counts[path]} > {caps.get(path, 0)}")
        details += [f"    {p}:{line}  {marker}" for p, line, marker in m.suppressions if p == path]
    summary = (
        f"{len(over)} files over their cap; {len(m.suppressions)} suppressions in "
        f"{len(counts)} files"
    )
    return Result("suppressions", not over, summary, details, HINTS["suppressions"])


def gate_raw_tags(m: Measures, baseline: dict[str, Any]) -> Result:
    caps: dict[str, int] = baseline["allowed"]["raw_tags"]
    details: list[str] = []
    failed = 0
    for feature, count in sorted(m.raw_tags.items(), key=lambda kv: -kv[1]):
        cap = caps.get(feature, 0)
        failed += count > cap
        mark = "  over" if count > cap else ""
        details.append(f"{FEATURES}{feature}  raw tags  {count}/{cap}{mark}")
    total = sum(m.raw_tags.values())
    summary = f"{failed} of {len(m.raw_tags)} features over their cap, {total} raw tags"
    return Result("raw_tags", not failed, summary, details)


def gate_baseline(baseline: dict[str, Any], base: dict[str, Any] | None, label: str) -> Result:
    if base is None:
        return Result("baseline", True, f"no baseline at {label} to compare with")
    missing = unexplained_raises(base, baseline)
    summary = f"{len(missing)} raised without a `raises` entry with a reason"
    return Result("baseline", not missing, summary, missing)


def run_gates(
    baseline: dict[str, Any], m: Measures, base: dict[str, Any] | None, label: str
) -> list[Result]:
    return [
        gate_layers(m),
        named_gate("cognitive", m.cognitive, baseline, "functions"),
        named_gate("cyclomatic", m.cyclomatic, baseline, "functions"),
        gate_defensive(m, baseline),
        named_gate("optional_fields", m.optional_fields, baseline, "core domain classes"),
        gate_any(m, baseline),
        gate_any_config(m),
        gate_fan_out(m, baseline),
        gate_suppressions(m, baseline),
        named_gate("file_lines", m.file_lines, baseline, "files"),
        gate_raw_tags(m, baseline),
        gate_baseline(baseline, base, label),
    ]
