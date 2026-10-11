"""App factory. Run with: uvicorn --factory agentos.main:create_app

`create_app()` is cheap and side-effect free (the OpenAPI export needs no .env or agentos.toml).
Config checks, migrations and background loops run in the lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from agentos.auth.routes import router as auth_router
from agentos.clock import utcnow
from agentos.config import ConfigError, Settings, load_settings
from agentos.context import build_context
from agentos.csrf import install_csrf_check
from agentos.db import MigrationError
from agentos.files.routes import router as files_router
from agentos.http_errors import install_error_handlers
from agentos.intake.routes import router as intake_router
from agentos.launchers.base import Launcher
from agentos.meta import router as meta_router
from agentos.models import StreamEvent
from agentos.runs import dispatcher
from agentos.runs.reaper import INTERVAL_S as REAPER_INTERVAL_S
from agentos.runs.reaper import Reaper
from agentos.runs.routes import router as runs_router
from agentos.social.routes import router as chat_router
from agentos.spa import router as spa_router
from agentos.stream import router as stream_router
from agentos.templates.routes import router as template_router
from agentos.templates.service import seed_default as seed_default_template
from agentos.tickets.routes import router as tickets_router
from agentos.tickets.service import backfill_repos as backfill_ticket_repos
from agentos.worker_api.routes import router as worker_router

log = logging.getLogger(__name__)

# Models that never appear in a JSON route but are part of the contract (SSE payloads).
EXTRA_SCHEMAS = (StreamEvent,)


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
        backfill_ticket_repos(ctx.db, ctx.space)
        stop = asyncio.Event()
        loops: list[asyncio.Task[None]] = []
        if run_loops:
            reaper = Reaper(ctx)
            loops = [
                asyncio.create_task(
                    _every("dispatcher", dispatcher.INTERVAL_S, lambda: dispatcher.tick(ctx), stop)
                ),
                asyncio.create_task(
                    _every("reaper", REAPER_INTERVAL_S, lambda: reaper.tick(utcnow()), stop)
                ),
            ]
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
        stream_router,
        worker_router,
        spa_router,  # last: its catch-all serves the SPA for every non-/api path
    ):
        app.include_router(router)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            for model in EXTRA_SCHEMAS:
                components[model.__name__] = model.model_json_schema(
                    ref_template="#/components/schemas/{model}"
                )
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app
