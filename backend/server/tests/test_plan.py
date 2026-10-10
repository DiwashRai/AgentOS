"""TASKS.md parsing (SPEC §6, §16 "Structured plan")."""

from __future__ import annotations

import pytest

from agentos.pipeline.plan import Plan, parse_plan


@pytest.mark.parametrize(
    ("text", "plan"),
    [
        ("", Plan(0, 0, None)),
        ("# Tasks\n", Plan(0, 0, None)),
        (
            "# Tasks\n\n- [x] Reproduce\n- [ ] Fix expiry\n- [ ] Add test\n",
            Plan(1, 3, "Fix expiry"),
        ),
        ("- [x] a\n- [X] b\n", Plan(2, 2, None)),
        ("* [ ] star bullet\n", Plan(0, 1, "star bullet")),
        ("- [x] parent\n  - [ ] nested child\n\t- [x] tab\n", Plan(2, 3, "nested child")),
        ("- [ ] crlf item\r\n- [x] done\r\n", Plan(1, 2, "crlf item")),
        ("- [ ]\n- [ ] second\n", Plan(0, 2, "second")),  # empty item counts, has no text
        ("- [ ]   padded text   \n", Plan(0, 1, "padded text")),
        # not task items
        (
            "- [] no space\n- [y] other\n-[ ] no bullet space\n- [x]glued\n1. [ ] numbered\n",
            Plan(0, 0, None),
        ),
        ("text - [ ] mid-line\n> - [ ] quoted\n", Plan(0, 0, None)),
        ("+ [ ] plus bullet\n", Plan(0, 0, None)),
    ],
)
def test_parse_plan(text: str, plan: Plan) -> None:
    assert parse_plan(text) == plan
