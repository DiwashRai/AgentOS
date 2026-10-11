"""The Space's settings in the db: the `settings` table (migration 0006), one JSON document per
area.

Areas: `space` (a SpaceSection), `repos` ({"repos": [RepoSpec...]}), `intake`
({"queries": [IntakeQuerySpec...]}). Each save overwrites its row and bumps `version`; no history.

`SpaceStore` is the read side every feature uses (through `AppContext.space`): the current rows as
one `SpaceConfig`, cached in-process and dropped on every save, so a change applies without a
restart. Correct within one server process (the only deployment shape).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from agentos.clock import from_db, to_db, utcnow
from agentos.db import Conn, Database
from agentos.models import IntakeQuerySpec, Person
from agentos.pipeline.space import IntakeSection, RepoSpec, SpaceConfig, SpaceSection

Area = Literal["space", "repos", "intake"]
AREAS: tuple[Area, ...] = ("space", "repos", "intake")


@dataclass(frozen=True, slots=True)
class AreaRow:
    value: dict[str, Any]
    version: int
    updated_at: datetime
    updated_by: Person | None  # None = seeded


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


def area_value(space: SpaceConfig, area: Area) -> dict[str, Any]:
    """The JSON document an area stores, from a SpaceConfig."""
    if area == "space":
        return space.space.model_dump(mode="json")
    if area == "repos":
        return {"repos": [r.model_dump(mode="json") for r in space.repos]}
    return {"queries": [q.model_dump(mode="json") for q in space.intake.queries]}


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


def load_space(conn: Conn) -> SpaceConfig:
    """The current settings as one SpaceConfig (an area without a row: its defaults)."""
    return SpaceConfig(
        space=section_of(read_area(conn, "space")),
        repos=repos_of(read_area(conn, "repos")),
        intake=IntakeSection(queries=queries_of(read_area(conn, "intake"))),
    )


def missing_areas(db: Database) -> list[Area]:
    with db.read() as conn:
        rows = conn.execute("SELECT area FROM settings").fetchall()
    present = {r["area"] for r in rows}
    return [a for a in AREAS if a not in present]


def seed(db: Database, space: SpaceConfig, areas: Collection[Area]) -> list[Area]:
    """Insert version 1 (updated_by NULL) of each of `areas` that has no row yet, from `space`, in
    one transaction. Idempotent. Returns the areas it inserted."""
    seeded: list[Area] = []
    now = to_db(utcnow())
    with db.tx() as conn:
        for area in AREAS:
            if area not in areas:
                continue
            cursor = conn.execute(
                "INSERT INTO settings(area, value, version, updated_at, updated_by) "
                "VALUES (?, ?, 1, ?, NULL) ON CONFLICT DO NOTHING",
                (area, json.dumps(area_value(space, area)), now),
            )
            if cursor.rowcount == 1:
                seeded.append(area)
    return seeded


class SpaceStore:
    """The current SpaceConfig, read from the db once and cached until `invalidate()`.

    Thread-safe (sync routes and the loops run in threads). A read that raced a save never caches
    the older value: each invalidation bumps a generation, and a load only caches if none happened
    while it read.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._cached: SpaceConfig | None = None
        self._generation = 0

    def current(self) -> SpaceConfig:
        with self._lock:
            if self._cached is not None:
                return self._cached
            generation = self._generation
        with self._db.read() as conn:
            space = load_space(conn)
        with self._lock:
            if generation == self._generation:
                self._cached = space
        return space

    def invalidate(self) -> None:
        """Call after a save's transaction committed."""
        with self._lock:
            self._generation += 1
            self._cached = None
