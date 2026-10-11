"""Change the Space's settings straight in the db (bypassing the API's checks), as a first-start
seed or an admin save would; the next `ctx.space` read sees the change."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentos.context import AppContext
from agentos.models import IntakeQuerySpec
from agentos.pipeline.space import RepoSpec, SpaceSection
from agentos.space_store import Area, read_area, write_area


def _write(ctx: AppContext, area: Area, value: dict[str, Any]) -> None:
    with ctx.db.tx() as conn:
        row = read_area(conn, area)
        write_area(conn, area, value, version=row.version + 1 if row else 1, actor_id=None)
    ctx.space_store.invalidate()


def set_space(ctx: AppContext, **changes: Any) -> None:
    """Update some SpaceSection fields (validated by SpaceSection)."""
    section = SpaceSection.model_validate({**ctx.space.space.model_dump(), **changes})
    _write(ctx, "space", section.model_dump(mode="json"))


def set_queries(ctx: AppContext, queries: Sequence[IntakeQuerySpec]) -> None:
    _write(ctx, "intake", {"queries": [q.model_dump(mode="json") for q in queries]})


def set_repos(ctx: AppContext, repos: Sequence[RepoSpec]) -> None:
    _write(ctx, "repos", {"repos": [r.model_dump(mode="json") for r in repos]})
