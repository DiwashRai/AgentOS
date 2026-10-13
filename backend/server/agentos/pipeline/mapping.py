"""Jira field mappings (request 22B): the matcher that turns a field's values into a target value
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

from agentos.errors import ApiError
from agentos.models import FieldMapping, MappingRule, MappingTarget
from agentos.validate import validate_base_branch

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


def check_mappings(mappings: Sequence[FieldMapping]) -> None:
    """The checks pydantic can't do: at most one mapping per target, and each rule's `value` is
    valid for its target. 422 `validation_error` naming the mapping and rule index."""
    seen: set[str] = set()
    for i, mapping in enumerate(mappings):
        if mapping.target in seen:
            raise ApiError(
                422,
                "validation_error",
                f"mappings.{i}.target: {mapping.target!r} has a mapping already",
            )
        seen.add(mapping.target)
        check_mapping(mapping, f"mappings.{i}")


def check_mapping(mapping: FieldMapping, prefix: str = "mapping") -> None:
    for j, rule in enumerate(mapping.rules):
        try:
            _check_value(mapping.target, rule.value)
        except ApiError as exc:
            message = exc.message.split(": ", 1)[-1]
            raise ApiError(
                422, "validation_error", f"{prefix}.rules.{j}.value: {message}"
            ) from None


def _check_value(target: MappingTarget, value: str) -> None:
    if target == "base_branch":
        validate_base_branch(value)


def _and(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    if len(items) <= 1:
        return "".join(items)
    return f"{VALUE_SEPARATOR.join(items[:-1])} and {items[-1]}"
