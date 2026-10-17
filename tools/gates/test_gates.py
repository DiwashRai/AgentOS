"""The tricky parts of the gates: the ratchet, raise detection, the baseline's schema, reading
mypy, and the gates' verdicts (per-file caps, layers)."""

from __future__ import annotations

import copy
from typing import Any

import baseline as bl
import card
import common
import gates
import mypy_any
import results
import schema
from common import Finding
from measures import Measures


def _baseline(**changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "allowed": {
            "uncapped": {"boundary": ["try"]},
            "defensive": {"a.py": {"isinstance": 3, "try": 1}},
            "suppressions": {"a.py": 5, "b.py": 2},
            "raw_tags": {"queue": 10},
        },
        "exceptions": {"cognitive": {"a.py::worse": 20, "a.py::better": 30, "a.py::fixed": 18}},
    }
    raw.update(changes)
    return bl.normalise(raw)


def _measures() -> Measures:
    m = Measures(layer={"a.py": "core"})
    m.defensive = {"a.py": {"isinstance": [1] * 5, "try": []}}  # isinstance up, try down
    m.suppressions = [("a.py", 1, "noqa")] * 7 + [("b.py", 1, "noqa")]  # a up, b down
    m.raw_tags = {"queue": 4}  # down
    m.cognitive = [
        Finding("a.py", 1, "worse", 25),
        Finding("a.py", 2, "better", 22),
        Finding("a.py", 3, "fixed", 9),
    ]
    return m


def test_ratchet_lowers_what_improved_and_never_raises() -> None:
    baseline = _baseline()
    before = bl.flatten(copy.deepcopy(baseline))

    bl.ratchet(baseline, _measures())

    after = bl.flatten(baseline)
    assert all(after[key] <= value for key, value in before.items() if key in after)
    assert after["allowed.defensive.a.py.isinstance"] == 3
    assert "allowed.defensive.a.py.try" not in after  # a cap that reaches 0 goes
    assert after["allowed.suppressions.a.py"] == 5
    assert after["allowed.suppressions.b.py"] == 1
    assert after["allowed.raw_tags.queue"] == 4
    assert baseline["exceptions"]["cognitive"] == {"a.py::worse": 20, "a.py::better": 22}


def _raised(entry: dict[str, str] | None) -> list[str]:
    old = _baseline()
    new = copy.deepcopy(old)
    new["allowed"]["defensive"]["a.py"]["isinstance"] = 4
    new["raises"] = [entry] if entry else []
    return bl.unexplained_raises(old, new)


def test_a_raise_needs_an_entry_with_a_reason() -> None:
    what = "allowed.defensive.a.py.isinstance"
    assert _raised(None)
    assert _raised({"date": "2026-10-17", "what": what, "reason": " "})
    assert not _raised({"date": "2026-10-17", "what": what, "reason": "parsing a new input"})
    assert not _raised({"what": "allowed.defensive", "reason": "covers the whole section"})


def test_a_new_named_offender_is_a_raise() -> None:
    old = _baseline()
    new = copy.deepcopy(old)
    new["exceptions"]["cyclomatic"]["b.py::new"] = 12
    assert bl.unexplained_raises(old, new)
    assert not bl.unexplained_raises(new, old)  # dropping one only tightens


def test_uncapping_a_kind_in_a_layer_is_a_raise() -> None:
    old = _baseline()
    new = copy.deepcopy(old)
    new["allowed"]["uncapped"]["boundary"].append("broad_except")
    new["allowed"]["uncapped"]["shell"] = ["isinstance"]
    assert len(bl.unexplained_raises(old, new)) == 2
    new["raises"] = [{"what": "allowed.uncapped", "reason": "the shell parses rows now"}]
    assert not bl.unexplained_raises(old, new)
    assert not bl.unexplained_raises(new | {"raises": []}, old)  # capping one only tightens


def test_switching_the_python_cyclomatic_tool_needs_a_raises_entry() -> None:
    old = _baseline()
    new = copy.deepcopy(old)
    new["limits"]["cyclomatic_python_tool"] = "ruff"
    assert bl.unexplained_raises(old, new)
    new["raises"] = [{"what": "limits.cyclomatic_python_tool", "reason": "McCabe as upstream"}]
    assert not bl.unexplained_raises(old, new)


def test_the_schema_rejects_what_the_raise_check_cannot_compare() -> None:
    entry = {"date": "2026-10-17", "what": "limits", "from": 1, "to": 2, "reason": "why"}
    assert not schema.problems({"limits": {"cognitive": 15}, "raises": [entry]})
    assert schema.problems({"limits": {"cognitive": 999.0}})  # a float would skip the raise check
    assert schema.problems({"limits": {"cognitive": True}})
    assert schema.problems({"allowed": {"suppressions": {"a.py": -1}}})
    assert schema.problems({"allowed": {"defensive": {"a.py": {"is_none": 1}}}})  # unknown kind
    assert schema.problems({"allowed": {"uncapped": {"nowhere": ["try"]}}})  # unknown layer
    assert schema.problems({"limit": {}})  # unknown key
    assert schema.problems({"raises": [entry | {"reason": " "}]})
    assert schema.problems({"raises": [{k: v for k, v in entry.items() if k != "to"}]})


def _diagnostic(code: str, message: str, line: int = 3) -> str:
    return (
        f'{{"file": "server\\\\agentos\\\\models.py", "line": {line}, "column": 1, '
        f'"message": "{message}", "hint": null, "code": "{code}", "severity": "error"}}'
    )


def test_only_any_diagnostics_are_budget_and_unreadable_mypy_output_fails() -> None:
    out = "\n".join(
        [
            _diagnostic("misc", 'Expression has type \\"Any\\"'),
            _diagnostic("explicit-any", 'Explicit \\"Any\\" is not allowed', line=4),
            _diagnostic("assignment", "Incompatible types in assignment", line=9),
        ]
    )
    run = mypy_any.parse_mypy(1, out, "")
    assert [line for line, _ in run.errors["backend/server/agentos/models.py"]] == [3, 4]
    assert len(run.problems) == 1  # the assignment error is no budget
    assert mypy_any.parse_mypy(1, "models.py:3:1: error: Expression has type Any", "").problems
    assert mypy_any.parse_mypy(2, "", "Traceback ...\nINTERNAL ERROR").problems
    assert not mypy_any.parse_mypy(0, "", "").problems


def test_a_mypy_override_naming_a_core_module_is_found() -> None:
    pyproject = """
        [[tool.mypy.overrides]]
        module = ["tests.*"]
        disallow_any_expr = false

        [[tool.mypy.overrides]]
        module = "agentos.pipeline.*"
        ignore_errors = true
    """.replace("\n        ", "\n")
    core = ["agentos.models", "agentos.pipeline", "agentos.pipeline.space"]
    assert len(mypy_any.core_overrides(core, pyproject)) == 1
    assert not mypy_any.core_overrides(["agentos.models"], pyproject)


def test_defensive_caps_are_per_file_and_the_boundary_checks_input_freely() -> None:
    caps = {"uncapped": {"boundary": ["try"]}, "defensive": {"a.py": {"try": 2}}}
    baseline = bl.normalise({"allowed": caps})
    m = Measures(layer={"a.py": "shell", "b.py": "shell", "c.py": "boundary"})
    m.defensive = {"a.py": {"try": [1]}, "b.py": {"try": [7]}, "c.py": {"try": [1, 2]}}

    result = results.gate_defensive(m, baseline)

    assert not result.ok  # a.py's removal doesn't pay for b.py's addition
    assert [line.split()[0] for line in result.details if "fix:" in line] == ["b.py"]


def test_suppression_caps_are_per_file() -> None:
    baseline = bl.normalise({"allowed": {"suppressions": {"a.py": 2}}})
    m = Measures()
    m.suppressions = [("a.py", 1, "noqa"), ("b.py", 3, "type: ignore")]  # total 2, as allowed

    result = results.gate_suppressions(m, baseline)

    assert not result.ok
    assert result.details[0].split()[0] == "b.py"


def test_a_python_file_in_no_layer_fails() -> None:
    layers = {"core": ["pkg/*/board.py"], "services": ["pkg/*"]}
    assert common.layer_of("pkg/tickets/board.py", layers) == "core"
    assert common.layer_of("pkg/tickets/service.py", layers) == "services"
    m = Measures(layer={p: common.layer_of(p, layers) for p in ["pkg/a.py", "elsewhere.py"]})

    result = results.gate_layers(m)

    assert not result.ok
    assert [line.split()[0] for line in result.details] == ["elsewhere.py"]


def test_new_raises_are_listed_for_approval() -> None:
    entry = {"date": "2026-10-17", "what": "layers", "from": "a", "to": "b", "reason": "why"}
    base = _baseline(raises=[entry])
    current = _baseline(raises=[entry, entry | {"what": "allowed.suppressions"}])

    lines = gates.to_approve(current, base, "HEAD")

    assert len(lines) == 3  # the heading, then the one new entry on two lines
    assert "allowed.suppressions" in lines[1]
    assert not gates.to_approve(base, base, "HEAD")


def test_the_card_lists_improvements_biggest_gain_first_and_removed_offenders() -> None:
    before = _baseline()
    after = copy.deepcopy(before)
    after["exceptions"]["cognitive"] = {"a.py::worse": 20, "a.py::better": 22}  # fixed: removed
    now = {"exceptions.cognitive.a.py::fixed": 9}

    lines = card.improved(before, after, now)

    assert [line.split("`")[1] for line in lines] == [
        "cognitive  a.py::fixed",
        "cognitive  a.py::better",
    ]
    assert "18 → 9 (removed)" in lines[0]
    assert not card.worse(before, after)
    assert not card.improved(before, before, now)


def test_a_raise_shows_in_the_card_as_worse_and_to_approve() -> None:
    entry = {"date": "2026-10-17", "what": "allowed.suppressions", "from": 2, "to": 3}
    before = _baseline()
    after = copy.deepcopy(before)
    after["allowed"]["suppressions"]["b.py"] = 3
    after["raises"] = [entry | {"reason": "a | in the reason"}]

    out = card.card(before, after, {}, "**Gates pass**")

    worse = card.worse(before, after)
    assert len(worse) == 1
    assert "suppressions  b.py" in worse[0]
    assert "a | in the reason" in worse[0]
    assert "| `allowed.suppressions` | 2 → 3 | a \\| in the reason |" in out
    assert "No change to the gate numbers." not in out
