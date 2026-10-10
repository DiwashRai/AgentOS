"""CSRF: every non-GET /api request (except worker routes) must carry `X-AgentOS: 1`.

A custom header cannot be sent cross-origin without a CORS preflight, and CORS is not enabled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from agentos.http_errors import error_response

CSRF_HEADER = "x-agentos"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def install_csrf_check(app: FastAPI) -> None:
    @app.middleware("http")
    async def _csrf(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if (
            path.startswith("/api/")
            and not path.startswith("/api/worker/")
            and request.method not in SAFE_METHODS
            and request.headers.get(CSRF_HEADER) != "1"
        ):
            return error_response(403, "csrf_header_missing", "Missing X-AgentOS: 1 header")
        return await call_next(request)
