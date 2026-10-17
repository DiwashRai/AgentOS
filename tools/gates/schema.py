"""The baseline's shape, checked on load: a number the raise check can't compare (`999.0`, `-1`,
`true`), an unknown key, kind or layer, or a malformed `raises` entry is an error, not a pass."""

from __future__ import annotations

import datetime
from typing import Any

from common import KINDS, NO_LAYER
from defaults import DEFAULT_LAYERS, DEFAULT_LIMITS, EXCEPTION_GATES, PY_CYCLOMATIC_TOOLS

TOP = {"version", "layers", "limits", "allowed", "exceptions", "raises"}
ALLOWED = {"uncapped", "defensive", "suppressions", "raw_tags"}
RAISE_KEYS = ("date", "what", "from", "to", "reason")


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _table(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _counts(where: str, value: object) -> list[str]:
    """A table of non-negative integers."""
    table = _table(value)
    if table is None:
        return [f"{where}: not a table"]
    return [
        f"{where}.{k}: {v!r} is not a non-negative integer"
        for k, v in table.items()
        if not _is_count(v)
    ]


def _unknown(where: str, table: dict[str, Any], known: set[str] | tuple[str, ...]) -> list[str]:
    return [f"{where}{key}: unknown key" for key in table if key not in known]


def _layers(value: object) -> list[str]:
    table = _table(value)
    if table is None:
        return ["layers: not a table"]
    out = [f"layers.{NO_LAYER}: reserved for files no layer matches"] if NO_LAYER in table else []
    for layer, patterns in table.items():
        if not (isinstance(patterns, list) and all(isinstance(p, str) for p in patterns)):
            out.append(f"layers.{layer}: not a list of path globs")
    return out


def _limits(value: object) -> list[str]:
    table = _table(value)
    if table is None:
        return ["limits: not a table"]
    out = _unknown("limits.", table, tuple(DEFAULT_LIMITS))
    for key, limit in table.items():
        if key == "cyclomatic_python_tool":
            if limit not in PY_CYCLOMATIC_TOOLS:
                out.append(f"limits.{key}: {limit!r}, not one of {PY_CYCLOMATIC_TOOLS}")
        elif not _is_count(limit):
            out.append(f"limits.{key}: {limit!r} is not a non-negative integer")
    return out


def _kinds(where: str, kinds: object) -> list[str]:
    if not isinstance(kinds, list):
        return [f"{where}: not a list of kinds"]
    return [
        f"{where}: {kind!r} is not a kind ({', '.join(KINDS)})"
        for kind in kinds
        if kind not in KINDS
    ]


def _allowed(value: object, layers: set[str]) -> list[str]:
    table = _table(value)
    if table is None:
        return ["allowed: not a table"]
    out = _unknown("allowed.", table, ALLOWED)
    uncapped = _table(table.get("uncapped", {}))
    if uncapped is None:
        out.append("allowed.uncapped: not a table")
    for layer, kinds in (uncapped or {}).items():
        out += [f"allowed.uncapped.{layer}: not a layer"] if layer not in layers else []
        out += _kinds(f"allowed.uncapped.{layer}", kinds)
    defensive = _table(table.get("defensive", {}))
    if defensive is None:
        out.append("allowed.defensive: not a table")
    for path, caps in (defensive or {}).items():
        out += _counts(f"allowed.defensive.{path}", caps)
        out += _kinds(f"allowed.defensive.{path}", list(caps) if isinstance(caps, dict) else [])
    out += _counts("allowed.suppressions", table.get("suppressions", {}))
    return out + _counts("allowed.raw_tags", table.get("raw_tags", {}))


def _raise(i: int, entry: object) -> list[str]:
    where = f"raises[{i}]"
    if not isinstance(entry, dict):
        return [f"{where}: not a table"]
    out = [f"{where}.{key}: missing" for key in RAISE_KEYS if key not in entry]
    for key in ("what", "reason"):
        if key in entry and not (isinstance(entry[key], str) and entry[key].strip()):
            out.append(f"{where}.{key}: must be a non-empty string")
    try:
        datetime.date.fromisoformat(str(entry.get("date", "")))
    except ValueError:
        out.append(f"{where}.date: {entry.get('date')!r} is not a YYYY-MM-DD date")
    return out


def problems(raw: object) -> list[str]:
    """Everything wrong with a baseline as loaded from JSON; empty when it is well formed."""
    if not isinstance(raw, dict):
        return ["not a JSON object"]
    out = _unknown("", raw, TOP)
    if "version" in raw and not _is_count(raw["version"]):
        out.append(f"version: {raw['version']!r} is not a non-negative integer")
    layers = raw.get("layers", DEFAULT_LAYERS)
    out += _layers(layers)
    out += _limits(raw.get("limits", {}))
    out += _allowed(raw.get("allowed", {}), set(layers) if isinstance(layers, dict) else set())
    exceptions = _table(raw.get("exceptions", {}))
    if exceptions is None:
        out.append("exceptions: not a table")
    out += _unknown("exceptions.", exceptions or {}, EXCEPTION_GATES)
    for gate, named in (exceptions or {}).items():
        out += _counts(f"exceptions.{gate}", named)
    entries = raw.get("raises", [])
    if not isinstance(entries, list):
        return [*out, "raises: not a list"]
    return out + [p for i, entry in enumerate(entries) for p in _raise(i, entry)]
