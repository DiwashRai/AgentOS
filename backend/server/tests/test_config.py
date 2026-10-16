"""Settings parsing, startup checks, the defaults a fresh db starts with and GET /api/config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.config import ConfigError, Settings, load_settings, startup_checks
from agentos.context import build_context
from agentos.launchers.github import GitHubActionsProvider
from agentos.launchers.mailbox import MailboxLauncher
from agentos.main import create_app
from agentos.models import AppConfig, RepoInfo, RunnerSettings, RuntimeSettings, SpaceSettings
from agentos.pipeline.runtime import BUILTIN_RUNTIMES
from tests.settings_support import seed_test_settings, set_space

CSRF = {"X-AgentOS": "1"}
BASE_URL = "http://127.0.0.1:8000"


def make(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "workspaces_dir": tmp_path / "workspaces",
        "dev_auth": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def problems(settings: Settings) -> list[str]:
    with pytest.raises(ConfigError) as caught:
        startup_checks(settings)
    return caught.value.problems


# ------------------------------------------------------------------ env parsing


def test_env_prefix_and_types(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTOS_PORT", "9001")
    monkeypatch.setenv("AGENTOS_DEV_AUTH", "1")
    monkeypatch.setenv("AGENTOS_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("PORT", "1234")  # unprefixed: ignored
    settings = Settings(_env_file=None)
    assert settings.port == 9001
    assert settings.dev_auth is True
    assert settings.data_dir == (tmp_path / "d").resolve()


def test_env_comma_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_ADMINS", "abc123 , ana@example.com")
    settings = Settings(_env_file=None)
    assert settings.admins == ["abc123", "ana@example.com"]


def test_empty_env_values_mean_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WORKER_ENV_FILE", "JIRA_URL", "ADMINS"):
        monkeypatch.setenv(f"AGENTOS_{name}", "")
    settings = Settings(_env_file=None)
    assert settings.worker_env_file is None
    assert settings.jira_url is None
    assert settings.admins == []


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.workspaces_dir == (Path.home() / ".agentos" / "workspaces").resolve()
    assert settings.data_dir == Path("data").resolve()
    assert settings.local_max_concurrency == 2
    assert settings.effective_worker_url == "http://127.0.0.1:8000"
    assert Settings(_env_file=None, worker_url="https://t.example/").effective_worker_url == (
        "https://t.example"
    )


def test_unlimited_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_LOCAL_MAX_CONCURRENCY", "unlimited")
    assert Settings(_env_file=None).local_max_concurrency is None


def test_env_example_parses(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    settings = Settings(_env_file=example)
    assert settings.data_dir == Path("data").resolve()  # inline `# comment` stripped
    assert settings.dev_auth is True
    assert settings.local_max_concurrency == 2


def test_load_settings_reports_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_LAUNCHER", "k8s")
    monkeypatch.setenv("AGENTOS_PORT", "eighty")
    with pytest.raises(ConfigError) as caught:
        load_settings(_env_file=None)
    text = str(caught.value)
    assert "AGENTOS_LAUNCHER" in text
    assert "AGENTOS_PORT" in text


# ------------------------------------------------------------------ startup checks


def test_valid_settings_pass_and_create_dirs(tmp_path: Path) -> None:
    settings = make(tmp_path)
    startup_checks(settings)
    assert settings.data_dir.is_dir()
    assert settings.logs_dir.is_dir()
    assert settings.workspaces_dir.is_dir()


def test_dev_auth_requires_loopback_host_and_public_url(tmp_path: Path) -> None:
    found = problems(make(tmp_path, host="0.0.0.0", public_url="https://agentos.example.com"))  # noqa: S104 - must be rejected
    assert any("AGENTOS_HOST" in p for p in found)
    assert any("AGENTOS_PUBLIC_URL" in p for p in found)
    assert len(found) == 2  # every problem reported at once


def test_dev_auth_accepts_localhost(tmp_path: Path) -> None:
    startup_checks(make(tmp_path, host="localhost", public_url="http://localhost:5173"))


def test_jira_required_without_dev_auth(tmp_path: Path) -> None:
    found = problems(make(tmp_path, dev_auth=False))
    assert found == ["AGENTOS_JIRA_URL is required unless AGENTOS_DEV_AUTH=1"]
    # The Space's Jira projects are set in the app (Settings > Space), not required at startup.
    startup_checks(make(tmp_path, dev_auth=False, jira_url="https://x.atlassian.net"))


JIRA_URL = "https://x.atlassian.net"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"jira_service_email": "bot@x.com"}, "both AGENTOS_JIRA_SERVICE_EMAIL"),
        ({"jira_service_token": "s3cret-tok"}, "both AGENTOS_JIRA_SERVICE_EMAIL"),
        (
            {"jira_url": None, "jira_service_email": "b@x.com", "jira_service_token": "s3cret-tok"},
            "AGENTOS_JIRA_SERVICE_TOKEN needs AGENTOS_JIRA_URL",
        ),
        (
            {"jira_service_email": "b@x.com", "jira_service_token": "s3cret tok"},
            "printable ASCII",
        ),
        ({"jira_url": "ftp://x"}, "AGENTOS_JIRA_URL"),  # checked in dev auth mode too
    ],
)
def test_jira_service_account_checks(
    tmp_path: Path, overrides: dict[str, Any], expected: str
) -> None:
    found = problems(make(tmp_path, **{"jira_url": JIRA_URL, **overrides}))
    assert any(expected in p for p in found), found
    assert not any("s3cret" in p for p in found)


@pytest.mark.parametrize(
    "overrides",
    [
        {"jira_service_email": "bot@x.com", "jira_service_token": "s3cret-tok"},  # dev auth: import
        {"jira_deployment": "datacenter", "jira_service_token": "pat"},  # DC: a PAT, no email
        {},  # no service account: no import
    ],
)
def test_jira_service_account_accepted(tmp_path: Path, overrides: dict[str, Any]) -> None:
    startup_checks(make(tmp_path, jira_url=JIRA_URL, **overrides))


def test_github_launcher_needs_a_token_but_not_a_repo(tmp_path: Path) -> None:
    # The repo/workflow/ref are set in the app (Settings > Runners), read at each launch.
    found = problems(make(tmp_path, launcher="github"))
    assert found == ["AGENTOS_GITHUB_TOKEN is required with AGENTOS_LAUNCHER=github"]
    startup_checks(make(tmp_path, launcher="github", github_token="t0ken"))  # noqa: S106 - fake


def test_github_launcher_is_built_from_settings(tmp_path: Path) -> None:
    github = {"launcher": "github", "github_token": "t0ken"}
    ctx = build_context(make(tmp_path, **github))
    assert list(ctx.providers) == ["github"]
    assert isinstance(ctx.providers["github"], GitHubActionsProvider)
    assert (ctx.route, ctx.mailbox) == (("push", "github", "live"), None)
    with pytest.raises(ConfigError) as caught:
        build_context(make(tmp_path, **{**github, "github_api_version": "latest"}))
    assert "AGENTOS_GITHUB_" in str(caught.value)
    assert "t0ken" not in str(caught.value)


ARTIFACTORY = {
    "artifactory_url": "https://artifactory.example/artifactory/generic-local/agentos",
    "artifactory_user": "svc",
    "artifactory_password": "s3cret-pw",
}


def test_mailbox_launcher_needs_the_github_token_and_artifactory(tmp_path: Path) -> None:
    found = problems(make(tmp_path, launcher="mailbox"))
    assert found == [
        "AGENTOS_GITHUB_TOKEN is required with AGENTOS_LAUNCHER=mailbox",
        "AGENTOS_ARTIFACTORY_URL is required with AGENTOS_LAUNCHER=mailbox",
        "AGENTOS_ARTIFACTORY_USER is required with AGENTOS_LAUNCHER=mailbox",
        "AGENTOS_ARTIFACTORY_PASSWORD is required with AGENTOS_LAUNCHER=mailbox",
    ]
    startup_checks(make(tmp_path, launcher="mailbox", github_token="t0ken", **ARTIFACTORY))  # noqa: S106 - fake


@pytest.mark.parametrize(
    "url",
    [
        "http://artifactory.example/artifactory/generic-local/agentos",  # not https
        "https://artifactory.example/generic-local/agentos",  # no /artifactory/
        "https://artifactory.example/artifactory",  # no repo
        "https:///artifactory/generic-local",  # no host
    ],
)
def test_mailbox_artifactory_url_must_be_https_with_a_repo(tmp_path: Path, url: str) -> None:
    settings = make(
        tmp_path,
        launcher="mailbox",
        github_token="t0ken",  # noqa: S106 - fake
        **{**ARTIFACTORY, "artifactory_url": url},
    )
    found = problems(settings)
    assert len(found) == 1
    assert found[0].startswith("AGENTOS_ARTIFACTORY_URL=")
    assert "s3cret-pw" not in found[0]


def test_mailbox_launcher_is_built_from_settings(tmp_path: Path) -> None:
    ctx = build_context(
        make(tmp_path, launcher="mailbox", github_token="t0ken", **ARTIFACTORY)  # noqa: S106 - fake
    )
    assert isinstance(ctx.mailbox, MailboxLauncher)
    assert ctx.providers == {"github": ctx.mailbox.github}
    assert isinstance(ctx.mailbox.github, GitHubActionsProvider)
    assert ctx.route == ("push", "github", "file")


def test_workspaces_inside_data_dir(tmp_path: Path) -> None:
    found = problems(make(tmp_path, workspaces_dir=tmp_path / "data" / "ws"))
    assert any("AGENTOS_WORKSPACES_DIR" in p and "AGENTOS_DATA_DIR" in p for p in found)


def test_data_dir_inside_workspaces(tmp_path: Path) -> None:
    found = problems(make(tmp_path, data_dir=tmp_path / "workspaces" / "data"))
    assert any("AGENTOS_DATA_DIR" in p for p in found)


def test_workspaces_inside_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    found = problems(make(tmp_path, workspaces_dir=repo / "not" / "yet" / "there"))
    assert any("git repo" in p for p in found)
    assert not (repo / "not").exists()  # nothing created when checks fail


def test_workspaces_inside_git_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")  # worktrees use a file
    assert any("git repo" in p for p in problems(make(tmp_path, workspaces_dir=repo / "ws")))


def test_missing_worker_env_file(tmp_path: Path) -> None:
    found = problems(make(tmp_path, worker_env_file=tmp_path / "missing.env"))
    assert any("AGENTOS_WORKER_ENV_FILE" in p for p in found)


def test_lifespan_fails_fast_on_bad_config(tmp_path: Path) -> None:
    app = create_app(make(tmp_path, launcher="github"))
    with pytest.raises(ConfigError, match="AGENTOS_GITHUB_TOKEN"), TestClient(app):
        pass


# ------------------------------------------------------------------ GET /api/health, /api/config


def test_health(tmp_path: Path) -> None:
    with TestClient(create_app(make(tmp_path))) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


def test_a_fresh_start_seeds_the_built_in_defaults(tmp_path: Path) -> None:
    with TestClient(create_app(make(tmp_path)), base_url=BASE_URL) as client:
        assert client.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).is_success
        config = AppConfig.model_validate(client.get("/api/config").json())
        space = SpaceSettings.model_validate(client.get("/api/settings/space").json())
        runtimes = RuntimeSettings.model_validate(client.get("/api/settings/runtimes").json())
        runner = RunnerSettings.model_validate(client.get("/api/settings/runner").json())

    assert (config.space_name, config.key_prefix, config.repos, config.default_repo) == (
        "AgentOS",
        "AOS",
        [],
        None,
    )
    assert config.runtimes == ["claude", "copilot"]
    assert (config.default_runtime, config.server_default_runtime) == ("claude", "claude")

    got = space.value
    assert (got.name, got.key_prefix, got.jira_projects, got.claim_timeout_s) == (
        "AgentOS",
        "AOS",
        [],
        900,
    )
    assert [r.model_dump() for r in runtimes.runtimes] == [
        r.model_dump(mode="json") for r in BUILTIN_RUNTIMES
    ]
    assert runner.value.model_dump() == {
        "github_repo": None,
        "workflow": "agentos-worker.yml",
        "ref": "main",
    }
    for area in (space, runtimes, runner):
        assert (area.version, area.updated_by) == (1, None)


def test_api_config_reflects_dev_settings(tmp_path: Path) -> None:
    settings = make(tmp_path)
    seed_test_settings(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        set_space(app.state.ctx, key_prefix="XYZ")
        config = AppConfig.model_validate(client.get("/api/config").json())
    assert config == AppConfig(
        auth_mode="dev",
        jira_deployment=None,
        jira_url=None,
        key_prefix="XYZ",
        space_name="Test Space",
        repos=[
            RepoInfo(name="r", url="https://github.com/o/r.git", default_branch="main"),
            RepoInfo(name="docs", url="git@github.com:o/docs.git", default_branch="develop"),
        ],
        default_repo="r",
        runtimes=["claude", "fake"],
        default_runtime="fake",
        default_model=None,
        default_effort=None,
        default_workflow_id=1,
        server_default_runtime="fake",
        sources=[],
    )


def test_api_config_reflects_jira_settings(tmp_path: Path) -> None:
    settings = make(
        tmp_path,
        dev_auth=False,
        jira_url="https://jira.example.com",
        jira_deployment="datacenter",
    )
    with TestClient(create_app(settings)) as client:
        config = client.get("/api/config").json()
    assert config["auth_mode"] == "jira"
    assert config["jira_deployment"] == "datacenter"
    assert config["jira_url"] == "https://jira.example.com"
