"""AppContext: everything a request or background loop needs, built once at startup.

No FastAPI here: routes get it through `agentos.http_deps.Ctx`; loops and tests get it directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentos.config import Settings, load_runtimes, startup_checks
from agentos.db import Database
from agentos.launchers.base import Launcher
from agentos.launchers.local import WORKER_PATH, LocalProcessLauncher
from agentos.pipeline.runtime import RuntimesConfig


@dataclass
class AppContext:
    settings: Settings
    runtimes: RuntimesConfig
    db: Database
    launcher: Launcher
    # Later: broker.


def build_context(settings: Settings, launcher: Launcher | None = None) -> AppContext:
    """Load agentos.toml, run the startup checks (creating data/logs/workspaces), migrate the db.

    `launcher=None` builds the configured one (`AGENTOS_LAUNCHER`; only `local` until M1).
    Raises config.ConfigError or db.MigrationError with a readable message.
    """
    runtimes = load_runtimes(settings.config)
    startup_checks(settings, runtimes)
    db = Database(settings.db_path)
    db.migrate()
    if launcher is None:
        launcher = LocalProcessLauncher.from_settings(settings, WORKER_PATH)
    return AppContext(settings=settings, runtimes=runtimes, db=db, launcher=launcher)
