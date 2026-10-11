"""AppContext: everything a request or background loop needs, built once at startup.

No FastAPI here: routes get it through `agentos.http_deps.Ctx`; loops and tests get it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentos.config import ConfigError, Settings, load_config, startup_checks
from agentos.db import Database
from agentos.launchers.base import Launcher
from agentos.launchers.github import GitHubActionsLauncher
from agentos.launchers.local import WORKER_PATH, LocalProcessLauncher
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.space import SpaceConfig
from agentos.ratelimit import RateLimiter
from agentos.sources.jira import JiraClient, JiraSource

LOGIN_ATTEMPTS_PER_MINUTE = 10  # SPEC §11


def _login_limiter() -> RateLimiter:
    return RateLimiter(LOGIN_ATTEMPTS_PER_MINUTE, 60.0)


@dataclass
class AppContext:
    settings: Settings
    runtimes: RuntimesConfig
    space: SpaceConfig  # the Space's name and repo catalog (agentos.toml)
    db: Database
    launcher: Launcher
    # The Jira REST client when AGENTOS_JIRA_URL is set (tests swap in a MockTransport one).
    jira: JiraClient | None = None
    # Jira login attempts per (client IP, submitted email/username).
    login_limiter: RateLimiter = field(default_factory=_login_limiter)
    # Later: broker.

    @property
    def jira_source(self) -> JiraSource | None:
        """Jira import: the Jira client with the service account (AGENTOS_JIRA_SERVICE_*), or None
        when either is missing (startup checks keep the pair consistent). Built per use from
        `jira` and `settings`, so a test that swaps either one is honoured."""
        token = self.settings.jira_service_token
        if self.jira is None or token is None:
            return None
        creds = self.jira.credentials(
            token=token.get_secret_value(), email=self.settings.jira_service_email
        )
        return JiraSource(self.jira, creds)


def build_context(settings: Settings, launcher: Launcher | None = None) -> AppContext:
    """Load agentos.toml, run the startup checks (creating data/logs/workspaces), migrate the db.

    `launcher=None` builds the configured one (`AGENTOS_LAUNCHER`).
    Raises config.ConfigError or db.MigrationError with a readable message.
    """
    config = load_config(settings.config)
    startup_checks(settings, config.runtimes)
    if launcher is None:
        launcher = _launcher(settings)
    db = Database(settings.db_path)
    db.migrate()
    jira = JiraClient(settings.jira_url, settings.jira_deployment) if settings.jira_url else None
    return AppContext(
        settings=settings,
        runtimes=config.runtimes,
        space=config.space,
        db=db,
        launcher=launcher,
        jira=jira,
    )


def _launcher(settings: Settings) -> Launcher:
    if settings.launcher == "local":
        return LocalProcessLauncher.from_settings(settings, WORKER_PATH)
    if settings.github_repo is None or settings.github_token is None:  # pragma: no cover
        raise ConfigError(["AGENTOS_LAUNCHER=github needs AGENTOS_GITHUB_REPO and _TOKEN"])
    try:
        return GitHubActionsLauncher(
            repo=settings.github_repo,
            workflow=settings.github_workflow,
            ref=settings.github_ref,
            token=settings.github_token.get_secret_value(),
            api_url=settings.github_api_url,
            api_version=settings.github_api_version,
        )
    except ValueError as exc:
        raise ConfigError([f"AGENTOS_GITHUB_*: {exc}"]) from None
