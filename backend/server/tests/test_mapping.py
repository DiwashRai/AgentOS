"""The Jira field mapping matcher (request 22B, `pipeline/mapping.py`) and the Jira value
normaliser (`sources/jira.field_values`): pure tables."""

from __future__ import annotations

from typing import Any

import pytest

from agentos.errors import ApiError
from agentos.models import FieldMapping, MappingRule
from agentos.pipeline.mapping import (
    MappingResult,
    apply_rules,
    check_mappings,
    first_rule,
    join_values,
    split_values,
)
from agentos.sources.jira import field_values


def rules(*pairs: tuple[str, str]) -> list[MappingRule]:
    return [MappingRule(match=m, value=v) for m, v in pairs]


RELEASES = rules(("version_1*", "App-1.Latest"), ("version_2*", "App-2.Latest"))


@pytest.mark.parametrize(
    ("match", "value", "hit"),
    [
        ("version_1*", "version_1.0", True),
        ("version_1*", "version_1", True),  # `*` may be empty
        ("version_1*", "version_10.2", True),
        ("version_1*", "aversion_1.0", False),  # the whole value
        ("version_1.0", "version_1x0", False),  # `.` is literal
        ("version_1.0", "version_1.0", True),
        ("*1.0", "version_1.0", True),
        ("v*_*", "v1_2", True),
        ("v*_*", "v12", False),
        ("VERSION_1*", "version_1.0", True),  # case-insensitive
        ("version_1*", "Version_1.0", True),
        ("  version_1*  ", "  version_1.0 ", True),  # both sides trimmed
        ("r?", "r1", False),  # `?` is literal (no fnmatch)
        ("r?", "r?", True),
        ("[ab]", "a", False),  # brackets are literal
        ("[ab]", "[AB]", True),
        ("a+b", "aab", False),  # regex metacharacters are literal
        ("a+b", "a+b", True),
        ("*", "anything at all", True),
        ("x", "x\ny", False),
    ],
)
def test_wildcard_match(match: str, value: str, hit: bool) -> None:
    assert (first_rule(rules((match, "b")), value) == 0) is hit


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["version_1.0"], MappingResult("App-1.Latest", 0, None)),
        (["version_2.3"], MappingResult("App-2.Latest", 1, None)),
        ([" version_1.0 "], MappingResult("App-1.Latest", 0, None)),
        # Several values that agree, or only one that matches.
        (["version_1.0", "version_1.2"], MappingResult("App-1.Latest", 0, None)),
        (["version_3.0", "version_2.0"], MappingResult("App-2.Latest", 1, None)),
        # Disagreeing matches.
        (
            ["version_1.2", "version_2.0"],
            MappingResult(None, None, "version_1.2 and version_2.0 map to different branches"),
        ),
        (
            ["version_1.0", "version_1.1", "version_2.0", "x"],
            MappingResult(
                None,
                None,
                "version_1.0, version_1.1 and version_2.0 map to different branches",
            ),
        ),
        # Nothing matches; no values.
        (["version_3.0"], MappingResult(None, None, "No rule matches version_3.0")),
        (["a", "b"], MappingResult(None, None, "No rule matches a, b")),
        ([], MappingResult(None, None, "Fix versions is empty in Jira")),
        (["  ", ""], MappingResult(None, None, "Fix versions is empty in Jira")),
    ],
)
def test_apply_rules(values: list[str], expected: MappingResult) -> None:
    assert apply_rules(RELEASES, values, "Fix versions") == expected


def test_the_first_matching_rule_wins() -> None:
    ordered = rules(("version_1.0", "Hotfix-1.0"), ("version_1*", "App-1.Latest"))
    assert apply_rules(ordered, ["version_1.0"], "F") == MappingResult("Hotfix-1.0", 0, None)
    assert apply_rules(ordered, ["version_1.1"], "F") == MappingResult("App-1.Latest", 1, None)
    # Two values on different rules that give the same branch agree (the first value's rule).
    same = rules(("a*", "B"), ("c*", "B"))
    assert apply_rules(same, ["c1", "a1"], "F") == MappingResult("B", 1, None)


def test_no_rules_match_nothing() -> None:
    assert apply_rules([], ["x"], "F") == MappingResult(None, None, "No rule matches x")


def test_values_are_stored_joined_and_read_back() -> None:
    assert join_values([" version_1.0", "version_1.2", "version_1.0", ""]) == (
        "version_1.0, version_1.2"
    )
    assert join_values([]) is None
    assert split_values("version_1.0, version_1.2") == ["version_1.0", "version_1.2"]
    assert split_values(None) == []


def mapping(*pairs: tuple[str, str], target: str = "base_branch") -> FieldMapping:
    return FieldMapping.model_validate(
        {
            "target": target,
            "source": "jira",
            "field": "fixVersions",
            "field_name": "Fix versions",
            "rules": [{"match": m, "value": v} for m, v in pairs],
        }
    )


def test_check_mappings() -> None:
    check_mappings([mapping(("version_1*", "App-1.Latest"))])
    with pytest.raises(ApiError) as bad_value:
        check_mappings([mapping(("a", "ok"), ("b", "bad..branch"))])
    assert (bad_value.value.status, bad_value.value.code) == (422, "validation_error")
    assert bad_value.value.message.startswith("mappings.0.rules.1.value: ")
    with pytest.raises(ApiError) as twice:
        check_mappings([mapping(("a", "x")), mapping(("b", "y"))])
    assert twice.value.message.startswith("mappings.1.target: ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("version_1.0", ["version_1.0"]),
        ("  padded ", ["padded"]),
        ("", []),
        (None, []),
        (42, []),
        (True, []),
        ({"name": "version_1.0", "id": "10001"}, ["version_1.0"]),  # a version
        ({"value": "Blue", "id": "3"}, ["Blue"]),  # a select option
        ({"name": "", "value": "Blue"}, ["Blue"]),
        ({"id": "3"}, []),
        (
            [{"name": "version_1.0", "released": False}, {"name": "version_1.2"}],
            ["version_1.0", "version_1.2"],
        ),  # Cloud fixVersions
        ([{"value": "A"}, "b", None, 3, {"x": 1}, ["nested"]], ["A", "b"]),
        ([], []),
    ],
)
def test_field_values(raw: Any, expected: list[str]) -> None:
    assert field_values(raw) == expected
