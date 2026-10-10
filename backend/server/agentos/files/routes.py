"""Versioned bundle file routes for a ticket."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos import stubs
from agentos.auth.deps import CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.models import FileContent, FileInfo, FileVersionInfo, FileWrite

router = APIRouter(prefix="/api/tickets/{key}", tags=["files"], responses=ERROR_RESPONSES)


@router.get("/files")
def list_files(key: str, _: CurrentUser) -> list[FileInfo]:
    return stubs.STORE.list_files(key)


@router.get("/files/{path:path}")
def get_file(
    key: str, path: str, _: CurrentUser, version: Annotated[int | None, Query(ge=1)] = None
) -> FileContent:
    return stubs.STORE.get_file(key, path, version)


@router.put("/files/{path:path}")
def put_file(key: str, path: str, body: FileWrite, _: CurrentUser) -> FileInfo:
    stubs.STORE.put_file(key, path, body.content, body.mode, body.base_version)
    return stubs.STORE.get_file(key, path, None)


@router.delete("/files/{path:path}", status_code=204)
def delete_file(
    key: str, path: str, base_version: Annotated[int, Query(ge=1)], _: CurrentUser
) -> None:
    current = stubs.STORE.get_file(key, path, None)
    stubs.STORE.put_file(key, path, None, current.mode, base_version)


@router.get("/versions/{path:path}")
def list_versions(key: str, path: str, _: CurrentUser) -> list[FileVersionInfo]:
    return stubs.STORE.versions(key, path)
