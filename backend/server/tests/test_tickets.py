"""Tickets: create, list filters, detail, patch and pipeline PUT (SPEC §4, §6, §10), and the
repo catalog (SPEC §17): repo by name, catalog changes."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.config import Settings
from agentos.context import AppContext
from agentos.main import create_app
from agentos.models import Me, Pipeline, TicketDetail, TicketSummary
from agentos.pipeline.scaffold import default_pipeline
from agentos.pipeline.space import RepoSpec
from tests.settings_support import set_repos, set_space

CSRF = {"X-AgentOS": "1"}
BASE_URL = "http://127.0.0.1:8000"
CreateTicket = Callable[..., TicketDetail]
AddRun = Callable[..., int]
LoginFn = Callable[..., Me]


def code(response: Any) -> str:
    value: str = response.json()["error"]["code"]
    return value


def post(client: TestClient, **body: Any) -> Any:
    return client.post("/api/tickets", json={"title": "T", **body}, headers=CSRF)


def patch(client: TestClient, key: str, **body: Any) -> Any:
    return client.patch(f"/api/tickets/{key}", json=body, headers=CSRF)


def board(client: TestClient, query: str = "") -> list[TicketSummary]:
    response = client.get(f"/api/tickets{query}")
    assert response.status_code == 200, response.text
    return [TicketSummary.model_validate(t) for t in response.json()]


def keys(client: TestClient, query: str = "") -> list[str]:
    return [t.key for t in board(client, query)]


@contextmanager
def started(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url=BASE_URL) as c:
        assert c.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).is_success
        yield c


# ------------------------------------------------------------------ create


def test_create_returns_a_fresh_ticket(authed: TestClient) -> None:
    response = post(authed, title="Fix login bug", description="It expires early.")
    assert response.status_code == 201, response.text
    t = TicketDetail.model_validate(response.json())
    me = Me.model_validate(authed.get("/api/me").json())
    assert t.key == "AOS-1"
    assert t.skill_name == "context-aos-1"
    assert (t.title, t.description, t.type, t.priority) == (
        "Fix login bug",
        "It expires early.",
        "task",
        "normal",
    )
    assert (t.state, t.workspace_mode) == ("open", "repo")
    assert (t.repo_name, t.repo_url, t.base_branch) == ("r", "https://github.com/o/r.git", "main")
    assert t.branch == "agent/AOS-1"
    assert t.pipeline == default_pipeline()
    assert t.pipeline_version == 1
    assert t.owner is None
    assert t.labels == []
    assert (t.attention, t.phase, t.next_stage, t.active_run_id) == (
        "inbox",
        "inbox",
        "plan",
        None,
    )
    assert [s.state for s in t.phase_track] == [
        "current",
        "pending",
        "pending",
        "skipped",
        "pending",
        "pending",
    ]
    assert [r.state for r in t.rail] == ["pending", "pending", "skipped", "pending", "pending"]
    assert (t.now.actor, t.now.person, t.now.run_id) == (None, None, None)
    assert t.tasks is None
    assert t.latest_run is None
    assert t.chat_count == 0
    assert t.source is None
    assert t.attention_since == t.created_at
    assert t.updated_at >= t.created_at
    assert me.id > 0


def test_keys_are_sequential_and_skip_taken_ones(authed: TestClient, ctx: AppContext) -> None:
    assert post(authed).json()["key"] == "AOS-1"
    with ctx.db.tx() as conn:  # e.g. an imported ticket that already uses the next key
        conn.execute(
            "INSERT INTO tickets(key, title, pipeline, created_at, updated_at) "
            "VALUES ('AOS-2', 't', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (default_pipeline().model_dump_json(),),
        )
    assert post(authed).json()["key"] == "AOS-3"
    assert post(authed).json()["key"] == "AOS-4"


def test_key_prefix_comes_from_the_space_settings(authed: TestClient, ctx: AppContext) -> None:
    assert post(authed).json()["key"] == "AOS-1"
    set_space(ctx, key_prefix="ZED")  # applies to the next ticket, no restart
    assert post(authed).json()["key"] == "ZED-1"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param({}, ("r", "https://github.com/o/r.git", "main"), id="omitted=default"),
        pytest.param({"repo": "docs"}, ("docs", "git@github.com:o/docs.git", "develop"), id="docs"),
        pytest.param(
            {"repo": "docs", "base_branch": "release/1.0"},
            ("docs", "git@github.com:o/docs.git", "release/1.0"),
            id="own-branch",
        ),
        pytest.param({"repo": None}, (None, None, None), id="null=no-repo"),
    ],
)
def test_create_picks_a_catalog_repo(
    authed: TestClient, body: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    t = TicketDetail.model_validate(post(authed, **body).json())
    assert (t.repo_name, t.repo_url, t.base_branch) == expected
    assert t.workspace_mode == ("none" if body.get("repo", "r") is None else "repo")
    summary = next(s for s in board(authed) if s.key == t.key)
    assert summary.repo_name == expected[0]


def test_omitted_repo_without_a_default_repo_is_no_repo(
    authed: TestClient, ctx: AppContext
) -> None:
    set_space(ctx, default_repo=None)
    t = TicketDetail.model_validate(post(authed).json())
    assert (t.workspace_mode, t.repo_name, t.branch) == ("none", None, None)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"repo": "nope"}, id="unknown-repo"),
        pytest.param({"repo": "R"}, id="repo-names-are-exact"),
        pytest.param({"repo_url": "https://github.com/o/r.git"}, id="no-repo-url-field"),
        pytest.param({"base_branch": "../main"}, id="base-branch"),
        pytest.param({"base_branch": "main.lock"}, id="base-branch-lock"),
        pytest.param({"title": "   "}, id="blank-title"),
        pytest.param({"labels": [f"l{i}" for i in range(21)]}, id="too-many-labels"),
        pytest.param({"labels": ["x" * 51]}, id="label-too-long"),
        pytest.param({"repo": None, "base_branch": "main"}, id="none-branch"),
    ],
)
def test_create_validates_input(authed: TestClient, body: dict[str, Any]) -> None:
    response = post(authed, **body)
    assert response.status_code == 422, response.text
    assert code(response) == "validation_error"
    assert board(authed) == []


def test_labels_are_stripped_and_deduped(authed: TestClient) -> None:
    labels = ["  ui ", "", "ui", "api", "   ", "x" * 50] + ["api"] * 30
    t = post(authed, labels=labels).json()
    assert t["labels"] == ["ui", "api", "x" * 50]


def test_no_repo_ticket(authed: TestClient) -> None:
    t = TicketDetail.model_validate(post(authed, title="Write a report", repo=None).json())
    assert (t.workspace_mode, t.repo_url, t.base_branch, t.branch) == ("none", None, None, None)
    stages = {s.id: s for s in t.pipeline.stages}
    assert not stages["open-pr"].enabled  # requires_repo stages are disabled
    assert stages["plan"].enabled
    assert stages["review"].enabled
    assert [(r.stage_id, r.state) for r in t.rail] == [
        ("plan", "pending"),
        ("implement", "pending"),
        ("lint", "skipped"),
        ("review", "pending"),
        ("open-pr", "skipped"),
    ]
    agents = authed.get(f"/api/tickets/{t.key}/files/AGENTS.md").json()["content"]
    assert agents.endswith(f"Put deliverables in .claude/skills/{t.skill_name}/out/.")


def test_owner(authed: TestClient, login: LoginFn, ctx: AppContext) -> None:
    sam = login("sam")
    login("dev")
    t = post(authed, owner_id=sam.id).json()
    assert t["owner"] == {"id": sam.id, "display_name": "sam", "avatar_url": None}
    response = post(authed, owner_id=999)
    assert response.status_code == 422
    assert code(response) == "user_not_found"
    for role, disabled in (("pending", 0), ("viewer", 1)):
        with ctx.db.tx() as conn:
            conn.execute(
                "UPDATE users SET role = ?, disabled = ? WHERE id = ?", (role, disabled, sam.id)
            )
        response = post(authed, owner_id=sam.id)
        assert response.status_code == 422
        assert code(response) == "user_not_found"


def test_scaffold_is_bundle_v1(authed: TestClient) -> None:
    title = 'Fix: "quoted" #title & <tags>'  # awkward titles are covered in test_render
    t = TicketDetail.model_validate(post(authed, title=title, description="Desc.").json())
    me = Me.model_validate(authed.get("/api/me").json())
    assert t.skill_name == "context-aos-1"
    d = ".claude/skills/context-aos-1"
    files = authed.get(f"/api/tickets/{t.key}/files").json()
    assert sorted(f["path"] for f in files) == sorted(
        [
            f"{d}/SKILL.md",
            f"{d}/TASKS.md",
            f"{d}/learnings.log",
            f"{d}/swarm.log",
            "AGENTS.md",
            "CLAUDE.md",
        ]
    )
    for f in files:
        assert f["version"] == 1
        assert f["author"] == {"id": me.id, "display_name": me.display_name, "avatar_url": None}
        assert f["author_run_id"] is None
    modes = {f["path"]: f["mode"] for f in files}
    assert modes["AGENTS.md"] == modes["CLAUDE.md"] == "block"
    skill = authed.get(f"/api/tickets/{t.key}/files/{d}/SKILL.md").json()["content"]
    lines = skill.split("\n")
    assert lines[0] == "---"
    assert lines[1] == f"name: {t.skill_name}"
    assert lines[2].startswith("description: ")
    description = json.loads(lines[2].removeprefix("description: "))
    assert description == f"Objective and working context for {t.key}. Use when working on {t.key}."
    assert lines[3] == "---"
    assert f"# {t.key}: {title}" in skill
    assert skill.endswith("## Objective\nDesc.\n")


def test_a_failed_scaffold_render_leaves_no_ticket_and_no_used_key(authed: TestClient) -> None:
    response = post(authed, description="x" * (1024 * 1024 + 1))  # SKILL.md > 1 MiB
    assert response.status_code == 413
    assert code(response) == "too_large"
    assert board(authed) == []
    assert post(authed).json()["key"] == "AOS-1"


# ------------------------------------------------------------------ list and detail


def test_list_filters(authed: TestClient, create_ticket: CreateTicket) -> None:
    a = create_ticket("Login page", type="bug", priority="high", labels=["Auth"])
    b = create_ticket("Board polish", type="feature", labels=["UI"])
    c = create_ticket("Old thing")
    assert patch(authed, c.key, state="closed").status_code == 200
    d = create_ticket("Finished thing")
    assert patch(authed, d.key, state="done").status_code == 200

    assert keys(authed) == [d.key, b.key, a.key]  # most recently updated first; closed hidden
    assert set(keys(authed, "?include_closed=true")) == {a.key, b.key, c.key, d.key}
    assert keys(authed, "?type=bug") == [a.key]
    assert keys(authed, "?priority=high") == [a.key]
    assert keys(authed, "?type=feature&priority=high") == []
    assert keys(authed, "?q=LOGIN") == [a.key]  # title, case-insensitive
    assert keys(authed, "?q=ui") == [b.key]  # label
    assert keys(authed, f"?q={b.key.lower()}") == [b.key]  # key
    assert keys(authed, "?q=thing") == [d.key]
    assert keys(authed, "?q=thing&include_closed=true") == [d.key, c.key]
    assert keys(authed, "?q=%20%20") == keys(authed)  # blank q = no filter


def test_detail_and_unknown_key(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket("Detail")
    response = authed.get(f"/api/tickets/{t.key}")
    assert response.status_code == 200
    assert TicketDetail.model_validate(response.json()) == t
    response = authed.get("/api/tickets/AOS-999")
    assert response.status_code == 404
    assert code(response) == "ticket_not_found"


def test_viewer_can_read_but_not_create(
    login: LoginFn, client: TestClient, create_ticket: CreateTicket
) -> None:
    login("dev")
    t = create_ticket()
    login("viewer", "viewer")
    assert client.get(f"/api/tickets/{t.key}").status_code == 200
    assert post(client).status_code == 403


# ------------------------------------------------------------------ patch


def test_patch_fields(authed: TestClient, create_ticket: CreateTicket, login: LoginFn) -> None:
    sam = login("sam")
    login("dev")
    t = create_ticket("Original title")
    response = patch(
        authed,
        t.key,
        title="  New title ",
        description="New description",
        type="bug",
        priority="low",
        labels=[" a", "a", "b"],
        owner_id=sam.id,
        base_branch="develop",
    )
    assert response.status_code == 200, response.text
    p = TicketDetail.model_validate(response.json())
    assert (p.title, p.description, p.type, p.priority, p.labels) == (
        "New title",
        "New description",
        "bug",
        "low",
        ["a", "b"],
    )
    assert p.skill_name == t.skill_name  # a title edit never renames it
    assert p.owner is not None
    assert p.owner.id == sam.id
    assert p.base_branch == "develop"
    assert p.updated_at > t.updated_at
    cleared = TicketDetail.model_validate(
        patch(authed, t.key, owner_id=None, base_branch=None).json()
    )
    assert cleared.owner is None
    assert (cleared.repo_name, cleared.base_branch) == ("r", "main")  # back to the repo's default


def test_patch_repo(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket(base_branch="feature/x")
    docs = TicketDetail.model_validate(patch(authed, t.key, repo="docs").json())
    assert (docs.repo_name, docs.repo_url) == ("docs", "git@github.com:o/docs.git")
    # Another repo drops the ticket's own branch: it starts from the new repo's default.
    assert docs.base_branch == "develop"
    assert docs.base_branch_info is not None
    assert (docs.base_branch_info.source, docs.base_branch_info.set_by) == ("repo_default", None)
    # ... unless the same PATCH sets one.
    both = TicketDetail.model_validate(patch(authed, t.key, repo="r", base_branch="rel").json())
    assert (both.repo_name, both.base_branch) == ("r", "rel")
    assert both.base_branch_info is not None
    assert both.base_branch_info.source == "manual"
    # Null = no-repo mode: the base branch goes, and so do the enabled requires_repo stages.
    none = TicketDetail.model_validate(patch(authed, t.key, repo=None).json())
    assert (none.workspace_mode, none.repo_name, none.repo_url) == ("none", None, None)
    assert (none.base_branch, none.branch) == (None, None)
    assert not next(s for s in none.pipeline.stages if s.id == "open-pr").enabled
    assert none.pipeline_version == t.pipeline_version + 1
    back = TicketDetail.model_validate(patch(authed, t.key, repo="docs").json())
    assert (back.workspace_mode, back.base_branch, back.branch) == (
        "repo",
        "develop",
        "agent/AOS-1",
    )


def test_patch_validates(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    none = create_ticket(repo=None)
    cases: list[tuple[str, dict[str, Any], str]] = [
        (t.key, {"title": None}, "validation_error"),
        (t.key, {"state": None}, "validation_error"),
        (t.key, {"title": " "}, "validation_error"),
        (t.key, {"repo": "nope"}, "validation_error"),
        (t.key, {"repo_url": "https://github.com/o/r.git"}, "validation_error"),
        (t.key, {"base_branch": "a..b"}, "validation_error"),
        (t.key, {"repo": None, "base_branch": "main"}, "validation_error"),
        (t.key, {"labels": [str(i) for i in range(21)]}, "validation_error"),
        (t.key, {"owner_id": 999}, "user_not_found"),
        (none.key, {"base_branch": "main"}, "validation_error"),
    ]
    for key, body, expected in cases:
        response = patch(authed, key, **body)
        assert response.status_code == 422, (body, response.text)
        assert code(response) == expected, body
    assert "'nope'" in patch(authed, t.key, repo="nope").json()["error"]["message"]
    assert patch(authed, none.key, repo=None).status_code == 200  # clearing is fine
    assert patch(authed, "AOS-999", title="x").status_code == 404


def test_patch_during_an_active_run(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    t = create_ticket()
    add_run(t.key, "queued", [("plan", "pending")])
    for body in ({"state": "done"}, {"repo": None}, {"repo": "docs"}, {"base_branch": "dev"}):
        response = patch(authed, t.key, **body)
        assert response.status_code == 409, body
        assert code(response) == "run_active"
    # Other fields, and values that leave the workspace as it is, are fine.
    response = patch(authed, t.key, title="Renamed", state="open", repo="r", base_branch="main")
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"


def test_unchanged_patch_keeps_updated_at(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket("Same")
    p = TicketDetail.model_validate(patch(authed, t.key, title="Same", labels=[]).json())
    assert p.updated_at == t.updated_at


# ------------------------------------------------------------------ pipeline


def put_pipeline(
    client: TestClient, key: str, pipeline: Pipeline | dict[str, Any], base: int
) -> Any:
    body = pipeline.model_dump() if isinstance(pipeline, Pipeline) else pipeline
    return client.put(
        f"/api/tickets/{key}/pipeline", json={"pipeline": body, "base_version": base}, headers=CSRF
    )


def test_put_pipeline(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    pipeline = t.pipeline.model_copy(update={"stages": t.pipeline.stages[1:]})
    response = put_pipeline(authed, t.key, pipeline, 1)
    assert response.status_code == 200, response.text
    p = TicketDetail.model_validate(response.json())
    assert p.pipeline_version == 2
    assert [s.id for s in p.pipeline.stages] == ["implement", "lint", "review", "open-pr"]
    assert [r.stage_id for r in p.rail] == ["implement", "lint", "review", "open-pr"]
    assert p.next_stage == "implement"
    assert p.updated_at > t.updated_at
    stale = put_pipeline(authed, t.key, t.pipeline, 1)
    assert stale.status_code == 409
    assert code(stale) == "stale_version"


def test_put_pipeline_is_locked_during_an_active_run(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    t = create_ticket()
    add_run(t.key, "dispatched", [("plan", "pending")])
    response = put_pipeline(authed, t.key, t.pipeline, 1)
    assert response.status_code == 409
    assert code(response) == "run_active"


def stage_dump(**overrides: Any) -> dict[str, Any]:
    return {"id": "s", "name": "S", "kind": "agent", "phase": "build", "prompt": "", **overrides}


@pytest.mark.parametrize(
    ("stages", "expected"),
    [
        pytest.param([stage_dump(), stage_dump()], "invalid_pipeline", id="duplicate-ids"),
        pytest.param([stage_dump(runtime="nope")], "invalid_pipeline", id="unknown-runtime"),
        pytest.param([stage_dump(kind="shell")], "invalid_pipeline", id="shell-without-command"),
        pytest.param(
            [stage_dump(prompt="{{ ticket.nope }}")], "render_error", id="prompt-render-error"
        ),
        pytest.param([stage_dump(prompt="{% if %}")], "render_error", id="prompt-syntax-error"),
    ],
)
def test_put_pipeline_validates(
    authed: TestClient, create_ticket: CreateTicket, stages: list[dict[str, Any]], expected: str
) -> None:
    t = create_ticket()
    response = put_pipeline(authed, t.key, {"stages": stages}, 1)
    assert response.status_code == 422, response.text
    assert code(response) == expected
    if expected == "render_error":
        assert "stage s" in response.json()["error"]["message"]
    assert authed.get(f"/api/tickets/{t.key}").json()["pipeline_version"] == 1


def test_put_pipeline_on_a_no_repo_ticket(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket(repo=None)
    enabled = [s.model_copy(update={"enabled": True}) for s in t.pipeline.stages]
    response = put_pipeline(authed, t.key, Pipeline(stages=enabled[:2] + enabled[3:]), 1)
    assert response.status_code == 422
    assert code(response) == "invalid_pipeline"
    assert "open-pr" in response.json()["error"]["message"]
    # Kept disabled, it is fine; its prompt still has to render (ticket.branch renders as None).
    assert put_pipeline(authed, t.key, t.pipeline, 1).status_code == 200


# ------------------------------------------------------------------ repo catalog changes


def test_a_catalog_change_reaches_existing_tickets(authed: TestClient, ctx: AppContext) -> None:
    key = post(authed, repo="docs").json()["key"]
    own = post(authed, repo="docs", base_branch="stable").json()["key"]

    set_space(ctx, default_repo=None)  # "r" leaves the catalog too
    set_repos(  # Settings > Repos: no restart needed
        ctx, [RepoSpec(name="docs", url="https://example.com/new/docs.git", default_branch="trunk")]
    )
    t = TicketDetail.model_validate(authed.get(f"/api/tickets/{key}").json())
    assert (t.repo_url, t.base_branch) == ("https://example.com/new/docs.git", "trunk")
    assert authed.get(f"/api/tickets/{own}").json()["base_branch"] == "stable"

    # The repo left the catalog: still a repo ticket, with nothing to resolve (dispatch refuses
    # it: test_runs).
    set_repos(ctx, [])
    t = TicketDetail.model_validate(authed.get(f"/api/tickets/{key}").json())
    assert (t.workspace_mode, t.repo_name, t.repo_url, t.base_branch) == (
        "repo",
        "docs",
        None,
        None,
    )
