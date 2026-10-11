"""Tickets (SPEC §4, §10, §13): create (key, slug, template copy, scaffold = bundle v1), the board
list and detail, patch and pipeline edits.

The board is batch-loaded: a fixed number of queries for any number of tickets (tickets + owners,
latest runs + creators, their stage_runs, the ever-succeeded GROUP BY, TASKS.md of running
tickets (of the ticket, for the detail), chat counts). Ticket id lists are bound as one JSON array
(`json_each(?)`), so the SQL stays constant. Facts go through the pure `board.derive`, then
`board.for_viewer`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agentos.clock import from_db, from_db_opt, to_db, utcnow
from agentos.config import Settings
from agentos.db import Conn, Database, Row
from agentos.errors import ApiError
from agentos.files.service import write_version
from agentos.models import (
    Me,
    Person,
    Pipeline,
    PipelineUpdate,
    Priority,
    RunSummary,
    SourceLink,
    TaskProgress,
    TicketCreate,
    TicketDetail,
    TicketPatch,
    TicketSummary,
    TicketType,
)
from agentos.pipeline.manifest import render_stages
from agentos.pipeline.plan import Plan, parse_plan
from agentos.pipeline.render import TicketRef, render_scaffold
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.scaffold import names, repo_name, slugify
from agentos.pipeline.segment import validate_pipeline
from agentos.templates import service as templates
from agentos.tickets.board import ACTIVE, RunFact, StageRunFact, TicketFacts, derive, for_viewer
from agentos.validate import validate_base_branch, validate_key, validate_repo_url

MAX_LABELS = 20
MAX_LABEL_CHARS = 50

_SELECT_TICKETS = (
    "SELECT t.*, o.display_name AS owner_name, o.avatar_url AS owner_avatar "
    "FROM tickets t LEFT JOIN users o ON o.id = t.owner_id "
)
_SQL_BOARD = (
    _SELECT_TICKETS + "WHERE (:type IS NULL OR t.type = :type) "
    "AND (:priority IS NULL OR t.priority = :priority) "
    "AND (:include_closed OR t.state != 'closed') "
    "ORDER BY t.updated_at DESC, t.id DESC"
)
_SQL_ONE = _SELECT_TICKETS + "WHERE t.key = ?"
_SQL_LATEST_RUNS = (
    "SELECT r.id, r.ticket_id, r.status, r.from_stage, r.stage_ids, r.created_by, r.created_at, "
    "r.started_at, r.finished_at, r.cancel_requested_at, r.error, r.launch_ref, "
    "u.display_name AS creator_name, u.avatar_url AS creator_avatar "
    "FROM runs r LEFT JOIN users u ON u.id = r.created_by "
    "WHERE r.id IN (SELECT MAX(id) FROM runs "
    "               WHERE ticket_id IN (SELECT value FROM json_each(?)) GROUP BY ticket_id)"
)
_SQL_STAGE_RUNS = (
    "SELECT run_id, idx, stage_id, status, started_at, finished_at FROM stage_runs "
    "WHERE run_id IN (SELECT value FROM json_each(?)) ORDER BY run_id, idx"
)
_SQL_EVER_SUCCEEDED = (
    "SELECT r.ticket_id, sr.stage_id FROM stage_runs sr JOIN runs r ON r.id = sr.run_id "
    "WHERE sr.status = 'succeeded' AND r.ticket_id IN (SELECT value FROM json_each(?)) "
    "GROUP BY r.ticket_id, sr.stage_id"
)
# Parameter: a JSON array of [ticket_id, path] pairs.
_SQL_CURRENT_CONTENT = (
    "SELECT fv.owner_id, fv.content FROM file_versions fv "
    "JOIN json_each(?) j ON fv.owner_id = json_extract(j.value, '$[0]') "
    "  AND lower(fv.path) = lower(json_extract(j.value, '$[1]')) "  # = path_key (ASCII paths)
    "WHERE fv.owner_type = 'ticket' AND fv.version = ("
    "  SELECT MAX(x.version) FROM file_versions x "
    "  WHERE x.owner_type = 'ticket' AND x.owner_id = fv.owner_id AND x.path = fv.path)"
)
_SQL_CHAT_COUNTS = (
    "SELECT ticket_id, COUNT(*) AS n FROM chat_messages "
    "WHERE ticket_id IN (SELECT value FROM json_each(?)) GROUP BY ticket_id"
)
_SQL_ACTIVE_RUN = (
    "SELECT id FROM runs WHERE ticket_id = ? AND status IN ('queued', 'dispatched', 'running')"
)


# ------------------------------------------------------------------ helpers for other services


def get_ticket_row(conn: Conn, key: str) -> Row:
    """The `tickets` row (plus `owner_name`, `owner_avatar`). 404 `ticket_not_found`."""
    row: Row | None = conn.execute(_SQL_ONE, (key,)).fetchone()
    if row is None:
        raise ApiError(404, "ticket_not_found", f"No ticket {key}")
    return row


def ticket_ref(row: Row) -> TicketRef:
    """The plain values rendering and manifest building need, from a `tickets` row."""
    return TicketRef(
        key=row["key"],
        slug=row["slug"],
        title=row["title"],
        description=row["description"],
        type=row["type"],
        source_url=row["source_url"],
        repo_url=row["repo_url"],
        base_branch=row["base_branch"],
        workspace_mode=row["workspace_mode"],
    )


def ticket_pipeline(row: Row) -> Pipeline:
    return Pipeline.model_validate_json(row["pipeline"])


def active_run_id(conn: Conn, ticket_id: int) -> int | None:
    """The ticket's queued/dispatched/running run (at most one: `runs_one_active`)."""
    row = conn.execute(_SQL_ACTIVE_RUN, (ticket_id,)).fetchone()
    return None if row is None else int(row["id"])


# ------------------------------------------------------------------ board: list and detail


def list_tickets(
    db: Database,
    viewer_id: int,
    *,
    q: str | None = None,
    ticket_type: TicketType | None = None,
    priority: Priority | None = None,
    include_closed: bool = False,
) -> list[TicketSummary]:
    """The board, newest activity first. `q` matches key, title or a label (case-insensitive)."""
    params = {"type": ticket_type, "priority": priority, "include_closed": include_closed}
    with db.read() as conn:
        rows = conn.execute(_SQL_BOARD, params).fetchall()
        needle = (q or "").strip().casefold()
        if needle:
            rows = [r for r in rows if _matches(r, needle)]
        facts = _load_facts(conn, rows)
    return [TicketSummary(**_summary_fields(r, facts, viewer_id)[0]) for r in rows]


def get_ticket(db: Database, key: str, viewer_id: int) -> TicketDetail:
    with db.read() as conn:
        return _detail(conn, key, viewer_id)


def _matches(row: Row, needle: str) -> bool:
    labels: list[str] = json.loads(row["labels"])
    return (
        needle in row["key"].casefold()
        or needle in row["title"].casefold()
        or any(needle in label.casefold() for label in labels)
    )


@dataclass
class _Facts:
    """Batch-loaded facts for a set of tickets, keyed by ticket id."""

    latest: dict[int, Row] = field(default_factory=dict)
    stage_runs: dict[int, list[StageRunFact]] = field(default_factory=lambda: defaultdict(list))
    ever: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))
    plans: dict[int, Plan] = field(default_factory=dict)  # parsed current TASKS.md
    chat: dict[int, int] = field(default_factory=dict)


def _load_facts(conn: Conn, rows: Sequence[Row], *, with_plan: bool = False) -> _Facts:
    """`plans` holds TASKS.md of running tickets only (the "now" line's next task), or of every
    row with `with_plan` (the detail's `plan`)."""
    facts = _Facts()
    if not rows:
        return facts
    ids = json.dumps([r["id"] for r in rows])
    for run in conn.execute(_SQL_LATEST_RUNS, (ids,)).fetchall():
        facts.latest[run["ticket_id"]] = run
    run_ids = json.dumps([run["id"] for run in facts.latest.values()])
    for sr in conn.execute(_SQL_STAGE_RUNS, (run_ids,)).fetchall():
        facts.stage_runs[sr["run_id"]].append(
            StageRunFact(
                idx=sr["idx"],
                stage_id=sr["stage_id"],
                status=sr["status"],
                started_at=from_db_opt(sr["started_at"]),
                finished_at=from_db_opt(sr["finished_at"]),
            )
        )
    for ev in conn.execute(_SQL_EVER_SUCCEEDED, (ids,)).fetchall():
        facts.ever[ev["ticket_id"]].add(ev["stage_id"])
    # TASKS.md content is only read by the "now" line of a running ticket (its next task), and by
    # the detail.
    wanted = [
        [r["id"], names(r["key"], r["slug"], r["workspace_mode"]).tasks_md]
        for r in rows
        if with_plan or (r["id"] in facts.latest and facts.latest[r["id"]]["status"] == "running")
    ]
    if wanted:
        for f in conn.execute(_SQL_CURRENT_CONTENT, (json.dumps(wanted),)).fetchall():
            if f["content"] is not None:
                facts.plans[f["owner_id"]] = parse_plan(f["content"])
    for c in conn.execute(_SQL_CHAT_COUNTS, (ids,)).fetchall():
        facts.chat[c["ticket_id"]] = c["n"]
    return facts


def _person(id_: int | None, name: str | None, avatar: str | None) -> Person | None:
    if id_ is None or name is None:
        return None
    return Person(id=id_, display_name=name, avatar_url=avatar)


def _run_fact(run: Row, stage_runs: list[StageRunFact]) -> RunFact:
    return RunFact(
        id=run["id"],
        status=run["status"],
        from_stage=run["from_stage"],
        stage_ids=tuple(json.loads(run["stage_ids"])),
        stage_runs=tuple(stage_runs),
        created_at=from_db(run["created_at"]),
        started_at=from_db_opt(run["started_at"]),
        finished_at=from_db_opt(run["finished_at"]),
    )


def _run_summary(run: Row, ticket_key: str) -> RunSummary:
    return RunSummary(
        id=run["id"],
        ticket_key=ticket_key,
        status=run["status"],
        from_stage=run["from_stage"],
        stage_ids=json.loads(run["stage_ids"]),
        created_by=_person(run["created_by"], run["creator_name"], run["creator_avatar"]),
        created_at=from_db(run["created_at"]),
        started_at=from_db_opt(run["started_at"]),
        finished_at=from_db_opt(run["finished_at"]),
        cancel_requested_at=from_db_opt(run["cancel_requested_at"]),
        error=run["error"],
        launch_ref=run["launch_ref"],
    )


def _tasks(row: Row) -> TaskProgress | None:
    total = row["tasks_total"]
    if not total:
        return None
    return TaskProgress(done=row["tasks_done"] or 0, total=total)


def _source(row: Row) -> SourceLink | None:
    if not (row["source"] and row["external_id"] and row["source_url"]):
        return None
    return SourceLink(source=row["source"], external_id=row["external_id"], url=row["source_url"])


def _summary_fields(
    row: Row, facts: _Facts, viewer_id: int
) -> tuple[dict[str, Any], str | None, int | None]:
    """TicketSummary fields, plus `next_stage` and `active_run_id` for the detail."""
    pipeline = ticket_pipeline(row)
    run = facts.latest.get(row["id"])
    latest = _run_fact(run, facts.stage_runs.get(run["id"], [])) if run is not None else None
    tasks = _tasks(row)
    plan = facts.plans.get(row["id"])
    owner = _person(row["owner_id"], row["owner_name"], row["owner_avatar"])
    board = derive(
        TicketFacts(
            state=row["state"],
            owner=owner,
            pipeline=pipeline,
            created_at=from_db(row["created_at"]),
            updated_at=from_db(row["updated_at"]),
            latest_run=latest,
            ever_succeeded=frozenset(facts.ever.get(row["id"], ())),
            tasks=tasks,
            next_task=plan.next_item if plan is not None else None,
        )
    )
    view = for_viewer(board, viewer_id)
    fields: dict[str, Any] = {
        "key": row["key"],
        "title": row["title"],
        "type": row["type"],
        "priority": row["priority"],
        "labels": json.loads(row["labels"]),
        "state": row["state"],
        "owner": owner,
        "attention": view.attention,
        "attention_since": board.attention_since,
        "phase": board.phase,
        "phase_track": board.phase_track,
        "rail": view.rail,
        "now": view.now,
        "tasks": tasks,
        "chat_count": facts.chat.get(row["id"], 0),
        "latest_run": _run_summary(run, row["key"]) if run is not None else None,
        "source": _source(row),
        "repo_name": repo_name(row["repo_url"]),
        "pr_url": row["pr_url"],
        "updated_at": from_db(row["updated_at"]),
    }
    active = run["id"] if run is not None and run["status"] in ACTIVE else None
    return fields, board.next_stage, active


def _detail(conn: Conn, key: str, viewer_id: int) -> TicketDetail:
    row = get_ticket_row(conn, key)
    facts = _load_facts(conn, [row], with_plan=True)
    fields, next_stage, active = _summary_fields(row, facts, viewer_id)
    plan = facts.plans.get(row["id"])
    n = names(row["key"], row["slug"], row["workspace_mode"])
    return TicketDetail(
        **fields,
        description=row["description"],
        slug=row["slug"],
        skill_name=n.skill_name,
        workspace_mode=row["workspace_mode"],
        repo_url=row["repo_url"],
        base_branch=row["base_branch"],
        branch=n.branch,
        pipeline=ticket_pipeline(row),
        pipeline_version=row["pipeline_version"],
        next_stage=next_stage,
        active_run_id=active,
        created_at=from_db(row["created_at"]),
        plan=list(plan.items) if plan is not None else None,
    )


# ------------------------------------------------------------------ create


def create_ticket(db: Database, settings: Settings, user: Me, body: TicketCreate) -> TicketDetail:
    """Allocate the key, copy the current template's pipeline, render its scaffold and write it as
    bundle v1, all in one transaction (a render error leaves no ticket and no used key)."""
    title = _title(body.title)
    labels = _labels(body.labels)
    repo_url: str | None = None
    base_branch: str | None = None
    if body.workspace_mode == "repo":
        repo_url = body.repo_url or settings.default_repo_url
        if repo_url is not None:
            repo_url = validate_repo_url(repo_url)
        base_branch = validate_base_branch(body.base_branch or settings.default_base_branch)
    elif body.repo_url is not None or body.base_branch is not None:
        raise ApiError(
            422, "validation_error", "repo_url/base_branch: a no-repo ticket can't have them"
        )
    now = to_db(utcnow())
    with db.tx() as conn:
        if body.owner_id is not None:
            _check_owner(conn, body.owner_id)
        key = _allocate_key(conn, settings.key_prefix)
        slug = slugify(title, key)
        template = templates.current(conn)
        pipeline = template.template.pipeline
        if body.workspace_mode == "none":
            pipeline = _without_repo_stages(pipeline)
        ref = TicketRef(
            key=key,
            slug=slug,
            title=title,
            description=body.description,
            type=body.type,
            source_url=None,
            repo_url=repo_url,
            base_branch=base_branch,
            workspace_mode=body.workspace_mode,
        )
        scaffold = render_scaffold(template.template.scaffold, ref)
        cursor = conn.execute(
            "INSERT INTO tickets(key, slug, title, description, type, priority, labels, state, "
            "workspace_mode, repo_url, base_branch, template_id, pipeline, pipeline_version, "
            "owner_id, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (
                key,
                slug,
                title,
                body.description,
                body.type,
                body.priority,
                json.dumps(labels),
                body.workspace_mode,
                repo_url,
                base_branch,
                template.id,
                pipeline.model_dump_json(),
                body.owner_id,
                user.id,
                now,
                now,
            ),
        )
        ticket_id = cursor.lastrowid
        if ticket_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
            raise RuntimeError("INSERT INTO tickets returned no rowid")
        for entry in scaffold:
            write_version(
                conn,
                ticket_id,
                entry.path,
                entry.content,
                entry.mode,
                author_user_id=user.id,
                now=from_db(now),
            )
        return _detail(conn, key, user.id)


def _allocate_key(conn: Conn, prefix: str) -> str:
    """The next free `{prefix}-{n}` (n from 1). Skips keys already taken (e.g. imported)."""
    conn.execute(
        "INSERT INTO key_counters(prefix, next) VALUES (?, 1) ON CONFLICT DO NOTHING", (prefix,)
    )
    row = conn.execute("SELECT next FROM key_counters WHERE prefix = ?", (prefix,)).fetchone()
    n: int = row["next"]
    while conn.execute("SELECT 1 FROM tickets WHERE key = ?", (f"{prefix}-{n}",)).fetchone():
        n += 1
    conn.execute("UPDATE key_counters SET next = ? WHERE prefix = ?", (n + 1, prefix))
    return validate_key(f"{prefix}-{n}")


def _without_repo_stages(pipeline: Pipeline) -> Pipeline:
    return pipeline.model_copy(
        update={
            "stages": [
                s.model_copy(update={"enabled": False}) if s.requires_repo else s
                for s in pipeline.stages
            ]
        }
    )


def _title(title: str) -> str:
    title = title.strip()
    if not title:
        raise ApiError(422, "validation_error", "title: must not be blank")
    return title


def _labels(labels: Sequence[str]) -> list[str]:
    """Strip, drop empties, dedupe (first wins); at most 20 labels of at most 50 chars."""
    out: list[str] = []
    for label in labels:
        label = label.strip()
        if label and label not in out:
            out.append(label)
    if len(out) > MAX_LABELS:
        raise ApiError(422, "validation_error", f"labels: at most {MAX_LABELS}")
    for label in out:
        if len(label) > MAX_LABEL_CHARS:
            raise ApiError(
                422, "validation_error", f"labels: {label[:20]!r}... is over {MAX_LABEL_CHARS}"
            )
    return out


def _check_owner(conn: Conn, owner_id: int) -> None:
    row = conn.execute("SELECT disabled FROM users WHERE id = ?", (owner_id,)).fetchone()
    if row is None or row["disabled"]:
        raise ApiError(422, "user_not_found", f"owner_id: no active user {owner_id}")


# ------------------------------------------------------------------ patch and pipeline


def patch_ticket(db: Database, key: str, user: Me, body: TicketPatch) -> TicketDetail:
    """Apply the set fields. `state`, `repo_url` and `base_branch` changes → 409 `run_active`
    while a run is active. The slug never changes (it names the skill directory)."""
    changes = body.model_dump(exclude_unset=True)
    with db.tx() as conn:
        row = get_ticket_row(conn, key)
        values: dict[str, Any] = {
            c: row[c]
            for c in (
                "title",
                "description",
                "type",
                "priority",
                "labels",
                "state",
                "repo_url",
                "base_branch",
                "owner_id",
            )
        }
        for name, value in changes.items():
            if name == "title":
                value = _title(value)
            elif name == "labels":
                value = json.dumps(_labels(value))
            elif name in ("repo_url", "base_branch") and value is not None:
                if row["workspace_mode"] == "none":
                    raise ApiError(422, "validation_error", f"{name}: a no-repo ticket has none")
                value = (
                    validate_repo_url(value) if name == "repo_url" else validate_base_branch(value)
                )
            elif name == "owner_id" and value is not None:
                _check_owner(conn, value)
            values[name] = value
        changed = {c for c, v in values.items() if v != row[c]}
        if not changed:
            return _detail(conn, key, user.id)
        guarded = sorted(changed & {"state", "repo_url", "base_branch"})
        if guarded and active_run_id(conn, row["id"]) is not None:
            raise ApiError(409, "run_active", f"Can't change {', '.join(guarded)} during a run")
        conn.execute(
            "UPDATE tickets SET title = :title, description = :description, type = :type, "
            "priority = :priority, labels = :labels, state = :state, repo_url = :repo_url, "
            "base_branch = :base_branch, owner_id = :owner_id, updated_at = :updated_at "
            "WHERE id = :id",
            {**values, "updated_at": to_db(utcnow()), "id": row["id"]},
        )
        return _detail(conn, key, user.id)


def put_pipeline(
    db: Database, runtimes: RuntimesConfig, key: str, user: Me, body: PipelineUpdate
) -> TicketDetail:
    """409 `run_active` / `stale_version`; 422 `invalid_pipeline` (incl. an enabled
    `requires_repo` stage on a no-repo ticket) or `render_error` (every agent prompt must render,
    so a template typo surfaces on save, not at dispatch)."""
    pipeline = body.pipeline
    with db.tx() as conn:
        row = get_ticket_row(conn, key)
        if active_run_id(conn, row["id"]) is not None:
            raise ApiError(409, "run_active", "The pipeline is locked while a run is active")
        if body.base_version != row["pipeline_version"]:
            raise ApiError(409, "stale_version", "The pipeline was changed by someone else")
        validate_pipeline(pipeline, runtimes=runtimes.runtimes.keys())
        if row["workspace_mode"] == "none":
            for stage in pipeline.stages:
                if stage.enabled and stage.requires_repo:
                    raise ApiError(
                        422,
                        "invalid_pipeline",
                        f"stage {stage.id}: requires a repo; this ticket has none",
                    )
        agent_stages = [s for s in pipeline.stages if s.kind == "agent"]
        render_stages(0, ticket_ref(row), agent_stages, runtimes)
        conn.execute(
            "UPDATE tickets SET pipeline = ?, pipeline_version = pipeline_version + 1, "
            "updated_at = ? WHERE id = ?",
            (pipeline.model_dump_json(), to_db(utcnow()), row["id"]),
        )
        return _detail(conn, key, user.id)
