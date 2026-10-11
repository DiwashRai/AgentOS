"""AppContext: everything a request or background loop needs, built once at startup.

No FastAPI here: routes get it through `agentos.http_deps.Ctx`; loops and tests get it directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agentos.config import (
    MOVED_ENV,
    ConfigError,
    FileConfig,
    Settings,
    load_config,
    seed_space,
    startup_checks,
)
from agentos.db import Database
from agentos.launchers.base import Launcher
from agentos.launchers.github import GitHubActionsLauncher
from agentos.launchers.local import WORKER_PATH, LocalProcessLauncher
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.space import SpaceConfig
from agentos.ratelimit import RateLimiter
from agentos.sources.jira import JiraClient, JiraSearch, JiraSource
from agentos.space_store import Area, SpaceStore, missing_areas, seed
from agentos.ttlcache import TtlCache

log = logging.getLogger(__name__)

LOGIN_ATTEMPTS_PER_MINUTE = 10  # SPEC §11
INTAKE_CACHE_TTL_S = 45.0  # request 14: how long an intake query's Jira answer is reused

# An intake query's last Jira answer (or its error message), by query id.
IntakeCache = TtlCache[str, JiraSearch | str]

# agentos.toml's Space tables: how the startup log names each and the tab that replaced it.
_TOML_TABLES: dict[Area, tuple[str, str]] = {
    "space": ("[space]", "Settings > Space"),
    "repos": ("[[repos]]", "Settings > Space (Repos)"),
    "intake": ("[[intake.queries]]", "Settings > Intake"),
}


def _login_limiter() -> RateLimiter:
    return RateLimiter(LOGIN_ATTEMPTS_PER_MINUTE, 60.0)


def _intake_cache() -> IntakeCache:
    return TtlCache(INTAKE_CACHE_TTL_S)


@dataclass
class AppContext:
    settings: Settings
    runtimes: RuntimesConfig
    db: Database
    launcher: Launcher
    # The Space's settings, repo catalog and intake queries (db `settings`, edited in the app).
    space_store: SpaceStore
    # The Jira REST client when AGENTOS_JIRA_URL is set (tests swap in a MockTransport one).
    jira: JiraClient | None = None
    # Jira login attempts per (client IP, submitted email/username).
    login_limiter: RateLimiter = field(default_factory=_login_limiter)
    intake_cache: IntakeCache = field(default_factory=_intake_cache)
    # Later: broker.

    @property
    def space(self) -> SpaceConfig:
        """The Space as currently saved (cached; a settings save clears the cache)."""
        return self.space_store.current()

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
    """Load agentos.toml, run the startup checks (creating data/logs/workspaces), migrate the db,
    seed the Space's settings on first start.

    `launcher=None` builds the configured one (`AGENTOS_LAUNCHER`).
    Raises config.ConfigError or db.MigrationError with a readable message.
    """
    config = load_config(settings.config)
    startup_checks(settings, config.runtimes)
    if launcher is None:
        launcher = _launcher(settings)
    db = Database(settings.db_path)
    db.migrate()
    seed_settings(db, settings, config)
    jira = JiraClient(settings.jira_url, settings.jira_deployment) if settings.jira_url else None
    return AppContext(
        settings=settings,
        runtimes=config.runtimes,
        db=db,
        launcher=launcher,
        space_store=SpaceStore(db),
        jira=jira,
    )


def seed_settings(db: Database, settings: Settings, config: FileConfig) -> list[Area]:
    """Seed each settings area that has no db row yet from agentos.toml and the moved env vars
    (one transaction; idempotent), then warn about every moved var and agentos.toml table that
    is now ignored. Returns the seeded areas. An invalid moved var → ConfigError, but only when
    it is about to seed (once seeded, it is just ignored)."""
    moved = settings.moved_env
    missing = missing_areas(db)
    seeded: list[Area] = []
    if missing:
        space = seed_space(config.space, moved) if "space" in missing else config.space
        seeded = seed(db, space, missing)
    for area in seeded:
        log.info("Settings: seeded %s from agentos.toml and env (first start)", area)
    for name, tab in MOVED_ENV.items():
        if name not in moved:
            continue
        if "space" in seeded:
            log.warning(
                "%s seeded %s on this first start and is ignored from now on: edit it in %s "
                "and remove the variable",
                name,
                tab,
                tab,
            )
        else:
            log.warning("%s is ignored: edit it in %s (and remove the variable)", name, tab)
    for area, (table, tab) in _TOML_TABLES.items():
        if area in config.space_tables and area not in seeded:
            log.warning("agentos.toml %s is ignored: edit it in %s (and remove it)", table, tab)
    return seeded


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
