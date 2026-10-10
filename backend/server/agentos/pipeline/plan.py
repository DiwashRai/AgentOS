"""TASKS.md parsing (SPEC §6, §16 "Structured plan").

Pure. The one function behind `tickets.tasks_done/total` and the board's "now" line; a stricter
plan schema means swapping this function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `- [ ] text`, `* [x] text`, any indentation; the box must be followed by whitespace or the end.
_ITEM_RE = re.compile(r"^[ \t]*[-*][ \t]+\[([ xX])\](?:[ \t]+(.*?))?[ \t]*$")


@dataclass(frozen=True, slots=True)
class Plan:
    done: int
    total: int
    next_item: str | None  # text of the first unticked item that has text


def parse_plan(text: str) -> Plan:
    done = total = 0
    next_item: str | None = None
    for line in text.splitlines():
        m = _ITEM_RE.match(line)
        if not m:
            continue
        total += 1
        if m.group(1) != " ":
            done += 1
        elif next_item is None and m.group(2):
            next_item = m.group(2)
    return Plan(done=done, total=total, next_item=next_item)
