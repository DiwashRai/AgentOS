"""The tricky parts of gates.py: the ratchet, raise detection, pattern detection, layers."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

import gates
from gates import Finding, Measures


def _baseline(**changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "allowed": {
            "defensive": {"core": {"is_none": 3, "try": 1}},
            "suppressions": 5,
            "raw_tags": {"queue": 10},
        },
        "exceptions": {"cognitive": {"a.py::worse": 20, "a.py::better": 30, "a.py::fixed": 18}},
    }
    raw.update(changes)
    return gates.normalise(raw)


def _measures() -> Measures:
    m = Measures()
    m.defensive = {"core": {"is_none": ["a.py:1"] * 5, "try": []}}  # is_none up, try down
    m.suppressions = ["a.py:1  noqa"] * 7  # up
    m.raw_tags = {"queue": 4}  # down
    m.cognitive = [
        Finding("a.py", 1, "worse", 25),
        Finding("a.py", 2, "better", 22),
        Finding("a.py", 3, "fixed", 9),
    ]
    return m


def test_ratchet_lowers_what_improved_and_never_raises() -> None:
    baseline = _baseline()
    before = gates.flatten(copy.deepcopy(baseline))

    gates.ratchet(baseline, _measures())

    after = gates.flatten(baseline)
    assert all(after[key] <= value for key, value in before.items() if key in after)
    assert after["allowed.defensive.core.is_none"] == 3
    assert after["allowed.defensive.core.try"] == 0
    assert after["allowed.suppressions"] == 5
    assert after["allowed.raw_tags.queue"] == 4
    assert baseline["exceptions"]["cognitive"] == {"a.py::worse": 20, "a.py::better": 22}


def _raised(entry: dict[str, str] | None) -> list[str]:
    old = _baseline()
    new = copy.deepcopy(old)
    new["allowed"]["defensive"]["core"]["is_none"] = 4
    new["raises"] = [entry] if entry else []
    return gates.unexplained_raises(old, new)


def test_a_raise_needs_an_entry_with_a_reason() -> None:
    what = "allowed.defensive.core.is_none"
    assert _raised(None)
    assert _raised({"date": "2026-10-17", "what": what, "reason": " "})
    assert not _raised({"date": "2026-10-17", "what": what, "reason": "parsing a new input"})
    assert not _raised({"what": "allowed.defensive", "reason": "covers the whole section"})


def test_a_new_named_offender_is_a_raise() -> None:
    old = _baseline()
    new = copy.deepcopy(old)
    new["exceptions"]["cyclomatic"]["b.py::new"] = 12
    assert gates.unexplained_raises(old, new)
    assert not gates.unexplained_raises(new, old)  # dropping one only tightens


def test_switching_the_python_cyclomatic_tool_needs_a_raises_entry() -> None:
    old = _baseline()
    new = copy.deepcopy(old)
    new["limits"]["cyclomatic_python_tool"] = "ruff"
    assert gates.unexplained_raises(old, new)
    new["raises"] = [{"what": "limits.cyclomatic_python_tool", "reason": "McCabe as upstream"}]
    assert not gates.unexplained_raises(old, new)


def _hits(source: str) -> dict[str, int]:
    report = gates.analyse_python("x.py", textwrap.dedent(source))
    return {kind: len(lines) for kind, lines in report.defensive.hits.items() if lines}


def test_defensive_patterns_and_their_look_alikes() -> None:
    source = """
        import contextlib
        from typing import Optional

        def f(a: int | None, b: Optional[str], c: int) -> list[int] | None:
            if a is None or b == None:
                return getattr(c, "x", None)
            if hasattr(c, "y") or isinstance(c, int):
                getattr(c, "z")
            with contextlib.suppress(OSError):
                pass
            match c:
                case int():
                    pass
            try:
                pass
            except Exception:
                pass
    """
    assert _hits(source) == {
        "is_none": 2,
        "optional": 3,
        "getattr_default": 1,
        "hasattr": 1,
        "isinstance": 2,
        "suppress": 1,
        "try": 1,
        "broad_except": 1,
    }


def test_docstrings_and_comments_are_not_code_lines() -> None:
    source = '"""Module.\n\nMore."""\n\n# a comment\ndef f():\n    """Doc."""\n    return 1\n'
    assert gates.analyse_python("x.py", source).code_lines == 2


def test_the_first_matching_layer_wins() -> None:
    layers = {"core": ["pkg/*/board.py"], "services": ["pkg/*"]}
    assert gates.layer_of("pkg/tickets/board.py", layers) == "core"
    assert gates.layer_of("pkg/tickets/service.py", layers) == "services"
    assert gates.layer_of("elsewhere.py", layers) == "other"


def _py_file(tmp_path: Path, source: str) -> gates.PyFile:
    path = tmp_path / "f.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8", newline="\n")
    report = gates.analyse_python(str(path), path.read_text(encoding="utf-8"))
    report.path = str(path)  # absolute, so ROOT / path is the file
    return report


def _cyclomatic(report: gates.PyFile, tool: str, limit: int = 10) -> dict[str, int]:
    limits = gates.DEFAULT_LIMITS | {"cyclomatic": limit, "cyclomatic_python_tool": tool}
    return {f.name: f.score for f in gates.cyclomatic_python([report], limits)}


def test_lizard_ignores_its_forgive_comments(tmp_path: Path) -> None:
    report = _py_file(
        tmp_path,
        """\
        def f(xs, flag):  # lizard forgives
            #lizard forgives
            ys = [x for x in xs if x] if flag and xs else []
            match flag:
                case 1:
                    return ys
                case _:
                    return None
        # GENERATED CODE
        def g(x):
            return x or 1
        """,
    )

    assert _cyclomatic(report, "lizard") == {"f": 7, "g": 2}  # f: 1 + for, if, ternary, and, cases


def test_both_python_tools_score_and_only_lizard_counts_boolean_operators(tmp_path: Path) -> None:
    report = _py_file(
        tmp_path,
        """\
        def h(a, b, c):  # noqa: C901
            if a and b or c:
                return 1
            return 0
        """,
    )

    assert _cyclomatic(report, "lizard") == {"h": 4}
    assert _cyclomatic(report, "ruff", limit=1) == {"h": 2}  # ruff lists only those over the limit


def _ts_measures(tmp_path: Path, source: str) -> dict[str, Any]:
    (tmp_path / "Page.tsx").write_text(textwrap.dedent(source), encoding="utf-8", newline="\n")
    rel = os.path.relpath(tmp_path / "Page.tsx", gates.WEB)
    script = Path(gates.__file__).with_name("ts_measures.mjs")
    out = subprocess.run(
        ["node", str(script), str(gates.WEB), rel], capture_output=True, text=True, check=True
    )
    measured: dict[str, Any] = next(iter(json.loads(out.stdout).values()))
    return measured


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_ts_measures_reads_comments_tags_and_names(tmp_path: Path) -> None:
    measured = _ts_measures(
        tmp_path,
        """\
        // eslint-disable-next-line no-console
        export function Page() {
          return (
            <div>
              // text, not a comment
              <Card onClick={() => 1} />
              <span />
            </div>
          );
        }
        """,
    )

    assert measured["code_lines"] == 9
    assert [marker for _, marker in measured["suppressions"]] == ["eslint-disable-next-line"]
    assert measured["raw_tags"] == [4, 7]
    assert [f["name"] for f in measured["functions"]] == ["Page", "Page.onClick(callback)"]
