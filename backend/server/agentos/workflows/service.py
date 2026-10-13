"""Workflows (request 23B): named pipeline presets, edited by admins in Settings > Workflows.

Applying one copies its pipeline into a ticket (at creation or a switch, `tickets.service`), and
the ticket records the workflow's id, name and version then; editing a workflow later never
changes existing tickets. Exactly one workflow is the default (`is_default`): what a ticket gets
when it names none. It can't be deleted; another becomes the default through Settings > Space.

A fresh or upgraded db gets "Standard" from the default template's pipeline at startup
(`seed_standard`), so there is always a default.
"""

from __future__ import annotations

import logging
from collections.abc import Collection

from agentos.clock import from_db, to_db, utcnow
from agentos.db import Conn, Database, IntegrityError, Row
from agentos.errors import ApiError
from agentos.models import Me, Person, Pipeline, Workflow, WorkflowCreate, WorkflowUpdate
from agentos.pipeline.render import check_prompts
from agentos.pipeline.segment import validate_pipeline
from agentos.space_store import read_area, runtime_entries_of
from agentos.templates.service import legacy_pipeline

log = logging.getLogger(__name__)

STANDARD_NAME = "Standard"
STANDARD_DESCRIPTION = "The pipeline every ticket started from before workflows."

_SQL = (
    "SELECT w.*, u.display_name AS updater_name, u.avatar_url AS updater_avatar "
    "FROM workflows w LEFT JOIN users u ON u.id = w.updated_by"
)


def seed_standard(db: Database) -> bool:
    """Startup: with no workflow at all, create "Standard" from the default template's pipeline
    and make it the default (after the template seed, so a fresh db has one). Idempotent."""
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM workflows LIMIT 1").fetchone() is not None:
            return False
        now = to_db(utcnow())
        conn.execute(
            "INSERT INTO workflows(name, description, pipeline, version, is_default, created_at, "
            "updated_at) VALUES (?, ?, ?, 1, 1, ?, ?)",
            (
                STANDARD_NAME,
                STANDARD_DESCRIPTION,
                legacy_pipeline(conn).model_dump_json(),
                now,
                now,
            ),
        )
    log.info("Workflows: created %r from the template's pipeline (the default)", STANDARD_NAME)
    return True


# ------------------------------------------------------------------ reads


def list_workflows(db: Database) -> list[Workflow]:
    with db.read() as conn:
        rows = conn.execute(f"{_SQL} ORDER BY lower(w.name), w.id").fetchall()
    return [_workflow(r) for r in rows]


def get(conn: Conn, workflow_id: int) -> Workflow:
    """404 `not_found`."""
    row = conn.execute(f"{_SQL} WHERE w.id = ?", (workflow_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", f"No workflow {workflow_id}")
    return _workflow(row)


def for_ticket(conn: Conn, workflow_id: int | None) -> Workflow:
    """The workflow a new ticket copies: `workflow_id`, or the default when None. An unknown id
    → 422 `validation_error` (it is a field of the request body)."""
    if workflow_id is None:
        return get(conn, default_id(conn))
    row = conn.execute(f"{_SQL} WHERE w.id = ?", (workflow_id,)).fetchone()
    if row is None:
        raise ApiError(422, "validation_error", f"workflow_id: no workflow {workflow_id}")
    return _workflow(row)


def default_id(conn: Conn) -> int:
    row = conn.execute("SELECT id FROM workflows WHERE is_default = 1").fetchone()
    if row is None:
        raise RuntimeError("no default workflow (seed_standard did not run)")
    return int(row["id"])


def get_default_id(db: Database) -> int:
    with db.read() as conn:
        return default_id(conn)


def set_default(conn: Conn, workflow_id: int) -> bool:
    """Make `workflow_id` the default (inside the caller's transaction; Settings > Space). 422
    `validation_error` if there is no such workflow. Returns whether the default changed."""
    if conn.execute("SELECT 1 FROM workflows WHERE id = ?", (workflow_id,)).fetchone() is None:
        raise ApiError(422, "validation_error", f"default_workflow_id: no workflow {workflow_id}")
    if default_id(conn) == workflow_id:
        return False
    conn.execute("UPDATE workflows SET is_default = 0 WHERE is_default = 1")
    conn.execute("UPDATE workflows SET is_default = 1 WHERE id = ?", (workflow_id,))
    return True


def runtime_uses(conn: Conn, names: Collection[str]) -> dict[str, list[str]]:
    """For each runtime in `names`, the workflows with an agent stage pinned to it (by name)."""
    uses: dict[str, list[str]] = {}
    for row in conn.execute("SELECT name, pipeline FROM workflows ORDER BY lower(name)"):
        pipeline = Pipeline.model_validate_json(row["pipeline"])
        for stage in pipeline.stages:
            if stage.kind == "agent" and stage.runtime is not None and stage.runtime in names:
                found = uses.setdefault(stage.runtime, [])
                if row["name"] not in found:
                    found.append(row["name"])
    return uses


# ------------------------------------------------------------------ writes, admin only


def create_workflow(db: Database, actor: Me, body: WorkflowCreate) -> Workflow:
    """422 `validation_error` (a name in use, ignoring case) / `invalid_pipeline` /
    `render_error`."""
    _check_pipeline_renders(body.pipeline)
    with db.tx() as conn:
        _check_pipeline(conn, body.pipeline)
        _check_name(conn, body.name, None)
        now = to_db(utcnow())
        try:
            cursor = conn.execute(
                "INSERT INTO workflows(name, description, pipeline, version, is_default, "
                "created_at, created_by, updated_at, updated_by) "
                "VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?)",
                (
                    body.name,
                    body.description,
                    body.pipeline.model_dump_json(),
                    now,
                    actor.id,
                    now,
                    actor.id,
                ),
            )
        except IntegrityError:
            raise _name_taken(body.name) from None
        workflow_id = cursor.lastrowid
        if workflow_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
            raise RuntimeError("INSERT INTO workflows returned no rowid")
        return get(conn, workflow_id)


def update_workflow(db: Database, actor: Me, workflow_id: int, body: WorkflowUpdate) -> Workflow:
    """Replace name, description and pipeline; version + 1. 404 `not_found`, 409 `stale_version`,
    422 like create. Tickets copied from it keep their pipeline."""
    _check_pipeline_renders(body.pipeline)
    with db.tx() as conn:
        current = get(conn, workflow_id)
        if body.base_version != current.version:
            raise ApiError(409, "stale_version", "The workflow was changed by someone else")
        _check_pipeline(conn, body.pipeline)
        _check_name(conn, body.name, workflow_id)
        try:
            conn.execute(
                "UPDATE workflows SET name = ?, description = ?, pipeline = ?, "
                "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
                (
                    body.name,
                    body.description,
                    body.pipeline.model_dump_json(),
                    to_db(utcnow()),
                    actor.id,
                    workflow_id,
                ),
            )
        except IntegrityError:
            raise _name_taken(body.name) from None
        return get(conn, workflow_id)


def delete_workflow(db: Database, workflow_id: int) -> None:
    """404 `not_found`; the default → 409 `in_use`. Tickets copied from it keep their pipeline and
    their record (its name and version; the id goes null)."""
    with db.tx() as conn:
        workflow = get(conn, workflow_id)
        if workflow.default:
            raise ApiError(
                409,
                "in_use",
                f"{workflow.name} is the default workflow: make another the default first",
            )
        conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))


# ------------------------------------------------------------------ helpers


def _check_pipeline_renders(pipeline: Pipeline) -> None:
    check_prompts(pipeline.stages)


def _check_pipeline(conn: Conn, pipeline: Pipeline) -> None:
    """The ticket pipeline checks (422 `invalid_pipeline`), runtimes read in the caller's
    transaction so a concurrent runtime removal can't slip by."""
    runtimes = frozenset(e.name for e in runtime_entries_of(read_area(conn, "runtimes")))
    validate_pipeline(pipeline, runtimes=runtimes)


def _check_name(conn: Conn, name: str, own_id: int | None) -> None:
    """Unique ignoring case (Unicode casefold; the db index only folds ASCII)."""
    folded = name.casefold()
    for row in conn.execute("SELECT id, name FROM workflows"):
        if row["id"] != own_id and row["name"].casefold() == folded:
            raise _name_taken(name)


def _name_taken(name: str) -> ApiError:
    return ApiError(422, "validation_error", f"name: a workflow named {name!r} exists already")


def _workflow(row: Row) -> Workflow:
    updated_by = None
    if row["updated_by"] is not None:
        updated_by = Person(
            id=row["updated_by"], display_name=row["updater_name"], avatar_url=row["updater_avatar"]
        )
    return Workflow(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        pipeline=Pipeline.model_validate_json(row["pipeline"]),
        version=row["version"],
        updated_at=from_db(row["updated_at"]),
        updated_by=updated_by,
        default=bool(row["is_default"]),
    )
