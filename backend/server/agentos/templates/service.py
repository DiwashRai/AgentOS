"""The default template (SPEC §4, §15): append-only versions, current = the max version.

Copied into a ticket at creation (`tickets.template_id` records the exact version) and never
linked afterwards, so a template edit only affects tickets created after it.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass

from pydantic import TypeAdapter

from agentos.clock import to_db, utcnow
from agentos.db import Conn, Database
from agentos.errors import ApiError
from agentos.models import FileEntry, Me, Pipeline, Template, TemplateUpdate
from agentos.pipeline.render import check_scaffold
from agentos.pipeline.scaffold import DEFAULT_SCAFFOLD, default_pipeline
from agentos.pipeline.segment import validate_pipeline

DEFAULT_NAME = "default"
_SCAFFOLD = TypeAdapter(list[FileEntry])


@dataclass(frozen=True, slots=True)
class TemplateRow:
    id: int
    template: Template


def _scaffold_json(entries: list[FileEntry]) -> str:
    return json.dumps([e.model_dump() for e in entries])


def seed_default(db: Database) -> None:
    """Startup: insert version 1 of the default template unless it exists. Idempotent."""
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO templates(name, version, pipeline, scaffold, created_at, created_by) "
            "VALUES (?, 1, ?, ?, ?, NULL) ON CONFLICT DO NOTHING",
            (
                DEFAULT_NAME,
                default_pipeline().model_dump_json(),
                _scaffold_json(DEFAULT_SCAFFOLD),
                to_db(utcnow()),
            ),
        )


def current(conn: Conn) -> TemplateRow:
    """The current default template. Seeded at startup, so a missing row is a server bug."""
    row = conn.execute(
        "SELECT id, version, pipeline, scaffold FROM templates WHERE name = ? "
        "ORDER BY version DESC LIMIT 1",
        (DEFAULT_NAME,),
    ).fetchone()
    if row is None:
        raise RuntimeError("the default template is missing (seed_default did not run)")
    return TemplateRow(
        id=row["id"],
        template=Template(
            pipeline=Pipeline.model_validate_json(row["pipeline"]),
            scaffold=_SCAFFOLD.validate_json(row["scaffold"]),
            version=row["version"],
        ),
    )


def get_template(db: Database) -> Template:
    with db.read() as conn:
        return current(conn).template


def put_template(
    db: Database, runtimes: Collection[str], actor: Me, body: TemplateUpdate
) -> Template:
    """Append version+1. 409 `stale_version` unless `base_version` is the current version.
    The checks raise 422 `invalid_pipeline` / `render_error` / `validation_error` or 413."""
    validate_pipeline(body.pipeline, runtimes=runtimes)
    check_scaffold(body.scaffold)
    with db.tx() as conn:
        version = current(conn).template.version
        if body.base_version != version:
            raise ApiError(409, "stale_version", "The template was changed by someone else")
        conn.execute(
            "INSERT INTO templates(name, version, pipeline, scaffold, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                DEFAULT_NAME,
                version + 1,
                body.pipeline.model_dump_json(),
                _scaffold_json(body.scaffold),
                to_db(utcnow()),
                actor.id,
            ),
        )
        return current(conn).template
