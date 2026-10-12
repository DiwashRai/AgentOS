"""Settings: pydantic-settings (AGENTOS_* env) + the optional agentos.toml seed file + startup
checks (SPEC §14, §17).

The server runs from `backend/`, so `.env` and relative paths resolve from there. What an admin
may change lives in the db and is edited in the app (Settings); env keeps what the server needs
to start, who may sign in, and secrets. `agentos.toml` is optional: if it exists, it is read only
to seed settings areas the db doesn't have yet (the first start, or the first start after an
upgrade that added an area); otherwise built-in defaults seed them.
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
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from agentos.models import LauncherName
from agentos.pipeline.runtime import NamedRuntime, RuntimesConfig, RuntimeSpec
from agentos.pipeline.space import SPACE_TOML_KEYS, RunnerSection, SpaceConfig, SpaceSection

# Env vars whose settings moved into the app, and the Settings tab that replaced each. A moved var
# that is still set doesn't stop startup: it is ignored with a warning, and its value seeds the
# setting on first start only (when the db has no row for it yet).
MOVED_ENV: dict[str, str] = {
    "AGENTOS_KEY_PREFIX": "Settings > Space",
    "AGENTOS_JIRA_PROJECTS": "Settings > Space",
    "AGENTOS_CLAIM_TIMEOUT_S": "Settings > Space",
    "AGENTOS_GITHUB_REPO": "Settings > Runners",
    "AGENTOS_GITHUB_WORKFLOW": "Settings > Runners",
    "AGENTOS_GITHUB_REF": "Settings > Runners",
}
# The settings area each moved var seeds.
MOVED_AREA: dict[str, Literal["space", "runner"]] = {
    "AGENTOS_KEY_PREFIX": "space",
    "AGENTOS_JIRA_PROJECTS": "space",
    "AGENTOS_CLAIM_TIMEOUT_S": "space",
    "AGENTOS_GITHUB_REPO": "runner",
    "AGENTOS_GITHUB_WORKFLOW": "runner",
    "AGENTOS_GITHUB_REF": "runner",
}
# The SpaceSection field each moved Space var seeds.
_MOVED_FIELD = {
    "AGENTOS_KEY_PREFIX": "key_prefix",
    "AGENTOS_JIRA_PROJECTS": "jira_projects",
    "AGENTOS_CLAIM_TIMEOUT_S": "claim_timeout_s",
}
# The RunnerSection field each moved runner var seeds.
_MOVED_RUNNER_FIELD = {
    "AGENTOS_GITHUB_REPO": "github_repo",
    "AGENTOS_GITHUB_WORKFLOW": "workflow",
    "AGENTOS_GITHUB_REF": "ref",
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
    # Optional, seed-only: read once to seed settings areas the db doesn't have yet (if it exists).
    config: Path = Path("agentos.toml")
    launcher: LauncherName = "local"
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
    # per run; which repo/workflow/ref is set in the app (Settings > Runners). The token needs
    # Actions: write on that repo.
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"  # GHES: https://<host>/api/v3
    github_api_version: str = "2022-11-28"
    # AGENTOS_LAUNCHER=mailbox (design/mailbox.md): runs are dispatched as with `github`, but the
    # manifest and the worker's results travel through an Artifactory folder
    # (https://<host>/artifactory/<repo>/<folder>, the same string as the runner repo's variable).
    artifactory_url: str | None = None
    artifactory_user: str | None = None
    artifactory_password: SecretStr | None = None

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

    @field_validator("public_url", "worker_url", "jira_url", "github_api_url", "artifactory_url")
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
    def config_set(self) -> bool:
        """AGENTOS_CONFIG was set (env, .env or code) rather than defaulted."""
        return "config" in self.model_fields_set

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
    """`agentos.toml`, a seed only (§17): the agent runtimes (`default_runtime` + `[runtimes.*]`),
    `[space]` name/default_repo, `[[repos]]`, `[[intake.queries]]`. Each seeds its settings area
    when the db has no row for it yet; after that the file is ignored. `runtimes` is None when the
    file has none (the built-ins seed them)."""

    runtimes: RuntimesConfig | None
    space: SpaceConfig


_M = TypeVar("_M", bound=BaseModel)
# Top-level keys that belong to SpaceConfig; the rest: runtimes.
_SPACE_KEYS = ("space", "repos", "intake")


class _TomlRuntimes(BaseModel):
    """agentos.toml's runtime part. `default_runtime` omitted = the first `[runtimes.*]` table."""

    model_config = ConfigDict(extra="forbid")

    default_runtime: str | None = None
    runtimes: dict[str, RuntimeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_exists(self) -> _TomlRuntimes:
        if self.default_runtime is not None and self.default_runtime not in self.runtimes:
            raise ValueError(f"default_runtime {self.default_runtime!r} is not in [runtimes]")
        return self

    def entries(self) -> list[dict[str, Any]]:
        """The Settings list: `default_runtime` first, then the file order."""
        names = list(self.runtimes)
        if self.default_runtime is not None:
            names.remove(self.default_runtime)
            names.insert(0, self.default_runtime)
        return [{"name": n, **self.runtimes[n].model_dump()} for n in names]


def load_config(path: Path) -> FileConfig:
    """Parse an `agentos.toml` (the seed file). Every problem is reported in one ConfigError.
    The caller only calls this when the file exists and an area still needs seeding."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError([f"{path}: not found"]) from None
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
    runtimes: RuntimesConfig | None = None
    if data:
        parsed = _validated(_TomlRuntimes, data, path, problems)
        if parsed is not None:
            entries: list[NamedRuntime] = []
            for entry in parsed.entries():
                named = _validated(NamedRuntime, entry, path, problems, f"runtimes.{entry['name']}")
                if named is not None:
                    entries.append(named)
            if len(entries) == len(parsed.runtimes):
                runtimes = RuntimesConfig.from_entries(entries)
    space = _validated(SpaceConfig, space_data, path, problems)
    if space is None or problems:
        raise ConfigError(problems)
    return FileConfig(runtimes=runtimes, space=space)


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


def seed_runner(moved_env: Mapping[str, str]) -> RunnerSection:
    """The first-start seed of Settings > Runners: the AGENTOS_GITHUB_REPO / _WORKFLOW / _REF
    values that are set, else the defaults (no repo, agentos-worker.yml, main). An invalid value
    → ConfigError naming the variable."""
    values = {f: moved_env[n] for n, f in _MOVED_RUNNER_FIELD.items() if n in moved_env}
    try:
        return RunnerSection.model_validate(values)
    except ValidationError as exc:
        problems: list[str] = []
        for err in exc.errors():
            loc = str(err["loc"][0]) if err["loc"] else ""
            var = next((n for n, f in _MOVED_RUNNER_FIELD.items() if f == loc), "AGENTOS_GITHUB_*")
            problems.append(f"{var}: {err['msg'].removeprefix('Value error, ')}")
        raise ConfigError(problems) from None


def _validated(
    model: type[_M], data: dict[str, Any], path: Path, problems: list[str], prefix: str = ""
) -> _M | None:
    """`model` validated from `data`, or None with its errors appended to `problems`."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            where = ".".join(p for p in (prefix, *(str(x) for x in err["loc"])) if p) or "(root)"
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


def _artifactory_problems(settings: Settings) -> list[str]:
    """AGENTOS_LAUNCHER=mailbox: all three Artifactory vars, and an https URL with an
    `/artifactory/<repo>` path. Problems never quote the password."""
    password = settings.artifactory_password
    values = {
        "AGENTOS_ARTIFACTORY_URL": settings.artifactory_url,
        "AGENTOS_ARTIFACTORY_USER": settings.artifactory_user,
        "AGENTOS_ARTIFACTORY_PASSWORD": password.get_secret_value() if password else None,
    }
    problems = [
        f"{name} is required with AGENTOS_LAUNCHER=mailbox"
        for name, value in values.items()
        if value is None or not value.strip()
    ]
    url = settings.artifactory_url
    if url:
        parts = urlsplit(url)
        segments = [s for s in parts.path.split("/") if s]
        repo_ok = "artifactory" in segments[:-1]  # .../artifactory/<repo>[/<folder>]
        if parts.scheme != "https" or not parts.hostname or not repo_ok:
            problems.append(
                f"AGENTOS_ARTIFACTORY_URL={url!r} must be an https URL like "
                "https://<host>/artifactory/<repo>/<folder>"
            )
    return problems


def startup_checks(settings: Settings) -> None:
    """Validate cross-field rules, then create the data, logs and workspaces dirs.

    Raises ConfigError listing every problem.
    """
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

    if settings.launcher in ("github", "mailbox"):
        # The repo/workflow/ref are set in the app (Settings > Runners) and read at each launch.
        if settings.github_token is None or not settings.github_token.get_secret_value().strip():
            problems.append(
                f"AGENTOS_GITHUB_TOKEN is required with AGENTOS_LAUNCHER={settings.launcher}"
            )
        if problem := _url_problem("AGENTOS_GITHUB_API_URL", settings.github_api_url):
            problems.append(problem)
    if settings.launcher == "mailbox":
        problems.extend(_artifactory_problems(settings))

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
