"""Settings in the app (Settings > Space, Repos, Intake): the endpoints and their rules, the
first-start seed from agentos.toml + env, the moved-var warnings, and changes applying without a
restart."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentos.config import ConfigError, Settings
from agentos.context import AppContext
from agentos.db import MIGRATIONS_DIR, Database
from agentos.intake import service as intake
from agentos.main import create_app
from agentos.models import (
    AppConfig,
    IntakeSettings,
    Me,
    RepoSettings,
    SpaceSettings,
    TicketDetail,
)
from agentos.pipeline.scaffold import default_pipeline
from agentos.pipeline.space import query_id
from agentos.sources.jira import JiraClient
from tests.run_support import FakeLauncher

CSRF = {"X-AgentOS": "1"}
BASE_URL = "http://127.0.0.1:8000"
FIXTURES = Path(__file__).parent / "fixtures"
RUNTIMES_TOML = FIXTURES / "runtimes_test.toml"
JIRA = "https://jira.example.com"
SERVICE_EMAIL = "bot@example.com"
SERVICE_TOKEN = "svc-token-0123456789"  # noqa: S105 - a fake token for the mock
LoginFn = Callable[..., Me]
CreateTicket = Callable[..., TicketDetail]


def code(response: httpx.Response) -> str:
    value: str = response.json()["error"]["code"]
    return value


def message(response: httpx.Response) -> str:
    value: str = response.json()["error"]["message"]
    return value


def space(client: TestClient) -> SpaceSettings:
    response = client.get("/api/settings/space")
    assert response.status_code == 200, response.text
    return SpaceSettings.model_validate(response.json())


def repos(client: TestClient) -> RepoSettings:
    response = client.get("/api/settings/repos")
    assert response.status_code == 200, response.text
    return RepoSettings.model_validate(response.json())


def queries(client: TestClient) -> IntakeSettings:
    response = client.get("/api/settings/intake")
    assert response.status_code == 200, response.text
    return IntakeSettings.model_validate(response.json())


def config(client: TestClient) -> AppConfig:
    return AppConfig.model_validate(client.get("/api/config").json())


def put_space(client: TestClient, **changes: Any) -> httpx.Response:
    """PUT the current Space settings with `changes` (base_version = the current version)."""
    current = space(client)
    body = current.model_dump(exclude={"version", "updated_at", "updated_by"})
    body |= {"base_version": current.version, **changes}
    response: httpx.Response = client.put("/api/settings/space", json=body, headers=CSRF)
    return response


def put_repos(client: TestClient, entries: list[dict[str, Any]], **extra: Any) -> httpx.Response:
    body = {"repos": entries, "base_version": repos(client).version, **extra}
    response: httpx.Response = client.put("/api/settings/repos", json=body, headers=CSRF)
    return response


def put_queries(client: TestClient, entries: list[dict[str, Any]], **extra: Any) -> httpx.Response:
    body = {"queries": entries, "base_version": queries(client).version, **extra}
    response: httpx.Response = client.put("/api/settings/intake", json=body, headers=CSRF)
    return response


R = {"name": "r", "url": "https://github.com/o/r.git", "default_branch": "main"}
DOCS = {"name": "docs", "url": "git@github.com:o/docs.git", "default_branch": "develop"}


# ------------------------------------------------------------------ space


def test_seeded_space_settings(authed: TestClient) -> None:
    got = space(authed)
    assert got.model_dump(exclude={"updated_at"}) == {
        "name": "Test Space",
        "default_repo": "r",
        "key_prefix": "AOS",
        "jira_projects": [],
        "claim_timeout_s": 900,
        "version": 1,
        "updated_by": None,  # seeded, not saved by anyone
    }


def test_put_space_applies_without_a_restart(authed: TestClient) -> None:
    me = Me.model_validate(authed.get("/api/me").json())
    before = space(authed)
    response = put_space(
        authed,
        name="  Renamed  ",
        default_repo="docs",
        key_prefix="ZED",
        jira_projects=["SCRUM", "OPS_2"],
        claim_timeout_s=60,
    )
    assert response.status_code == 200, response.text
    saved = SpaceSettings.model_validate(response.json())
    assert (saved.name, saved.default_repo, saved.key_prefix) == ("Renamed", "docs", "ZED")
    assert (saved.jira_projects, saved.claim_timeout_s) == (["SCRUM", "OPS_2"], 60)
    assert saved.version == before.version + 1
    assert saved.updated_by is not None
    assert (saved.updated_by.id, saved.updated_at >= before.updated_at) == (me.id, True)
    assert space(authed) == saved
    cfg = config(authed)
    assert (cfg.space_name, cfg.key_prefix, cfg.default_repo) == ("Renamed", "ZED", "docs")
    ticket = authed.post("/api/tickets", json={"title": "T"}, headers=CSRF).json()
    assert (ticket["key"], ticket["repo_name"]) == ("ZED-1", "docs")


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        pytest.param({"key_prefix": "aos"}, "key_prefix", id="prefix-lowercase"),
        pytest.param({"key_prefix": "1AB"}, "key_prefix", id="prefix-digit-first"),
        pytest.param({"key_prefix": "ABCDEFGHIJK"}, "key_prefix", id="prefix-too-long"),
        pytest.param({"key_prefix": ""}, "key_prefix", id="prefix-empty"),
        pytest.param({"jira_projects": ["scrum"]}, "jira_projects.0", id="project-lowercase"),
        pytest.param({"jira_projects": ["S"]}, "jira_projects.0", id="project-too-short"),
        pytest.param({"jira_projects": ["OK", "SC-1"]}, "jira_projects.1", id="project-dash"),
        pytest.param({"jira_projects": ["OPS", "OPS"]}, "jira_projects", id="project-twice"),
        pytest.param(
            {"key_prefix": "SCRUM", "jira_projects": ["SCRUM"]},
            "key_prefix",
            id="prefix-is-a-project",
        ),
        pytest.param({"claim_timeout_s": 59}, "claim_timeout_s", id="timeout-low"),
        pytest.param({"claim_timeout_s": 86401}, "claim_timeout_s", id="timeout-high"),
        pytest.param({"name": "   "}, "name", id="name-blank"),
        pytest.param({"name": "x" * 101}, "name", id="name-long"),
        pytest.param({"default_repo": "nope"}, "default_repo", id="unknown-repo"),
        pytest.param({"default_repo": None, "version": 1}, "version", id="read-only-field"),
    ],
)
def test_put_space_validates(authed: TestClient, changes: dict[str, Any], where: str) -> None:
    response = put_space(authed, **changes)
    assert (response.status_code, code(response)) == (422, "validation_error"), response.text
    assert where in message(response)
    assert space(authed).version == 1  # nothing saved


def test_a_project_key_can_not_become_the_prefix(authed: TestClient) -> None:
    """Checked both ways: adding the prefix as a project is refused too."""
    assert put_space(authed, jira_projects=["SCRUM"]).status_code == 200
    response = put_space(authed, key_prefix="SCRUM")
    assert (response.status_code, code(response)) == (422, "validation_error")
    response = put_space(authed, jira_projects=["SCRUM", "AOS"])
    assert (response.status_code, code(response)) == (422, "validation_error")


def test_projects_are_required_while_sign_in_is_jira(authed: TestClient, ctx: AppContext) -> None:
    assert put_space(authed, jira_projects=[]).status_code == 200  # dev auth: optional
    ctx.settings = ctx.settings.model_copy(update={"dev_auth": False})
    response = put_space(authed, jira_projects=[])
    assert (response.status_code, code(response)) == (422, "validation_error")
    assert "jira_projects" in message(response)
    assert put_space(authed, jira_projects=["SCRUM"]).status_code == 200


def test_put_space_with_a_stale_version(authed: TestClient) -> None:
    assert put_space(authed, name="First").status_code == 200
    response = put_space(authed, name="Second", base_version=1)
    assert (response.status_code, code(response)) == (409, "stale_version")
    assert space(authed).name == "First"


def test_settings_are_admin_only(client: TestClient, login: LoginFn) -> None:
    login("wanda", "writer")
    for area in ("space", "repos", "intake"):
        response = client.get(f"/api/settings/{area}")
        assert (response.status_code, code(response)) == (403, "forbidden")


# ------------------------------------------------------------------ repos


def test_seeded_repos_count_open_tickets(authed: TestClient, create_ticket: CreateTicket) -> None:
    create_ticket("open on r")
    create_ticket("open on docs", repo="docs")
    done = create_ticket("done on docs", repo="docs")
    authed.post(f"/api/tickets/{done.key}/resolve", json={"outcome": "completed"}, headers=CSRF)
    create_ticket("no repo", repo=None)
    got = repos(authed)
    assert [(e.name, e.url, e.default_branch, e.open_ticket_count) for e in got.repos] == [
        ("r", "https://github.com/o/r.git", "main", 1),
        ("docs", "git@github.com:o/docs.git", "develop", 1),
    ]
    assert (got.version, got.updated_by) == (1, None)


def test_put_repos_applies_without_a_restart(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    ticket = create_ticket("on docs", repo="docs")
    new = {"name": "new.repo", "url": "https://example.com/new.git", "default_branch": "trunk"}
    moved = {**DOCS, "url": "https://example.com/docs.git", "default_branch": "main"}
    response = put_repos(authed, [new, moved, R])  # add, change, reorder
    assert response.status_code == 200, response.text
    saved = RepoSettings.model_validate(response.json())
    assert [e.name for e in saved.repos] == ["new.repo", "docs", "r"]
    assert (saved.version, saved.updated_by is not None) == (2, True)
    assert [r.name for r in config(authed).repos] == ["new.repo", "docs", "r"]
    t = TicketDetail.model_validate(authed.get(f"/api/tickets/{ticket.key}").json())
    assert (t.repo_url, t.base_branch) == ("https://example.com/docs.git", "main")
    created = authed.post("/api/tickets", json={"title": "x", "repo": "new.repo"}, headers=CSRF)
    assert created.status_code == 201, created.text


def test_removing_a_repo_open_tickets_use_is_in_use(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    create_ticket("a", repo="docs")
    create_ticket("b", repo="docs")
    response = put_repos(authed, [R])
    assert (response.status_code, code(response)) == (409, "in_use")
    assert "'docs' (2 open tickets use it)" in message(response)
    assert len(repos(authed).repos) == 2


def test_done_and_closed_tickets_dont_block_a_removal(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    done = create_ticket("done", repo="docs")
    closed = create_ticket("closed", repo="docs")
    authed.post(f"/api/tickets/{done.key}/resolve", json={"outcome": "completed"}, headers=CSRF)
    authed.patch(f"/api/tickets/{closed.key}", json={"state": "closed"}, headers=CSRF)
    assert put_repos(authed, [R]).status_code == 200
    t = TicketDetail.model_validate(authed.get(f"/api/tickets/{done.key}").json())
    assert (t.repo_name, t.repo_url) == ("docs", None)  # kept as a plain name


def test_removing_the_default_repo_is_in_use(authed: TestClient) -> None:
    response = put_repos(authed, [DOCS])
    assert (response.status_code, code(response)) == (409, "in_use")
    assert "'r' (it is the Space's default repo)" in message(response)
    assert put_space(authed, default_repo=None).status_code == 200
    assert put_repos(authed, [DOCS]).status_code == 200
    assert config(authed).default_repo is None


@pytest.mark.parametrize(
    ("entries", "where"),
    [
        pytest.param([{**R, "name": "Bad Name"}], "repos.0.name", id="bad-name"),
        pytest.param([R, {**DOCS, "url": "file:///tmp/r"}], "repos.1.url", id="file-url"),
        pytest.param(
            [{**R, "url": "https://user:pw@example.com/r.git"}], "repos.0.url", id="credentials"
        ),
        pytest.param([{**R, "default_branch": "a..b"}], "repos.0.default_branch", id="branch"),
        pytest.param([R, {**DOCS, "name": "r"}], "repos.1.name", id="duplicate"),
        pytest.param([{"name": "r", "url": R["url"]}], "default_branch", id="missing-field"),
        pytest.param([{**R, "open_ticket_count": 0}], "open_ticket_count", id="read-only-field"),
    ],
)
def test_put_repos_validates(authed: TestClient, entries: list[dict[str, Any]], where: str) -> None:
    response = put_repos(authed, entries)
    assert (response.status_code, code(response)) == (422, "validation_error"), response.text
    assert where in message(response)
    assert repos(authed).version == 1


def test_put_repos_with_a_stale_version(authed: TestClient) -> None:
    assert put_repos(authed, [R, DOCS]).status_code == 200
    response = put_repos(authed, [R], base_version=1)
    assert (response.status_code, code(response)) == (409, "stale_version")


# ------------------------------------------------------------------ intake


def test_query_ids() -> None:
    assert query_id("SCRUM: to do", set()) == "scrum-to-do"
    assert query_id("SCRUM: to do", {"scrum-to-do"}) == "scrum-to-do-2"
    assert query_id("SCRUM: to do", {"scrum-to-do", "scrum-to-do-2"}) == "scrum-to-do-3"
    assert query_id("  ---  ", set()) == "query"
    assert query_id("Ünïcode!", set()) == "n-code"
    long = query_id("x" * 80, set())
    assert long == "x" * 64
    assert query_id("x" * 80, {long}) == "x" * 62 + "-2"


def test_put_intake_makes_ids_and_keeps_sent_ones(authed: TestClient) -> None:
    assert queries(authed).model_dump(exclude={"updated_at"}) == {
        "queries": [],
        "version": 1,
        "updated_by": None,
    }
    response = put_queries(
        authed,
        [
            {"id": None, "name": "To do", "jql": "project = SCRUM"},
            {"id": "to-do", "name": "Kept", "jql": " a "},
            {"id": None, "name": "To do", "jql": "b"},
            {"id": None, "name": "!!!", "jql": "c"},
        ],
    )
    assert response.status_code == 200, response.text
    saved = IntakeSettings.model_validate(response.json())
    assert [(q.id, q.name, q.jql) for q in saved.queries] == [
        ("to-do-2", "To do", "project = SCRUM"),
        ("to-do", "Kept", "a"),
        ("to-do-3", "To do", "b"),
        ("query", "!!!", "c"),
    ]
    assert (saved.version, saved.updated_by is not None) == (2, True)
    # Sent back as-is (ids included), they stay; order = tab order.
    again = put_queries(authed, [q.model_dump() for q in reversed(saved.queries)])
    assert [q["id"] for q in again.json()["queries"]] == ["query", "to-do-3", "to-do", "to-do-2"]


@pytest.mark.parametrize(
    ("entries", "where"),
    [
        pytest.param([{"id": "Bad Id", "name": "x", "jql": "a"}], "queries.0.id", id="bad-id"),
        pytest.param(
            [{"id": "a", "name": "x", "jql": "a"}, {"id": "a", "name": "y", "jql": "b"}],
            "queries.1.id",
            id="duplicate-id",
        ),
        pytest.param([{"id": None, "name": " ", "jql": "a"}], "queries.0.name", id="blank-name"),
        pytest.param([{"id": None, "name": "x", "jql": ""}], "queries.0.jql", id="empty-jql"),
        pytest.param([{"name": "x", "jql": "a"}], "queries.0.id", id="id-is-required"),
    ],
)
def test_put_intake_validates(
    authed: TestClient, entries: list[dict[str, Any]], where: str
) -> None:
    response = put_queries(authed, entries)
    assert (response.status_code, code(response)) == (422, "validation_error"), response.text
    assert where in message(response)


def test_put_intake_with_a_stale_version(authed: TestClient) -> None:
    assert put_queries(authed, []).status_code == 200
    response = put_queries(authed, [], base_version=1)
    assert (response.status_code, code(response)) == (409, "stale_version")


def test_saving_intake_drops_the_cache_and_applies_at_once(
    authed: TestClient, ctx: AppContext, search: FakeSearch
) -> None:
    ctx.intake_cache.put("a", "an old answer")
    response = put_queries(authed, [{"id": "a", "name": "A", "jql": "project = SCRUM"}])
    assert response.status_code == 200
    assert ctx.intake_cache.get("a") is None
    search.results["project = SCRUM"] = [issue("SCRUM-1")]
    listed = authed.get("/api/intake").json()
    assert [(q["id"], q["error"], q["total"]) for q in listed] == [("a", None, 1)]


# ------------------------------------------------------------------ intake preview


def issue(key: str, minutes_ago: int = 0) -> dict[str, Any]:
    return {
        "id": "1",
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "issuetype": {"name": "Story"},
            "priority": {"name": "Medium"},
            "status": {"name": "To Do"},
            "assignee": None,
            "updated": f"2026-10-11T12:{59 - minutes_ago:02d}:00.000+0000",
        },
    }


class FakeSearch:
    """Jira's JQL search: matches by JQL (without the appended ORDER BY), or a failure."""

    def __init__(self) -> None:
        self.results: dict[str, list[dict[str, Any]]] = {}
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        jql = json.loads(request.content)["jql"].removesuffix(" ORDER BY updated DESC")
        if jql == "bad":
            return httpx.Response(400, json={"errorMessages": ["nope"]})
        matches = self.results.get(jql, [])
        if request.url.path.endswith("/approximate-count"):
            return httpx.Response(200, json={"count": len(matches)})
        return httpx.Response(200, json={"issues": matches, "isLast": True})


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Dev auth plus a Jira service account (for the preview)."""
    return settings.model_copy(
        update={
            "jira_url": JIRA,
            "jira_service_email": SERVICE_EMAIL,
            "jira_service_token": SecretStr(SERVICE_TOKEN),
        }
    )


@pytest.fixture
def search(ctx: AppContext) -> FakeSearch:
    fake = FakeSearch()
    ctx.jira = JiraClient(JIRA, "cloud", transport=httpx.MockTransport(fake.handler))
    return fake


def preview(client: TestClient, jql: str) -> httpx.Response:
    response: httpx.Response = client.post(
        "/api/settings/intake/preview", json={"jql": jql}, headers=CSRF
    )
    return response


def test_preview_is_live_capped_and_filtered(
    authed: TestClient, search: FakeSearch, create_ticket: CreateTicket, ctx: AppContext
) -> None:
    assert put_space(authed, jira_projects=["SCRUM"]).status_code == 200
    search.results["project = SCRUM"] = [
        *(issue(f"SCRUM-{n}", n) for n in range(1, 9)),
        issue("OPS-1"),  # outside the Space's projects
    ]
    native = create_ticket("taken")
    with ctx.db.tx() as conn:  # SCRUM-2 is in AgentOS already
        conn.execute("UPDATE tickets SET key = 'SCRUM-2' WHERE key = ?", (native.key,))
    response = preview(authed, "  project = SCRUM ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["key"] for i in body["items"]] == [
        "SCRUM-1",
        "SCRUM-3",
        "SCRUM-4",
        "SCRUM-5",
        "SCRUM-6",
    ]
    assert (body["total"], body["error"]) == (7, None)
    calls = search.calls
    assert preview(authed, "project = SCRUM").status_code == 200
    assert search.calls > calls  # not cached
    assert SERVICE_TOKEN not in response.text


def test_preview_errors(authed: TestClient, search: FakeSearch, ctx: AppContext) -> None:
    bad = preview(authed, "bad").json()
    assert bad == {"total": 0, "error": "Jira rejected the query: nope", "items": []}
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": None})
    calls = search.calls
    assert preview(authed, "x").json()["error"] == intake.NOT_CONFIGURED
    assert search.calls == calls
    blank = preview(authed, "   ")
    assert (blank.status_code, code(blank)) == (422, "validation_error")


def test_preview_is_admin_only(client: TestClient, login: LoginFn, search: FakeSearch) -> None:
    login("wanda", "writer")
    assert preview(client, "x").status_code == 403
    assert search.calls == 0


# ------------------------------------------------------------------ server_default_runtime


def test_config_names_the_server_default_runtime(authed: TestClient) -> None:
    assert (config(authed).default_runtime, config(authed).server_default_runtime) == (
        "fake",
        "fake",
    )
    template = authed.get("/api/template").json()
    body = {
        "pipeline": template["pipeline"],
        "scaffold": template["scaffold"],
        "defaults": {"runtime": "claude"},
        "base_version": template["version"],
    }
    assert authed.put("/api/template", json=body, headers=CSRF).status_code == 200
    assert (config(authed).default_runtime, config(authed).server_default_runtime) == (
        "claude",
        "fake",
    )


# ------------------------------------------------------------------ the first-start seed


SEED_TOML = (
    'default_runtime = "fake"\n'
    '[space]\nname = "Work"\ndefault_repo = "agentos"\n'
    '[[repos]]\nname = "agentos"\nurl = "https://github.com/o/agentos.git"\n'
    '[[repos]]\nname = "agentos-scratch"\nurl = "https://github.com/o/scratch.git"\n'
    '[[intake.queries]]\nid = "todo"\nname = "To do"\njql = "project = SCRUM"\n'
    '[[intake.queries]]\nid = "bugs"\nname = "Bugs"\njql = "type = Bug"\n'
    '[runtimes.fake]\nargv = ["python", "fake_agent.py"]\n'
)
SEED_ENV = {
    "AGENTOS_KEY_PREFIX": "AOS",
    "AGENTOS_JIRA_PROJECTS": "SCRUM, OPS",
    "AGENTOS_CLAIM_TIMEOUT_S": "600",
}


@pytest.fixture
def seed_toml(tmp_path: Path) -> Path:
    path = tmp_path / "seed.toml"
    path.write_text(SEED_TOML, encoding="utf-8")
    return path


def make(tmp_path: Path, config: Path, **overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspaces_dir=tmp_path / "workspaces",
        dev_auth=True,
        config=config,
        **overrides,
    )


@contextmanager
def started(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, launcher=FakeLauncher(), run_loops=False)
    with TestClient(app, base_url=BASE_URL) as c:
        assert c.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).is_success
        yield c


def set_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_first_start_seeds_from_toml_and_env(
    tmp_path: Path, seed_toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_env(monkeypatch, SEED_ENV)
    with started(make(tmp_path, seed_toml)) as c:
        got = space(c)
        assert (got.name, got.default_repo, got.key_prefix) == ("Work", "agentos", "AOS")
        assert (got.jira_projects, got.claim_timeout_s) == (["SCRUM", "OPS"], 600)
        assert (got.version, got.updated_by) == (1, None)
        assert [r.name for r in repos(c).repos] == ["agentos", "agentos-scratch"]
        assert [(q.id, q.jql) for q in queries(c).queries] == [
            ("todo", "project = SCRUM"),
            ("bugs", "type = Bug"),
        ]
        assert config(c).space_name == "Work"


def test_the_seed_is_idempotent_and_later_sources_are_ignored_with_warnings(
    tmp_path: Path,
    seed_toml: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    set_env(monkeypatch, SEED_ENV)
    with started(make(tmp_path, seed_toml)) as c:
        assert put_space(c, name="Edited in the app").status_code == 200
    # Restart with other values (an invalid one too: ignored, so no crash).
    seed_toml.write_text(SEED_TOML.replace('name = "Work"', 'name = "Toml"'), encoding="utf-8")
    set_env(monkeypatch, {"AGENTOS_KEY_PREFIX": "bad", "AGENTOS_CLAIM_TIMEOUT_S": "5"})
    caplog.clear()
    with caplog.at_level(logging.WARNING), started(make(tmp_path, seed_toml)) as c:
        got = space(c)
        assert (got.name, got.key_prefix, got.claim_timeout_s, got.version) == (
            "Edited in the app",
            "AOS",
            600,
            2,
        )
        assert len(repos(c).repos) == 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    for name in SEED_ENV:
        assert any(f"{name} is ignored: edit it in Settings > Space" in w for w in warnings), name
    for table in ("[space]", "[[repos]]", "[[intake.queries]]"):
        assert any(f"agentos.toml {table} is ignored" in w for w in warnings), table


def test_the_first_start_says_which_vars_seeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AGENTOS_KEY_PREFIX", "XY")
    with caplog.at_level(logging.WARNING), started(make(tmp_path, RUNTIMES_TOML)) as c:
        assert space(c).key_prefix == "XY"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("AGENTOS_KEY_PREFIX seeded Settings > Space" in w for w in warnings), warnings
    assert not any("agentos.toml" in w for w in warnings)  # used for the seed: not ignored


def test_a_moved_var_in_the_env_file_does_not_stop_startup(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AGENTOS_PORT=9001\nAGENTOS_JIRA_PROJECTS=SCRUM\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)  # extra="forbid" would refuse an unknown key
    assert (settings.port, settings.moved_env) == (9001, {"AGENTOS_JIRA_PROJECTS": "SCRUM"})


def test_env_wins_over_the_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AGENTOS_KEY_PREFIX=FILE\n", encoding="utf-8")
    monkeypatch.setenv("AGENTOS_KEY_PREFIX", "ENV")
    assert Settings(_env_file=env_file).moved_env == {"AGENTOS_KEY_PREFIX": "ENV"}


@pytest.mark.parametrize(
    ("env", "named"),
    [
        pytest.param({"AGENTOS_KEY_PREFIX": "aos"}, "AGENTOS_KEY_PREFIX", id="prefix"),
        pytest.param({"AGENTOS_KEY_PREFIX": "A-B"}, "AGENTOS_KEY_PREFIX", id="prefix-dash"),
        pytest.param({"AGENTOS_JIRA_PROJECTS": "scrum"}, "AGENTOS_JIRA_PROJECTS", id="project"),
        pytest.param({"AGENTOS_JIRA_PROJECTS": "OK,S"}, "AGENTOS_JIRA_PROJECTS", id="short"),
        pytest.param({"AGENTOS_CLAIM_TIMEOUT_S": "30"}, "AGENTOS_CLAIM_TIMEOUT_S", id="timeout"),
        pytest.param(
            {"AGENTOS_KEY_PREFIX": "SCRUM", "AGENTOS_JIRA_PROJECTS": "SCRUM"},
            "key_prefix",
            id="prefix-is-a-project",
        ),
    ],
)
def test_an_invalid_seed_value_fails_the_first_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], named: str
) -> None:
    set_env(monkeypatch, env)
    app = create_app(make(tmp_path, RUNTIMES_TOML), launcher=FakeLauncher(), run_loops=False)
    with pytest.raises(ConfigError, match=named), TestClient(app):
        pass


def test_space_keys_other_than_name_and_default_repo_are_refused_in_toml(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        SEED_TOML.replace('name = "Work"\n', 'name = "Work"\nkey_prefix = "X"\n'), encoding="utf-8"
    )
    app = create_app(make(tmp_path, path), launcher=FakeLauncher(), run_loops=False)
    with pytest.raises(ConfigError, match=r"space\.key_prefix"), TestClient(app):
        pass


def test_an_upgraded_instance_keeps_its_config(
    tmp_path: Path, seed_toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A db from before migration 0006 (the owner's container): the seed reproduces what toml +
    env said, and its tickets keep resolving their repo."""
    old = tmp_path / "migrations-0005"
    old.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("000[1-5]_*.sql")):
        shutil.copy(path, old / path.name)
    set_env(monkeypatch, SEED_ENV)  # as the container's env file sets them today
    settings = make(tmp_path, seed_toml)
    settings.data_dir.mkdir(parents=True)
    db = Database(settings.db_path, migrations_dir=old)
    assert db.migrate() == 5
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tickets(key, slug, title, workspace_mode, repo, pipeline, created_at, "
            "updated_at) VALUES ('SCRUM-6', '', 'Imported', 'repo', 'agentos-scratch', ?, "
            "'2026-10-11T12:00:00.000000+00:00', '2026-10-11T12:00:00.000000+00:00')",
            (default_pipeline().model_dump_json(),),
        )
    with started(settings) as c:
        old_ticket = TicketDetail.model_validate(c.get("/api/tickets/SCRUM-6").json())
        assert old_ticket.repo_url == "https://github.com/o/scratch.git"
        assert repos(c).repos[1].open_ticket_count == 1
        ticket = TicketDetail.model_validate(
            c.post("/api/tickets", json={"title": "T"}, headers=CSRF).json()
        )
        assert (ticket.key, ticket.repo_name) == ("AOS-1", "agentos")
        assert ticket.repo_url == "https://github.com/o/agentos.git"
        cfg = config(c)
        assert (cfg.space_name, cfg.default_repo, cfg.key_prefix) == ("Work", "agentos", "AOS")
        assert [r.name for r in cfg.repos] == ["agentos", "agentos-scratch"]
        assert space(c).jira_projects == ["SCRUM", "OPS"]
        assert len(queries(c).queries) == 2
    assert Database(settings.db_path).user_version() == 6
