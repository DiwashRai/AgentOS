"""The default template (SPEC §4, §15): append-only versions, current = the max version.

Its scaffold is rendered into a ticket at creation (`tickets.template_id` records the exact
version) and never linked afterwards, so a template edit only affects tickets created after it.
The exception is `defaults` (the Space's default runtime/model/effort): it is read from the
current version whenever a run's stages are resolved, so a change reaches every ticket whose
stages leave them null.

A ticket's pipeline comes from a workflow (request 23B, `agentos.workflows`). The `pipeline`
column stays: the startup seed fills it and `legacy_pipeline` hands it to the "Standard" workflow
seed; a template save copies it over unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import TypeAdapter

from agentos.clock import to_db, utcnow
from agentos.db import Conn, Database
from agentos.errors import ApiError
from agentos.models import FileEntry, Me, Pipeline, Template, TemplateDefaults, TemplateUpdate
from agentos.pipeline.render import check_scaffold
from agentos.pipeline.scaffold import DEFAULT_SCAFFOLD, default_pipeline
from agentos.space_store import read_area, runtime_entries_of

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
        "SELECT id, version, pipeline, scaffold, defaults FROM templates WHERE name = ? "
        "ORDER BY version DESC LIMIT 1",
        (DEFAULT_NAME,),
    ).fetchone()
    if row is None:
        raise RuntimeError("the default template is missing (seed_default did not run)")
    return TemplateRow(
        id=row["id"],
        template=Template(
            scaffold=_SCAFFOLD.validate_json(row["scaffold"]),
            defaults=TemplateDefaults.model_validate_json(row["defaults"]),
            version=row["version"],
        ),
    )


def legacy_pipeline(conn: Conn) -> Pipeline:
    """The current version's stored pipeline: what tickets copied before workflows, and what the
    "Standard" workflow is seeded from."""
    row = conn.execute(
        "SELECT pipeline FROM templates WHERE name = ? ORDER BY version DESC LIMIT 1",
        (DEFAULT_NAME,),
    ).fetchone()
    if row is None:
        raise RuntimeError("the default template is missing (seed_default did not run)")
    return Pipeline.model_validate_json(row["pipeline"])


def current_defaults(conn: Conn) -> TemplateDefaults:
    """The Space's agent defaults as the admin set them (the current version's). Runs resolve
    null stage runtimes/models against these at dispatch and claim (`pipeline.manifest`)."""
    row = conn.execute(
        "SELECT defaults FROM templates WHERE name = ? ORDER BY version DESC LIMIT 1",
        (DEFAULT_NAME,),
    ).fetchone()
    if row is None:
        raise RuntimeError("the default template is missing (seed_default did not run)")
    return TemplateDefaults.model_validate_json(row["defaults"])


def get_template(db: Database) -> Template:
    with db.read() as conn:
        return current(conn).template


def get_defaults(db: Database) -> TemplateDefaults:
    with db.read() as conn:
        return current_defaults(conn)


def put_template(db: Database, actor: Me, body: TemplateUpdate) -> Template:
    """Append version+1. 409 `stale_version` unless `base_version` is the current version.
    The checks raise 422 `render_error` / `validation_error` or 413. `defaults` omitted → the
    current version's are carried over. Runtimes are checked against Settings > Runners in the
    same transaction, so a concurrent runtime removal can't slip by."""
    check_scaffold(body.scaffold)
    with db.tx() as conn:
        runtimes = _runtime_names(conn)
        runtime = None if body.defaults is None else body.defaults.runtime
        if runtime is not None and runtime not in runtimes:
            raise ApiError(
                422, "validation_error", f"defaults.runtime: unknown runtime {runtime!r}"
            )
        template = current(conn).template
        if body.base_version != template.version:
            raise ApiError(409, "stale_version", "The template was changed by someone else")
        defaults = template.defaults if body.defaults is None else body.defaults
        conn.execute(
            "INSERT INTO templates(name, version, pipeline, scaffold, defaults, created_at, "
            "created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                DEFAULT_NAME,
                template.version + 1,
                legacy_pipeline(conn).model_dump_json(),
                _scaffold_json(body.scaffold),
                defaults.model_dump_json(),
                to_db(utcnow()),
                actor.id,
            ),
        )
        return current(conn).template


def _runtime_names(conn: Conn) -> frozenset[str]:
    return frozenset(e.name for e in runtime_entries_of(read_area(conn, "runtimes")))
