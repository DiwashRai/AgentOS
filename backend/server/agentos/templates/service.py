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
import logging
from dataclasses import dataclass

from pydantic import TypeAdapter

from agentos.clock import to_db, utcnow
from agentos.db import Conn, Database
from agentos.errors import ApiError
from agentos.models import FileEntry, Me, Pipeline, Template, TemplateDefaults, TemplateUpdate
from agentos.pipeline.render import check_scaffold
from agentos.pipeline.scaffold import (
    DEFAULT_SCAFFOLD,
    LEGACY_SKILL_MD_V1,
    SKILL_MD,
    SKILL_MD_PATH,
    default_pipeline,
)
from agentos.space_store import read_area, runtime_entries_of

log = logging.getLogger(__name__)

DEFAULT_NAME = "default"
_SCAFFOLD = TypeAdapter(list[FileEntry])


@dataclass(frozen=True, slots=True)
class TemplateRow:
    id: int
    template: Template


def _scaffold_json(entries: list[FileEntry]) -> str:
    return json.dumps([e.model_dump() for e in entries])


def seed_default(db: Database) -> None:
    """Startup: insert version 1 of the default template unless it exists, then upgrade a
    legacy default SKILL.md (`_upgrade_skill_md`). Idempotent."""
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
        _upgrade_skill_md(conn)


def _upgrade_skill_md(conn: Conn) -> None:
    """If the current version's SKILL.md entry is still exactly `LEGACY_SKILL_MD_V1` (the seed
    whose description carried the title), append version+1 with `SKILL_MD` in its place and
    everything else unchanged. A customised SKILL.md is left alone; once upgraded, the current
    version no longer matches, so this runs at most once. Existing tickets are unaffected: their
    scaffold was rendered into their bundle at creation."""
    row = conn.execute(
        "SELECT version, pipeline, scaffold, defaults FROM templates WHERE name = ? "
        "ORDER BY version DESC LIMIT 1",
        (DEFAULT_NAME,),
    ).fetchone()
    if row is None:  # pragma: no cover - seeded just above
        return
    scaffold = _SCAFFOLD.validate_json(row["scaffold"])
    upgraded = [
        e.model_copy(update={"content": SKILL_MD})
        if e.path == SKILL_MD_PATH and e.content == LEGACY_SKILL_MD_V1
        else e
        for e in scaffold
    ]
    if upgraded == scaffold:
        return
    conn.execute(
        "INSERT INTO templates(name, version, pipeline, scaffold, defaults, created_at, "
        "created_by) VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (
            DEFAULT_NAME,
            row["version"] + 1,
            row["pipeline"],
            _scaffold_json(upgraded),
            row["defaults"],
            to_db(utcnow()),
        ),
    )
    log.info("Upgraded the default template's SKILL.md to v%d (key-only)", row["version"] + 1)


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
