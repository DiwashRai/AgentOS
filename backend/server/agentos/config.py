"""Settings: pydantic-settings (AGENTOS_* env) + agentos.toml loader (runtimes, and the Space's
first-start seed) + startup checks (SPEC §14, §17).

The server runs from `backend/`, so `.env`, `agentos.toml` and relative paths resolve from there.
What an admin may change lives in the db and is edited in the app (Settings); env keeps what the
server needs to start, who may sign in, and secrets.
"""

from __future__ import annotations

import ipaddress
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ModelWrapValidatorHandler,
    PrivateAttr,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.space import SPACE_TOML_KEYS, SpaceConfig, SpaceSection

# Env vars whose settings moved into the app, and the Settings tab that replaced each. A moved var
# that is still set doesn't stop startup: it is ignored with a warning, and its value seeds the
# setting on first start only (when the db has no row for it yet).
MOVED_ENV: dict[str, str] = {
    "AGENTOS_KEY_PREFIX": "Settings > Space",
    "AGENTOS_JIRA_PROJECTS": "Settings > Space",
    "AGENTOS_CLAIM_TIMEOUT_S": "Settings > Space",
}
# The SpaceSection field each moved var seeds.
_MOVED_FIELD = {
    "AGENTOS_KEY_PREFIX": "key_prefix",
    "AGENTOS_JIRA_PROJECTS": "jira_projects",
    "AGENTOS_CLAIM_TIMEOUT_S": "claim_timeout_s",
}


class ConfigError(Exception):
    """Invalid configuration. `problems` lists every problem found, so one restart fixes all."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))


def _split_csv(value: object) -> object:
    """`a, b,,c` → ["a", "b", "c"]; lists (from code) pass through."""
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `AGENTOS_JIRA_URL=` means "unset", not ""
        extra="forbid",  # a typo'd AGENTOS_* key in .env fails startup instead of being ignored
        validate_default=True,
    )

    data_dir: Path = Path("./data")
    workspaces_dir: Path = Path("~/.agentos/workspaces")
    public_url: str = "http://127.0.0.1:8000"
    worker_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    config: Path = Path("agentos.toml")
    launcher: Literal["local", "github"] = "local"
    local_max_concurrency: int | None = 2  # None ("none"/"unlimited" in env) = no limit
    worker_env_file: Path | None = None
    web_dir: Path | None = None  # the built SPA (web/dist) to serve; None = API only (dev)
    dev_auth: bool = False
    trust_proxy: bool = False
    jira_url: str | None = None
    jira_deployment: Literal["cloud", "datacenter"] = "cloud"
    # The service account for server-to-Jira calls (SPEC §11): issue import. Cloud: email + classic
    # API token; Data Center: a PAT (the email is unused). Import is on when the token is set.
    jira_service_email: str | None = None
    jira_service_token: SecretStr | None = None
    admins: Annotated[list[str], NoDecode] = []
    # AGENTOS_LAUNCHER=github: the workflow (backend/deploy/github/agentos-worker.yml) dispatched
    # per run, on `github_ref` of `github_repo`. The token needs Actions: write on that repo.
    github_repo: str | None = None
    github_workflow: str = "agentos-worker.yml"
    github_ref: str = "main"
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"  # GHES: https://<host>/api/v3
    github_api_version: str = "2022-11-28"

    # MOVED_ENV vars that were set (env wins over .env), by name: the first-start seed only.
    _moved_env: dict[str, str] = PrivateAttr(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def _set_aside_moved(cls, data: Any, handler: ModelWrapValidatorHandler[Settings]) -> Settings:
        """Take the MOVED_ENV vars out before validation (`extra="forbid"` would refuse them in
        `.env`; env vars that aren't fields are never read) and keep their raw values aside."""
        moved: dict[str, str] = {}
        if isinstance(data, dict):
            data = dict(data)
            for name in MOVED_ENV:  # .env extras arrive under their full lower-case name
                value = data.pop(name.lower(), None)
                if isinstance(value, str) and value.strip():
                    moved[name] = value.strip()
        environ = {k.upper(): v for k, v in os.environ.items()}
        for name in MOVED_ENV:
            if environ.get(name, "").strip():
                moved[name] = environ[name].strip()
        settings = handler(data)
        settings._moved_env = moved
        return settings

    @property
    def moved_env(self) -> dict[str, str]:
        """The moved vars that are still set (name → raw value)."""
        return dict(self._moved_env)

    @field_validator("data_dir", "workspaces_dir", "config", "worker_env_file", "web_dir")
    @classmethod
    def _absolute(cls, value: Path | None) -> Path | None:
        return None if value is None else value.expanduser().resolve()

    @field_validator("public_url", "worker_url", "jira_url", "github_api_url")
    @classmethod
    def _strip_slash(cls, value: str | None) -> str | None:
        return None if value is None else value.rstrip("/")

    @field_validator("admins", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("local_max_concurrency", mode="before")
    @classmethod
    def _unlimited(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in ("none", "unlimited"):
            return None
        return value

    @property
    def effective_worker_url(self) -> str:
        """What workers dial: WORKER_URL, defaulting to PUBLIC_URL."""
        return self.worker_url or self.public_url

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agentos.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def load_settings(**overrides: Any) -> Settings:
    """`Settings()` with validation errors turned into a readable ConfigError."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        raise ConfigError([_describe(err) for err in exc.errors()]) from None


def _describe(err: Any) -> str:
    field = ".".join(str(p) for p in err.get("loc", ()))
    name = f"AGENTOS_{field.upper()}" if field else "settings"
    return f"{name}: {err.get('msg', 'invalid')}"


@dataclass(frozen=True, slots=True)
class FileConfig:
    """`agentos.toml`: the agent runtimes (SPEC §7) and the Space's first-start seed (§17):
    `[space]` name/default_repo, `[[repos]]`, `[[intake.queries]]`. Once an area has a db row,
    its tables here are ignored. `space_tables` = which of `space`, `repos`, `intake` the file has.
    """

    runtimes: RuntimesConfig
    space: SpaceConfig
    space_tables: frozenset[str] = frozenset()


_M = TypeVar("_M", bound=BaseModel)
# Top-level keys that belong to SpaceConfig; the rest: runtimes.
_SPACE_KEYS = ("space", "repos", "intake")


def load_config(path: Path) -> FileConfig:
    """Parse `agentos.toml` once. Every problem in both parts is reported in one ConfigError."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            [f"{path}: config not found (copy agentos.toml.example, or set AGENTOS_CONFIG)"]
        ) from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError([f"{path}: cannot read TOML: {exc}"]) from None
    space_data = {k: data.pop(k) for k in _SPACE_KEYS if k in data}
    problems: list[str] = []
    table = space_data.get("space")
    if isinstance(table, dict):  # only name/default_repo: the rest is env (seed) or the app
        for key in sorted(set(table) - set(SPACE_TOML_KEYS)):
            del table[key]
            problems.append(
                f"{path}: space.{key}: not an agentos.toml key (only {', '.join(SPACE_TOML_KEYS)})"
            )
    runtimes = _validated(RuntimesConfig, data, path, problems)
    space = _validated(SpaceConfig, space_data, path, problems)
    if runtimes is None or space is None or problems:
        raise ConfigError(problems)
    return FileConfig(runtimes=runtimes, space=space, space_tables=frozenset(space_data))


def seed_space(file_space: SpaceConfig, moved_env: Mapping[str, str]) -> SpaceConfig:
    """The first-start seed: agentos.toml's Space with the values of the MOVED_ENV vars that are
    set. An invalid value → ConfigError naming the variable (as when they were settings)."""
    values: dict[str, Any] = file_space.space.model_dump()
    for name, field in _MOVED_FIELD.items():
        if name in moved_env:
            raw = moved_env[name]
            values[field] = _split_csv(raw) if field == "jira_projects" else raw
    try:
        section = SpaceSection.model_validate(values)
    except ValidationError as exc:
        problems: list[str] = []
        for err in exc.errors():
            loc = str(err["loc"][0]) if err["loc"] else ""
            var = next((n for n, f in _MOVED_FIELD.items() if f == loc), None)
            where = var or (f"agentos.toml space.{loc}" if loc else "AGENTOS_* (Space seed)")
            problems.append(f"{where}: {err['msg'].removeprefix('Value error, ')}")
        raise ConfigError(problems) from None
    return file_space.model_copy(update={"space": section})


def _validated(model: type[_M], data: dict[str, Any], path: Path, problems: list[str]) -> _M | None:
    """`model` validated from `data`, or None with its errors appended to `problems`."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]) or "(root)"
            problems.append(f"{path}: {where}: {err['msg']}")
        return None


def is_loopback_host(host: str | None) -> bool:
    """127.0.0.0/8, ::1 or `localhost`."""
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _url_problem(name: str, url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"{name}={url!r} must be an http(s) URL with a host"
    return None


def _git_repo_containing(path: Path) -> Path | None:
    """The nearest existing ancestor of `path` (itself included) that holds a `.git` entry."""
    for candidate in (path, *path.parents):
        if candidate.exists() and (candidate / ".git").exists():
            return candidate
    return None


def _jira_service_problems(settings: Settings) -> list[str]:
    """The import service account: the token needs AGENTOS_JIRA_URL; Cloud needs the email and
    the token together (or neither). Problems name the settings, never the token's value."""
    email, secret = settings.jira_service_email, settings.jira_service_token
    token = secret.get_secret_value() if secret is not None else None
    problems: list[str] = []
    if token is not None:
        if not settings.jira_url:
            problems.append("AGENTOS_JIRA_SERVICE_TOKEN needs AGENTOS_JIRA_URL")
        if not (token.isascii() and token.isprintable() and " " not in token):
            problems.append("AGENTOS_JIRA_SERVICE_TOKEN must be printable ASCII without spaces")
    if settings.jira_deployment == "cloud" and (email is None) != (token is None):
        problems.append(
            "Jira Cloud needs both AGENTOS_JIRA_SERVICE_EMAIL and AGENTOS_JIRA_SERVICE_TOKEN "
            "(or neither: no Jira import)"
        )
    return problems


def startup_checks(settings: Settings, runtimes: RuntimesConfig) -> None:
    """Validate cross-field rules, then create the data, logs and workspaces dirs.

    Raises ConfigError listing every problem. `runtimes` is already validated by its model
    (default_runtime exists); it is a parameter so later checks can cross-reference it.
    """
    del runtimes  # nothing cross-checked yet
    problems: list[str] = []

    urls = {"AGENTOS_PUBLIC_URL": settings.public_url, "AGENTOS_WORKER_URL": settings.worker_url}
    for name, url in urls.items():
        if url is not None and (problem := _url_problem(name, url)):
            problems.append(problem)

    if settings.dev_auth:
        if not is_loopback_host(settings.host):
            problems.append(
                f"AGENTOS_DEV_AUTH=1 requires a loopback AGENTOS_HOST (got {settings.host!r})"
            )
        if not is_loopback_host(urlsplit(settings.public_url).hostname):
            problems.append(
                "AGENTOS_DEV_AUTH=1 requires a loopback AGENTOS_PUBLIC_URL "
                f"(got {settings.public_url!r})"
            )
    elif not settings.jira_url:
        problems.append("AGENTOS_JIRA_URL is required unless AGENTOS_DEV_AUTH=1")
    if settings.jira_url and (problem := _url_problem("AGENTOS_JIRA_URL", settings.jira_url)):
        problems.append(problem)
    problems.extend(_jira_service_problems(settings))

    if settings.launcher == "github":
        # The launcher's own constructor checks the values' shapes (context.build_context).
        for name, value in (
            ("AGENTOS_GITHUB_REPO", settings.github_repo),
            ("AGENTOS_GITHUB_TOKEN", settings.github_token),
        ):
            if not value:
                problems.append(f"{name} is required with AGENTOS_LAUNCHER=github")
        if problem := _url_problem("AGENTOS_GITHUB_API_URL", settings.github_api_url):
            problems.append(problem)

    if settings.local_max_concurrency is not None and settings.local_max_concurrency < 1:
        problems.append("AGENTOS_LOCAL_MAX_CONCURRENCY must be >= 1 (or 'unlimited')")
    if settings.worker_env_file is not None and not settings.worker_env_file.is_file():
        problems.append(f"AGENTOS_WORKER_ENV_FILE={settings.worker_env_file} does not exist")

    # SPEC §12: workers run outside DATA_DIR (agents could reach the DB) and outside any git
    # repo (parent CLAUDE.md / AGENTS.md would leak into agents).
    workspaces, data = settings.workspaces_dir, settings.data_dir
    if workspaces.is_relative_to(data) or data.is_relative_to(workspaces):
        problems.append(
            f"AGENTOS_WORKSPACES_DIR ({workspaces}) and AGENTOS_DATA_DIR ({data}) must not "
            "contain one another"
        )
    if (repo := _git_repo_containing(workspaces)) is not None:
        problems.append(
            f"AGENTOS_WORKSPACES_DIR ({workspaces}) is inside the git repo {repo}; "
            "move it outside any repo (default: ~/.agentos/workspaces)"
        )

    if problems:
        raise ConfigError(problems)

    for directory in (settings.data_dir, settings.logs_dir, settings.workspaces_dir):
        directory.mkdir(parents=True, exist_ok=True)
