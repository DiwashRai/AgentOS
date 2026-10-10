"""AppContext: everything a request or background loop needs, built once at startup.

No FastAPI here: routes get it through `agentos.http_deps.Ctx`; loops and tests get it directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentos.config import Settings, load_runtimes, startup_checks
from agentos.db import Database
from agentos.pipeline.runtime import RuntimesConfig


@dataclass
class AppContext:
    settings: Settings
    runtimes: RuntimesConfig
    db: Database
    # Later: launcher, broker.


def build_context(settings: Settings) -> AppContext:
    """Load agentos.toml, run the startup checks (creating data/logs/workspaces), migrate the db.

    Raises config.ConfigError or db.MigrationError with a readable message.
    """
    runtimes = load_runtimes(settings.config)
    startup_checks(settings, runtimes)
    db = Database(settings.db_path)
    db.migrate()
    return AppContext(settings=settings, runtimes=runtimes, db=db)
