"""Ticket source plugin interface (SPEC §12). Implementations arrive in M1+."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class SourceTicket:
    external_id: str
    key: str
    title: str
    description: str
    type: str
    priority: str
    labels: list[str]
    url: str


class TicketSource(Protocol):
    name: Literal["jira", "github", "todoist"]

    def parse_ref(self, ref: str) -> str | None:
        """URL or key → external_id, or None if the ref is not for this source."""
        ...

    def fetch(self, external_id: str) -> SourceTicket: ...

    def post_comment(self, external_id: str, body: str) -> str:
        """Returns the external comment id."""
        ...
