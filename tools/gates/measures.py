"""Every measure in one `Measures`: the node, mypy and Python measures, run side by side."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common import FAN_OUT_EXEMPT, FEATURES, ROOT, Finding, layer_of
from defensive import analyse_python
from mypy_any import finish_any, start_any
from python_measures import cognitive_python, cyclomatic_python, fan_out, python_files
from ts_measures import finish_ts, start_ts


@dataclass
class Measures:
    files: int = 0
    cognitive: list[Finding] = field(default_factory=list[Finding])
    cyclomatic: list[Finding] = field(default_factory=list[Finding])
    file_lines: list[Finding] = field(default_factory=list[Finding])
    fan_out: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    layer: dict[str, str] = field(default_factory=dict[str, str])  # Python path -> layer
    # path -> kind -> lines
    defensive: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    cleanup_try: list[str] = field(default_factory=list[str])  # "path:line", not counted
    optional_fields: list[Finding] = field(default_factory=list[Finding])  # core classes
    any: list[Finding] = field(default_factory=list[Finding])  # core files
    # core path -> (line, message) per `Any` error
    any_errors: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    suppressions: list[str] = field(default_factory=list[str])  # "path:line  marker"
    raw_tags: dict[str, int] = field(default_factory=dict[str, int])  # feature -> count

    def scores(self, gate: str) -> dict[str, int]:
        """The current score per named id for an exception gate (the worst on a name clash)."""
        if gate == "fan_out":
            return {k: len(v) for k, v in self.fan_out.items() if k not in FAN_OUT_EXEMPT}
        out: dict[str, int] = {}
        for f in self.findings(gate):
            out[f.id] = max(out.get(f.id, 0), f.score)
        return out

    def findings(self, gate: str) -> list[Finding]:
        found: dict[str, list[Finding]] = {
            "cognitive": self.cognitive,
            "cyclomatic": self.cyclomatic,
            "file_lines": self.file_lines,
            "optional_fields": self.optional_fields,
            "any": self.any,
        }
        return found[gate]

    def counts(self, uncapped_kinds: dict[str, list[str]]) -> dict[str, dict[str, int]]:
        """Per file, its counts of the kinds its layer caps."""
        out: dict[str, dict[str, int]] = {}
        for path, kinds in self.defensive.items():
            uncapped = uncapped_kinds.get(self.layer[path], [])
            row = {k: len(lines) for k, lines in kinds.items() if lines and k not in uncapped}
            if row:
                out[path] = row
        return out


def measure(layers: dict[str, list[str]], limits: dict[str, Any]) -> Measures:
    ts = start_ts(limits)
    paths = python_files()
    m = Measures(layer={p: layer_of(p, layers) for p in paths})
    mypy = start_any([p for p in paths if m.layer[p] == "core"])
    py = [analyse_python(p, (ROOT / p).read_text(encoding="utf-8")) for p in paths]
    m.files = len(py) + len(ts.files)
    m.cognitive = cognitive_python(py)
    m.cyclomatic = cyclomatic_python(py, limits)
    m.fan_out = fan_out()
    for f in py:
        m.file_lines.append(Finding(f.path, 1, "file", f.code_lines))
        m.suppressions += [f"{f.path}:{line}  {marker}" for line, marker in f.suppressions]
        m.defensive[f.path] = f.defensive.hits
        m.cleanup_try += [f"{f.path}:{line}" for line in f.defensive.cleanup_try]
        if m.layer[f.path] == "core":
            m.optional_fields += [Finding(f.path, *c) for c in f.defensive.optional_fields]

    m.any_errors = finish_any(mypy)
    m.any = [Finding(p, errors[0][0], "Any", len(errors)) for p, errors in m.any_errors.items()]
    ts_measures, cognitive, cyclomatic = finish_ts(ts)
    m.cognitive += cognitive
    m.cyclomatic += cyclomatic
    for path, data in ts_measures.items():
        m.file_lines.append(Finding(path, 1, "file", data["code_lines"]))
        m.suppressions += [f"{path}:{line}  {marker}" for line, marker in data["suppressions"]]
        if path.startswith(FEATURES) and path.endswith(".tsx"):
            feature = path.removeprefix(FEATURES).split("/")[0]
            m.raw_tags[feature] = m.raw_tags.get(feature, 0) + len(data["raw_tags"])
    return m
