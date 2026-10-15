"""Settings > Runners (S2): the runtimes and runner endpoints and their rules, runtimes and the
runner applying without a restart (config, manifests, the github launcher)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentos.config import Settings
from agentos.context import AppContext, runner_target
from agentos.launchers.github import NO_REPO, GitHubActionsLauncher
from agentos.main import create_app
from agentos.models import AppConfig, Manifest, RunnerSettings, RuntimeSettings, TicketDetail
from tests.run_support import RunDriver
from tests.test_runs import put_pipeline, stage_of

CSRF = {"X-AgentOS": "1"}
BASE_URL = "http://127.0.0.1:8000"
FAKE_ARGV = ["python", "fake_agent.py"]
GH_TOKEN = "github_pat_SECRET123"  # noqa: S105 - test value
LoginFn = Callable[..., Any]
CreateTicket = Callable[..., TicketDetail]


def error(response: httpx.Response) -> tuple[int, str, str]:
    body = response.json()["error"]
    return response.status_code, body["code"], body["message"]


def runtimes(client: TestClient) -> RuntimeSettings:
    response = client.get("/api/settings/runtimes")
    assert response.status_code == 200, response.text
    return RuntimeSettings.model_validate(response.json())


def runner(client: TestClient) -> RunnerSettings:
    response = client.get("/api/settings/runner")
    assert response.status_code == 200, response.text
    return RunnerSettings.model_validate(response.json())


def config(client: TestClient) -> AppConfig:
    return AppConfig.model_validate(client.get("/api/config").json())


def entries(client: TestClient) -> list[dict[str, Any]]:
    return [r.model_dump() for r in runtimes(client).runtimes]


def put_runtimes(client: TestClient, items: list[dict[str, Any]], **extra: Any) -> httpx.Response:
    body = {"runtimes": items, "base_version": runtimes(client).version, **extra}
    response: httpx.Response = client.put("/api/settings/runtimes", json=body, headers=CSRF)
    return response


def put_runner(client: TestClient, **changes: Any) -> httpx.Response:
    current = runner(client)
    body = {
        "github_repo": current.github_repo,
        "workflow": current.workflow,
        "ref": current.ref,
        "base_version": current.version,
        **changes,
    }
    response: httpx.Response = client.put("/api/settings/runner", json=body, headers=CSRF)
    return response


def runtime(name: str, argv: list[str], **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "argv": argv,
        "model_args": ["--model", "{model}"],
        "agent_args": [],
        "prompt_via": "stdin",
        "prompt_args": [],
    }
    return base | fields


def set_template(client: TestClient, **changes: Any) -> httpx.Response:
    current = client.get("/api/template").json()
    body = {
        "scaffold": current["scaffold"],
        "base_version": current["version"],
        **changes,
    }
    response: httpx.Response = client.put("/api/template", json=body, headers=CSRF)
    return response


# ------------------------------------------------------------------ runtimes


def test_seeded_runtimes_from_the_test_toml(authed: TestClient) -> None:
    got = runtimes(authed)
    assert [r.name for r in got.runtimes] == ["fake", "claude"]  # default_runtime first
    assert got.runtimes[0].model_dump() == {
        "name": "fake",
        "argv": FAKE_ARGV,
        "model_args": ["--model", "{model}"],
        "effort_args": [],
        "agent_args": [],
        "prompt_via": "stdin",
        "prompt_args": [],
        "session_args": [],
        "resume_args": [],
        "verdict_args": [],
        "verdict_from": "file",
    }
    assert (got.version, got.updated_by) == (1, None)


def test_save_reorders_and_the_first_is_the_server_default(authed: TestClient) -> None:
    fake, claude = entries(authed)
    saved = put_runtimes(authed, [claude, fake])
    assert saved.status_code == 200, saved.text
    got = RuntimeSettings.model_validate(saved.json())
    assert [r.name for r in got.runtimes] == ["claude", "fake"]
    assert got.version == 2
    assert got.updated_by is not None
    assert got.updated_by.display_name == "dev"
    cfg = config(authed)  # no restart
    assert (cfg.server_default_runtime, cfg.default_runtime) == ("claude", "claude")
    assert cfg.runtimes == ["claude", "fake"]


def test_added_runtimes_show_in_config_and_the_template_accepts_them(authed: TestClient) -> None:
    added = runtime("gemini", ["gemini", "--yolo"], prompt_via="arg", prompt_args=["{prompt}"])
    assert put_runtimes(authed, [*entries(authed), added]).status_code == 200
    assert config(authed).runtimes == ["claude", "fake", "gemini"]
    assert set_template(authed, defaults={"runtime": "gemini"}).status_code == 200
    assert config(authed).default_runtime == "gemini"


def test_a_stale_save_is_refused(authed: TestClient) -> None:
    current = entries(authed)
    assert put_runtimes(authed, current).status_code == 200
    stale = put_runtimes(authed, current, base_version=1)
    assert error(stale)[:2] == (409, "stale_version")
    assert runtimes(authed).version == 2


@pytest.mark.parametrize(
    ("items", "where"),
    [
        pytest.param([], "runtimes", id="none"),
        pytest.param([runtime("a", ["a"]), runtime("a", ["b"])], "runtimes.1.name", id="dup"),
        pytest.param([runtime("Bad Name", ["a"])], "name", id="name"),
        pytest.param([runtime("a", [])], "argv", id="no-argv"),
        pytest.param([runtime("a", ["  "])], "runtimes.0", id="blank-program"),
        pytest.param([runtime("a", ["a"], prompt_via="arg")], "runtimes.0", id="arg-no-prompt"),
        pytest.param(
            [runtime("a", ["a"], prompt_args=["-p", "{prompt}"])], "runtimes.0", id="stdin-args"
        ),
        pytest.param([runtime("a", ["a"], prompt_via="file")], "prompt_via", id="via"),
        pytest.param([{"name": "a", "argv": ["a"]}], "model_args", id="missing-fields"),
    ],
)
def test_runtimes_are_validated(authed: TestClient, items: list[Any], where: str) -> None:
    status, code, message = error(put_runtimes(authed, items))
    assert (status, code) == (422, "validation_error")
    assert where in message, message
    assert runtimes(authed).version == 1


def test_removing_a_runtime_the_template_or_a_workflow_uses_is_refused(authed: TestClient) -> None:
    fake, _claude = entries(authed)
    assert set_template(authed, defaults={"runtime": "claude"}).status_code == 200
    status, code, message = error(put_runtimes(authed, [fake]))
    assert (status, code) == (409, "in_use")
    assert "'claude' (it is the template's default runtime)" in message

    pipeline = authed.get("/api/workflows").json()[0]["pipeline"]
    for stage in pipeline["stages"]:
        if stage["kind"] == "agent" and stage["id"] in ("plan", "review"):
            stage["runtime"] = "claude"
    body = {"name": "Careful", "pipeline": pipeline}
    assert authed.post("/api/workflows", json=body, headers=CSRF).status_code == 201
    assert set_template(authed, defaults={}).status_code == 200
    status, code, message = error(put_runtimes(authed, [fake]))
    assert (status, code) == (409, "in_use")
    assert "'claude' (the workflow Careful uses it)" in message
    assert [r.name for r in runtimes(authed).runtimes] == ["fake", "claude"]


def test_removing_an_unused_runtime_works_and_pinned_ticket_stages_fail_at_dispatch(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    key = create_ticket().key
    put_pipeline(authed, key, lambda p: stage_of(p, "plan").update(runtime="claude"))
    fake, _claude = entries(authed)
    assert put_runtimes(authed, [fake]).status_code == 200  # tickets' own stages aren't checked
    assert config(authed).runtimes == ["fake"]
    status, code, message = error(runs.dispatch(key, "plan"))
    assert (status, code) == (422, "invalid_stage")
    assert "unknown runtime 'claude'" in message


def test_the_template_cannot_use_a_runtime_that_is_gone(authed: TestClient) -> None:
    fake, _claude = entries(authed)
    assert put_runtimes(authed, [fake]).status_code == 200
    status, code, _ = error(set_template(authed, defaults={"runtime": "claude"}))
    assert (status, code) == (422, "validation_error")


def test_a_manifest_built_after_a_save_uses_the_new_argv(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    key = create_ticket().key
    run = runs.queue(key, "plan")  # dispatched before the save; built at claim
    fake, claude = entries(authed)
    fake["argv"] = ["python", "fake_agent.py", "--new"]
    assert put_runtimes(authed, [fake, claude]).status_code == 200
    manifest: Manifest = runs.launch_and_claim(run.id).manifest
    assert manifest.stages[0].argv == ["python", "fake_agent.py", "--new"]


def test_runtimes_are_admin_only(client: TestClient, login: LoginFn) -> None:
    login("wanda", role="writer")
    assert client.get("/api/settings/runtimes").status_code == 403
    body = {"runtimes": [runtime("a", ["a"])], "base_version": 1}
    assert client.put("/api/settings/runtimes", json=body, headers=CSRF).status_code == 403


# ------------------------------------------------------------------ runner


def test_seeded_runner_with_the_local_launcher(authed: TestClient) -> None:
    got = runner(authed)
    assert got.model_dump(exclude={"updated_at"}) == {
        "launcher": "local",
        "github_repo": None,
        "workflow": "agentos-worker.yml",
        "ref": "main",
        "token_set": False,
        "version": 1,
        "updated_by": None,
    }


def test_save_the_runner(authed: TestClient) -> None:
    saved = put_runner(authed, github_repo="DiwashRai/AgentOS", workflow="w.yaml", ref=" release ")
    assert saved.status_code == 200, saved.text
    got = RunnerSettings.model_validate(saved.json())
    assert (got.github_repo, got.workflow, got.ref, got.version) == (
        "DiwashRai/AgentOS",
        "w.yaml",
        "release",
        2,
    )
    assert got.updated_by is not None
    assert runner(authed) == got
    cleared = put_runner(authed, github_repo=None)
    assert cleared.status_code == 200, cleared.text
    assert runner(authed).github_repo is None
    stale = put_runner(authed, base_version=1)
    assert error(stale)[:2] == (409, "stale_version")


@pytest.mark.parametrize(
    "changes",
    [
        {"github_repo": "AgentOS"},
        {"github_repo": "a/b/c"},
        {"github_repo": ""},
        {"workflow": "../x.yml"},
        {"workflow": "agentos-worker"},
        {"ref": "  "},
        {"launcher": "github"},  # read-only (env)
        {"token_set": True},
    ],
)
def test_runner_is_validated(authed: TestClient, changes: dict[str, Any]) -> None:
    assert error(put_runner(authed, **changes))[:2] == (422, "validation_error")
    assert runner(authed).version == 1


def test_runner_is_admin_only(client: TestClient, login: LoginFn) -> None:
    login("wanda", role="writer")
    assert client.get("/api/settings/runner").status_code == 403
    body = {"github_repo": "o/r", "workflow": "w.yml", "ref": "main", "base_version": 1}
    assert client.put("/api/settings/runner", json=body, headers=CSRF).status_code == 403


def test_github_launcher_reads_the_runner_at_each_launch(
    authed: TestClient, ctx: AppContext, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    """No repo set → the run fails at launch saying where to set it (GitHub isn't called). Once
    an admin saves one, the next launch goes there, no restart."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    ctx.launcher = GitHubActionsLauncher(
        target=runner_target(ctx.space_store),
        token=GH_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    key = create_ticket().key
    first = runs.queue(key, "plan")
    assert runs.tick() == [first.id]
    failed = runs.get(first.id)
    assert (failed.status, failed.error) == ("failed", f"launch failed: {NO_REPO}")
    assert seen == []

    assert put_runner(authed, github_repo="o/r", ref="dev").status_code == 200
    second = runs.queue(key, "plan")
    assert runs.tick() == [second.id]
    [request] = seen
    assert str(request.url) == (
        "https://api.github.com/repos/o/r/actions/workflows/agentos-worker.yml/dispatches"
    )
    assert json.loads(request.content)["ref"] == "dev"
    assert runs.get(second.id).launch_ref == (
        "https://github.com/o/r/actions/workflows/agentos-worker.yml"
    )


def test_runner_get_reports_the_env_launcher_and_token(settings: Settings) -> None:
    github = settings.model_copy(update={"launcher": "github", "github_token": SecretStr(GH_TOKEN)})
    with TestClient(create_app(github, run_loops=False), base_url=BASE_URL) as c:
        assert c.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).is_success
        got = runner(c)
        assert (got.launcher, got.token_set, got.github_repo) == ("github", True, None)
        assert GH_TOKEN not in c.get("/api/settings/runner").text
