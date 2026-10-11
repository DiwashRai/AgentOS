"""AppContext: everything a request or background loop needs, built once at startup.

No FastAPI here: routes get it through `agentos.http_deps.Ctx`; loops and tests get it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentos.config import Settings, load_config, startup_checks
from agentos.db import Database
from agentos.launchers.base import Launcher
from agentos.launchers.local import WORKER_PATH, LocalProcessLauncher
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.space import SpaceConfig
from agentos.ratelimit import RateLimiter
from agentos.sources.jira import JiraClient

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


def build_context(settings: Settings, launcher: Launcher | None = None) -> AppContext:
    """Load agentos.toml, run the startup checks (creating data/logs/workspaces), migrate the db.

    `launcher=None` builds the configured one (`AGENTOS_LAUNCHER`; only `local` until M1).
    Raises config.ConfigError or db.MigrationError with a readable message.
    """
    config = load_config(settings.config)
    startup_checks(settings, config.runtimes)
    db = Database(settings.db_path)
    db.migrate()
    if launcher is None:
        launcher = LocalProcessLauncher.from_settings(settings, WORKER_PATH)
    jira = JiraClient(settings.jira_url, settings.jira_deployment) if settings.jira_url else None
    return AppContext(
        settings=settings,
        runtimes=config.runtimes,
        space=config.space,
        db=db,
        launcher=launcher,
        jira=jira,
    )
