"""Settings: pydantic-settings (AGENTOS_* env) + startup checks (SPEC §14, §17).

The server runs from `backend/`, so `.env` and relative paths resolve from there. What an admin
may change lives in the db and is edited in the app (Settings); env keeps what the server needs
to start, who may sign in, and secrets.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from agentos.models import LauncherName


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
    public_url: str = "http://127.0.0.1:8000"
    worker_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    launcher: LauncherName | None = None  # None: this server starts no workers (clients only)
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
    # The mailbox store (design/mailbox.md, `mailbox_store`): file-report runs are dispatched as
    # with `github`, but the manifest and the worker's results travel through an Artifactory
    # folder (https://<host>/artifactory/<repo>/<folder>, the runner repo variable's string).
    artifactory_url: str | None = None
    artifactory_user: str | None = None
    artifactory_password: SecretStr | None = None

    @field_validator("data_dir", "web_dir")
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

    @property
    def effective_worker_url(self) -> str:
        """What workers dial: WORKER_URL, defaulting to PUBLIC_URL."""
        return self.worker_url or self.public_url

    @property
    def mailbox_store(self) -> bool:
        """AGENTOS_LAUNCHER=mailbox, or another launcher with AGENTOS_ARTIFACTORY_URL."""
        return self.launcher == "mailbox" or bool(self.launcher and self.artifactory_url)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agentos.db"


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
    """The mailbox store: all three Artifactory vars, and an https URL with an
    `/artifactory/<repo>` path. Problems never quote the password."""
    password = settings.artifactory_password
    values = {
        "AGENTOS_ARTIFACTORY_URL": settings.artifactory_url,
        "AGENTOS_ARTIFACTORY_USER": settings.artifactory_user,
        "AGENTOS_ARTIFACTORY_PASSWORD": password.get_secret_value() if password else None,
    }
    problems = [
        f"{name} is required for the mailbox"
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
    """Validate cross-field rules, then create the data dir.

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

    if settings.launcher is not None:
        # The repo/workflow/ref are set in the app (Settings > Runners) and read at each launch.
        if settings.github_token is None or not settings.github_token.get_secret_value().strip():
            problems.append(
                f"AGENTOS_GITHUB_TOKEN is required with AGENTOS_LAUNCHER={settings.launcher}"
            )
        if problem := _url_problem("AGENTOS_GITHUB_API_URL", settings.github_api_url):
            problems.append(problem)
    if settings.mailbox_store:
        problems.extend(_artifactory_problems(settings))

    if problems:
        raise ConfigError(problems)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
