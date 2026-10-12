"""Settings > Space, Repos, Intake, Runtimes and Runner: read and save each area's document (SPEC
"Settings in the app"). Storage and the cached read side every feature uses live in
`agentos.space_store`.

Every save: validate (422 `validation_error` with the field path), then in one transaction check
`base_version` (409 `stale_version`) and the cross-area rules, write version+1, and after the
commit drop the SpaceStore cache so the change applies at once.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from agentos.db import Conn, Database
from agentos.errors import ApiError
from agentos.models import (
    IntakeQuerySpec,
    IntakeSettings,
    IntakeSettingsUpdate,
    Me,
    RepoEntry,
    RepoSettings,
    RepoSettingsUpdate,
    RunnerSettings,
    RunnerSettingsUpdate,
    RuntimeEntry,
    RuntimeSettings,
    RuntimeSettingsUpdate,
    SpaceSettings,
    SpaceSettingsUpdate,
)
from agentos.pipeline.runtime import NamedRuntime
from agentos.pipeline.space import RepoSpec, RunnerSection, SpaceSection, query_id
from agentos.sources.jira import JiraSearch
from agentos.space_store import (
    Area,
    AreaRow,
    SpaceStore,
    queries_of,
    read_area,
    repos_of,
    runner_of,
    runner_value,
    runtime_entries_of,
    runtimes_value,
    section_of,
    write_area,
)
from agentos.templates.service import current as current_template
from agentos.ttlcache import TtlCache

_M = TypeVar("_M", bound=BaseModel)
_LABELS: dict[Area, str] = {
    "space": "Space settings",
    "repos": "repos",
    "intake": "intake queries",
    "runtimes": "runtimes",
    "runner": "runner settings",
}


# ------------------------------------------------------------------ space


def get_space(db: Database) -> SpaceSettings:
    with db.read() as conn:
        return _space_settings(_row(conn, "space"))


def put_space(
    db: Database, store: SpaceStore, actor: Me, body: SpaceSettingsUpdate, *, jira_auth: bool
) -> SpaceSettings:
    """Replace the Space settings. 422 when `key_prefix` is one of `jira_projects`, a project
    repeats, `jira_projects` is empty while sign-in is with Jira (`jira_auth`), or `default_repo`
    isn't in the repo catalog."""
    section = _validated(SpaceSection, body.model_dump(exclude={"base_version"}))
    if jira_auth and not section.jira_projects:
        raise ApiError(
            422,
            "validation_error",
            "jira_projects: at least one is needed while people sign in with Jira",
        )
    with db.tx() as conn:
        row = _current(conn, "space", body.base_version)
        default = section.default_repo
        if default is not None and default not in {r.name for r in _repos(conn)}:
            raise ApiError(
                422, "validation_error", f"default_repo: {default!r} is not in this Space's repos"
            )
        write_area(
            conn,
            "space",
            section.model_dump(mode="json"),
            version=row.version + 1,
            actor_id=actor.id,
        )
        result = _space_settings(_row(conn, "space"))
    store.invalidate()
    return result


def _space_settings(row: AreaRow) -> SpaceSettings:
    section = section_of(row)
    return SpaceSettings(
        name=section.name,
        default_repo=section.default_repo,
        key_prefix=section.key_prefix,
        jira_projects=list(section.jira_projects),
        claim_timeout_s=section.claim_timeout_s,
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


# ------------------------------------------------------------------ repos


def get_repos(db: Database) -> RepoSettings:
    with db.read() as conn:
        return _repo_settings(conn)


def put_repos(db: Database, store: SpaceStore, actor: Me, body: RepoSettingsUpdate) -> RepoSettings:
    """Replace the repo catalog (in the order sent). A name missing from the body is a removal:
    409 `in_use` while open tickets or the Space's `default_repo` use it."""
    specs: list[RepoSpec] = []
    for i, entry in enumerate(body.repos):
        spec = _validated(RepoSpec, entry.model_dump(), f"repos.{i}")
        if any(s.name == spec.name for s in specs):
            raise ApiError(
                422, "validation_error", f"repos.{i}.name: {spec.name!r} is listed twice"
            )
        specs.append(spec)
    with db.tx() as conn:
        row = _current(conn, "repos", body.base_version)
        kept = {s.name for s in specs}
        removed = [r.name for r in repos_of(row) if r.name not in kept]
        if removed:
            _check_unused(conn, removed)
        value = {"repos": [s.model_dump(mode="json") for s in specs]}
        write_area(conn, "repos", value, version=row.version + 1, actor_id=actor.id)
        result = _repo_settings(conn)
    store.invalidate()
    return result


def _check_unused(conn: Conn, removed: list[str]) -> None:
    default = section_of(read_area(conn, "space")).default_repo
    counts = _open_counts(conn)
    problems: list[str] = []
    for name in removed:
        uses: list[str] = []
        if n := counts.get(name, 0):
            uses.append(f"{n} open ticket{'s' if n != 1 else ''} use{'s' if n == 1 else ''} it")
        if name == default:
            uses.append("it is the Space's default repo")
        if uses:
            problems.append(f"{name!r} ({' and '.join(uses)})")
    if problems:
        raise ApiError(409, "in_use", f"Can't remove {'; '.join(problems)}")


def _open_counts(conn: Conn) -> Mapping[str, int]:
    rows = conn.execute(
        "SELECT repo, COUNT(*) AS n FROM tickets WHERE state = 'open' AND repo IS NOT NULL "
        "GROUP BY repo"
    ).fetchall()
    return {r["repo"]: r["n"] for r in rows}


def _repos(conn: Conn) -> tuple[RepoSpec, ...]:
    return repos_of(read_area(conn, "repos"))


def _repo_settings(conn: Conn) -> RepoSettings:
    row = _row(conn, "repos")
    counts = _open_counts(conn)
    return RepoSettings(
        repos=[
            RepoEntry(
                name=r.name,
                url=r.url,
                default_branch=r.default_branch,
                open_ticket_count=counts.get(r.name, 0),
            )
            for r in repos_of(row)
        ],
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


# ------------------------------------------------------------------ intake


def get_intake(db: Database) -> IntakeSettings:
    with db.read() as conn:
        return _intake_settings(_row(conn, "intake"))


def put_intake(
    db: Database,
    store: SpaceStore,
    cache: TtlCache[str, JiraSearch | str],
    actor: Me,
    body: IntakeSettingsUpdate,
) -> IntakeSettings:
    """Replace the intake queries (in tab order). A query with `id: null` gets a new unique id
    made from its name; sent ids are kept (422 if one repeats). Drops the intake cache."""
    sent: set[str] = set()
    for i, query in enumerate(body.queries):
        if query.id is not None:
            if query.id in sent:
                raise ApiError(
                    422, "validation_error", f"queries.{i}.id: {query.id!r} is listed twice"
                )
            sent.add(query.id)
    taken = set(sent)
    specs: list[IntakeQuerySpec] = []
    for query in body.queries:
        qid = query.id
        if qid is None:
            qid = query_id(query.name, taken)
            taken.add(qid)
        specs.append(IntakeQuerySpec(id=qid, name=query.name, jql=query.jql))
    with db.tx() as conn:
        row = _current(conn, "intake", body.base_version)
        value = {"queries": [s.model_dump(mode="json") for s in specs]}
        write_area(conn, "intake", value, version=row.version + 1, actor_id=actor.id)
        result = _intake_settings(_row(conn, "intake"))
    cache.clear()
    store.invalidate()
    return result


def _intake_settings(row: AreaRow) -> IntakeSettings:
    return IntakeSettings(
        queries=list(queries_of(row)),
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


# ------------------------------------------------------------------ runtimes


def get_runtimes(db: Database) -> RuntimeSettings:
    with db.read() as conn:
        return _runtime_settings(_row(conn, "runtimes"))


def put_runtimes(
    db: Database, store: SpaceStore, actor: Me, body: RuntimeSettingsUpdate
) -> RuntimeSettings:
    """Replace the runtimes (in order: the first is the server default). A name missing from the
    body is a removal: 409 `in_use` while the current template's `defaults` or stages use it."""
    entries: list[NamedRuntime] = []
    for i, entry in enumerate(body.runtimes):
        named = _validated(NamedRuntime, entry.model_dump(), f"runtimes.{i}")
        if any(e.name == named.name for e in entries):
            raise ApiError(
                422, "validation_error", f"runtimes.{i}.name: {named.name!r} is listed twice"
            )
        entries.append(named)
    with db.tx() as conn:
        row = _current(conn, "runtimes", body.base_version)
        kept = {e.name for e in entries}
        removed = [e.name for e in runtime_entries_of(row) if e.name not in kept]
        if removed:
            _check_runtimes_unused(conn, removed)
        write_area(
            conn, "runtimes", runtimes_value(entries), version=row.version + 1, actor_id=actor.id
        )
        result = _runtime_settings(_row(conn, "runtimes"))
    store.invalidate()
    return result


def _check_runtimes_unused(conn: Conn, removed: list[str]) -> None:
    """409 `in_use` naming each removed runtime the current template still uses (its `defaults`
    or any agent stage). Tickets' own pipelines aren't checked: a stage pinned to a removed
    runtime fails its dispatch (422 `invalid_stage`) until the ticket's pipeline is edited."""
    template = current_template(conn).template
    problems: list[str] = []
    for name in removed:
        uses: list[str] = []
        if template.defaults.runtime == name:
            uses.append("it is the template's default runtime")
        n = sum(1 for s in template.pipeline.stages if s.kind == "agent" and s.runtime == name)
        if n:
            uses.append(f"{n} template stage{'s' if n != 1 else ''} use{'s' if n == 1 else ''} it")
        if uses:
            problems.append(f"{name!r} ({' and '.join(uses)})")
    if problems:
        raise ApiError(409, "in_use", f"Can't remove {'; '.join(problems)}")


def _runtime_settings(row: AreaRow) -> RuntimeSettings:
    return RuntimeSettings(
        runtimes=[
            RuntimeEntry(
                name=e.name,
                argv=e.argv,
                model_args=e.model_args,
                agent_args=e.agent_args,
                prompt_via=e.prompt_via,
                prompt_args=e.prompt_args,
            )
            for e in runtime_entries_of(row)
        ],
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


# ------------------------------------------------------------------ runner


def get_runner(
    db: Database, *, launcher: Literal["local", "github"], token_set: bool
) -> RunnerSettings:
    """`launcher` (AGENTOS_LAUNCHER) and `token_set` (AGENTOS_GITHUB_TOKEN is set) come from env."""
    with db.read() as conn:
        return _runner_settings(_row(conn, "runner"), launcher=launcher, token_set=token_set)


def put_runner(
    db: Database,
    store: SpaceStore,
    actor: Me,
    body: RunnerSettingsUpdate,
    *,
    launcher: Literal["local", "github"],
    token_set: bool,
) -> RunnerSettings:
    """Replace where runs are dispatched. The github launcher reads it at its next launch."""
    section = _validated(RunnerSection, body.model_dump(exclude={"base_version"}))
    with db.tx() as conn:
        row = _current(conn, "runner", body.base_version)
        write_area(
            conn, "runner", runner_value(section), version=row.version + 1, actor_id=actor.id
        )
        result = _runner_settings(_row(conn, "runner"), launcher=launcher, token_set=token_set)
    store.invalidate()
    return result


def _runner_settings(
    row: AreaRow, *, launcher: Literal["local", "github"], token_set: bool
) -> RunnerSettings:
    runner = runner_of(row)
    return RunnerSettings(
        launcher=launcher,
        github_repo=runner.github_repo,
        workflow=runner.workflow,
        ref=runner.ref,
        token_set=token_set,
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


# ------------------------------------------------------------------ helpers


def _row(conn: Conn, area: Area) -> AreaRow:
    """The area's row. Seeded at startup, so a missing row is a server bug."""
    row = read_area(conn, area)
    if row is None:
        raise RuntimeError(f"the {area!r} settings are missing (seed_settings did not run)")
    return row


def _current(conn: Conn, area: Area, base_version: int) -> AreaRow:
    """The area's row, if `base_version` is its version; else 409 `stale_version`."""
    row = _row(conn, area)
    if base_version != row.version:
        raise ApiError(409, "stale_version", f"The {_LABELS[area]} were changed by someone else")
    return row


def _validated(model: type[_M], data: dict[str, Any], prefix: str = "") -> _M:
    """`model` from `data`, or 422 `validation_error` naming the first problem's field path."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        path = ".".join(p for p in (prefix, *(str(x) for x in err["loc"])) if p)
        message = err["msg"].removeprefix("Value error, ")
        raise ApiError(422, "validation_error", f"{path}: {message}" if path else message) from None
