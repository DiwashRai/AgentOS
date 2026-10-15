"""The test Space, and changes to the Space's settings straight in the db (bypassing the API's
checks) as an admin save would; the next `ctx.space` read sees the change."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentos.config import Settings
from agentos.context import AppContext
from agentos.db import Database
from agentos.models import IntakeQuerySpec
from agentos.pipeline.runtime import NamedRuntime
from agentos.pipeline.space import RepoSpec, RunnerSection, SpaceConfig, SpaceSection
from agentos.space_store import (
    AREAS,
    Area,
    mappings_value,
    read_area,
    runner_value,
    runtimes_value,
    seed,
    space_values,
    write_area,
)

# New tickets get the repo `r` unless they pass `repo` (null = no repo).
TEST_SPACE = SpaceConfig(
    space=SpaceSection(name="Test Space", default_repo="r"),
    repos=(
        RepoSpec(name="r", url="https://github.com/o/r.git"),
        RepoSpec(name="docs", url="git@github.com:o/docs.git", default_branch="develop"),
    ),
)

# In Settings order: `fake` is the server default.
TEST_RUNTIMES: tuple[NamedRuntime, ...] = (
    NamedRuntime(
        name="fake",
        argv=["python", "fake_agent.py"],
        model_args=["--model", "{model}"],
        prompt_via="stdin",
    ),
    NamedRuntime(
        name="claude",
        argv=["claude", "-p", "--output-format", "stream-json", "--verbose"],
        model_args=["--model", "{model}"],
        agent_args=["--agent", "{agent}"],
        prompt_via="stdin",
    ),
)


def seed_test_settings(
    settings: Settings,
    space: SpaceConfig = TEST_SPACE,
    runtimes: Sequence[NamedRuntime] = TEST_RUNTIMES,
) -> None:
    """Migrate the data dir's db and write every settings area as a first start's seed does
    (version 1, updated_by NULL): `space` and `runtimes`, the default runner and no mappings.
    Call it before the app starts, so its startup finds every area present and seeds none."""
    db = Database(settings.db_path)
    db.migrate()

    values: dict[Area, dict[str, Any]] = {
        **space_values(space),
        "runtimes": runtimes_value(list(runtimes)),
        "runner": runner_value(RunnerSection()),
        "mappings": mappings_value([]),
    }
    assert seed(db, values) == list(AREAS), "the db already had settings"


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
