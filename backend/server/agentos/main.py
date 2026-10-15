"""App factory. Run with: uvicorn --factory agentos.main:create_app

`create_app()` is cheap and side-effect free (the OpenAPI export needs no .env).
Config checks, migrations and background loops run in the lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI

from agentos.auth.routes import router as auth_router
from agentos.clock import utcnow
from agentos.config import ConfigError, Settings, load_settings
from agentos.context import AppContext, build_context
from agentos.csrf import install_csrf_check
from agentos.db import MigrationError
from agentos.files.routes import router as files_router
from agentos.http_errors import install_error_handlers
from agentos.intake.routes import router as intake_router
from agentos.launchers.base import Launcher
from agentos.launchers.mailbox import MailboxLauncher
from agentos.mailbox import dispatcher as mailbox_dispatcher
from agentos.mailbox.reaper import INTERVAL_S as MAILBOX_REAPER_INTERVAL_S
from agentos.mailbox.reaper import MailboxReaper
from agentos.meta import router as meta_router
from agentos.runs import dispatcher
from agentos.runs.reaper import INTERVAL_S as REAPER_INTERVAL_S
from agentos.runs.reaper import Reaper
from agentos.runs.routes import router as runs_router
from agentos.settings.routes import router as settings_router
from agentos.social.routes import router as chat_router
from agentos.spa import router as spa_router
from agentos.templates.routes import router as template_router
from agentos.templates.service import seed_default as seed_default_template
from agentos.tickets.routes import router as tickets_router
from agentos.worker_api.routes import router as worker_router
from agentos.workflows.routes import router as workflows_router
from agentos.workflows.service import seed_standard as seed_standard_workflow

log = logging.getLogger(__name__)


async def _every(
    name: str, interval_s: float, tick: Callable[[], object], stop: asyncio.Event
) -> None:
    """Run the synchronous `tick` in a thread now and then every `interval_s` until `stop` is
    set. A failing tick is logged and the loop carries on."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
        except Exception:
            log.exception("%s tick failed", name)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)


def _loops(ctx: AppContext, stop: asyncio.Event) -> list[Coroutine[Any, Any, None]]:
    """The background loops: the runs reaper always; the runs dispatcher, or with the mailbox
    launcher the mailbox dispatcher and reaper instead (D8: the runs dispatcher would hand
    mailbox runs to `launch()`, which refuses them)."""
    reaper = Reaper(ctx)
    loops = [_every("reaper", REAPER_INTERVAL_S, lambda: reaper.tick(utcnow()), stop)]
    launcher = ctx.launcher
    if isinstance(launcher, MailboxLauncher):
        mailbox_reaper = MailboxReaper(ctx, launcher)
        loops += [
            _every(
                "mailbox-dispatcher",
                mailbox_dispatcher.INTERVAL_S,
                lambda: mailbox_dispatcher.tick(ctx, launcher),
                stop,
            ),
            _every(
                "mailbox-reaper",
                MAILBOX_REAPER_INTERVAL_S,
                lambda: mailbox_reaper.tick(utcnow()),
                stop,
            ),
        ]
    else:
        loops.append(
            _every("dispatcher", dispatcher.INTERVAL_S, lambda: dispatcher.tick(ctx), stop)
        )
    return loops


def create_app(
    settings: Settings | None = None,
    *,
    launcher: Launcher | None = None,
    run_loops: bool = True,
) -> FastAPI:
    """`settings=None` reads AGENTOS_* env + `.env` at startup (in the lifespan).

    `launcher=None` builds the configured launcher. `run_loops=False` starts no dispatcher or
    reaper (tests drive `dispatcher.tick` / `Reaper.tick` themselves).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            ctx = build_context(
                settings if settings is not None else load_settings(), launcher=launcher
            )
        except (ConfigError, MigrationError) as exc:
            log.critical("AgentOS cannot start. %s", exc)  # fail fast with the readable message
            raise
        app.state.ctx = ctx
        seed_default_template(ctx.db)
        seed_standard_workflow(ctx.db)  # after the template: Standard copies its pipeline
        stop = asyncio.Event()
        loops: list[asyncio.Task[None]] = []
        if run_loops:
            loops = [asyncio.create_task(task) for task in _loops(ctx, stop)]
        try:
            yield
        finally:
            stop.set()
            await asyncio.gather(*loops, return_exceptions=True)

    app = FastAPI(title="AgentOS", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    install_csrf_check(app)
    for router in (
        meta_router,
        auth_router,
        tickets_router,
        intake_router,
        files_router,
        chat_router,
        runs_router,
        template_router,
        workflows_router,
        settings_router,
        worker_router,
        spa_router,  # last: its catch-all serves the SPA for every non-/api path
    ):
        app.include_router(router)
    return app
