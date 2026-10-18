"""Gates: measured limits on how AgentOS's code grows. Separate from `just check`.

`just gates` runs every gate and prints a pass/fail line per gate, each offender as
`path:line  name  score > limit`. `just ratchet` lowers the baseline where the code improved and
never raises it. `--init` writes the first baseline.

The baseline (`quality-baseline.json` at the repo root) holds the limits, the layers (path globs,
first match wins: core, boundary, shell; a Python file in none fails), the defensive and
suppression caps per file, the raw-tag caps per feature, the named existing offenders and a
`raises` list; it is checked against its schema on load. With no baseline every cap is 0, nothing
is named and the limits are the defaults. A number that went up against the baseline at the base
revision (HEAD, or with `--base REF` the merge base with REF) needs a new `raises` entry naming
it, and every new entry is listed for the owner to approve.

The core holds no `Any` (mypy with every `Any` an error, run on the core files only) and no domain
class with optional fields that stand in for a missing state type. The baseline's
`allowed.uncapped` names the kinds a layer may use freely (the boundary checks chaotic input).
"""

from __future__ import annotations

import argparse
import copy
import io
import sys
import time
from collections.abc import Iterator
from typing import Any

from baseline import (
    BASELINE,
    BaselineError,
    at_revision,
    git,
    initial_baseline,
    load,
    new_raises,
    ratchet,
    rebaseline_cyclomatic,
    save,
)
from measures import Measures, measure
from results import Result, run_gates


def report(
    results: list[Result], m: Measures, seconds: float, can_ratchet: bool, approve: list[str]
) -> Iterator[str]:
    yield f"gates: {m.files} files in {seconds:.1f} s"
    for r in results:
        yield f"  {'ok  ' if r.ok else 'FAIL'} {r.name:<15} {r.summary}"
        yield from (f"         {line}" for line in r.details)
        if not r.ok and r.hint:
            yield f"         fix: {r.hint}"
    yield from approve
    if can_ratchet:
        yield "ratchet available: run `just ratchet`"
    failed = [r.name for r in results if not r.ok]
    yield f"FAIL: {', '.join(failed)}" if failed else "PASS"


def to_approve(baseline: dict[str, Any], base: dict[str, Any] | None, label: str) -> list[str]:
    """The `raises` entries that aren't in the base revision's baseline, one line each."""
    entries = new_raises(base, baseline) if base else baseline["raises"]
    if not entries:
        return []
    lines = [f"raises to approve ({len(entries)} not in the baseline at {label}):"]
    for e in entries:
        lines.append(f"  {e['date']}  {e['what']}: {e['from']} -> {e['to']}")
        lines.append(f"      {e['reason']}")
    return lines


def base_revision(ref: str | None) -> tuple[str, str] | None:
    """The revision to compare the baseline with, and how to name it; None if `ref` is unknown."""
    if ref is None:
        return "HEAD", "HEAD"
    merge_base = git("merge-base", "HEAD", ref)
    if merge_base is None:
        return None
    return merge_base.strip(), f"the merge base with {ref} ({merge_base[:7]})"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ratchet", action="store_true", help="lower the baseline, never raise")
    group.add_argument("--init", action="store_true", help="write the first baseline")
    group.add_argument(
        "--rebaseline-cyclomatic",
        action="store_true",
        help="rewrite the named cyclomatic exceptions (after switching the Python tool)",
    )
    parser.add_argument(
        "--base", metavar="REF", help="compare with the merge base with REF instead of HEAD"
    )
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # a Windows pipe may not be UTF-8

    started = time.monotonic()
    exists = BASELINE.exists()
    try:
        baseline = load()
    except BaselineError as error:
        print("\n".join([f"{error}:", *(f"  {p}" for p in error.problems)]))
        return 2
    if args.init and exists:
        print(f"{BASELINE.name} exists already; edit it or run `just ratchet`")
        return 2
    if (args.ratchet or args.rebaseline_cyclomatic) and not exists:
        print(f"no {BASELINE.name} yet; write the first one with --init")
        return 2
    revision = base_revision(args.base)
    if revision is None:
        print(f"--base {args.base}: no merge base with HEAD")
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
    rev, label = revision
    base = at_revision(rev)
    results = run_gates(baseline, m, base, label)
    can_ratchet = exists and bool(ratchet(copy.deepcopy(baseline), m))
    approve = to_approve(baseline, base, label)
    for line in report(results, m, time.monotonic() - started, can_ratchet, approve):
        print(line)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
