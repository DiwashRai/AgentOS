"""AppContext: everything a request or background loop needs, built once at startup.

No FastAPI here: routes get it through `agentos.http_deps.Ctx`; loops and tests get it directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentos.config import ConfigError, Settings, startup_checks
from agentos.db import Database
from agentos.launchers.base import Launcher
from agentos.launchers.github import GitHubActionsLauncher, GitHubTarget
from agentos.launchers.local import WORKER_PATH, LocalProcessLauncher
from agentos.launchers.mailbox import MailboxLauncher
from agentos.models import FieldMapping, JiraField
from agentos.pipeline.runtime import BUILTIN_RUNTIMES, RuntimesConfig
from agentos.pipeline.space import RunnerSection, SpaceConfig
from agentos.ratelimit import RateLimiter
from agentos.sources.jira import JiraClient, JiraSearch, JiraSource
from agentos.space_store import (
    Area,
    SpaceStore,
    add_builtin_effort_args,
    mappings_value,
    missing_areas,
    runner_value,
    runtimes_value,
    seed,
    space_values,
)
from agentos.stores.artifactory import ArtifactoryStore
from agentos.ttlcache import TtlCache

log = logging.getLogger(__name__)

LOGIN_ATTEMPTS_PER_MINUTE = 10  # SPEC §11
INTAKE_CACHE_TTL_S = 45.0  # request 14: how long an intake query's Jira answer is reused
JIRA_FIELDS_CACHE_TTL_S = 600.0  # request 22B: how long Jira's field list is reused

# An intake query's last Jira answer (or its error message), by query id.
IntakeCache = TtlCache[str, JiraSearch | str]
# Jira's field list (the mapping field picker), under one key; only successes are cached.
JiraFieldsCache = TtlCache[str, list[JiraField]]


def _login_limiter() -> RateLimiter:
    return RateLimiter(LOGIN_ATTEMPTS_PER_MINUTE, 60.0)


def _intake_cache() -> IntakeCache:
    return TtlCache(INTAKE_CACHE_TTL_S)


def _jira_fields_cache() -> JiraFieldsCache:
    return TtlCache(JIRA_FIELDS_CACHE_TTL_S)


@dataclass
class AppContext:
    settings: Settings
    db: Database
    launcher: Launcher
    # The Space's settings, repo catalog, intake queries, runtimes and runner (db `settings`,
    # edited in the app).
    space_store: SpaceStore
    # The Jira REST client when AGENTOS_JIRA_URL is set (tests swap in a MockTransport one).
    jira: JiraClient | None = None
    # Jira login attempts per (client IP, submitted email/username).
    login_limiter: RateLimiter = field(default_factory=_login_limiter)
    intake_cache: IntakeCache = field(default_factory=_intake_cache)
    jira_fields_cache: JiraFieldsCache = field(default_factory=_jira_fields_cache)

    @property
    def space(self) -> SpaceConfig:
        """The Space as currently saved (cached; a settings save clears the cache)."""
        return self.space_store.current()

    @property
    def runtimes(self) -> RuntimesConfig:
        """The agent runtimes as currently saved (Settings > Runners); the first is the default."""
        return self.space_store.runtimes()

    @property
    def runner(self) -> RunnerSection:
        """Where the github launcher dispatches runs, as currently saved (Settings > Runners)."""
        return self.space_store.runner()

    @property
    def mappings(self) -> tuple[FieldMapping, ...]:
        """The Jira field mappings as currently saved (Settings > Field mappings)."""
        return self.space_store.mappings()

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
    """Run the startup checks (creating data/logs/workspaces), migrate the db, seed the settings
    areas the db doesn't have yet (`seed_settings`).

    `launcher=None` builds the configured one (`AGENTOS_LAUNCHER`).
    Raises config.ConfigError or db.MigrationError with a readable message.
    """
    startup_checks(settings)
    db = Database(settings.db_path)
    db.migrate()
    seed_settings(db)
    for name in add_builtin_effort_args(db):
        log.info("Settings: runtime %r gained the built-in effort_args (request 23A)", name)
    store = SpaceStore(db)
    if launcher is None:
        launcher = _launcher(settings, store)
    jira = JiraClient(settings.jira_url, settings.jira_deployment) if settings.jira_url else None
    return AppContext(settings=settings, db=db, launcher=launcher, space_store=store, jira=jira)


def seed_settings(db: Database) -> list[Area]:
    """Insert the built-in defaults for each settings area that has no db row yet (version 1, by
    nobody; one transaction; idempotent). Returns the seeded areas.

    The defaults: the default Space with no repos or intake queries, the built-in runtimes
    (claude + copilot), the default runner (no repo) and no field mappings."""
    defaults: dict[Area, dict[str, Any]] = {
        **space_values(SpaceConfig()),
        "runtimes": runtimes_value(list(BUILTIN_RUNTIMES)),
        "runner": runner_value(RunnerSection()),
        "mappings": mappings_value([]),
    }
    missing = missing_areas(db)
    seeded = seed(db, {area: defaults[area] for area in missing}) if missing else []

    for area in seeded:
        log.info("Settings: seeded %s with the defaults (first start)", area)
    return seeded


def runner_target(store: SpaceStore) -> Callable[[], GitHubTarget]:
    """What the github launcher asks at every launch: Settings > Runners as currently saved."""

    def target() -> GitHubTarget:
        runner = store.runner()
        return GitHubTarget(repo=runner.github_repo, workflow=runner.workflow, ref=runner.ref)

    return target


def _launcher(settings: Settings, store: SpaceStore) -> Launcher:
    if settings.launcher == "local":
        return LocalProcessLauncher.from_settings(settings, WORKER_PATH)
    github = _github_launcher(settings, store)
    if settings.launcher == "github":
        return github
    url, user, password = (
        settings.artifactory_url,
        settings.artifactory_user,
        settings.artifactory_password,
    )
    if url is None or user is None or password is None:  # pragma: no cover - startup_checks
        raise ConfigError(["AGENTOS_LAUNCHER=mailbox needs the AGENTOS_ARTIFACTORY_* vars"])
    artifactory = ArtifactoryStore(base_url=url, user=user, password=password.get_secret_value())
    return MailboxLauncher(github=github, store=artifactory, worker_path=WORKER_PATH)


def _github_launcher(settings: Settings, store: SpaceStore) -> GitHubActionsLauncher:
    if settings.github_token is None:  # pragma: no cover - startup_checks requires it
        raise ConfigError([f"AGENTOS_LAUNCHER={settings.launcher} needs AGENTOS_GITHUB_TOKEN"])
    try:
        return GitHubActionsLauncher(
            target=runner_target(store),
            token=settings.github_token.get_secret_value(),
            api_url=settings.github_api_url,
            api_version=settings.github_api_version,
        )
    except ValueError as exc:
        raise ConfigError([f"AGENTOS_GITHUB_*: {exc}"]) from None
