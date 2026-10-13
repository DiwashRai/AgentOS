"""Ticket source plugin interface (SPEC §12): intake adapters (Jira today; GitHub Issues later).

A source turns a pasted ref into its external id, fetches the ticket and maps it to AgentOS
values (type, priority). It never touches the db; the tickets service stores what it returns.
Everything it returns is untrusted text (SPEC §1 principle 8): stored as data, never rendered as
anything but text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from agentos.models import Priority, TicketType


@dataclass(frozen=True, slots=True)
class SourceTicket:
    """A fetched ticket. `external_id` = what `SourceLink.external_id` stores (Jira: the issue
    key, which also becomes the ticket key); `project` = the source's project/namespace."""

    external_id: str
    key: str
    project: str
    title: str
    description: str
    type: TicketType
    priority: Priority
    labels: list[str]
    url: str
    # The values of the extra fields `fetch` was asked for (request 22B), by field id, as plain
    # strings (none = an empty list).
    field_values: Mapping[str, list[str]] = field(default_factory=dict)


class SourceError(Exception):
    """Base class for source failures. Messages never carry credentials."""


class SourceNotFound(SourceError):
    """The source can't see the ticket (unknown, or hidden from the service account)."""


class SourceUnauthorized(SourceError):
    """The source rejected the service account's credentials."""


class SourceUnavailable(SourceError):
    """Network error, timeout, 5xx or an unexpected response."""


class TicketSource(Protocol):
    @property
    def name(self) -> Literal["jira", "github", "todoist"]: ...

    def parse_ref(self, ref: str) -> str | None:
        """URL or key → external_id, or None if the ref is not for this source."""
        ...

    def fetch(self, external_id: str, extra_fields: Sequence[str] = ()) -> SourceTicket:
        """`extra_fields`: source field ids to read too, into `SourceTicket.field_values` (the
        field mappings). SourceNotFound / SourceUnauthorized / SourceUnavailable on failure."""
        ...

    def post_comment(self, external_id: str, body: str) -> str:
        """Returns the external comment id."""
        ...
