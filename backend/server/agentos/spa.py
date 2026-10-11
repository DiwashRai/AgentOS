"""Serves the built SPA (`AGENTOS_WEB_DIR`, e.g. web/dist) from the API server (the container).

Included LAST in `create_app`. A real file under web_dir is served as is; any other path is a
client route and gets `index.html`. `/api` and `/api/*` never match here, so API routing (incl.
404/405 and the JSON error body) behaves exactly as without the SPA. With web_dir unset (dev:
Vite serves the UI) every path here is a plain 404.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope

from agentos.http_deps import Ctx

# Vite puts content-hashed files under assets/: safe to cache forever. Everything else
# (index.html above all) must be revalidated, or an upgrade keeps serving the old bundle.
ASSETS_DIR = "assets"
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_REVALIDATE = "no-cache"


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class _SpaRoute(APIRoute):
    """Never matches `/api` paths: a catch-all that matched them would turn an API 405 (known path,
    wrong method) into this route's response and shadow the API's own 404."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope.get("type") == "http" and _is_api(scope.get("path", "")):
            return Match.NONE, {}
        return super().matches(scope)


router = APIRouter(route_class=_SpaRoute, include_in_schema=False)


def _file_under(root: Path, rel: str) -> Path | None:
    """`root/rel` if it is an existing regular file inside `root` (no traversal), else None."""
    try:
        candidate = (root / rel).resolve()
    except (OSError, ValueError):  # e.g. an embedded NUL byte
        return None
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate
    return None


@router.api_route("/{path:path}", methods=["GET", "HEAD"])
def spa(path: str, ctx: Ctx) -> FileResponse:
    web_dir = ctx.settings.web_dir
    if web_dir is None:
        raise HTTPException(status_code=404)
    root = web_dir.resolve()
    if path and (found := _file_under(root, path)) is not None:
        hashed = found.is_relative_to(root / ASSETS_DIR)
        return FileResponse(
            found, headers={"Cache-Control": CACHE_IMMUTABLE if hashed else CACHE_REVALIDATE}
        )
    if path == ASSETS_DIR or path.startswith(f"{ASSETS_DIR}/"):
        # A missing hashed asset (e.g. an old tab after an upgrade) must not get index.html with
        # a 200: the browser would try to run HTML as a script.
        raise HTTPException(status_code=404)
    index = _file_under(root, "index.html")
    if index is None:
        raise HTTPException(status_code=404)
    return FileResponse(index, headers={"Cache-Control": CACHE_REVALIDATE})
