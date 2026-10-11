"""Intake candidates (request 14): for each intake query's JQL (Settings > Intake), the matching
Jira issues that aren't in AgentOS yet, fetched live with the service account. "Take in" is the
existing import. `preview` answers one JQL being edited (live, uncached).

Jira's answer is cached per query (`AppContext.intake_cache`, ~45 s), errors included; the
already-imported filter reads the db on every call, so a taken-in issue leaves the list at once.
A failing query answers with its `error` and no items; the others are unaffected.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from concurrent.futures import ThreadPoolExecutor

from agentos.db import Conn, Database
from agentos.errors import ApiError
from agentos.models import IntakeAssignee, IntakeItem, IntakePreview, IntakeQuery, IntakeQuerySpec
from agentos.sources.jira import (
    JiraBadQuery,
    JiraCandidate,
    JiraError,
    JiraSearch,
    JiraSource,
    JiraUnauthorized,
)
from agentos.ttlcache import TtlCache
from agentos.validate import validate_key

# How many matches are read per query (newest-updated first): the already-imported ones are
# filtered out of this window, then the rest is capped at ITEM_CAP.
FETCH_LIMIT = 100
ITEM_CAP = 50
PREVIEW_CAP = 5
MAX_PARALLEL = 4

NOT_CONFIGURED = "Jira import isn't configured (AGENTOS_JIRA_URL and AGENTOS_JIRA_SERVICE_TOKEN)"
UNAUTHORIZED = "Jira rejected the service account (AGENTOS_JIRA_SERVICE_EMAIL / _TOKEN)"
UNAVAILABLE = "Jira didn't answer; try again shortly"

# The keys (a JSON array, `json_each`) a ticket already has as its Jira source or its own key.
_IN_AGENTOS_SQL = (
    "SELECT j.value AS k FROM json_each(?) j WHERE EXISTS ("
    "  SELECT 1 FROM tickets t"
    "  WHERE (t.source = 'jira' AND t.external_id = j.value) OR t.key = j.value)"
)


def list_intake(
    db: Database,
    queries: Sequence[IntakeQuerySpec],
    source: JiraSource | None,
    projects: Collection[str],
    cache: TtlCache[str, JiraSearch | str],
) -> list[IntakeQuery]:
    """Every configured query, in config order, with its candidates.

    A candidate is a match that "Take in" would create: not imported yet (no ticket has it as its
    Jira source or as its key) and importable (a valid key, in the Space's Jira `projects` when
    those are set). Items are newest-updated first, at most ITEM_CAP; `total` counts every
    candidate (exact when the JQL matches at most FETCH_LIMIT issues, else estimated from Jira's
    count minus the non-candidates seen in the window). No service account → each query's `error`
    says so.
    """
    if source is None:
        return [_failed(spec, NOT_CONFIGURED) for spec in queries]
    results = _fetch_all(source, queries, cache)
    keys = {c.key for r in results.values() if isinstance(r, JiraSearch) for c in r.issues}
    with db.read() as conn:
        taken = _in_agentos(conn, keys)
    return [_answer(spec, results[spec.id], taken, projects) for spec in queries]


def _fetch_all(
    source: JiraSource,
    queries: Sequence[IntakeQuerySpec],
    cache: TtlCache[str, JiraSearch | str],
) -> dict[str, JiraSearch | str]:
    """Each query's Jira answer: from the cache, or fetched (in parallel) and cached."""
    results: dict[str, JiraSearch | str] = {}
    missing: list[IntakeQuerySpec] = []
    for spec in queries:
        cached = cache.get(spec.id)
        if cached is None:
            missing.append(spec)
        else:
            results[spec.id] = cached
    if missing:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(missing))) as pool:
            fetched = pool.map(lambda spec: _fetch(source, spec.jql), missing)
            for spec, result in zip(missing, fetched, strict=True):
                cache.put(spec.id, result)
                results[spec.id] = result
    return results


def _fetch(source: JiraSource, jql: str) -> JiraSearch | str:
    """The search, or a short message (never credentials) saying why it failed."""
    try:
        return source.search(jql, FETCH_LIMIT)
    except JiraBadQuery as exc:
        return f"Jira rejected the query: {exc}"
    except JiraUnauthorized:
        return UNAUTHORIZED
    except JiraError:
        return UNAVAILABLE


def _in_agentos(conn: Conn, keys: Collection[str]) -> set[str]:
    """The keys among `keys` that a ticket already has, as its Jira source or as its own key."""
    if not keys:
        return set()
    rows = conn.execute(_IN_AGENTOS_SQL, (json.dumps(sorted(keys)),)).fetchall()
    return {str(row["k"]) for row in rows}


def _importable(key: str, projects: Collection[str]) -> bool:
    """Whether POST /api/tickets/import would accept the key (see tickets' `_check_import_key`)."""
    try:
        validate_key(key)
    except ApiError:
        return False
    return not projects or key.rsplit("-", 1)[0] in projects


def preview(
    db: Database, jql: str, source: JiraSource | None, projects: Collection[str]
) -> IntakePreview:
    """Settings > Intake "Test query": one JQL, fetched live (never cached), with the same
    filtering, total and error messages as `list_intake`, at most PREVIEW_CAP items."""
    if source is None:
        return IntakePreview(total=0, error=NOT_CONFIGURED, items=[])
    result = _fetch(source, jql)
    if isinstance(result, str):
        return IntakePreview(total=0, error=result, items=[])
    with db.read() as conn:
        taken = _in_agentos(conn, {c.key for c in result.issues})
    items, total = _candidates(result, taken, projects, PREVIEW_CAP)
    return IntakePreview(total=total, error=None, items=items)


def _candidates(
    result: JiraSearch, taken: Collection[str], projects: Collection[str], cap: int
) -> tuple[list[IntakeItem], int]:
    """The first `cap` candidates (newest-updated first) and how many there are in all."""
    fresh = [c for c in result.issues if c.key not in taken and _importable(c.key, projects)]
    fresh.sort(key=lambda c: c.updated_at, reverse=True)
    others = len(result.issues) - len(fresh)
    return [_item(c) for c in fresh[:cap]], max(result.count - others, len(fresh))


def _answer(
    spec: IntakeQuerySpec,
    result: JiraSearch | str,
    taken: Collection[str],
    projects: Collection[str],
) -> IntakeQuery:
    if isinstance(result, str):
        return _failed(spec, result)
    items, total = _candidates(result, taken, projects, ITEM_CAP)
    return IntakeQuery(
        id=spec.id, name=spec.name, jql=spec.jql, items=items, total=total, error=None
    )


def _item(c: JiraCandidate) -> IntakeItem:
    return IntakeItem(
        key=c.key,
        title=c.title,
        type=c.type,
        priority=c.priority,
        status=c.status,
        assignee=IntakeAssignee(display_name=c.assignee) if c.assignee is not None else None,
        updated_at=c.updated_at,
        url=c.url,
    )


def _failed(spec: IntakeQuerySpec, error: str) -> IntakeQuery:
    return IntakeQuery(id=spec.id, name=spec.name, jql=spec.jql, items=[], total=0, error=error)
