"""Settings parsing, agentos.toml loading, startup checks and GET /api/config."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.config import (
    ConfigError,
    Settings,
    load_config,
    load_settings,
    startup_checks,
)
from agentos.main import create_app
from agentos.models import AppConfig, RepoInfo
from agentos.pipeline.runtime import RuntimesConfig

RUNTIMES_TOML = Path(__file__).parent / "fixtures" / "runtimes_test.toml"
RUNTIME_Y = '[runtimes.y]\nargv = ["y"]\n'
RUNTIMES_ONLY = 'default_runtime = "y"\n' + RUNTIME_Y


def make(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "workspaces_dir": tmp_path / "workspaces",
        "dev_auth": True,
        "config": RUNTIMES_TOML,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def problems(settings: Settings) -> list[str]:
    with pytest.raises(ConfigError) as caught:
        startup_checks(settings, load_config(settings.config).runtimes)
    return caught.value.problems


@pytest.fixture
def runtimes() -> RuntimesConfig:
    return load_config(RUNTIMES_TOML).runtimes


# ------------------------------------------------------------------ env parsing


def test_env_prefix_and_types(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTOS_PORT", "9001")
    monkeypatch.setenv("AGENTOS_DEV_AUTH", "1")
    monkeypatch.setenv("AGENTOS_KEY_PREFIX", "XY")
    monkeypatch.setenv("AGENTOS_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("PORT", "1234")  # unprefixed: ignored
    settings = Settings(_env_file=None)
    assert settings.port == 9001
    assert settings.dev_auth is True
    assert settings.key_prefix == "XY"
    assert settings.data_dir == (tmp_path / "d").resolve()


def test_env_comma_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_JIRA_PROJECTS", "SCRUM, OPS_2,,AB")
    monkeypatch.setenv("AGENTOS_ADMINS", "abc123 , ana@example.com")
    settings = Settings(_env_file=None)
    assert settings.jira_projects == ["SCRUM", "OPS_2", "AB"]
    assert settings.admins == ["abc123", "ana@example.com"]


def test_empty_env_values_mean_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WORKER_ENV_FILE", "JIRA_URL", "JIRA_PROJECTS", "ADMINS"):
        monkeypatch.setenv(f"AGENTOS_{name}", "")
    settings = Settings(_env_file=None)
    assert settings.worker_env_file is None
    assert settings.jira_url is None
    assert settings.jira_projects == []
    assert settings.admins == []


@pytest.mark.parametrize("projects", ["scrum", "S", "SC-1", "1AB", "OK,bad"])
def test_jira_projects_must_be_project_keys(monkeypatch: pytest.MonkeyPatch, projects: str) -> None:
    monkeypatch.setenv("AGENTOS_JIRA_PROJECTS", projects)
    with pytest.raises(ConfigError, match="AGENTOS_JIRA_PROJECTS"):
        load_settings(_env_file=None)


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


# ------------------------------------------------------------------ agentos.toml


def test_load_config() -> None:
    config = load_config(RUNTIMES_TOML)
    assert config.runtimes.default_runtime == "fake"
    assert set(config.runtimes.runtimes) == {"fake", "claude"}
    assert (config.space.name, config.space.default_repo) == ("Test Space", "r")
    assert [(r.name, r.url, r.default_branch) for r in config.space.repos] == [
        ("r", "https://github.com/o/r.git", "main"),
        ("docs", "git@github.com:o/docs.git", "develop"),
    ]


def test_space_is_optional(tmp_path: Path) -> None:
    path = tmp_path / "plain.toml"
    path.write_text(RUNTIMES_ONLY, encoding="utf-8")
    space = load_config(path).space
    assert (space.name, space.default_repo, space.repos) == ("AgentOS", None, ())


def test_missing_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml_syntax(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("default_runtime = \n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read TOML"):
        load_config(path)


def test_invalid_toml_content(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('default_runtime = "x"\n[runtimes.y]\nargv = []\n', encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert any("runtimes.y.argv" in p for p in caught.value.problems)


def test_default_runtime_must_exist(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('default_runtime = "x"\n[runtimes.y]\nargv = ["y"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="default_runtime 'x'"):
        load_config(path)


def test_bad_repo_catalog_reports_every_problem(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(
        'default_runtime = "x"\n'  # a runtime problem too: reported with the catalog's
        "[space]\nnom = 1\n"
        '[[repos]]\nname = "Bad Name"\nurl = "file:///tmp/r"\ndefault_branch = "a..b"\n'
        '[[repos]]\nname = "ok"\nurl = "https://user:pw@example.com/r.git"\n'
        '[runtimes.y]\nargv = ["y"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    found = caught.value.problems
    for where in (
        "default_runtime 'x'",
        "space.nom",
        "repos.0.name",
        "repos.0.url",
        "repos.0.default_branch",
        "repos.1.url",
    ):
        assert any(where in p for p in found), (where, found)
    assert len(found) == 6


@pytest.mark.parametrize(
    ("catalog", "why"),
    [
        ('[space]\ndefault_repo = "nope"\n', "default_repo 'nope' is not in [[repos]]"),
        (
            '[[repos]]\nname = "a"\nurl = "https://e.com/a.git"\n'
            '[[repos]]\nname = "a"\nurl = "https://e.com/b.git"\n',
            "duplicate name 'a'",
        ),
    ],
)
def test_repo_catalog_cross_checks(tmp_path: Path, catalog: str, why: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('default_runtime = "y"\n' + catalog + RUNTIME_Y, encoding="utf-8")
    with pytest.raises(ConfigError, match=re.escape(why)):
        load_config(path)


# ------------------------------------------------------------------ startup checks


def test_valid_settings_pass_and_create_dirs(tmp_path: Path, runtimes: RuntimesConfig) -> None:
    settings = make(tmp_path)
    startup_checks(settings, runtimes)
    assert settings.data_dir.is_dir()
    assert settings.logs_dir.is_dir()
    assert settings.workspaces_dir.is_dir()


def test_dev_auth_requires_loopback_host_and_public_url(tmp_path: Path) -> None:
    found = problems(make(tmp_path, host="0.0.0.0", public_url="https://agentos.example.com"))  # noqa: S104 - must be rejected
    assert any("AGENTOS_HOST" in p for p in found)
    assert any("AGENTOS_PUBLIC_URL" in p for p in found)
    assert len(found) == 2  # every problem reported at once


def test_dev_auth_accepts_localhost(tmp_path: Path, runtimes: RuntimesConfig) -> None:
    startup_checks(make(tmp_path, host="localhost", public_url="http://localhost:5173"), runtimes)


def test_jira_required_without_dev_auth(tmp_path: Path, runtimes: RuntimesConfig) -> None:
    found = problems(make(tmp_path, dev_auth=False))
    assert any("AGENTOS_JIRA_URL" in p for p in found)
    assert any("AGENTOS_JIRA_PROJECTS" in p for p in found)
    ok = make(tmp_path, dev_auth=False, jira_url="https://x.atlassian.net", jira_projects=["XY"])
    startup_checks(ok, runtimes)


def test_github_launcher_not_available(tmp_path: Path) -> None:
    assert any("M1" in p for p in problems(make(tmp_path, launcher="github")))


@pytest.mark.parametrize("prefix", ["aos", "1AB", "A-B", ""])
def test_bad_key_prefix(tmp_path: Path, prefix: str) -> None:
    assert any("AGENTOS_KEY_PREFIX" in p for p in problems(make(tmp_path, key_prefix=prefix)))


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
    app = create_app(make(tmp_path, key_prefix="bad"))
    with pytest.raises(ConfigError, match="AGENTOS_KEY_PREFIX"), TestClient(app):
        pass


# ------------------------------------------------------------------ GET /api/health, /api/config


def test_health(tmp_path: Path) -> None:
    with TestClient(create_app(make(tmp_path))) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


def test_api_config_reflects_dev_settings(tmp_path: Path) -> None:
    settings = make(tmp_path, key_prefix="XYZ")
    with TestClient(create_app(settings)) as client:
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
    )


def test_api_config_reflects_jira_settings(tmp_path: Path) -> None:
    settings = make(
        tmp_path,
        dev_auth=False,
        jira_url="https://jira.example.com",
        jira_projects=["SCRUM"],
        jira_deployment="datacenter",
    )
    with TestClient(create_app(settings)) as client:
        config = client.get("/api/config").json()
    assert config["auth_mode"] == "jira"
    assert config["jira_deployment"] == "datacenter"
    assert config["jira_url"] == "https://jira.example.com"


def test_api_config_without_a_space(tmp_path: Path) -> None:
    path = tmp_path / "plain.toml"
    path.write_text(RUNTIMES_ONLY, encoding="utf-8")
    with TestClient(create_app(make(tmp_path, config=path))) as client:
        config = client.get("/api/config").json()
    assert (config["space_name"], config["repos"], config["default_repo"]) == ("AgentOS", [], None)
