"""Settings: pydantic-settings (AGENTOS_* env) + agentos.toml loader + startup checks (SPEC §14).

The server runs from `backend/`, so `.env`, `agentos.toml` and relative paths resolve from there.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from agentos.errors import ApiError
from agentos.pipeline.runtime import RuntimesConfig
from agentos.validate import validate_base_branch, validate_repo_url

KEY_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]*$")


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
        env_ignore_empty=True,  # `AGENTOS_DEFAULT_REPO_URL=` means "unset", not ""
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
    key_prefix: str = "AOS"
    default_repo_url: str | None = None
    default_base_branch: str = "main"
    launcher: Literal["local", "github"] = "local"
    local_max_concurrency: int | None = 2  # None ("none"/"unlimited" in env) = no limit
    worker_env_file: Path | None = None
    claim_timeout_s: int = 900
    dev_auth: bool = False
    trust_proxy: bool = False
    jira_url: str | None = None
    jira_deployment: Literal["cloud", "datacenter"] = "cloud"
    jira_board_ids: Annotated[list[int], NoDecode] = []
    admins: Annotated[list[str], NoDecode] = []

    @field_validator("data_dir", "workspaces_dir", "config", "worker_env_file")
    @classmethod
    def _absolute(cls, value: Path | None) -> Path | None:
        return None if value is None else value.expanduser().resolve()

    @field_validator("public_url", "worker_url", "jira_url")
    @classmethod
    def _strip_slash(cls, value: str | None) -> str | None:
        return None if value is None else value.rstrip("/")

    @field_validator("jira_board_ids", "admins", mode="before")
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


def load_runtimes(path: Path) -> RuntimesConfig:
    """Parse `agentos.toml` (SPEC §7) into RuntimesConfig."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            [f"{path}: runtime config not found (copy agentos.toml.example, or set AGENTOS_CONFIG)"]
        ) from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError([f"{path}: cannot read TOML: {exc}"]) from None
    try:
        return RuntimesConfig.model_validate(data)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]) or "(root)"
            problems.append(f"{path}: {where}: {err['msg']}")
        raise ConfigError(problems) from None


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
    else:
        if not settings.jira_url:
            problems.append("AGENTOS_JIRA_URL is required unless AGENTOS_DEV_AUTH=1")
        elif problem := _url_problem("AGENTOS_JIRA_URL", settings.jira_url):
            problems.append(problem)
        if not settings.jira_board_ids:
            problems.append("AGENTOS_JIRA_BOARD_IDS is required unless AGENTOS_DEV_AUTH=1")

    if settings.launcher == "github":
        problems.append("AGENTOS_LAUNCHER=github is not available until M1; use 'local'")

    if not KEY_PREFIX_RE.match(settings.key_prefix):
        problems.append(
            f"AGENTOS_KEY_PREFIX={settings.key_prefix!r} must match {KEY_PREFIX_RE.pattern}"
        )

    for name, value, check in (
        ("AGENTOS_DEFAULT_REPO_URL", settings.default_repo_url, validate_repo_url),
        ("AGENTOS_DEFAULT_BASE_BRANCH", settings.default_base_branch, validate_base_branch),
    ):
        if value is not None:
            try:
                check(value)
            except ApiError as exc:
                problems.append(f"{name}={value!r}: {exc.message}")

    if settings.local_max_concurrency is not None and settings.local_max_concurrency < 1:
        problems.append("AGENTOS_LOCAL_MAX_CONCURRENCY must be >= 1 (or 'unlimited')")
    if settings.claim_timeout_s < 1:
        problems.append("AGENTOS_CLAIM_TIMEOUT_S must be >= 1")
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
