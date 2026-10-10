"""Versioned bundle file routes for a ticket."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos.auth.deps import CurrentUser, Writer
from agentos.errors import ERROR_RESPONSES
from agentos.files import service
from agentos.http_deps import Ctx
from agentos.models import FileContent, FileInfo, FileVersionInfo, FileWrite

router = APIRouter(prefix="/api/tickets/{key}", tags=["files"], responses=ERROR_RESPONSES)


@router.get("/files")
def list_files(key: str, _: CurrentUser, ctx: Ctx) -> list[FileInfo]:
    return service.list_files(ctx.db, key)


@router.get("/files/{path:path}")
def get_file(
    key: str,
    path: str,
    _: CurrentUser,
    ctx: Ctx,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> FileContent:
    return service.get_file(ctx.db, key, path, version)


@router.put("/files/{path:path}")
def put_file(key: str, path: str, body: FileWrite, user: Writer, ctx: Ctx) -> FileInfo:
    return service.put_file(ctx.db, key, path, body, user)


@router.delete("/files/{path:path}", status_code=204)
def delete_file(
    key: str, path: str, base_version: Annotated[int, Query(ge=1)], user: Writer, ctx: Ctx
) -> None:
    service.delete_file(ctx.db, key, path, base_version, user)


@router.get("/versions/{path:path}")
def list_versions(key: str, path: str, _: CurrentUser, ctx: Ctx) -> list[FileVersionInfo]:
    return service.list_versions(ctx.db, key, path)
