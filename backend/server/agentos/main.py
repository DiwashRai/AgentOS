"""App factory. Run with: uvicorn --factory agentos.main:create_app

`create_app()` is cheap and side-effect free (the OpenAPI export needs no .env or agentos.toml).
Config checks, migrations and background loops run in the lifespan.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from agentos.auth.routes import router as auth_router
from agentos.config import ConfigError, Settings, load_settings
from agentos.context import build_context
from agentos.csrf import install_csrf_check
from agentos.db import MigrationError
from agentos.files.routes import router as files_router
from agentos.http_errors import install_error_handlers
from agentos.meta import router as meta_router
from agentos.models import StreamEvent
from agentos.runs.routes import router as runs_router
from agentos.social.routes import router as chat_router
from agentos.stream import router as stream_router
from agentos.templates.routes import router as template_router
from agentos.templates.service import seed_default as seed_default_template
from agentos.tickets.routes import router as tickets_router
from agentos.worker_api.routes import router as worker_router

log = logging.getLogger(__name__)

# Models that never appear in a JSON route but are part of the contract (SSE payloads).
EXTRA_SCHEMAS = (StreamEvent,)


def create_app(settings: Settings | None = None) -> FastAPI:
    """`settings=None` reads AGENTOS_* env + `.env` at startup (in the lifespan)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            ctx = build_context(settings if settings is not None else load_settings())
        except (ConfigError, MigrationError) as exc:
            log.critical("AgentOS cannot start. %s", exc)  # fail fast with the readable message
            raise
        app.state.ctx = ctx
        seed_default_template(ctx.db)
        # --- Later tasks: start the background loops (dispatcher, reaper) here as asyncio
        # tasks, and cancel + await them after `yield`.
        yield

    app = FastAPI(title="AgentOS", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    install_csrf_check(app)
    for router in (
        meta_router,
        auth_router,
        tickets_router,
        files_router,
        chat_router,
        runs_router,
        template_router,
        stream_router,
        worker_router,
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
