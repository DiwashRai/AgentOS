"""App factory. Run with: uvicorn --factory agentos.main:create_app"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from agentos.auth.routes import router as auth_router
from agentos.csrf import install_csrf_check
from agentos.errors import install_error_handlers
from agentos.files.routes import router as files_router
from agentos.meta import router as meta_router
from agentos.models import StreamEvent
from agentos.runs.routes import router as runs_router
from agentos.social.routes import router as chat_router
from agentos.stream import router as stream_router
from agentos.templates.routes import router as template_router
from agentos.tickets.routes import router as tickets_router
from agentos.worker_api.routes import router as worker_router

# Models that never appear in a JSON route but are part of the contract (SSE payloads).
EXTRA_SCHEMAS = (StreamEvent,)


def create_app() -> FastAPI:
    app = FastAPI(title="AgentOS", version="0.1.0")
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
