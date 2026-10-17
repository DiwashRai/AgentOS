"""Gates: measured limits on how AgentOS's code grows. Separate from `just check`.

`just gates` runs every gate and prints a pass/fail line per gate, each offender as
`path:line  name  score > limit`. `just ratchet` lowers the baseline where the code improved and
never raises it. `--init` writes the first baseline.

The baseline (`quality-baseline.json` at the repo root) holds the limits, the layers (path globs,
first match wins: core, boundary, shell, worker), the defensive caps per file, the raw-tag caps per
feature, the named existing offenders and a `raises` list. With no baseline every cap is 0, nothing
is named and the limits are the defaults. A number in the baseline that went up against the
committed one (`git show HEAD:...`) needs a new `raises` entry naming it.

The core holds no `Any` (mypy with every `Any` an error, run on the core files only) and no domain
class with optional fields that stand in for a missing state type. The baseline's
`allowed.uncapped` names the kinds a layer may use freely (the boundary checks chaotic input).
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from baseline import (
    BASELINE,
    git_show,
    initial_baseline,
    load,
    normalise,
    ratchet,
    rebaseline_cyclomatic,
    save,
    unexplained_raises,
)
from common import AGENTOS, FAN_OUT_EXEMPT, FEATURES, KINDS, Finding
from measures import Measures, measure

HINTS = {
    "isinstance": "parse it at the boundary; in the core, match on a union of your own types",
    "try": "clean up with `with` (contextlib.closing / ExitStack); "
    "a try belongs only inside a context manager",
    "optional_fields": "fields that are null together: model the states as separate types",
    "any": "parse it at the boundary into a typed value; the core never sees Any",
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


def _named_gate(name: str, found: list[Finding], baseline: dict[str, Any], what: str) -> Result:
    limit: int = baseline["limits"][name]
    named: dict[str, int] = baseline["exceptions"][name]
    over = _over(name, found, baseline)
    details = [f"{f.path}:{f.line}  {f.name}  {f.score} > {named.get(f.id, limit)}" for f in over]
    top = max((f.score for f in found), default=0)
    summary = f"{len(over)} {what} over the limit {limit} ({len(named)} named), worst {top}"
    return Result(name, not over, summary, details, HINTS.get(name, ""))


def gate_any(m: Measures, baseline: dict[str, Any]) -> Result:
    """mypy's `Any` errors per core file, each offending file's errors listed."""
    result = _named_gate("any", m.any, baseline, "core files with Any errors")
    for f in _over("any", m.any, baseline):
        result.details += [f"    {f.path}:{line}  {msg}" for line, msg in m.any_errors[f.path]]
    return result


def gate_defensive(m: Measures, baseline: dict[str, Any]) -> Result:
    """Each file's capped kinds against its own caps; a per-layer table as the summary."""
    caps: dict[str, dict[str, int]] = baseline["allowed"]["defensive"]
    uncapped_kinds: dict[str, list[str]] = baseline["allowed"]["uncapped"]
    table = {layer: {kind: [0, 0] for kind in KINDS} for layer in [*baseline["layers"], "other"]}
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
    cap: int = baseline["allowed"]["suppressions"]
    total = len(m.suppressions)
    details = m.suppressions if total > cap else []
    return Result("suppressions", total <= cap, f"{total} suppressions, cap {cap}", details)


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


def gate_baseline(baseline: dict[str, Any]) -> Result:
    committed = git_show(BASELINE.name)
    if committed is None:
        return Result("baseline", True, "no committed baseline to compare with")
    missing = unexplained_raises(normalise(json.loads(committed)), baseline)
    summary = f"{len(missing)} raised without a `raises` entry with a reason"
    return Result("baseline", not missing, summary, missing)


def run_gates(baseline: dict[str, Any], m: Measures) -> list[Result]:
    return [
        _named_gate("cognitive", m.cognitive, baseline, "functions"),
        _named_gate("cyclomatic", m.cyclomatic, baseline, "functions"),
        gate_defensive(m, baseline),
        _named_gate("optional_fields", m.optional_fields, baseline, "core domain classes"),
        gate_any(m, baseline),
        gate_fan_out(m, baseline),
        gate_suppressions(m, baseline),
        _named_gate("file_lines", m.file_lines, baseline, "files"),
        gate_raw_tags(m, baseline),
        gate_baseline(baseline),
    ]


def report(results: list[Result], m: Measures, seconds: float, can_ratchet: bool) -> Iterator[str]:
    yield f"gates: {m.files} files in {seconds:.1f} s"
    for r in results:
        yield f"  {'ok  ' if r.ok else 'FAIL'} {r.name:<15} {r.summary}"
        yield from (f"         {line}" for line in r.details)
        if not r.ok and r.hint:
            yield f"         fix: {r.hint}"
    if can_ratchet:
        yield "ratchet available: run `just ratchet`"
    failed = [r.name for r in results if not r.ok]
    yield f"FAIL: {', '.join(failed)}" if failed else "PASS"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ratchet", action="store_true", help="lower the baseline, never raise")
    group.add_argument("--init", action="store_true", help="write the first baseline")
    group.add_argument(
        "--rebaseline-cyclomatic",
        action="store_true",
        help="rewrite the named cyclomatic exceptions (after switching the Python tool)",
    )
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # a Windows pipe may not be UTF-8

    started = time.monotonic()
    exists = BASELINE.exists()
    baseline = load()
    if args.init and exists:
        print(f"{BASELINE.name} exists already; edit it or run `just ratchet`")
        return 2
    if (args.ratchet or args.rebaseline_cyclomatic) and not exists:
        print(f"no {BASELINE.name} yet; write the first one with --init")
        return 2
    m = measure(baseline["layers"], baseline["limits"])

    if args.init:
        save(initial_baseline(m))
        print(f"wrote {BASELINE.name}")
        return 0
    if args.rebaseline_cyclomatic:
        rebaseline_cyclomatic(baseline, m)
        save(baseline)
        print("rewrote exceptions.cyclomatic: add a `raises` entry for it, with the reason")
        return 0
    if args.ratchet:
        changes = ratchet(baseline, m)
        save(baseline)
        print("\n".join(changes) if changes else "nothing to ratchet")
        return 0
    results = run_gates(baseline, m)
    can_ratchet = exists and bool(ratchet(copy.deepcopy(baseline), m))
    for line in report(results, m, time.monotonic() - started, can_ratchet):
        print(line)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
