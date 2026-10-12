"""TASKS.md parsing (SPEC §6, §16 "Structured plan").

Pure. The one function behind `tickets.tasks_done/total`, the board's "now" line and the ticket
detail's `plan`; a stricter plan schema means swapping this function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentos.models import PlanItem

# `- [ ] text`, `* [x] text`, any indentation; the box must be followed by whitespace or the end.
_ITEM_RE = re.compile(r"^[ \t]*[-*][ \t]+\[([ xX])\](?:[ \t]+(.*?))?[ \t]*$")
# An ATX heading (CommonMark): up to 3 spaces, 1-6 `#`, then whitespace or the end; an optional
# closing run of `#` (after whitespace) is not part of the text.
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))??(?:[ \t]+#+)?[ \t]*$")


@dataclass(frozen=True, slots=True)
class Plan:
    done: int
    total: int
    next_item: str | None  # text of the first unticked item that has text
    # Every counted checklist line, in order: done == sum(i.done), total == len(items). Each
    # carries the nearest heading above it as its `section`.
    items: tuple[PlanItem, ...]


def parse_plan(text: str) -> Plan:
    items: list[PlanItem] = []
    section: str | None = None
    for line in text.splitlines():
        if heading := _HEADING_RE.match(line):
            section = (heading.group(1) or "").strip() or None
        elif m := _ITEM_RE.match(line):
            items.append(
                PlanItem(text=(m.group(2) or "").strip(), done=m.group(1) != " ", section=section)
            )
    return Plan(
        done=sum(i.done for i in items),
        total=len(items),
        next_item=next((i.text for i in items if not i.done and i.text), None),
        items=tuple(items),
    )
