"""TASKS.md parsing (SPEC §6, §16 "Structured plan")."""

from __future__ import annotations

import pytest

from agentos.models import PlanItem
from agentos.pipeline.plan import Plan, parse_plan


def plan(done: int, total: int, next_item: str | None, *items: tuple[str, bool]) -> Plan:
    return Plan(done, total, next_item, tuple(PlanItem(text=t, done=d) for t, d in items))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", plan(0, 0, None)),
        ("# Tasks\n", plan(0, 0, None)),
        (
            "# Tasks\n\n- [x] Reproduce\n- [ ] Fix expiry\n- [ ] Add test\n",
            plan(
                1, 3, "Fix expiry", ("Reproduce", True), ("Fix expiry", False), ("Add test", False)
            ),
        ),
        ("- [x] a\n- [X] b\n", plan(2, 2, None, ("a", True), ("b", True))),
        ("* [ ] star bullet\n", plan(0, 1, "star bullet", ("star bullet", False))),
        (
            "- [x] parent\n  - [ ] nested child\n\t- [x] tab\n",
            plan(2, 3, "nested child", ("parent", True), ("nested child", False), ("tab", True)),
        ),
        (
            "- [ ] crlf item\r\n- [x] done\r\n",
            plan(1, 2, "crlf item", ("crlf item", False), ("done", True)),
        ),
        # an empty item counts and has no text
        ("- [ ]\n- [ ] second\n", plan(0, 2, "second", ("", False), ("second", False))),
        ("- [ ]   padded text   \n", plan(0, 1, "padded text", ("padded text", False))),
        # not task items
        (
            "- [] no space\n- [y] other\n-[ ] no bullet space\n- [x]glued\n1. [ ] numbered\n",
            plan(0, 0, None),
        ),
        ("text - [ ] mid-line\n> - [ ] quoted\n", plan(0, 0, None)),
        ("+ [ ] plus bullet\n", plan(0, 0, None)),
    ],
)
def test_parse_plan(text: str, expected: Plan) -> None:
    got = parse_plan(text)
    assert got == expected
    assert (got.done, got.total) == (sum(i.done for i in got.items), len(got.items))
