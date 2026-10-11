"""Tickets (SPEC §4, §10, §13): create (key, slug, template copy, scaffold = bundle v1), import
from a ticket source (§12), the board list and detail, patch and pipeline edits.

The board is batch-loaded: a fixed number of queries for any number of tickets (tickets + owners,
latest runs + creators, their stage_runs, the ever-succeeded GROUP BY, TASKS.md of running
tickets (of the ticket, for the detail), chat counts). Ticket id lists are bound as one JSON array
(`json_each(?)`), so the SQL stays constant. Facts go through the pure `board.derive`, then
`board.for_viewer`.
"""

from __future__ import annotations

import json
import logging
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
    ImportRequest,
    ImportResult,
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
    WorkspaceMode,
)
from agentos.pipeline.manifest import render_stages
from agentos.pipeline.plan import Plan, parse_plan
from agentos.pipeline.render import TicketRef, render_scaffold
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.scaffold import names, slugify
from agentos.pipeline.segment import validate_pipeline
from agentos.pipeline.space import SpaceConfig
from agentos.sources.base import (
    SourceNotFound,
    SourceUnauthorized,
    SourceUnavailable,
    TicketSource,
)
from agentos.templates import service as templates
from agentos.tickets.board import ACTIVE, RunFact, StageRunFact, TicketFacts, derive, for_viewer
from agentos.validate import validate_base_branch, validate_key

log = logging.getLogger(__name__)

MAX_LABELS = 20
MAX_LABEL_CHARS = 50
MAX_TITLE_CHARS = 200  # TicketCreate.title max_length: an imported summary is cut to it

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
    "r.started_at, r.finished_at, r.cancel_requested_at, r.error, r.launcher, r.launch_ref, "
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


def workspace_mode(row: Row) -> WorkspaceMode:
    """Derived from `repo` alone (SPEC §17): the stored `workspace_mode` column only mirrors it.
    A legacy ticket whose URL matched no catalog entry has no `repo`, so it acts as no-repo."""
    return "repo" if row["repo"] is not None else "none"


def ticket_ref(row: Row, space: SpaceConfig) -> TicketRef:
    """The plain values rendering and manifest building need, from a `tickets` row, with the
    repo resolved through the Space's catalog."""
    repo = space.resolve(row["repo"], row["base_branch"])
    return TicketRef(
        key=row["key"],
        slug=row["slug"],
        title=row["title"],
        description=row["description"],
        type=row["type"],
        source_url=row["source_url"],
        repo_url=repo.url,
        base_branch=repo.base_branch,
        workspace_mode=repo.mode,
    )


def run_ticket_ref(row: Row, space: SpaceConfig) -> TicketRef:
    """`ticket_ref` for a run (dispatch, claim): 422 `validation_error` naming the repo if it is
    no longer in the catalog."""
    if row["repo"] is not None:
        space.require_repo(row["repo"])
    return ticket_ref(row, space)


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
        [r["id"], names(r["key"], r["slug"], workspace_mode(r)).tasks_md]
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
        launch_url=launch_url(run["launcher"], run["launch_ref"]),
    )


def launch_url(launcher: str, launch_ref: str | None) -> str | None:
    """The launcher's page for a run: the GitHub launcher's ref is the Actions run URL; the local
    launcher's is a PID (no page)."""
    if launcher == "github" and launch_ref is not None and launch_ref.startswith("https://"):
        return launch_ref
    return None


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
        "repo_name": row["repo"],
        "pr_url": row["pr_url"],
        "updated_at": from_db(row["updated_at"]),
    }
    active = run["id"] if run is not None and run["status"] in ACTIVE else None
    return fields, board.next_stage, active


def _detail(conn: Conn, space: SpaceConfig, key: str, viewer_id: int) -> TicketDetail:
    row = get_ticket_row(conn, key)
    facts = _load_facts(conn, [row], with_plan=True)
    fields, next_stage, active = _summary_fields(row, facts, viewer_id)
    plan = facts.plans.get(row["id"])
    repo = space.resolve(row["repo"], row["base_branch"])
    n = names(row["key"], row["slug"], repo.mode)
    return TicketDetail(
        **fields,
        description=row["description"],
        slug=row["slug"],
        skill_name=n.skill_name,
        workspace_mode=repo.mode,
        repo_url=repo.url,
        base_branch=repo.base_branch,
        branch=n.branch,
        pipeline=ticket_pipeline(row),
        pipeline_version=row["pipeline_version"],
        next_stage=next_stage,
        active_run_id=active,
        created_at=from_db(row["created_at"]),
        plan=list(plan.items) if plan is not None else None,
    )


# ------------------------------------------------------------------ create


def create_ticket(
    db: Database, settings: Settings, space: SpaceConfig, user: Me, body: TicketCreate
) -> TicketDetail:
    """Allocate the key, copy the current template's pipeline, render its scaffold and write it as
    bundle v1, all in one transaction (a render error leaves no ticket and no used key).

    `repo` omitted → the Space's `default_repo`; null → no-repo mode; not in the catalog → 422.
    `base_branch` is stored only when given (null = the repo's default_branch)."""
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
        key = _allocate_key(conn, settings.key_prefix)
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
                owner_id=body.owner_id,
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
    base_branch: str | None  # the ticket's own; None = the repo's default_branch
    owner_id: int | None
    source: SourceLink | None = None


def _insert_ticket(conn: Conn, space: SpaceConfig, user: Me, new: _NewTicket) -> int:
    """Insert the ticket with a copy of the current template's pipeline and its rendered scaffold
    as bundle v1 (inside the caller's transaction, so a render error leaves nothing behind)."""
    resolved = space.resolve(new.repo, new.base_branch)
    now = utcnow()
    slug = slugify(new.title, new.key)
    template = templates.current(conn)
    pipeline = template.template.pipeline
    if resolved.mode == "none":
        pipeline = _without_repo_stages(pipeline)
    source = new.source
    ref = TicketRef(
        key=new.key,
        slug=slug,
        title=new.title,
        description=new.description,
        type=new.type,
        source_url=source.url if source is not None else None,
        repo_url=resolved.url,
        base_branch=resolved.base_branch,
        workspace_mode=resolved.mode,
    )
    scaffold = render_scaffold(template.template.scaffold, ref)
    cursor = conn.execute(
        "INSERT INTO tickets(key, slug, title, description, type, priority, labels, state, "
        "workspace_mode, repo, base_branch, template_id, pipeline, pipeline_version, "
        "owner_id, source, external_id, source_url, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
        (
            new.key,
            slug,
            new.title,
            new.description,
            new.type,
            new.priority,
            json.dumps(new.labels),
            resolved.mode,
            new.repo,
            new.base_branch,
            template.id,
            pipeline.model_dump_json(),
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
    settings: Settings,
    space: SpaceConfig,
    source: TicketSource | None,
    user: Me,
    body: ImportRequest,
) -> ImportResult:
    """Take in a source ticket by key or link (manual take-in: the importer owns it).

    Not configured → 501; an unparseable ref, a project outside AGENTOS_JIRA_PROJECTS or a key
    AgentOS can't use → 422; imported before → that ticket, `created: false`, without a source
    call. The source can't see it → 404 `ticket_not_found`; the source rejects the service
    account or is down → 502 `upstream_error`. Another ticket already has the key → 409
    `key_taken`. The fetch happens outside the write transaction; inside it the import is
    re-checked, so two concurrent imports of one issue end with one ticket.

    The ticket gets the key, the source link, the mapped fields (imported text is stored as plain,
    untrusted data), the Space's `default_repo` and the template, exactly like a native ticket.
    """
    if source is None:
        raise ApiError(
            501,
            "not_implemented",
            "Jira import isn't configured (AGENTOS_JIRA_URL and AGENTOS_JIRA_SERVICE_TOKEN)",
        )
    external_id = source.parse_ref(body.ref)
    if external_id is None:
        raise ApiError(
            422, "validation_error", f"ref: not a {source.name} issue key or link to one"
        )
    if source.name == "jira":  # the external id is the issue key: check it before any Jira call
        _check_import_key(settings, external_id)
    with db.read() as conn:
        if (key := _imported_key(conn, source.name, external_id)) is not None:
            return ImportResult(ticket=_detail(conn, space, key, user.id), created=False)
    try:
        fetched = source.fetch(external_id)
    except SourceNotFound:
        raise ApiError(404, "ticket_not_found", f"{source.name} can't see {external_id}") from None
    except SourceUnauthorized:
        raise ApiError(
            502,
            "upstream_error",
            f"{source.name} rejected the service account (AGENTOS_JIRA_SERVICE_EMAIL / _TOKEN)",
        ) from None
    except SourceUnavailable:
        raise ApiError(
            502, "upstream_error", f"{source.name} didn't answer; try again shortly"
        ) from None
    _check_import_key(settings, fetched.key)  # a moved issue answers with its new key
    new = _NewTicket(
        key=fetched.key,
        title=fetched.title[:MAX_TITLE_CHARS].strip() or fetched.key,
        description=fetched.description,
        type=fetched.type,
        priority=fetched.priority,
        labels=_fit_labels(fetched.labels),
        repo=space.default_repo,
        base_branch=None,
        owner_id=user.id,
        source=SourceLink(source=source.name, external_id=fetched.external_id, url=fetched.url),
    )
    with db.tx() as conn:
        if (key := _imported_key(conn, source.name, fetched.external_id)) is not None:
            return ImportResult(ticket=_detail(conn, space, key, user.id), created=False)
        if conn.execute("SELECT 1 FROM tickets WHERE key = ?", (new.key,)).fetchone():
            raise ApiError(409, "key_taken", f"Another ticket already has the key {new.key}")
        _insert_ticket(conn, space, user, new)
        return ImportResult(ticket=_detail(conn, space, new.key, user.id), created=True)


def _check_import_key(settings: Settings, key: str) -> None:
    """The key must be a valid ticket key, in one of the Space's Jira projects when those are set
    (the Space is the trust boundary)."""
    validate_key(key)
    project = key.rsplit("-", 1)[0]
    if settings.jira_projects and project not in settings.jira_projects:
        projects = ", ".join(settings.jira_projects)
        raise ApiError(
            422, "validation_error", f"ref: {key} is not in this Space's Jira projects ({projects})"
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


def patch_ticket(
    db: Database, space: SpaceConfig, key: str, user: Me, body: TicketPatch
) -> TicketDetail:
    """Apply the set fields. `state`, `repo` and `base_branch` changes → 409 `run_active` while a
    run is active. The slug never changes (it names the skill directory).

    `repo` must be a catalog name (422 otherwise); null switches to no-repo mode, which clears
    `base_branch` and disables the pipeline's `requires_repo` stages (as creation does).
    `base_branch` null = the repo's default_branch."""
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
        changed = {c for c, v in values.items() if v != row[c]}
        if not changed:
            return _detail(conn, space, key, user.id)
        # Repo fields count as changed only if the resolved workspace changes (setting the base
        # branch to the repo's default changes nothing a run sees).
        guarded = changed & {"state"}
        before = space.resolve(row["repo"], row["base_branch"])
        if before != space.resolve(values["repo"], values["base_branch"]):
            guarded |= changed & {"repo", "base_branch"}
        if guarded and active_run_id(conn, row["id"]) is not None:
            raise ApiError(
                409, "run_active", f"Can't change {', '.join(sorted(guarded))} during a run"
            )
        now = to_db(utcnow())
        conn.execute(
            "UPDATE tickets SET title = :title, description = :description, type = :type, "
            "priority = :priority, labels = :labels, state = :state, repo = :repo, "
            "workspace_mode = :workspace_mode, base_branch = :base_branch, owner_id = :owner_id, "
            "updated_at = :updated_at WHERE id = :id",
            {
                **values,
                "workspace_mode": "repo" if values["repo"] is not None else "none",
                "updated_at": now,
                "id": row["id"],
            },
        )
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
    so a template typo surfaces on save, not at dispatch)."""
    pipeline = body.pipeline
    with db.tx() as conn:
        row = get_ticket_row(conn, key)
        if active_run_id(conn, row["id"]) is not None:
            raise ApiError(409, "run_active", "The pipeline is locked while a run is active")
        if body.base_version != row["pipeline_version"]:
            raise ApiError(409, "stale_version", "The pipeline was changed by someone else")
        validate_pipeline(pipeline, runtimes=runtimes.runtimes.keys())
        if workspace_mode(row) == "none":
            for stage in pipeline.stages:
                if stage.enabled and stage.requires_repo:
                    raise ApiError(
                        422,
                        "invalid_pipeline",
                        f"stage {stage.id}: requires a repo; this ticket has none",
                    )
        agent_stages = [s for s in pipeline.stages if s.kind == "agent"]
        render_stages(0, ticket_ref(row, space), agent_stages, runtimes)
        conn.execute(
            "UPDATE tickets SET pipeline = ?, pipeline_version = pipeline_version + 1, "
            "updated_at = ? WHERE id = ?",
            (pipeline.model_dump_json(), to_db(utcnow()), row["id"]),
        )
        return _detail(conn, space, key, user.id)


# ------------------------------------------------------------------ repo backfill (startup)


def backfill_repos(db: Database, space: SpaceConfig) -> list[str]:
    """Give tickets created before the repo catalog (migration 0003) their catalog `repo`.

    Idempotent; run once at startup. A repo-mode ticket without `repo` gets the catalog entry
    whose URL matches its old `repo_url` (`space.repo_for_url`); a stored base branch equal to that
    repo's default_branch is cleared, so a catalog change reaches it. Unmatched tickets stay
    without a repo (they act as no-repo tickets) and are retried on the next startup. Returns their
    keys, which are also logged as one warning.
    """
    unmatched: list[str] = []
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT id, key, repo_url, base_branch FROM tickets "
            "WHERE repo IS NULL AND workspace_mode = 'repo' ORDER BY id"
        ).fetchall()
        for row in rows:
            spec = space.repo_for_url(row["repo_url"]) if row["repo_url"] else None
            if spec is None:
                unmatched.append(row["key"])
                continue
            own = row["base_branch"] if row["base_branch"] != spec.default_branch else None
            conn.execute(
                "UPDATE tickets SET repo = ?, base_branch = ? WHERE id = ?",
                (spec.name, own, row["id"]),
            )
    if unmatched:
        log.warning(
            "Tickets whose repo_url matches no [[repos]] entry in agentos.toml (they act as "
            "no-repo tickets until it does, or until their repo is set): %s",
            ", ".join(unmatched),
        )
    return unmatched
