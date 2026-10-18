"""Why a JSON value doesn't fit our types: `Unfit` is the answer `parse` returns, `Misfit`
the error a record raises for a rule between its fields.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Unfit:
    """Why a JSON value doesn't fit our type: where (a path such as `stages[1].argv`), then why."""

    why: str


class Misfit(ValueError):
    """A record that breaks a rule between its fields: `field` is the one named in the path."""

    def __init__(self, field: str, why: str) -> None:
        super().__init__(why)
        self.field = field
