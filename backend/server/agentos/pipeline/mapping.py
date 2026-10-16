"""Jira field mappings: the matcher that turns a field's values into a target value
through admin-defined rules, and the per-target checks a save runs.

Pure: the import, the Settings re-apply and the preview pass the rules and values in, so all three
match the same way and the UI never matches patterns itself.

A rule's `match` is a wildcard pattern, not a regex and not fnmatch: `*` is any run of characters
(also none) and everything else is literal (`?`, `[`, `.` included). It must match the whole
value, case-insensitively, both sides trimmed. Rules are ordered; the first match wins per value.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from agentos.models import (
    MAX_MAPPINGS,
    FieldMapping,
    FieldMappingEntry,
    MappingRule,
    MappingsDoc,
    MappingTarget,
    ValidationIssue,
)
from agentos.validate import base_branch_problem, parsed, too_many

# How a mapping's raw values are stored (`tickets.base_branch_source_value`) and read back.
VALUE_SEPARATOR = ", "


@dataclass(frozen=True, slots=True)
class MappingResult:
    """`value`: the target's value, None = blank; `rule`: the index of the rule that gave it;
    `reason`: why it is blank (None when set)."""

    value: str | None
    rule: int | None
    reason: str | None


def pattern(match: str) -> re.Pattern[str]:
    """A rule's `match` as a compiled whole-value, case-insensitive regex (`*` = `.*`)."""
    parts = (re.escape(part) for part in match.strip().split("*"))
    return re.compile(".*".join(parts), re.IGNORECASE | re.DOTALL)


def first_rule(rules: Sequence[MappingRule], value: str) -> int | None:
    """The index of the first rule whose `match` matches `value` (trimmed), or None."""
    value = value.strip()
    for i, rule in enumerate(rules):
        if pattern(rule.match).fullmatch(value):
            return i
    return None


def clean_values(values: Sequence[str]) -> list[str]:
    """Trimmed, blanks dropped, repeats dropped (first kept), in order."""
    out: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


def apply_rules(
    rules: Sequence[MappingRule], values: Sequence[str], field_name: str
) -> MappingResult:
    """Map each value. All matches give one value (or only one value matches) → that value and
    the first matching value's rule; matches disagree → blank; nothing matches → blank; no
    values → blank ("<field_name> is empty in Jira")."""
    cleaned = clean_values(values)
    if not cleaned:
        return MappingResult(None, None, f"{field_name} is empty in Jira")
    matched: list[tuple[str, int]] = []  # (value, rule index)
    for value in cleaned:
        index = first_rule(rules, value)
        if index is not None:
            matched.append((value, index))
    if not matched:
        return MappingResult(None, None, f"No rule matches {VALUE_SEPARATOR.join(cleaned)}")
    results = {rules[i].value for _, i in matched}
    if len(results) > 1:
        names = _and([value for value, _ in matched])
        return MappingResult(None, None, f"{names} map to different branches")
    value, index = matched[0]
    return MappingResult(rules[index].value, index, None)


def join_values(values: Sequence[str]) -> str | None:
    """The values as stored in `base_branch_source_value` (None when there are none)."""
    cleaned = clean_values(values)
    return VALUE_SEPARATOR.join(cleaned) if cleaned else None


def split_values(stored: str | None) -> list[str]:
    """`join_values` read back. A value that itself contains ", " splits into two here (an
    accepted limit: release names don't contain one)."""
    return clean_values(stored.split(VALUE_SEPARATOR)) if stored else []


def mapping_for(mappings: Sequence[FieldMapping], target: MappingTarget) -> FieldMapping | None:
    return next((m for m in mappings if m.target == target), None)


def check_mappings(
    doc: MappingsDoc, prefix: Sequence[str | int] = ("value",)
) -> tuple[tuple[FieldMapping, ...] | None, list[ValidationIssue]]:
    """The field mappings document, every problem at `<prefix>.mappings.<i>...`: each mapping as
    `check_mapping` checks it, and at most one mapping per target. Answers the normalised mappings
    (trimmed), or None when there is any issue."""
    issues = too_many(doc.mappings, MAX_MAPPINGS, (*prefix, "mappings"), "mappings")
    mappings: list[FieldMapping] = []
    seen: set[str] = set()
    for i, entry in enumerate(doc.mappings):
        if entry.target in seen:
            loc: list[str | int] = [*prefix, "mappings", i, "target"]
            issues.append(ValidationIssue(loc=loc, message="This target has a mapping already."))
        seen.add(entry.target)

        mapping, problems = check_mapping(entry, (*prefix, "mappings", i))
        issues += problems
        if mapping is not None:
            mappings.append(mapping)

    return (None if issues else tuple(mappings)), issues


def check_mapping(
    entry: FieldMappingEntry, prefix: Sequence[str | int] = ("mapping",)
) -> tuple[FieldMapping | None, list[ValidationIssue]]:
    """One mapping, every problem at `<prefix>...`: its fields and rules (`FieldMapping`), and each
    rule's `value` valid for the target (checked even when another field fails)."""
    mapping, issues = parsed(FieldMapping, entry.model_dump(), prefix)

    reported = {tuple(issue.loc) for issue in issues}
    for j, rule in enumerate(entry.rules):
        loc: list[str | int] = [*prefix, "rules", j, "value"]
        if tuple(loc) in reported:
            continue
        if entry.target == "base_branch" and (problem := base_branch_problem(rule.value.strip())):
            issues.append(ValidationIssue(loc=loc, message=problem))

    return (None if issues else mapping), issues


def _and(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    if len(items) <= 1:
        return "".join(items)
    return f"{VALUE_SEPARATOR.join(items[:-1])} and {items[-1]}"
