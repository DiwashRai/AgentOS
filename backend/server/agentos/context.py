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
from agentos.launchers.base import Provider
from agentos.launchers.github import GitHubActionsProvider, GitHubTarget
from agentos.launchers.mailbox import Mailbox, MailboxLauncher
from agentos.models import FieldMapping, JiraField, LauncherName, Route
from agentos.pipeline.runtime import BUILTIN_RUNTIMES, RuntimesConfig
from agentos.pipeline.space import RunnerSection, SpaceConfig
from agentos.ratelimit import RateLimiter
from agentos.sources.jira import JiraClient, JiraSearch, JiraSource
from agentos.space_store import (
    Area,
    Snapshot,
    load_snapshot,
    load_space,
    mappings_of,
    mappings_value,
    missing_areas,
    read_area,
    runner_of,
    runner_value,
    runtimes_of,
    runtimes_value,
    seed,
    space_values,
)
from agentos.stores.artifactory import ArtifactoryStore
from agentos.ttlcache import TtlCache
from agentos.worker_archive import worker_file

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
    # What starts push jobs, by the provider name a run's route stores.
    providers: dict[str, Provider]
    # The route a `target: "server"` dispatch stores; None = this server starts no workers.
    route: Route | None
    # The mailbox, when a store is configured: its loops dispatch and watch the file-report runs.
    mailbox: Mailbox | None = None
    # The Jira REST client when AGENTOS_JIRA_URL is set (tests swap in a MockTransport one).
    jira: JiraClient | None = None
    # Jira login attempts per (client IP, submitted email/username).
    login_limiter: RateLimiter = field(default_factory=_login_limiter)
    intake_cache: IntakeCache = field(default_factory=_intake_cache)
    jira_fields_cache: JiraFieldsCache = field(default_factory=_jira_fields_cache)

    # The settings areas (db `settings`, edited in the app) are read from the db on each access,
    # so a save applies at once. A caller that needs several areas takes one `snapshot()`.

    @property
    def space(self) -> SpaceConfig:
        """The Space's settings, repo catalog and intake queries as currently saved."""
        with self.db.read() as conn:
            return load_space(conn)

    @property
    def runtimes(self) -> RuntimesConfig:
        """The agent runtimes as currently saved (Settings > Runners); the first is the default."""
        with self.db.read() as conn:
            return runtimes_of(read_area(conn, "runtimes"))

    @property
    def runner(self) -> RunnerSection:
        """Where the github provider dispatches runs, as currently saved (Settings > Runners)."""
        return read_runner(self.db)

    @property
    def mappings(self) -> tuple[FieldMapping, ...]:
        """The Jira field mappings as currently saved (Settings > Field mappings)."""
        with self.db.read() as conn:
            return mappings_of(read_area(conn, "mappings"))

    def snapshot(self) -> Snapshot:
        """Every settings area as currently saved, read in one transaction."""
        with self.db.read() as conn:
            return load_snapshot(conn)

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


def build_context(settings: Settings, providers: dict[str, Provider] | None = None) -> AppContext:
    """Run the startup checks (creating the data dir), build the served worker.py once
    (`worker_file`), migrate the db, seed the settings areas the db doesn't have yet
    (`seed_settings`).

    `providers=None` builds the configured ones (`AGENTOS_LAUNCHER`, with the mailbox); the
    default route always comes from `AGENTOS_LAUNCHER`.
    Raises config.ConfigError or db.MigrationError with a readable message.
    """
    startup_checks(settings)
    worker_file()  # build worker.py now: a missing worker source fails startup, not a request
    db = Database(settings.db_path)
    db.migrate()
    seed_settings(db)
    mailbox = None
    if providers is None:
        providers, mailbox = _providers(settings, db)
    jira = JiraClient(settings.jira_url, settings.jira_deployment) if settings.jira_url else None
    route = server_route(settings.launcher)
    return AppContext(settings, db, providers, route, mailbox, jira)


def server_route(launcher: LauncherName | None) -> Route | None:
    """The server's route: `github` reports live, `mailbox` through a file; None without one."""
    if launcher is None:
        return None
    return Route("push", "github", "file" if launcher == "mailbox" else "live")


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


def read_runner(db: Database) -> RunnerSection:
    """Settings > Runners as currently saved."""
    with db.read() as conn:
        return runner_of(read_area(conn, "runner"))


def runner_target(db: Database) -> Callable[[], GitHubTarget]:
    """What the github provider asks at every start: Settings > Runners as currently saved."""

    def target() -> GitHubTarget:
        runner = read_runner(db)
        return GitHubTarget(repo=runner.github_repo, workflow=runner.workflow, ref=runner.ref)

    return target


def _providers(settings: Settings, db: Database) -> tuple[dict[str, Provider], Mailbox | None]:
    if settings.launcher is None:
        return {}, None
    github = _github_provider(settings, db)
    if not settings.mailbox_store:
        return {"github": github}, None
    url, user, password = (
        settings.artifactory_url,
        settings.artifactory_user,
        settings.artifactory_password,
    )
    if url is None or user is None or password is None:  # pragma: no cover - startup_checks
        raise ConfigError(["the mailbox needs the AGENTOS_ARTIFACTORY_* vars"])
    artifactory = ArtifactoryStore(base_url=url, user=user, password=password.get_secret_value())
    mailbox = MailboxLauncher(github=github, store=artifactory, worker=worker_file().data)
    return {"github": github}, mailbox


def _github_provider(settings: Settings, db: Database) -> GitHubActionsProvider:
    if settings.github_token is None:  # pragma: no cover - startup_checks requires it
        raise ConfigError([f"AGENTOS_LAUNCHER={settings.launcher} needs AGENTOS_GITHUB_TOKEN"])
    try:
        return GitHubActionsProvider(
            target=runner_target(db),
            token=settings.github_token.get_secret_value(),
            api_url=settings.github_api_url,
            api_version=settings.github_api_version,
        )
    except ValueError as exc:
        raise ConfigError([f"AGENTOS_GITHUB_*: {exc}"]) from None
