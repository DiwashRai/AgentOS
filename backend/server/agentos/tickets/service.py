"""Tickets (SPEC §4, §10, §13): create (key, workflow copy, scaffold = bundle v1), import
from a ticket source (§12), the board list and detail, patch and pipeline edits.

The board is batch-loaded: a fixed number of queries for any number of tickets (tickets + owners,
latest runs + creators, their stage_runs, the ever-succeeded GROUP BY, TASKS.md of running
tickets (of the ticket, for the detail), chat counts, which client-run creators have a client
online). Ticket id lists are bound as one JSON array (`json_each(?)`), so the SQL stays constant.
Facts go through the pure `board.derive`, then `board.for_viewer`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, Json, StrictInt, StrictStr

from agentos.clients.service import online_user_ids, waiting_client_run_id
from agentos.clock import from_db, from_db_opt, to_db, utcnow
from agentos.db import Conn, Database, Row
from agentos.errors import ApiError
from agentos.files.service import write_version
from agentos.models import (
    BaseBranchInfo,
    BaseBranchSource,
    DispatchTarget,
    FieldMapping,
    ImportRequest,
    ImportResult,
    LogDelivery,
    Me,
    Person,
    Pipeline,
    PipelineUpdate,
    Priority,
    ReportMode,
    Resolution,
    ResolveOutcome,
    ResolveRequest,
    RunStatus,
    RunSummary,
    SourceLink,
    StageRunStatus,
    StartMode,
    TaskProgress,
    TicketCreate,
    TicketDetail,
    TicketPatch,
    TicketSummary,
    TicketType,
    TicketWorkflow,
    WorkflowSwitch,
    WorkspaceMode,
)
from agentos.pipeline.manifest import render_stages
from agentos.pipeline.mapping import apply_rules, join_values, mapping_for
from agentos.pipeline.plan import Plan, parse_plan
from agentos.pipeline.render import TicketRef, render_scaffold
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.scaffold import names
from agentos.pipeline.segment import validate_pipeline
from agentos.pipeline.space import ResolvedRepo, SpaceConfig
from agentos.sources.jira import JiraNotFound, JiraSource, JiraUnauthorized, JiraUnavailable
from agentos.templates import service as templates
from agentos.tickets.board import (
    ACTIVE,
    Dispatched,
    Failed,
    Finished,
    Queued,
    RunFact,
    Running,
    RunState,
    StageRunFact,
    TicketFacts,
    derive,
    for_viewer,
)
from agentos.validate import validate_base_branch, validate_key
from agentos.workflows import service as workflows

MAX_LABELS = 20
MAX_LABEL_CHARS = 50
MAX_TITLE_CHARS = 200  # TicketCreate.title max_length: an imported summary is cut to it

_SELECT_TICKETS = (
    "SELECT t.*, o.display_name AS owner_name, o.avatar_url AS owner_avatar, "
    "rv.display_name AS resolver_name, rv.avatar_url AS resolver_avatar, "
    "bs.display_name AS base_setter_name, bs.avatar_url AS base_setter_avatar "
    "FROM tickets t LEFT JOIN users o ON o.id = t.owner_id "
    "LEFT JOIN users rv ON rv.id = t.resolved_by "
    "LEFT JOIN users bs ON bs.id = t.base_branch_set_by "
)
# An abandoned ticket (closed with a resolution) is listed like a done one; `include_closed` only
# adds tickets closed through PATCH `state` (no resolution).
_SQL_BOARD = (
    _SELECT_TICKETS + "WHERE (:type IS NULL OR t.type = :type) "
    "AND (:priority IS NULL OR t.priority = :priority) "
    "AND (:include_closed OR t.state != 'closed' OR t.resolution_outcome IS NOT NULL) "
    "ORDER BY t.updated_at DESC, t.id DESC"
)
_SQL_ONE = _SELECT_TICKETS + "WHERE t.key = ?"
_SQL_LATEST_RUNS = (
    "SELECT r.id, r.ticket_id, r.status, r.from_stage, r.stage_ids, r.created_by, r.created_at, "
    "r.started_at, r.finished_at, r.stop_requested_at, r.stop_reason, r.queue_reason, r.error, "
    "r.start, r.report, r.launch_ref, "
    "u.display_name AS creator_name, u.avatar_url AS creator_avatar "
    "FROM runs r LEFT JOIN users u ON u.id = r.created_by "
    "WHERE r.id IN (SELECT MAX(id) FROM runs "
    "               WHERE ticket_id IN (SELECT value FROM json_each(?)) GROUP BY ticket_id)"
)
_SQL_STAGE_RUNS = (
    "SELECT run_id, idx, stage_id, round, status, started_at, finished_at FROM stage_runs "
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
_SQL_CLEAR_RESOLUTION = (
    "UPDATE tickets SET resolution_outcome = NULL, resolution_note = NULL, resolved_by = NULL, "
    "resolved_at = NULL WHERE id = ?"
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


def workspace_mode(row: Row) -> WorkspaceMode:
    """Derived from `repo` alone (SPEC §17): a ticket with no `repo` is a no-repo ticket."""
    return "repo" if row["repo"] is not None else "none"


def resolve_workspace(row: Row, space: SpaceConfig) -> ResolvedRepo:
    """A `tickets` row's repo and base branch, resolved through the Space's catalog
    (`SpaceConfig.resolve` → `resolve_base_branch`, the one rule for the base branch)."""
    return space.resolve(row["repo"], row["base_branch"], source=row["base_branch_source"])


def ticket_ref(row: Row, space: SpaceConfig) -> TicketRef:
    """The plain values rendering and manifest building need, from a `tickets` row, with the
    repo resolved through the Space's catalog."""
    repo = resolve_workspace(row, space)
    return TicketRef(
        key=row["key"],
        title=row["title"],
        description=row["description"],
        type=row["type"],
        source_url=row["source_url"],
        repo_url=repo.url,
        base_branch=repo.base_branch,
        workspace_mode=repo.mode,
    )


def run_ticket_ref(row: Row, space: SpaceConfig) -> TicketRef:
    """`ticket_ref` for a run (its job's build): 422 `validation_error` naming the repo if it is
    no longer in the catalog; 409 `base_branch_missing` for a repo ticket whose base branch
    resolves to nothing (the board's `dispatch_block` says the same)."""
    if row["repo"] is not None:
        space.require_repo(row["repo"])
    ref = ticket_ref(row, space)
    if ref.workspace_mode == "repo" and ref.base_branch is None:
        raise ApiError(
            409,
            "base_branch_missing",
            f"{row['key']} has no base branch to start from; set one first",
        )
    return ref


def ticket_pipeline(row: Row) -> Pipeline:
    return Pipeline.model_validate_json(row["pipeline"])


def active_run_id(conn: Conn, ticket_id: int) -> int | None:
    """The ticket's queued/dispatched/running run (at most one: `runs_one_active`)."""
    row = conn.execute(_SQL_ACTIVE_RUN, (ticket_id,)).fetchone()
    return None if row is None else int(row["id"])


# ------------------------------------------------------------------ board: list and detail


def list_tickets(
    db: Database,
    space: SpaceConfig,
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
    return [TicketSummary(**_summary_fields(r, facts, space, viewer_id)[0]) for r in rows]


def get_ticket(db: Database, space: SpaceConfig, key: str, viewer_id: int) -> TicketDetail:
    with db.read() as conn:
        return _detail(conn, space, key, viewer_id)


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
    online: set[int] = field(default_factory=set)  # creators of latest client runs with a client on


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
        facts.stage_runs[sr["run_id"]].append(stage_run_fact(sr))
    for ev in conn.execute(_SQL_EVER_SUCCEEDED, (ids,)).fetchall():
        facts.ever[ev["ticket_id"]].add(ev["stage_id"])
    # TASKS.md content is only read by the "now" line of a running ticket (its next task), and by
    # the detail.
    wanted = [
        [r["id"], names(r["key"], workspace_mode(r)).tasks_md]
        for r in rows
        if with_plan or (r["id"] in facts.latest and facts.latest[r["id"]]["status"] == "running")
    ]
    if wanted:
        for f in conn.execute(_SQL_CURRENT_CONTENT, (json.dumps(wanted),)).fetchall():
            if f["content"] is not None:
                facts.plans[f["owner_id"]] = parse_plan(f["content"])
    for c in conn.execute(_SQL_CHAT_COUNTS, (ids,)).fetchall():
        facts.chat[c["ticket_id"]] = c["n"]
    creators = [
        run["created_by"]
        for run in facts.latest.values()
        if run["start"] == "pull" and run["created_by"] is not None
    ]
    if creators:
        facts.online = online_user_ids(conn, creators, utcnow())
    return facts


def person(id_: int | None, name: str | None, avatar: str | None) -> Person | None:
    if id_ is None or name is None:
        return None
    return Person(id=id_, display_name=name, avatar_url=avatar)


class _StageRunRow(BaseModel):
    """The `stage_runs` columns the board reads."""

    idx: StrictInt
    stage_id: StrictStr
    status: StageRunStatus
    round: StrictInt
    started_at: StrictStr | None


class _RunRow(BaseModel):
    """The `runs` columns (and the creator's name) the board reads. A run plans at least one
    stage: dispatch writes its `stage_ids` in the transaction that inserts it."""

    id: StrictInt
    status: RunStatus
    from_stage: StrictStr
    stage_ids: Json[Annotated[list[StrictStr], Field(min_length=1)]]
    created_at: StrictStr
    started_at: StrictStr | None
    finished_at: StrictStr | None
    queue_reason: StrictStr | None
    start: StartMode
    creator_name: StrictStr | None


def stage_run_fact(sr: Row) -> StageRunFact:
    """A stage_run row that doesn't fit `_StageRunRow` is corrupt: ValidationError."""
    row = _StageRunRow.model_validate(dict(sr))
    return StageRunFact(
        idx=row.idx,
        stage_id=row.stage_id,
        status=row.status,
        round=row.round,
        started=row.started_at is not None,
    )


def run_fact(run: Row, stage_runs: list[StageRunFact]) -> RunFact:
    """The board's view of a run row, the only place the board's run rows are read; one that
    doesn't fit `_RunRow` is corrupt: ValidationError. `stage_runs` come in idx order; a run
    without any (never expected) counts its planned stages as pending."""
    row = _RunRow.model_validate(dict(run))
    created_at = from_db(row.created_at)
    first_id, *more_ids = row.stage_ids
    pending = [
        StageRunFact(idx=i, stage_id=s, status="pending") for i, s in enumerate(row.stage_ids)
    ]
    first_run, *more_runs = stage_runs or pending
    return RunFact(
        id=row.id,
        state=_run_state(row, created_at),
        from_stage=row.from_stage,
        stage_ids=(first_id, *more_ids),
        stage_runs=(first_run, *more_runs),
        created_at=created_at,
        target=run_target(row.start),
        creator_name=row.creator_name,
    )


def _run_state(row: _RunRow, created_at: datetime) -> RunState:
    """A missing `started_at` or `finished_at` (never expected) reads as `created_at`."""
    match row.status:
        case "queued":
            return Queued(row.queue_reason)
        case "dispatched":
            return Dispatched()
        case "running":
            return Running(from_db_opt(row.started_at) or created_at)
        case "gated" | "succeeded" as status:
            return Finished(status, from_db_opt(row.finished_at) or created_at)
        case "failed" | "cancelled" as status:
            return Failed(status, from_db_opt(row.finished_at) or created_at)


def run_summary(run: Row, ticket_key: str, online: set[int]) -> RunSummary:
    return RunSummary(
        id=run["id"],
        ticket_key=ticket_key,
        status=run["status"],
        from_stage=run["from_stage"],
        stage_ids=json.loads(run["stage_ids"]),
        created_by=person(run["created_by"], run["creator_name"], run["creator_avatar"]),
        created_at=from_db(run["created_at"]),
        started_at=from_db_opt(run["started_at"]),
        finished_at=from_db_opt(run["finished_at"]),
        stop_requested_at=from_db_opt(run["stop_requested_at"]),
        stop_reason=run["stop_reason"],
        queue_reason=run["queue_reason"],
        error=run["error"],
        launch_ref=run["launch_ref"],
        launch_url=launch_url(run["start"], run["launch_ref"]),
        log_delivery=log_delivery(run["report"]),
        target=run_target(run["start"]),
        client_online=client_online(run["start"], run["created_by"], online),
    )


def run_target(start: StartMode) -> DispatchTarget:
    return "client" if start == "pull" else "server"


def client_online(start: StartMode, created_by: int | None, online: set[int]) -> bool | None:
    """A client (pull) run: whether its creator has a client online (`online`: such user ids,
    from `clients.service.online_user_ids`). Null for a server run."""
    if start != "pull":
        return None
    return created_by in online


def launch_url(start: StartMode, launch_ref: str | None) -> str | None:
    """The provider's page for a push run: GitHub's ref is the Actions run URL. A client run's
    ref is the client's name (no page)."""
    if start == "push" and launch_ref is not None and launch_ref.startswith("https://"):
        return launch_ref
    return None


def log_delivery(report: ReportMode) -> LogDelivery:
    """File-report (mailbox) runs replay their recorded events when the job ends; live runs
    stream them."""
    return "at_end" if report == "file" else "live"


def _tasks(row: Row) -> TaskProgress | None:
    total = row["tasks_total"]
    if not total:
        return None
    return TaskProgress(done=row["tasks_done"] or 0, total=total)


def _source(row: Row) -> SourceLink | None:
    if not (row["source"] and row["external_id"] and row["source_url"]):
        return None
    return SourceLink(source=row["source"], external_id=row["external_id"], url=row["source_url"])


def _resolution(row: Row) -> Resolution | None:
    """From the `resolution_*` columns (and the resolver's name, joined in `_SELECT_TICKETS`).
    A resolver whose user row is gone (never expected: users are disabled, not deleted) → None."""
    by = person(row["resolved_by"], row["resolver_name"], row["resolver_avatar"])
    if row["resolution_outcome"] is None or by is None:
        return None
    return Resolution(
        outcome=row["resolution_outcome"],
        note=row["resolution_note"],
        by=by,
        at=from_db(row["resolved_at"]),
    )


def _summary_fields(
    row: Row, facts: _Facts, space: SpaceConfig, viewer_id: int
) -> tuple[dict[str, Any], str | None, int | None]:
    """TicketSummary fields, plus `next_stage` and `active_run_id` for the detail."""
    pipeline = ticket_pipeline(row)
    run = facts.latest.get(row["id"])
    latest = run_fact(run, facts.stage_runs.get(run["id"], [])) if run is not None else None
    tasks = _tasks(row)
    plan = facts.plans.get(row["id"])
    owner = person(row["owner_id"], row["owner_name"], row["owner_avatar"])
    workspace = resolve_workspace(row, space)
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
            workspace_mode=workspace.mode,
            base_branch=workspace.base_branch,
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
        "resolution": _resolution(row),
        "owner": owner,
        "attention": view.attention,
        "attention_since": board.attention_since,
        "phase": board.phase,
        "phase_track": board.phase_track,
        "rail": view.rail,
        "now": view.now,
        "tasks": tasks,
        "chat_count": facts.chat.get(row["id"], 0),
        "latest_run": run_summary(run, row["key"], facts.online) if run is not None else None,
        "source": _source(row),
        "repo_name": row["repo"],
        "pr_url": row["pr_url"],
        "dispatch_block": board.dispatch_block,
        "updated_at": from_db(row["updated_at"]),
    }
    active = run["id"] if run is not None and run["status"] in ACTIVE else None
    return fields, board.next_stage, active


def _detail(conn: Conn, space: SpaceConfig, key: str, viewer_id: int) -> TicketDetail:
    row = get_ticket_row(conn, key)
    facts = _load_facts(conn, [row], with_plan=True)
    fields, next_stage, active = _summary_fields(row, facts, space, viewer_id)
    plan = facts.plans.get(row["id"])
    repo = resolve_workspace(row, space)
    n = names(row["key"], repo.mode)
    return TicketDetail(
        **fields,
        description=row["description"],
        skill_name=n.skill_name,
        workspace_mode=repo.mode,
        repo_url=repo.url,
        base_branch=repo.base_branch,
        base_branch_info=_base_branch_info(row, repo),
        branch=n.branch,
        pipeline=ticket_pipeline(row),
        pipeline_version=row["pipeline_version"],
        workflow=_ticket_workflow(row),
        next_stage=next_stage,
        active_run_id=active,
        created_at=from_db(row["created_at"]),
        plan=list(plan.items) if plan is not None else None,
    )


def _ticket_workflow(row: Row) -> TicketWorkflow | None:
    """The workflow the pipeline was copied from (null before workflows existed)."""
    if row["workflow_name"] is None:
        return None
    return TicketWorkflow(
        id=row["workflow_id"], name=row["workflow_name"], version=row["workflow_version"]
    )


def _base_branch_info(row: Row, repo: ResolvedRepo) -> BaseBranchInfo | None:
    """Where the resolved base branch came from (null for a no-repo ticket)."""
    if repo.mode == "none" or repo.origin is None:
        return None
    reason: str | None = None
    if repo.origin == "none":
        if row["base_branch_source"] == "mapping":
            reason = row["base_branch_reason"] or "No mapping rule gave a base branch"
        elif repo.url is None:  # the repo left the catalog (url is required in the catalog)
            reason = f"The repo {repo.name} is not in this Space's repo catalog"
        else:
            reason = f"The repo {repo.name} has no default branch"
    locked_by = row["base_branch_locked_by_run"]
    return BaseBranchInfo(
        source=repo.origin,
        source_value=row["base_branch_source_value"],
        reason=reason,
        set_by=(
            person(row["base_branch_set_by"], row["base_setter_name"], row["base_setter_avatar"])
            if repo.origin == "manual"
            else None
        ),
        locked=locked_by is not None,
        locked_by_run=locked_by,
    )


# ------------------------------------------------------------------ create


def create_ticket(db: Database, space: SpaceConfig, user: Me, body: TicketCreate) -> TicketDetail:
    """Allocate the key, copy the workflow's pipeline, render the current template's scaffold and
    write it as bundle v1, all in one transaction (a render error leaves no ticket and no used key).

    `repo` omitted → the Space's `default_repo`; null → no-repo mode; not in the catalog → 422.
    `base_branch` given → the ticket's own (`manual`, set by the creator); null → `default`: the
    repo's default_branch, which may be unset (the ticket is created blank, blocked from
    dispatch)."""
    title = _title(body.title)
    labels = _labels(body.labels)
    repo = body.repo if "repo" in body.model_fields_set else space.default_repo
    if repo is not None:
        space.require_repo(repo)
    elif body.base_branch is not None:
        raise ApiError(422, "validation_error", "base_branch: a no-repo ticket can't have one")
    base_branch = validate_base_branch(body.base_branch) if body.base_branch is not None else None
    with db.tx() as conn:
        if body.owner_id is not None:
            _check_owner(conn, body.owner_id)
        key = _allocate_key(conn, space.key_prefix)
        _insert_ticket(
            conn,
            space,
            user,
            _NewTicket(
                key=key,
                title=title,
                description=body.description,
                type=body.type,
                priority=body.priority,
                labels=labels,
                repo=repo,
                base_branch=base_branch,
                base_branch_source="manual" if base_branch is not None else "default",
                base_branch_set_by=user.id if base_branch is not None else None,
                owner_id=body.owner_id,
                workflow_id=body.workflow_id,
            ),
        )
        return _detail(conn, space, key, user.id)


@dataclass(frozen=True, slots=True)
class _NewTicket:
    """A validated new ticket (native or imported): what `_insert_ticket` stores."""

    key: str
    title: str
    description: str
    type: TicketType
    priority: Priority
    labels: list[str]
    repo: str | None  # a catalog name (already checked); None = no-repo mode
    base_branch: str | None  # the ticket's own; None = none of its own
    base_branch_source: BaseBranchSource  # `manual` iff base_branch is set by hand
    base_branch_set_by: int | None  # who set it (`manual` only)
    owner_id: int | None
    source: SourceLink | None = None
    base_branch_source_value: str | None = None  # the raw field text a mapping read
    base_branch_reason: str | None = None  # why a mapping left it blank
    workflow_id: int | None = None  # None = the default workflow


def _insert_ticket(conn: Conn, space: SpaceConfig, user: Me, new: _NewTicket) -> int:
    """Insert the ticket with a copy of its workflow's pipeline (422 for an unknown workflow) and
    the current template's rendered scaffold as bundle v1 (inside the caller's transaction, so a
    render error leaves nothing behind). A no-repo ticket gets `requires_repo` stages disabled."""
    resolved = space.resolve(new.repo, new.base_branch, source=new.base_branch_source)
    now = utcnow()
    workflow = workflows.for_ticket(conn, new.workflow_id)
    template = templates.current(conn)
    pipeline = workflow.pipeline
    if resolved.mode == "none":
        pipeline = _without_repo_stages(pipeline)
    source = new.source
    ref = TicketRef(
        key=new.key,
        title=new.title,
        description=new.description,
        type=new.type,
        source_url=source.url if source is not None else None,
        repo_url=resolved.url,
        base_branch=resolved.base_branch,
        workspace_mode=resolved.mode,
    )
    scaffold = render_scaffold(template.scaffold, ref)
    cursor = conn.execute(
        "INSERT INTO tickets(key, title, description, type, priority, labels, state, "
        "repo, base_branch, base_branch_source, base_branch_set_by, "
        "base_branch_source_value, base_branch_reason, "
        "pipeline, pipeline_version, workflow_id, workflow_name, workflow_version, "
        "owner_id, source, external_id, source_url, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?)",
        (
            new.key,
            new.title,
            new.description,
            new.type,
            new.priority,
            json.dumps(new.labels),
            new.repo,
            new.base_branch,
            new.base_branch_source,
            new.base_branch_set_by,
            new.base_branch_source_value,
            new.base_branch_reason,
            pipeline.model_dump_json(),
            workflow.id,
            workflow.name,
            workflow.version,
            new.owner_id,
            source.source if source is not None else None,
            source.external_id if source is not None else None,
            source.url if source is not None else None,
            user.id,
            to_db(now),
            to_db(now),
        ),
    )
    ticket_id = cursor.lastrowid
    if ticket_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
        raise RuntimeError("INSERT INTO tickets returned no rowid")
    for entry in scaffold:
        write_version(
            conn, ticket_id, entry.path, entry.content, entry.mode, author_user_id=user.id, now=now
        )
    return ticket_id


# ------------------------------------------------------------------ import (SPEC §12, §17)


def import_ticket(
    db: Database,
    space: SpaceConfig,
    source: JiraSource | None,
    user: Me,
    body: ImportRequest,
    mappings: Sequence[FieldMapping] = (),
) -> ImportResult:
    """Take in a Jira issue by key or link (manual take-in: the importer owns it).

    Not configured → 501; an unparseable ref, a project outside the Space's Jira projects or a key
    AgentOS can't use → 422; imported before → that ticket, `created: false`, without a Jira
    call. Jira can't see it → 404 `ticket_not_found`; Jira rejects the service account or is
    down → 502 `upstream_error`. Another ticket already has the key → 409
    `key_taken`. The fetch happens outside the write transaction; inside it the import is
    re-checked, so two concurrent imports of one issue end with one ticket.

    The ticket gets the key, the source link, the mapped fields (imported text is stored as plain,
    untrusted data), the Space's `default_repo` and the template, exactly like a native ticket.

    A `base_branch` field mapping (request 22B) on a repo ticket: its Jira field is read in the
    same call and the rules set the ticket's own base branch (`mapping`), or leave it blank with
    a reason; a blank mapped ticket does not fall back to the repo default (blocked from
    dispatch until someone sets one). No mapping: the repo default applies (`default`).
    """
    if source is None:
        raise ApiError(
            501,
            "not_implemented",
            "Jira import isn't configured (AGENTOS_JIRA_URL and AGENTOS_JIRA_SERVICE_TOKEN)",
        )
    external_id = source.parse_ref(body.ref)
    if external_id is None:
        raise ApiError(422, "validation_error", "ref: not a jira issue key or link to one")
    _check_import_key(space.jira_projects, external_id)  # the issue key, before any Jira call
    with db.read() as conn:
        if (key := _imported_key(conn, "jira", external_id)) is not None:
            return ImportResult(ticket=_detail(conn, space, key, user.id), created=False)
        workflows.for_ticket(conn, body.workflow_id)  # 422 before the Jira call (rechecked below)
    repo = space.default_repo
    mapping = mapping_for(mappings, "base_branch")
    if repo is None:
        mapping = None  # a no-repo ticket has no base branch
    try:
        fetched = source.fetch(external_id, (mapping.field,) if mapping is not None else ())
    except JiraNotFound:
        raise ApiError(404, "ticket_not_found", f"jira can't see {external_id}") from None
    except JiraUnauthorized:
        raise ApiError(
            502,
            "upstream_error",
            "jira rejected the service account (AGENTOS_JIRA_SERVICE_EMAIL / _TOKEN)",
        ) from None
    except JiraUnavailable:
        raise ApiError(502, "upstream_error", "jira didn't answer; try again shortly") from None
    _check_import_key(space.jira_projects, fetched.key)  # a moved issue answers with its new key
    new = _NewTicket(
        key=fetched.key,
        title=fetched.title[:MAX_TITLE_CHARS].strip() or fetched.key,
        description=fetched.description,
        type=fetched.type,
        priority=fetched.priority,
        labels=_fit_labels(fetched.labels),
        repo=repo,
        base_branch=None,
        base_branch_source="default",
        base_branch_set_by=None,
        owner_id=user.id,
        source=SourceLink(source="jira", external_id=fetched.external_id, url=fetched.url),
        workflow_id=body.workflow_id,
    )
    if mapping is not None:
        values = fetched.field_values.get(mapping.field, [])
        mapped = apply_rules(mapping.rules, values, mapping.field_name)
        new = replace(
            new,
            base_branch=mapped.value,
            base_branch_source="mapping",
            base_branch_source_value=join_values(values),
            base_branch_reason=mapped.reason,
        )
    with db.tx() as conn:
        if (key := _imported_key(conn, "jira", fetched.external_id)) is not None:
            return ImportResult(ticket=_detail(conn, space, key, user.id), created=False)
        if conn.execute("SELECT 1 FROM tickets WHERE key = ?", (new.key,)).fetchone():
            raise ApiError(409, "key_taken", f"Another ticket already has the key {new.key}")
        _insert_ticket(conn, space, user, new)
        return ImportResult(ticket=_detail(conn, space, new.key, user.id), created=True)


def _check_import_key(projects: Sequence[str], key: str) -> None:
    """The key must be a valid ticket key, in one of the Space's Jira projects when those are set
    (the Space is the trust boundary)."""
    validate_key(key)
    project = key.rsplit("-", 1)[0]
    if projects and project not in projects:
        raise ApiError(
            422,
            "validation_error",
            f"ref: {key} is not in this Space's Jira projects ({', '.join(projects)})",
        )


def _imported_key(conn: Conn, source: str, external_id: str) -> str | None:
    """The key of the ticket imported from (source, external_id), if any."""
    row = conn.execute(
        "SELECT key FROM tickets WHERE source = ? AND external_id = ?", (source, external_id)
    ).fetchone()
    return None if row is None else str(row["key"])


def _fit_labels(labels: Sequence[str]) -> list[str]:
    """Imported labels within the label limits: what doesn't fit is dropped, never an error."""
    out: list[str] = []
    for label in labels:
        label = label.strip()
        if label and len(label) <= MAX_LABEL_CHARS and label not in out:
            out.append(label)
    return out[:MAX_LABELS]


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
    """An owner must be an enabled, approved user (a `pending` one can't see the ticket)."""
    row = conn.execute("SELECT disabled, role FROM users WHERE id = ?", (owner_id,)).fetchone()
    if row is None or row["disabled"] or row["role"] == "pending":
        raise ApiError(422, "user_not_found", f"owner_id: no active user {owner_id}")


# ------------------------------------------------------------------ patch and pipeline

_PATCHABLE = (
    "title",
    "description",
    "type",
    "priority",
    "labels",
    "state",
    "repo",
    "base_branch",
    "owner_id",
)
# What a waiting client run (queued or dispatched; its package is built at the take) will run on
# its owner's machine besides the context files: the owner, and the fields its prompts render.
_CLIENT_RUN_INTENT = frozenset({"owner_id", "title", "description", "type"})


def _base_branch_provenance(
    row: Row, values: dict[str, Any], changes: dict[str, Any], user: Me, *, locked: bool
) -> dict[str, Any]:
    """The `base_branch_source` / `_set_by` / `_reason` columns after a PATCH (`values`: the
    patched fields). A locked ticket keeps them (its branch and repo can't change)."""
    keep = {
        "base_branch_source": row["base_branch_source"],
        "base_branch_set_by": row["base_branch_set_by"],
        "base_branch_reason": row["base_branch_reason"],
    }
    if locked:
        return keep
    reset = {
        "base_branch_source": "default",
        "base_branch_set_by": None,
        "base_branch_reason": None,
    }
    if "base_branch" in changes or values["repo"] is None:
        own = values["base_branch"]
        if own is None:
            return reset
        if own == row["base_branch"] and row["base_branch_source"] == "manual":
            return keep  # the same branch again: still set by whoever set it
        return {
            "base_branch_source": "manual",
            "base_branch_set_by": user.id,
            "base_branch_reason": None,
        }
    if values["repo"] != row["repo"]:
        return reset
    return keep


def patch_ticket(
    db: Database, space: SpaceConfig, key: str, user: Me, body: TicketPatch
) -> TicketDetail:
    """Apply the set fields. `state`, `repo` and `base_branch` changes → 409 `run_active` while a
    run is active; `owner_id`, `title`, `description` and `type` changes → 409 `run_active` while
    a client run waits (queued or dispatched), so it runs what its owner dispatched. A `state`
    change clears the resolution (`resolve_ticket` sets it).

    `repo` must be a catalog name (422 otherwise); null switches to no-repo mode, which clears
    `base_branch` and disables the pipeline's `requires_repo` stages (as creation does). Another
    repo drops the ticket's own base branch (back to `default`) unless the PATCH sets one.
    `base_branch` "x" → the ticket's own, `manual`, set by `user` (`source_value` is kept for the
    record); null → `default` (the repo's default_branch).

    Locked (`base_branch_locked_by_run`: a run has started work from the base branch): a change
    of `repo` or the ticket's own `base_branch` → 409 `base_branch_locked`; sending the current
    values is fine and changes nothing."""
    changes = body.model_dump(exclude_unset=True)
    with db.tx() as conn:
        row = get_ticket_row(conn, key)
        values: dict[str, Any] = {c: row[c] for c in _PATCHABLE}
        for name, value in changes.items():
            if name == "title":
                value = _title(value)
            elif name == "labels":
                value = json.dumps(_labels(value))
            elif name == "repo" and value is not None:
                value = space.require_repo(value).name
            elif name == "base_branch" and value is not None:
                value = validate_base_branch(value)
            elif name == "owner_id" and value is not None:
                _check_owner(conn, value)
            values[name] = value
        if values["repo"] is None:
            if changes.get("base_branch") is not None:
                raise ApiError(422, "validation_error", "base_branch: a no-repo ticket has none")
            values["base_branch"] = None
        elif values["repo"] != row["repo"] and "base_branch" not in changes:
            values["base_branch"] = None  # another repo starts from its own default
        locked_by = row["base_branch_locked_by_run"]
        if locked_by is not None and (
            values["repo"] != row["repo"] or values["base_branch"] != row["base_branch"]
        ):
            raise ApiError(
                409,
                "base_branch_locked",
                f"The base branch is locked: run #{locked_by} has started work on it",
            )
        base = _base_branch_provenance(row, values, changes, user, locked=locked_by is not None)
        changed = {c for c, v in values.items() if v != row[c]}
        changed |= {c for c, v in base.items() if v != row[c]}
        if not changed:
            return _detail(conn, space, key, user.id)
        # Repo fields count as changed only if the resolved workspace changes (setting the base
        # branch to the repo's default changes nothing a run sees).
        guarded = changed & {"state"}
        before = resolve_workspace(row, space)
        after = space.resolve(
            values["repo"], values["base_branch"], source=base["base_branch_source"]
        )
        if (before.mode, before.name, before.url, before.base_branch) != (
            after.mode,
            after.name,
            after.url,
            after.base_branch,
        ):
            guarded |= changed & {"repo", "base_branch"}
        if guarded and active_run_id(conn, row["id"]) is not None:
            raise ApiError(
                409, "run_active", f"Can't change {', '.join(sorted(guarded))} during a run"
            )

        intent = changed & _CLIENT_RUN_INTENT
        if intent and waiting_client_run_id(conn, row["id"]) is not None:
            raise ApiError(
                409,
                "run_active",
                f"Can't change {', '.join(sorted(intent))} while a run waits for a client",
            )
        now = to_db(utcnow())
        conn.execute(
            "UPDATE tickets SET title = :title, description = :description, type = :type, "
            "priority = :priority, labels = :labels, state = :state, repo = :repo, "
            "base_branch = :base_branch, "
            "base_branch_source = :base_branch_source, base_branch_set_by = :base_branch_set_by, "
            "base_branch_reason = :base_branch_reason, owner_id = :owner_id, "
            "updated_at = :updated_at WHERE id = :id",
            {
                **values,
                **base,
                "updated_at": now,
                "id": row["id"],
            },
        )
        if "state" in changed:  # PATCH carries no outcome: reopen, or done/closed unresolved
            conn.execute(_SQL_CLEAR_RESOLUTION, (row["id"],))
        if row["repo"] is not None and values["repo"] is None:
            pipeline = ticket_pipeline(row)
            stripped = _without_repo_stages(pipeline)
            if stripped != pipeline:
                conn.execute(
                    "UPDATE tickets SET pipeline = ?, pipeline_version = pipeline_version + 1 "
                    "WHERE id = ?",
                    (stripped.model_dump_json(), row["id"]),
                )
        return _detail(conn, space, key, user.id)


_RESOLVED_STATE: dict[ResolveOutcome, str] = {"completed": "done", "abandoned": "closed"}


def resolve_ticket(
    db: Database, space: SpaceConfig, key: str, user: Me, body: ResolveRequest
) -> TicketDetail:
    """Close the ticket as `completed` (state done) or `abandoned` (state closed), with an optional
    note (stripped; blank → null). 409 `run_active` while a run is active. Resolving a resolved
    ticket again replaces its resolution (e.g. Abandon → Complete, or a new note)."""
    note = (body.note or "").strip() or None
    with db.tx() as conn:
        row = get_ticket_row(conn, key)
        if active_run_id(conn, row["id"]) is not None:
            raise ApiError(409, "run_active", "Can't close a ticket during a run")
        now = to_db(utcnow())
        conn.execute(
            "UPDATE tickets SET state = ?, resolution_outcome = ?, resolution_note = ?, "
            "resolved_by = ?, resolved_at = ?, updated_at = ? WHERE id = ?",
            (_RESOLVED_STATE[body.outcome], body.outcome, note, user.id, now, now, row["id"]),
        )
        return _detail(conn, space, key, user.id)


def put_pipeline(
    db: Database,
    runtimes: RuntimesConfig,
    space: SpaceConfig,
    key: str,
    user: Me,
    body: PipelineUpdate,
) -> TicketDetail:
    """409 `run_active` / `stale_version`; 422 `invalid_pipeline` (incl. an enabled
    `requires_repo` stage on a no-repo ticket) or `render_error` (every agent prompt must render,
    so a template typo surfaces on save, not at dispatch). The ticket's `workflow` record stays:
    it says where the pipeline was copied from, not that it is unchanged."""
    pipeline = body.pipeline
    with db.tx() as conn:
        row = _pipeline_row(conn, key, body.base_version)
        if workspace_mode(row) == "none":
            for stage in pipeline.stages:
                if stage.enabled and stage.requires_repo:
                    raise ApiError(
                        422,
                        "invalid_pipeline",
                        f"stage {stage.id}: requires a repo; this ticket has none",
                    )
        _store_pipeline(conn, row, space, runtimes, pipeline)
        return _detail(conn, space, key, user.id)


def switch_workflow(
    db: Database,
    runtimes: RuntimesConfig,
    space: SpaceConfig,
    key: str,
    user: Me,
    body: WorkflowSwitch,
) -> TicketDetail:
    """Replace the ticket's pipeline with a copy of the workflow's and record the workflow (its
    current version). Allowed after runs: the ticket branch carries on, and the board reads the
    run history by stage id (stages the new pipeline shares keep their state; new ones start
    pending). A no-repo ticket gets `requires_repo` stages disabled, as at creation.

    409 `run_active` / `stale_version` (`base_version` = the ticket's `pipeline_version`); 404
    `not_found` for an unknown workflow; 422 `invalid_pipeline` / `render_error` when the copy
    fails the checks a pipeline save runs (e.g. a stage pinned to a runtime removed since)."""
    with db.tx() as conn:
        row = _pipeline_row(conn, key, body.base_version)
        workflow = workflows.get(conn, body.workflow_id)
        pipeline = workflow.pipeline
        if workspace_mode(row) == "none":
            pipeline = _without_repo_stages(pipeline)
        _store_pipeline(conn, row, space, runtimes, pipeline)
        conn.execute(
            "UPDATE tickets SET workflow_id = ?, workflow_name = ?, workflow_version = ? "
            "WHERE id = ?",
            (workflow.id, workflow.name, workflow.version, row["id"]),
        )
        return _detail(conn, space, key, user.id)


def _pipeline_row(conn: Conn, key: str, base_version: int) -> Row:
    """The ticket row for a pipeline change: 409 `run_active` during a run, 409 `stale_version`
    unless `base_version` is its `pipeline_version`."""
    row = get_ticket_row(conn, key)
    if active_run_id(conn, row["id"]) is not None:
        raise ApiError(409, "run_active", "The pipeline is locked while a run is active")
    if base_version != row["pipeline_version"]:
        raise ApiError(409, "stale_version", "The pipeline was changed by someone else")
    return row


def _store_pipeline(
    conn: Conn, row: Row, space: SpaceConfig, runtimes: RuntimesConfig, pipeline: Pipeline
) -> None:
    """Check `pipeline` (422 `invalid_pipeline` / `render_error`) and store it, version + 1."""
    validate_pipeline(pipeline, runtimes=runtimes.runtimes.keys())
    agent_stages = [s for s in pipeline.stages if s.kind == "agent"]
    defaults = templates.current_defaults(conn)
    render_stages(0, ticket_ref(row, space), agent_stages, runtimes, defaults=defaults)
    conn.execute(
        "UPDATE tickets SET pipeline = ?, pipeline_version = pipeline_version + 1, "
        "updated_at = ? WHERE id = ?",
        (pipeline.model_dump_json(), to_db(utcnow()), row["id"]),
    )
