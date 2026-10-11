"""The default template (SPEC §4, §15): append-only versions, current = the max version.

Copied into a ticket at creation (`tickets.template_id` records the exact version) and never
linked afterwards, so a template edit only affects tickets created after it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection
from dataclasses import dataclass

from pydantic import TypeAdapter

from agentos.clock import to_db, utcnow
from agentos.db import Conn, Database
from agentos.errors import ApiError
from agentos.models import FileEntry, Me, Pipeline, Template, TemplateUpdate
from agentos.pipeline.render import check_scaffold
from agentos.pipeline.scaffold import (
    DEFAULT_SCAFFOLD,
    LEGACY_SKILL_MD_V1,
    SKILL_MD,
    SKILL_MD_PATH,
    default_pipeline,
)
from agentos.pipeline.segment import validate_pipeline

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
        "SELECT version, pipeline, scaffold FROM templates WHERE name = ? "
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
        "INSERT INTO templates(name, version, pipeline, scaffold, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        (
            DEFAULT_NAME,
            row["version"] + 1,
            row["pipeline"],
            _scaffold_json(upgraded),
            to_db(utcnow()),
        ),
    )
    log.info("Upgraded the default template's SKILL.md to v%d (key-only)", row["version"] + 1)


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
