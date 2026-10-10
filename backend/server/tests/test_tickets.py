"""Tickets: create, list filters, detail, patch and pipeline PUT (SPEC §4, §6, §10)."""

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
    assert t.slug == "fix-login-bug"
    assert t.skill_name == "context-aos-1-fix-login-bug"
    assert (t.title, t.description, t.type, t.priority) == (
        "Fix login bug",
        "It expires early.",
        "task",
        "normal",
    )
    assert (t.state, t.workspace_mode, t.repo_url, t.base_branch) == ("open", "repo", None, "main")
    assert t.branch == "agent/AOS-1"
    assert t.repo_name is None
    assert t.pipeline == default_pipeline()
    assert t.pipeline_version == 1
    assert t.owner is None
    assert t.labels == []
    assert (t.attention, t.phase, t.next_stage, t.active_run_id) == (
        "up_next",
        "inbox",
        "plan",
        None,
    )
    assert t.tasks is None
    assert t.latest_run is None
    assert t.chat_count == 0
    assert t.source is None
    assert t.phase_since == t.created_at
    assert t.updated_at >= t.created_at
    assert me.id > 0


def test_keys_are_sequential_and_skip_taken_ones(authed: TestClient, ctx: AppContext) -> None:
    assert post(authed).json()["key"] == "AOS-1"
    with ctx.db.tx() as conn:  # e.g. an imported ticket that already uses the next key
        conn.execute(
            "INSERT INTO tickets(key, slug, title, workspace_mode, pipeline, created_at, "
            "updated_at) VALUES ('AOS-2', 's', 't', 'none', ?, '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')",
            (default_pipeline().model_dump_json(),),
        )
    assert post(authed).json()["key"] == "AOS-3"
    assert post(authed).json()["key"] == "AOS-4"


def test_key_prefix_and_repo_defaults_come_from_settings(settings: Settings) -> None:
    custom = settings.model_copy(
        update={
            "key_prefix": "ZED",
            "default_repo_url": "https://github.com/org/repo.git",
            "default_base_branch": "develop",
        }
    )
    with started(custom) as c:
        t = TicketDetail.model_validate(post(c).json())
        assert t.key == "ZED-1"
        assert t.repo_url == "https://github.com/org/repo.git"
        assert t.repo_name == "repo"
        assert t.base_branch == "develop"
        explicit = post(c, repo_url="git@github.com:a/b.git", base_branch="release/1.0").json()
        assert (explicit["repo_url"], explicit["base_branch"]) == (
            "git@github.com:a/b.git",
            "release/1.0",
        )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"repo_url": "file:///etc"}, id="repo-url"),
        pytest.param({"repo_url": "https://user:pw@example.com/r.git"}, id="credentials"),
        pytest.param({"base_branch": "../main"}, id="base-branch"),
        pytest.param({"base_branch": "main.lock"}, id="base-branch-lock"),
        pytest.param({"title": "   "}, id="blank-title"),
        pytest.param({"labels": [f"l{i}" for i in range(21)]}, id="too-many-labels"),
        pytest.param({"labels": ["x" * 51]}, id="label-too-long"),
        pytest.param({"workspace_mode": "none", "repo_url": "https://e.com/r.git"}, id="none-url"),
        pytest.param({"workspace_mode": "none", "base_branch": "main"}, id="none-branch"),
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
    t = TicketDetail.model_validate(
        post(authed, title="Write a report", workspace_mode="none").json()
    )
    assert (t.workspace_mode, t.repo_url, t.base_branch, t.branch) == ("none", None, None, None)
    stages = {s.id: s for s in t.pipeline.stages}
    assert not stages["open-pr"].enabled  # requires_repo stages are disabled
    assert stages["plan"].enabled
    assert stages["review"].enabled
    assert [r.stage_id for r in t.rail] == ["plan", "implement", "review"]
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
    with ctx.db.tx() as conn:
        conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (sam.id,))
    response = post(authed, owner_id=sam.id)
    assert response.status_code == 422
    assert code(response) == "user_not_found"


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ('Fix: "quoted" #title & <tags>', "fix-quoted-title-tags"),
        ("Ünïcödé — façade ☃ 😀", "unicode-facade"),
        ("!!!", "ticket"),
        ("x" * 200, "x" * 50),
        ("Line\nbreak\ttab", "line-break-tab"),
    ],
)
def test_scaffold_is_bundle_v1(authed: TestClient, title: str, slug: str) -> None:
    t = TicketDetail.model_validate(post(authed, title=title, description="Desc.").json())
    me = Me.model_validate(authed.get("/api/me").json())
    assert t.slug == slug
    assert len(t.skill_name) <= 64
    d = f".claude/skills/{t.skill_name}"
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
    assert title in description  # the title survives the YAML-safe JSON escaping intact
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
    t = create_ticket("Original title", repo_url="https://github.com/o/r.git")
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
    assert p.slug == t.slug  # the slug is immutable
    assert p.owner is not None
    assert p.owner.id == sam.id
    assert p.base_branch == "develop"
    assert p.updated_at > t.updated_at
    cleared = patch(authed, t.key, owner_id=None, repo_url=None, base_branch=None).json()
    assert (cleared["owner"], cleared["repo_url"], cleared["base_branch"]) == (None, None, None)
    assert cleared["repo_name"] is None


def test_patch_validates(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    none = create_ticket(workspace_mode="none")
    cases: list[tuple[str, dict[str, Any], str]] = [
        (t.key, {"title": None}, "validation_error"),
        (t.key, {"state": None}, "validation_error"),
        (t.key, {"title": " "}, "validation_error"),
        (t.key, {"repo_url": "ftp://x/y"}, "validation_error"),
        (t.key, {"base_branch": "a..b"}, "validation_error"),
        (t.key, {"labels": [str(i) for i in range(21)]}, "validation_error"),
        (t.key, {"owner_id": 999}, "user_not_found"),
        (none.key, {"repo_url": "https://e.com/r.git"}, "validation_error"),
        (none.key, {"base_branch": "main"}, "validation_error"),
    ]
    for key, body, expected in cases:
        response = patch(authed, key, **body)
        assert response.status_code == 422, (body, response.text)
        assert code(response) == expected, body
    assert patch(authed, none.key, repo_url=None).status_code == 200  # clearing is fine
    assert patch(authed, "AOS-999", title="x").status_code == 404


def test_patch_during_an_active_run(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    t = create_ticket(repo_url="https://github.com/o/r.git")
    add_run(t.key, "queued", [("plan", "pending")])
    for body in ({"state": "done"}, {"repo_url": None}, {"base_branch": "dev"}):
        response = patch(authed, t.key, **body)
        assert response.status_code == 409, body
        assert code(response) == "run_active"
    # Other fields, and unchanged values of the guarded ones, are fine.
    response = patch(authed, t.key, title="Renamed", state="open", base_branch="main")
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
    assert [r.stage_id for r in p.rail] == ["implement", "review", "open-pr"]
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
    t = create_ticket(workspace_mode="none")
    enabled = [s.model_copy(update={"enabled": True}) for s in t.pipeline.stages]
    response = put_pipeline(authed, t.key, Pipeline(stages=enabled[:2] + enabled[3:]), 1)
    assert response.status_code == 422
    assert code(response) == "invalid_pipeline"
    assert "open-pr" in response.json()["error"]["message"]
    # Kept disabled, it is fine; its prompt still has to render (ticket.branch renders as None).
    assert put_pipeline(authed, t.key, t.pipeline, 1).status_code == 200
