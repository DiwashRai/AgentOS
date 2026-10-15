"""Settings > Space, Repos, Intake, Runtimes, Runner and Field mappings (plus the Jira field
picker and the mapping preview): read, check and save each area's document. Storage lives in
`agentos.space_store`.

Space, repos, intake, runner and mappings are versioned documents (`Document`), read and saved by
one generic `read` / `save`; `validate` runs a save's checks without writing. A save: check (one
422 `validation_error` listing every problem at its `value...` field: the area's pure check plus
the rules that need the db or the server's config), then in one transaction the stale check
(409 `stale_version`), the area's own refusals (repos: 409 `in_use`), the write at version+1 and
the area's after-write step, then its after-commit step. Features read the settings per request,
so the change applies at once. Runtimes keep their own read and save.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from agentos.clock import to_db, utcnow
from agentos.db import Conn, Database
from agentos.errors import ApiError, field_error, validation_error
from agentos.intake import service as intake
from agentos.models import (
    FieldMapping,
    FieldMappingEntry,
    IntakeDoc,
    IntakeQueryEntry,
    IntakeSettings,
    JiraField,
    LauncherName,
    MappingPreview,
    MappingPreviewRequest,
    MappingsDoc,
    MappingSettings,
    Me,
    RepoEntry,
    ReposDoc,
    RepoSettings,
    RunnerDoc,
    RunnerSettings,
    RuntimeEntry,
    RuntimeSettings,
    RuntimeSettingsUpdate,
    SpaceDoc,
    SpaceSettings,
    ValidationIssue,
)
from agentos.pipeline.mapping import (
    apply_rules,
    check_mapping,
    check_mappings,
    clean_values,
    mapping_for,
    split_values,
)
from agentos.pipeline.runtime import NamedRuntime
from agentos.pipeline.space import (
    VALUE,
    IntakeQuerySpec,
    RepoSpec,
    RunnerSection,
    SpaceSection,
    check_intake,
    check_repos,
    check_runner,
    check_space,
)
from agentos.sources.jira import JiraError, JiraNotFound, JiraSearch, JiraSource, JiraUnauthorized
from agentos.space_store import (
    Area,
    AreaRow,
    mappings_of,
    mappings_value,
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
from agentos.validate import parsed
from agentos.workflows import service as workflows

log = logging.getLogger(__name__)

_LABELS: dict[Area, str] = {
    "space": "Space settings",
    "repos": "repos",
    "intake": "intake queries",
    "runtimes": "runtimes",
    "runner": "runner settings",
    "mappings": "field mappings",
}


@dataclass(frozen=True)
class SettingsEnv:
    """What the settings documents take from the server: whether people sign in with Jira, the
    env's launcher and whether its GitHub token is set (shown read-only with the runner), and the
    intake cache a save of the queries drops."""

    jira_auth: bool
    launcher: LauncherName
    token_set: bool
    intake_cache: TtlCache[str, JiraSearch | str]


_V = TypeVar("_V", bound=BaseModel)  # the document as sent (`value`)
_D = TypeVar("_D")  # the checked, normalised document
_R = TypeVar("_R", bound=BaseModel)  # the document as read


@dataclass(frozen=True)
class Document(Generic[_V, _D, _R]):
    """One settings area as a versioned document.

    `check`: the rules that need neither the db nor a transaction (the pure check plus config
    rules), answering the normalised document or None, and every issue. `db_check`: the rules
    that read other areas, on the sent value (so they report alongside the others). `stored`: the
    db document. `read`: the read model of the area's row. `before_write` may refuse the save
    (409); `after_write` runs in the save's transaction; `after_commit` once it is committed."""

    area: Area
    check: Callable[[_V, SettingsEnv], tuple[_D | None, list[ValidationIssue]]]
    stored: Callable[[_D], dict[str, Any]]
    read: Callable[[Conn, AreaRow, SettingsEnv], _R]
    db_check: Callable[[Conn, _V], list[ValidationIssue]] | None = None
    before_write: Callable[[Conn, AreaRow, _D], None] | None = None
    after_write: Callable[[Conn, _D], None] | None = None
    after_commit: Callable[[SettingsEnv], None] | None = None


def read(db: Database, doc: Document[_V, _D, _R], env: SettingsEnv) -> _R:
    with db.read() as conn:
        return doc.read(conn, _row(conn, doc.area), env)


def validate(db: Database, doc: Document[_V, _D, _R], value: _V, env: SettingsEnv) -> None:
    """A save's checks without the save: nothing, or the 422 the save would give. A save's 409s
    (`stale_version`, `in_use`) aren't checked."""
    checked, issues = doc.check(value, env)
    with db.read() as conn:
        _raise_issues(conn, doc, value, checked, issues)


def save(
    db: Database,
    doc: Document[_V, _D, _R],
    actor: Me,
    value: _V,
    base_version: int,
    env: SettingsEnv,
) -> _R:
    """Replace the area's document with `value` (see the module docstring) and answer its read."""
    checked, issues = doc.check(value, env)

    with db.tx() as conn:
        normalised = _raise_issues(conn, doc, value, checked, issues)
        row = _current(conn, doc.area, base_version)
        if doc.before_write is not None:
            doc.before_write(conn, row, normalised)

        write_area(
            conn, doc.area, doc.stored(normalised), version=row.version + 1, actor_id=actor.id
        )
        if doc.after_write is not None:
            doc.after_write(conn, normalised)
        result = doc.read(conn, _row(conn, doc.area), env)

    if doc.after_commit is not None:
        doc.after_commit(env)
    return result


def _raise_issues(
    conn: Conn,
    doc: Document[_V, _D, _R],
    value: _V,
    checked: _D | None,
    issues: list[ValidationIssue],
) -> _D:
    """`checked`, or one 422 listing `issues` plus the db-dependent ones."""
    if doc.db_check is not None:
        issues = [*issues, *doc.db_check(conn, value)]
    if checked is None or issues:
        raise validation_error(issues)
    return checked


def _meta(row: AreaRow) -> dict[str, Any]:
    return {"version": row.version, "updated_at": row.updated_at, "updated_by": row.updated_by}


# ------------------------------------------------------------------ space


def _check_space(
    value: SpaceDoc, env: SettingsEnv
) -> tuple[SpaceSection | None, list[ValidationIssue]]:
    """Plus: at least one project while people sign in with Jira (`env.jira_auth`)."""
    section, issues = check_space(value)
    if env.jira_auth and not value.jira_projects:
        loc: list[str | int] = [*VALUE, "jira_projects"]
        message = "At least one is needed while people sign in with Jira."
        issues.append(ValidationIssue(loc=loc, message=message))
    return (None if issues else section), issues


def _space_db_check(conn: Conn, value: SpaceDoc) -> list[ValidationIssue]:
    """`default_repo` is in the repo catalog."""
    default = value.default_repo
    if default is None or default in {r.name for r in _repos(conn)}:
        return []
    message = f"{default} isn't one of this Space's repos."
    return [ValidationIssue(loc=[*VALUE, "default_repo"], message=message)]


def _space_settings(conn: Conn, row: AreaRow, env: SettingsEnv) -> SpaceSettings:
    value = SpaceDoc.model_validate(section_of(row).model_dump())
    return SpaceSettings(value=value, **_meta(row))


SPACE: Document[SpaceDoc, SpaceSection, SpaceSettings] = Document(
    area="space",
    check=_check_space,
    db_check=_space_db_check,
    stored=lambda section: section.model_dump(mode="json"),
    read=_space_settings,
)


# ------------------------------------------------------------------ repos


def _check_unused(conn: Conn, row: AreaRow, specs: tuple[RepoSpec, ...]) -> None:
    """A repo missing from the save is a removal: 409 `in_use` while open tickets or the Space's
    `default_repo` use it."""
    kept = {s.name for s in specs}
    removed = [r.name for r in repos_of(row) if r.name not in kept]
    if not removed:
        return

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


def _repo_settings(conn: Conn, row: AreaRow, env: SettingsEnv) -> RepoSettings:
    specs = repos_of(row)
    counts = _open_counts(conn)
    entries = [RepoEntry(name=r.name, url=r.url, default_branch=r.default_branch) for r in specs]
    return RepoSettings(
        value=ReposDoc(repos=entries),
        open_ticket_counts={r.name: counts.get(r.name, 0) for r in specs},
        **_meta(row),
    )


REPOS: Document[ReposDoc, tuple[RepoSpec, ...], RepoSettings] = Document(
    area="repos",
    check=lambda value, env: check_repos(value),
    stored=lambda specs: {"repos": [s.model_dump(mode="json") for s in specs]},
    read=_repo_settings,
    before_write=_check_unused,
)


# ------------------------------------------------------------------ intake


def _intake_settings(conn: Conn, row: AreaRow, env: SettingsEnv) -> IntakeSettings:
    queries = [IntakeQueryEntry.model_validate(q.model_dump()) for q in queries_of(row)]
    return IntakeSettings(value=IntakeDoc(queries=queries), **_meta(row))


def _drop_intake_cache(env: SettingsEnv) -> None:
    env.intake_cache.clear()


INTAKE: Document[IntakeDoc, tuple[IntakeQuerySpec, ...], IntakeSettings] = Document(
    area="intake",
    check=lambda value, env: check_intake(value),
    stored=lambda specs: {"queries": [s.model_dump(mode="json") for s in specs]},
    read=_intake_settings,
    after_commit=_drop_intake_cache,
)


# ------------------------------------------------------------------ runner


def _runner_settings(conn: Conn, row: AreaRow, env: SettingsEnv) -> RunnerSettings:
    value = RunnerDoc.model_validate(runner_of(row).model_dump())
    return RunnerSettings(value=value, launcher=env.launcher, token_set=env.token_set, **_meta(row))


RUNNER: Document[RunnerDoc, RunnerSection, RunnerSettings] = Document(
    area="runner",
    check=lambda value, env: check_runner(value),
    stored=runner_value,
    read=_runner_settings,
)


# ------------------------------------------------------------------ field mappings

# Tickets a `base_branch` mapping re-apply may change: repo tickets whose base branch came from a
# mapping, not locked by a run, and with no active run (as a PATCH, which answers `run_active`).
_SQL_MAPPED_TICKETS = (
    "SELECT t.id, t.key, t.base_branch, t.base_branch_source_value, t.base_branch_reason "
    "FROM tickets t WHERE t.repo IS NOT NULL AND t.base_branch_source = 'mapping' "
    "AND t.base_branch_locked_by_run IS NULL AND NOT EXISTS ("
    "  SELECT 1 FROM runs r WHERE r.ticket_id = t.id "
    "  AND r.status IN ('queued', 'dispatched', 'running')) "
    "ORDER BY t.id"
)
_FIELDS_KEY = "fields"  # the one key of the Jira fields cache


def _reapply_base_branch(conn: Conn, mappings: tuple[FieldMapping, ...]) -> None:
    """Re-apply a `base_branch` mapping to every ticket `_SQL_MAPPED_TICKETS` selects, from the
    Jira value stored at import (no Jira call), setting its base branch and blank reason.
    Hand-set (`manual`), repo-default (`default`) and locked tickets are never touched; with the
    `base_branch` mapping removed, mapped tickets keep what they have."""
    mapping = mapping_for(mappings, "base_branch")
    rows = conn.execute(_SQL_MAPPED_TICKETS).fetchall()
    if mapping is None:
        if rows:
            log.info(
                "Field mappings: no base_branch mapping any more; %d mapped ticket(s) keep their "
                "base branch",
                len(rows),
            )
        return

    now = to_db(utcnow())
    changed = 0
    for row in rows:
        values = split_values(row["base_branch_source_value"])
        mapped = apply_rules(mapping.rules, values, mapping.field_name)
        if (mapped.value, mapped.reason) == (row["base_branch"], row["base_branch_reason"]):
            continue
        conn.execute(
            "UPDATE tickets SET base_branch = ?, base_branch_reason = ?, updated_at = ? "
            "WHERE id = ?",
            (mapped.value, mapped.reason, now, row["id"]),
        )
        changed += 1
    log.info(
        "Field mappings: re-applied base_branch rules to %d ticket(s); %d changed",
        len(rows),
        changed,
    )


def _mapping_settings(conn: Conn, row: AreaRow, env: SettingsEnv) -> MappingSettings:
    entries = [FieldMappingEntry.model_validate(m.model_dump()) for m in mappings_of(row)]
    return MappingSettings(value=MappingsDoc(mappings=entries), **_meta(row))


MAPPINGS: Document[MappingsDoc, tuple[FieldMapping, ...], MappingSettings] = Document(
    area="mappings",
    check=lambda value, env: check_mappings(value),
    stored=lambda mappings: mappings_value(list(mappings)),
    read=_mapping_settings,
    after_write=_reapply_base_branch,
)


def jira_fields(
    source: JiraSource | None, cache: TtlCache[str, list[JiraField]]
) -> list[JiraField]:
    """Jira's fields for the mapping field picker, by name; cached (`AppContext.jira_fields_cache`,
    successes only). 501 `not_implemented` without a Jira service account; 502 `upstream_error`
    when Jira rejects it or doesn't answer."""
    if source is None:
        raise ApiError(501, "not_implemented", intake.NOT_CONFIGURED)
    cached = cache.get(_FIELDS_KEY)
    if cached is not None:
        return cached
    try:
        fields = source.jira_fields()
    except JiraUnauthorized:
        raise ApiError(502, "upstream_error", intake.UNAUTHORIZED) from None
    except JiraError:
        raise ApiError(502, "upstream_error", intake.UNAVAILABLE) from None
    cache.put(_FIELDS_KEY, fields)
    return fields


def preview_mapping(source: JiraSource | None, body: MappingPreviewRequest) -> MappingPreview:
    """Run the (unsaved) mapping on `values`, or on the field of `issue` read live with the
    service account (any issue it can see). The mapping is checked as on save (422, at
    `mapping...`). With `issue`: 501 without a service account, 422 for a ref that isn't a key or
    link to this Jira, 404 `ticket_not_found` when Jira can't see it, 502 `upstream_error` when
    Jira fails."""
    mapping, issues = check_mapping(body.mapping)
    if mapping is None:
        raise validation_error(issues)

    if body.values is not None:
        values = body.values
    else:
        values = _issue_values(source, body.issue or "", mapping.field)
    cleaned = clean_values(values)
    mapped = apply_rules(mapping.rules, cleaned, mapping.field_name)
    return MappingPreview(
        values=cleaned, value=mapped.value, rule=mapped.rule, reason=mapped.reason
    )


def _issue_values(source: JiraSource | None, ref: str, field_id: str) -> list[str]:
    if source is None:
        raise ApiError(501, "not_implemented", intake.NOT_CONFIGURED)
    key = source.parse_ref(ref)
    if key is None:
        raise field_error(["issue"], "Not a Jira issue key or a link to one.")
    try:
        return source.read_fields(key, [field_id]).get(field_id, [])
    except JiraNotFound:
        raise ApiError(404, "ticket_not_found", f"Jira can't see {key}") from None
    except JiraUnauthorized:
        raise ApiError(502, "upstream_error", intake.UNAUTHORIZED) from None
    except JiraError:
        raise ApiError(502, "upstream_error", intake.UNAVAILABLE) from None


# ------------------------------------------------------------------ runtimes


def get_runtimes(db: Database) -> RuntimeSettings:
    with db.read() as conn:
        return _runtime_settings(_row(conn, "runtimes"))


def put_runtimes(db: Database, actor: Me, body: RuntimeSettingsUpdate) -> RuntimeSettings:
    """Replace the runtimes (in order: the first is the server default). A name missing from the
    body is a removal: 409 `in_use` while the template's `defaults` or a workflow's stages use
    it."""
    entries: list[NamedRuntime] = []
    issues: list[ValidationIssue] = []
    names: set[str] = set()
    for i, entry in enumerate(body.runtimes):
        named, problems = parsed(NamedRuntime, entry.model_dump(), ("runtimes", i))
        issues += problems
        if entry.name in names:
            issues.append(
                ValidationIssue(
                    loc=["runtimes", i, "name"], message=f"{entry.name!r} is listed twice"
                )
            )
        names.add(entry.name)
        if named is not None:
            entries.append(named)
    if issues:
        raise validation_error(issues)

    with db.tx() as conn:
        row = _current(conn, "runtimes", body.base_version)
        kept = {e.name for e in entries}
        removed = [e.name for e in runtime_entries_of(row) if e.name not in kept]
        if removed:
            _check_runtimes_unused(conn, removed)
        write_area(
            conn, "runtimes", runtimes_value(entries), version=row.version + 1, actor_id=actor.id
        )
        return _runtime_settings(_row(conn, "runtimes"))


def _check_runtimes_unused(conn: Conn, removed: list[str]) -> None:
    """409 `in_use` naming each removed runtime the template's `defaults` or a workflow's agent
    stage still uses. Tickets' own pipelines aren't checked: a stage pinned to a removed runtime
    fails its dispatch (422 `invalid_stage`) until the ticket's pipeline is edited."""
    template = current_template(conn)
    pinned = workflows.runtime_uses(conn, removed)
    problems: list[str] = []
    for name in removed:
        uses: list[str] = []
        if template.defaults.runtime == name:
            uses.append("it is the template's default runtime")
        if names := pinned.get(name):
            what = "the workflow" if len(names) == 1 else "the workflows"
            uses.append(f"{what} {', '.join(names)} use{'s' if len(names) == 1 else ''} it")
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
                effort_args=e.effort_args,
                agent_args=e.agent_args,
                prompt_via=e.prompt_via,
                prompt_args=e.prompt_args,
                session_args=e.session_args,
                resume_args=e.resume_args,
                verdict_args=e.verdict_args,
                verdict_from=e.verdict_from,
            )
            for e in runtime_entries_of(row)
        ],
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
