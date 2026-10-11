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


@dataclass(frozen=True, slots=True)
class Plan:
    done: int
    total: int
    next_item: str | None  # text of the first unticked item that has text
    # Every counted checklist line, in order: done == sum(i.done), total == len(items).
    items: tuple[PlanItem, ...]


def parse_plan(text: str) -> Plan:
    items: list[PlanItem] = []
    for line in text.splitlines():
        m = _ITEM_RE.match(line)
        if m:
            items.append(PlanItem(text=(m.group(2) or "").strip(), done=m.group(1) != " "))
    return Plan(
        done=sum(i.done for i in items),
        total=len(items),
        next_item=next((i.text for i in items if not i.done and i.text), None),
        items=tuple(items),
    )
