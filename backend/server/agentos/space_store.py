"""The Space's settings in the db: the `settings` table (migration 0006), one JSON document per
area.

Areas: `space` (a SpaceSection), `repos` ({"repos": [RepoSpec...]}), `intake`
({"queries": [IntakeQuerySpec...]}), `runtimes` ({"runtimes": [NamedRuntime...]}, in order: the
first is the server default), `runner` (a RunnerSection) and `mappings` ({"mappings":
[FieldMapping...]}, request 22B). Each save overwrites its row and bumps `version`; no history.

`SpaceStore` is the read side every feature uses (through `AppContext.space` / `.runtimes` /
`.runner` / `.mappings`): the current rows as one `Snapshot`, cached in-process and dropped on
every save, so a
change applies without a restart. Correct within one server process (the only deployment shape).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from agentos.clock import from_db, to_db, utcnow
from agentos.db import Conn, Database
from agentos.models import FieldMapping, IntakeQuerySpec, Person
from agentos.pipeline.runtime import BUILTIN_RUNTIMES, NamedRuntime, RuntimesConfig
from agentos.pipeline.space import (
    IntakeSection,
    RepoSpec,
    RunnerSection,
    SpaceConfig,
    SpaceSection,
)

Area = Literal["space", "repos", "intake", "runtimes", "runner", "mappings"]
AREAS: tuple[Area, ...] = ("space", "repos", "intake", "runtimes", "runner", "mappings")


@dataclass(frozen=True, slots=True)
class AreaRow:
    value: dict[str, Any]
    version: int
    updated_at: datetime
    updated_by: Person | None  # None = seeded


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Every area as currently saved, read in one transaction."""

    space: SpaceConfig
    runtimes: RuntimesConfig
    runner: RunnerSection
    mappings: tuple[FieldMapping, ...]


def read_area(conn: Conn, area: Area) -> AreaRow | None:
    row = conn.execute(
        "SELECT s.value, s.version, s.updated_at, s.updated_by, u.display_name, u.avatar_url "
        "FROM settings s LEFT JOIN users u ON u.id = s.updated_by WHERE s.area = ?",
        (area,),
    ).fetchone()
    if row is None:
        return None
    by = None
    if row["updated_by"] is not None:
        by = Person(
            id=row["updated_by"], display_name=row["display_name"], avatar_url=row["avatar_url"]
        )
    value: dict[str, Any] = json.loads(row["value"])
    return AreaRow(
        value=value, version=row["version"], updated_at=from_db(row["updated_at"]), updated_by=by
    )


def write_area(
    conn: Conn, area: Area, value: dict[str, Any], *, version: int, actor_id: int | None
) -> None:
    """Store `value` as the area's document at `version` (the caller checked the base version)."""
    conn.execute(
        "INSERT INTO settings(area, value, version, updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(area) DO UPDATE SET value = excluded.value, version = excluded.version, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (area, json.dumps(value), version, to_db(utcnow()), actor_id),
    )


# ------------------------------------------------------------------ documents ↔ models


def space_values(space: SpaceConfig) -> dict[Area, dict[str, Any]]:
    """The `space`, `repos` and `intake` documents of a SpaceConfig."""
    return {
        "space": space.space.model_dump(mode="json"),
        "repos": {"repos": [r.model_dump(mode="json") for r in space.repos]},
        "intake": {"queries": [q.model_dump(mode="json") for q in space.intake.queries]},
    }


def runtimes_value(entries: list[NamedRuntime]) -> dict[str, Any]:
    return {"runtimes": [e.model_dump(mode="json") for e in entries]}


def runner_value(runner: RunnerSection) -> dict[str, Any]:
    return runner.model_dump(mode="json")


def repos_of(row: AreaRow | None) -> tuple[RepoSpec, ...]:
    if row is None:
        return ()
    return tuple(RepoSpec.model_validate(r) for r in row.value.get("repos", []))


def queries_of(row: AreaRow | None) -> tuple[IntakeQuerySpec, ...]:
    if row is None:
        return ()
    return tuple(IntakeQuerySpec.model_validate(q) for q in row.value.get("queries", []))


def section_of(row: AreaRow | None) -> SpaceSection:
    return SpaceSection() if row is None else SpaceSection.model_validate(row.value)


def runtime_entries_of(row: AreaRow | None) -> list[NamedRuntime]:
    """The runtimes in Settings order (no row: the built-ins)."""
    if row is None:
        return list(BUILTIN_RUNTIMES)
    return [NamedRuntime.model_validate(r) for r in row.value.get("runtimes", [])]


def runtimes_of(row: AreaRow | None) -> RuntimesConfig:
    return RuntimesConfig.from_entries(runtime_entries_of(row))


def runner_of(row: AreaRow | None) -> RunnerSection:
    return RunnerSection() if row is None else RunnerSection.model_validate(row.value)


def mappings_value(mappings: list[FieldMapping]) -> dict[str, Any]:
    return {"mappings": [m.model_dump(mode="json") for m in mappings]}


def mappings_of(row: AreaRow | None) -> tuple[FieldMapping, ...]:
    if row is None:
        return ()
    return tuple(FieldMapping.model_validate(m) for m in row.value.get("mappings", []))


def load_space(conn: Conn) -> SpaceConfig:
    """The current settings as one SpaceConfig (an area without a row: its defaults)."""
    return SpaceConfig(
        space=section_of(read_area(conn, "space")),
        repos=repos_of(read_area(conn, "repos")),
        intake=IntakeSection(queries=queries_of(read_area(conn, "intake"))),
    )


def load_snapshot(conn: Conn) -> Snapshot:
    return Snapshot(
        space=load_space(conn),
        runtimes=runtimes_of(read_area(conn, "runtimes")),
        runner=runner_of(read_area(conn, "runner")),
        mappings=mappings_of(read_area(conn, "mappings")),
    )


# ------------------------------------------------------------------ the first-start seed


def missing_areas(db: Database) -> list[Area]:
    with db.read() as conn:
        rows = conn.execute("SELECT area FROM settings").fetchall()
    present = {r["area"] for r in rows}
    return [a for a in AREAS if a not in present]


def seed(db: Database, values: Mapping[Area, dict[str, Any]]) -> list[Area]:
    """Insert version 1 (updated_by NULL) of each area in `values` that has no row yet, in one
    transaction. Idempotent. Returns the areas it inserted."""
    seeded: list[Area] = []
    now = to_db(utcnow())
    with db.tx() as conn:
        for area in AREAS:
            if area not in values:
                continue
            cursor = conn.execute(
                "INSERT INTO settings(area, value, version, updated_at, updated_by) "
                "VALUES (?, ?, 1, ?, NULL) ON CONFLICT DO NOTHING",
                (area, json.dumps(values[area]), now),
            )
            if cursor.rowcount == 1:
                seeded.append(area)
    return seeded


# ------------------------------------------------------------------ the cached read side


class SpaceStore:
    """The current settings Snapshot, read from the db once and cached until `invalidate()`.

    Thread-safe (sync routes and the loops run in threads). A read that raced a save never caches
    the older value: each invalidation bumps a generation, and a load only caches if none happened
    while it read.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._cached: Snapshot | None = None
        self._generation = 0

    def snapshot(self) -> Snapshot:
        with self._lock:
            if self._cached is not None:
                return self._cached
            generation = self._generation
        with self._db.read() as conn:
            snapshot = load_snapshot(conn)
        with self._lock:
            if generation == self._generation:
                self._cached = snapshot
        return snapshot

    def current(self) -> SpaceConfig:
        """The Space's settings, repo catalog and intake queries."""
        return self.snapshot().space

    def runtimes(self) -> RuntimesConfig:
        return self.snapshot().runtimes

    def runner(self) -> RunnerSection:
        return self.snapshot().runner

    def mappings(self) -> tuple[FieldMapping, ...]:
        return self.snapshot().mappings

    def invalidate(self) -> None:
        """Call after a save's transaction committed."""
        with self._lock:
            self._generation += 1
            self._cached = None
