"""`just card`: markdown for a PR description, showing what a branch did to the gate numbers.

"Before" is the baseline committed at the merge base with `--base`; "after" is the working
baseline with the ratchet applied to today's measures (what it holds once `just ratchet` has run).
The card adds no measures of its own: it compares the two baselines and runs the gates once.
"""

from __future__ import annotations

import argparse
import copy
import io
import sys
from typing import Any

from baseline import at_revision, covers, flatten, load, new_raises, normalise, raised, ratchet
from common import layer_of
from defaults import EXCEPTION_GATES
from gates import base_revision
from measures import measure
from results import run_gates

SHOWN = 25  # improvements listed before "+N more"


def _cell(before: int | None, after: int | None) -> str:
    if before == after:
        return "-" if before is None else str(before)
    mark = " ▼" if (after or 0) < (before or 0) else " ▲"
    return f"{'-' if before is None else before} → {'-' if after is None else after}{mark}"


def _defensive(baseline: dict[str, Any], layer: str) -> int:
    caps: dict[str, dict[str, int]] = baseline["allowed"]["defensive"]
    in_layer = (k for p, k in caps.items() if layer_of(p, baseline["layers"]) == layer)
    return sum(sum(kinds.values()) for kinds in in_layer)


def summary(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """One row per gate: named offenders, worst named score and their total (▼ better, ▲ worse)."""
    rows = ["| Gate | Named | Worst | Total |", "|---|---|---|---|"]
    for gate in EXCEPTION_GATES:
        b: dict[str, int] = before["exceptions"][gate]
        a: dict[str, int] = after["exceptions"][gate]
        worst = _cell(max(b.values(), default=None), max(a.values(), default=None))
        total = _cell(sum(b.values()), sum(a.values()))
        rows.append(f"| {gate} | {_cell(len(b), len(a))} | {worst} | {total} |")
    for layer in dict.fromkeys([*before["layers"], *after["layers"]]):
        total = _cell(_defensive(before, layer), _defensive(after, layer))
        rows.append(f"| defensive ({layer}) | | | {total} |")
    for section in ("suppressions", "raw_tags"):
        b, a = before["allowed"][section], after["allowed"][section]
        rows.append(f"| {section} | | | {_cell(sum(b.values()), sum(a.values()))} |")
    return rows


def _label(key: str) -> str:
    """`exceptions.cognitive.a.py::f` -> `cognitive  a.py::f`."""
    section, _, rest = key.removeprefix("exceptions.").removeprefix("allowed.").partition(".")
    return f"{section}  {rest}" if rest else section


def improved(before: dict[str, Any], after: dict[str, Any], now: dict[str, int]) -> list[str]:
    """Each number that went down, biggest gain first; `now` gives a removed offender's score."""
    b, a = flatten(before), flatten(after)
    found: list[tuple[int, str]] = []
    for key, old in b.items():
        if key in a and a[key] >= old:
            continue
        new = a.get(key, now.get(key, 0))
        gone = "" if key in a else " (removed)"
        found.append((old - new, f"`{_label(key)}`  {old} → {new}{gone}"))
    lines = [line for _, line in sorted(found, key=lambda gain: (-gain[0], gain[1]))]
    more = [f"- +{len(lines) - SHOWN} more"] if len(lines) > SHOWN else []
    return [f"- {line}" for line in lines[:SHOWN]] + more


def worse(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Each number that went up, with the `raises` entry that names it (if any)."""
    b, a = flatten(before), flatten(after)
    entries = new_raises(before, after)
    lines: list[str] = []
    for key in raised(before, after):
        entry = next((e for e in entries if covers(e.get("what", ""), key)), None)
        why = f"raises `{entry['what']}`: {entry['reason']}" if entry else "no raises entry"
        lines.append(f"- `{_label(key)}`  {b.get(key, '-')} → {a.get(key, 'changed')}; {why}")
    return lines


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def raises_table(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    rows = ["| What | From → to | Reason |", "|---|---|---|"]
    for e in new_raises(before, after):
        what, span = _escape(e.get("what")), f"{_escape(e.get('from'))} → {_escape(e.get('to'))}"
        rows.append(f"| `{what}` | {span} | {_escape(e.get('reason'))} |")
    return rows if len(rows) > 2 else []


def card(
    before: dict[str, Any], after: dict[str, Any], now: dict[str, int], verdict: str
) -> list[str]:
    better, up, table = (
        improved(before, after, now),
        worse(before, after),
        raises_table(before, after),
    )
    unchanged = [] if better or up or table else ["", "No change to the gate numbers."]
    sections = [("Improved", better), ("Worse", up), ("Raises to approve", table)]
    out = [verdict, *unchanged, "", *summary(before, after)]
    for title, lines in sections:
        if lines:
            out += ["", f"### {title}", "", *lines]
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", metavar="REF", default="origin/cleanup")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # a Windows pipe may not be UTF-8
    revision = base_revision(args.base)
    if revision is None:
        print(f"--base {args.base}: no merge base with HEAD")
        return 2
    rev, label = revision
    working = load()
    m = measure(working["layers"], working["limits"])
    base = at_revision(rev)
    failed = [r.name for r in run_gates(working, m, base, label) if not r.ok]
    after = copy.deepcopy(working)
    pending = bool(ratchet(after, m))
    gates = f"**Gates fail:** {', '.join(failed)}" if failed else "**Gates pass**"
    todo = "ratchet pending: run `just ratchet`" if pending else "baseline up to date"
    against = label if base else f"{label}, which has no baseline"
    now = {f"exceptions.{g}.{k}": v for g in EXCEPTION_GATES for k, v in m.scores(g).items()}
    verdict = f"{gates}; {todo} (against {against})"
    print("\n".join(card(base or normalise({}), after, now, verdict)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
