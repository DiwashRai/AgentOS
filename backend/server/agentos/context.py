"""AppContext: everything a request or background loop needs, built once at startup.

No FastAPI here: routes get it through `agentos.http_deps.Ctx`; loops and tests get it directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentos.config import (
    MOVED_AREA,
    MOVED_ENV,
    ConfigError,
    FileConfig,
    Settings,
    load_config,
    seed_runner,
    seed_space,
    startup_checks,
)
from agentos.db import Database
from agentos.launchers.base import Launcher
from agentos.launchers.github import GitHubActionsLauncher, GitHubTarget
from agentos.launchers.local import WORKER_PATH, LocalProcessLauncher
from agentos.pipeline.runtime import BUILTIN_RUNTIMES, RuntimesConfig
from agentos.pipeline.space import RunnerSection, SpaceConfig
from agentos.ratelimit import RateLimiter
from agentos.sources.jira import JiraClient, JiraSearch, JiraSource
from agentos.space_store import (
    Area,
    SpaceStore,
    missing_areas,
    runner_value,
    runtimes_value,
    seed,
    space_values,
)
from agentos.ttlcache import TtlCache

log = logging.getLogger(__name__)

LOGIN_ATTEMPTS_PER_MINUTE = 10  # SPEC §11
INTAKE_CACHE_TTL_S = 45.0  # request 14: how long an intake query's Jira answer is reused

# An intake query's last Jira answer (or its error message), by query id.
IntakeCache = TtlCache[str, JiraSearch | str]

# The settings areas agentos.toml can seed.
_SPACE_AREAS: tuple[Area, ...] = ("space", "repos", "intake")
_FILE_AREAS: frozenset[Area] = frozenset({*_SPACE_AREAS, "runtimes"})


def _login_limiter() -> RateLimiter:
    return RateLimiter(LOGIN_ATTEMPTS_PER_MINUTE, 60.0)


def _intake_cache() -> IntakeCache:
    return TtlCache(INTAKE_CACHE_TTL_S)


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
    # Later: broker.

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
    seed_settings(db, settings)
    store = SpaceStore(db)
    if launcher is None:
        launcher = _launcher(settings, store)
    jira = JiraClient(settings.jira_url, settings.jira_deployment) if settings.jira_url else None
    return AppContext(settings=settings, db=db, launcher=launcher, space_store=store, jira=jira)


def seed_settings(db: Database, settings: Settings) -> list[Area]:
    """Seed each settings area that has no db row yet (one transaction; idempotent), then warn
    about every moved var and a seed file that is now ignored. Returns the seeded areas.

    Sources: `space`/`repos`/`intake` from agentos.toml (AGENTOS_CONFIG, if the file exists) plus
    the moved Space vars, else the defaults; `runtimes` from the file's `[runtimes.*]` (its
    `default_runtime` first), else the built-in claude + copilot; `runner` from the moved
    AGENTOS_GITHUB_* vars, else no repo. The file is only read when an area it can seed is
    missing. An invalid file or moved var → ConfigError, but only when it is about to seed."""
    moved = settings.moved_env
    missing = missing_areas(db)
    path = settings.config
    file: FileConfig | None = None
    if path.is_file() and _FILE_AREAS.intersection(missing):
        file = load_config(path)
    values: dict[Area, dict[str, Any]] = {}
    if any(area in missing for area in _SPACE_AREAS):
        space = file.space if file is not None else SpaceConfig()
        if "space" in missing:
            space = seed_space(space, moved)
        values |= {a: v for a, v in space_values(space).items() if a in missing}
    file_runtimes = file.runtimes if file is not None else None
    runtimes_from_file = file_runtimes is not None
    if "runtimes" in missing:
        runtimes = file_runtimes or RuntimesConfig.from_entries(BUILTIN_RUNTIMES)
        values["runtimes"] = runtimes_value(runtimes.entries())
    if "runner" in missing:
        values["runner"] = runner_value(seed_runner(moved))
    seeded = seed(db, values) if values else []

    from_file = [
        a
        for a in seeded
        if file is not None and a in _FILE_AREAS and (a != "runtimes" or runtimes_from_file)
    ]
    for area in seeded:
        source = f"{path.name} and env" if area in from_file else "env and defaults"
        log.info("Settings: seeded %s from %s (first start)", area, source)
    for name, tab in MOVED_ENV.items():
        if name not in moved:
            continue
        if MOVED_AREA[name] in seeded:
            log.warning(
                "%s seeded %s on this first start and is ignored from now on: edit it in %s "
                "and remove the variable",
                name,
                tab,
                tab,
            )
        else:
            log.warning("%s is ignored: edit it in %s (and remove the variable)", name, tab)
    _warn_about_the_seed_file(settings, from_file)
    return seeded


def _warn_about_the_seed_file(settings: Settings, from_file: list[Area]) -> None:
    path = settings.config
    var = " (and AGENTOS_CONFIG)" if settings.config_set else ""
    if from_file:
        log.warning(
            "%s seeded %s on this first start and is ignored from now on: settings are edited "
            "in the app (Settings). You can delete the file%s",
            path,
            ", ".join(from_file),
            var,
        )
    elif path.is_file():
        log.warning(
            "%s is ignored: settings live in the db and are edited in the app (Settings); it is "
            "only read once, to seed them on the first start. Delete the file%s",
            path,
            var,
        )
    elif settings.config_set:
        log.warning(
            "AGENTOS_CONFIG is no longer needed: agentos.toml is only read once, to seed settings "
            "on the first start (remove the variable)"
        )


def runner_target(store: SpaceStore) -> Callable[[], GitHubTarget]:
    """What the github launcher asks at every launch: Settings > Runners as currently saved."""

    def target() -> GitHubTarget:
        runner = store.runner()
        return GitHubTarget(repo=runner.github_repo, workflow=runner.workflow, ref=runner.ref)

    return target


def _launcher(settings: Settings, store: SpaceStore) -> Launcher:
    if settings.launcher == "local":
        return LocalProcessLauncher.from_settings(settings, WORKER_PATH)
    if settings.github_token is None:  # pragma: no cover - startup_checks requires it
        raise ConfigError(["AGENTOS_LAUNCHER=github needs AGENTOS_GITHUB_TOKEN"])
    try:
        return GitHubActionsLauncher(
            target=runner_target(store),
            token=settings.github_token.get_secret_value(),
            api_url=settings.github_api_url,
            api_version=settings.github_api_version,
        )
    except ValueError as exc:
        raise ConfigError([f"AGENTOS_GITHUB_*: {exc}"]) from None
