"""A ticket's versioned bundle files (SPEC §6 "Versioned files"; `owner_type='ticket'`).

Two layers:
- Bundle primitives that take a connection, so the caller owns the transaction: `write_version`
  (any author; no base_version or lock check) and `current_bundle`. The run service uses these for
  worker sync and for the manifest at claim.
- Human routes (`list_files`, `get_file`, `put_file`, `delete_file`, `list_versions`) wrap them
  with the ticket lookup, `base_version` and bundle-lock rules.

A version row with `content IS NULL` is a delete. "Current" = the max version of a path, absent
if that version is a delete. Version numbers count per path and include deletes.
"""

from __future__ import annotations

from datetime import datetime

from agentos.broker import Broker
from agentos.clients.service import waiting_client_run_id
from agentos.clock import from_db, to_db, utcnow
from agentos.db import Conn, Database, Row
from agentos.errors import ApiError
from agentos.models import (
    FileContent,
    FileEntry,
    FileInfo,
    FileVersionInfo,
    FileWrite,
    Me,
    Mode,
    Person,
)
from agentos.pipeline.plan import parse_plan
from agentos.pipeline.scaffold import names
from agentos.validate import (
    MAX_BUNDLE_BYTES,
    check_file_size,
    path_key,
    validate_block_content,
    validate_path,
)

OWNER_TYPE = "ticket"

# SQL is written out in full (no string building). `fv` = file_versions, `u` = its human author.
# "Current" rows: join each path's max version. Parameters start with (ticket_id, ticket_id).

_SQL_CURRENT_INFOS = (
    "SELECT fv.path, fv.version, fv.content IS NULL AS deleted, fv.mode, "
    "COALESCE(length(CAST(fv.content AS BLOB)), 0) AS size, fv.author_user_id, "
    "fv.author_run_id, fv.created_at, u.display_name AS author_name, "
    "u.avatar_url AS author_avatar "
    "FROM file_versions fv "
    "JOIN (SELECT path, MAX(version) AS version FROM file_versions "
    "      WHERE owner_type = 'ticket' AND owner_id = ? GROUP BY path) cur "
    "  ON cur.path = fv.path AND cur.version = fv.version "
    "LEFT JOIN users u ON u.id = fv.author_user_id "
    "WHERE fv.owner_type = 'ticket' AND fv.owner_id = ? AND fv.content IS NOT NULL "
    "ORDER BY fv.path"
)
_SQL_CURRENT_CONTENTS = (
    "SELECT fv.path, fv.content, fv.mode, length(CAST(fv.content AS BLOB)) AS size "
    "FROM file_versions fv "
    "JOIN (SELECT path, MAX(version) AS version FROM file_versions "
    "      WHERE owner_type = 'ticket' AND owner_id = ? GROUP BY path) cur "
    "  ON cur.path = fv.path AND cur.version = fv.version "
    "WHERE fv.owner_type = 'ticket' AND fv.owner_id = ? AND fv.content IS NOT NULL "
    "ORDER BY fv.path"
)
_SQL_CURRENT_SIZES = (
    "SELECT fv.path, length(CAST(fv.content AS BLOB)) AS size "
    "FROM file_versions fv "
    "JOIN (SELECT path, MAX(version) AS version FROM file_versions "
    "      WHERE owner_type = 'ticket' AND owner_id = ? GROUP BY path) cur "
    "  ON cur.path = fv.path AND cur.version = fv.version "
    "WHERE fv.owner_type = 'ticket' AND fv.owner_id = ? AND fv.content IS NOT NULL"
)
# Versions of one path. Parameters: (ticket_id, path).
_SQL_PATH_VERSIONS = (
    "SELECT fv.path, fv.version, fv.content IS NULL AS deleted, fv.mode, "
    "COALESCE(length(CAST(fv.content AS BLOB)), 0) AS size, fv.author_user_id, "
    "fv.author_run_id, fv.created_at, u.display_name AS author_name, "
    "u.avatar_url AS author_avatar, fv.content "
    "FROM file_versions fv LEFT JOIN users u ON u.id = fv.author_user_id "
    "WHERE fv.owner_type = 'ticket' AND fv.owner_id = ? AND fv.path = ? "
)
_SQL_VERSIONS = _SQL_PATH_VERSIONS + "ORDER BY fv.version"
_SQL_LATEST = _SQL_PATH_VERSIONS + "ORDER BY fv.version DESC LIMIT 1"
_SQL_ONE_VERSION = _SQL_PATH_VERSIONS + "AND fv.version = ?"


# ------------------------------------------------------------------ bundle primitives (run service)


def write_version(
    conn: Conn,
    ticket_id: int,
    path: str,
    content: str | None,
    mode: Mode,
    *,
    author_user_id: int | None = None,
    author_run_id: int | None = None,
    author_stage: str | None = None,
    now: datetime | None = None,
) -> int | None:
    """Append a version of `path` (`content=None` deletes it) unless nothing would change.

    Validates the path (422), file size (413), block content (422), case-insensitive uniqueness
    against the ticket's other current paths (422) and the bundle total (413). No base_version or
    bundle-lock check: human callers do those first. A TASKS.md write recomputes
    `tickets.tasks_done/total`; every appended version bumps `tickets.updated_at`.

    Returns the path's latest version number afterwards (a delete is a version too), or None when
    the path never existed (deleting an absent file appends nothing).
    """
    path = validate_path(path)
    latest = _latest(conn, ticket_id, path)
    latest_version: int | None = latest["version"] if latest is not None else None
    if content is None:
        if latest is None or latest["deleted"]:
            return latest_version  # already absent
    else:
        if (
            latest is not None
            and not latest["deleted"]
            and latest["content"] == content
            and latest["mode"] == mode
        ):
            return latest_version  # idempotent: unchanged
        size = check_file_size(content)
        if mode == "block":
            validate_block_content(content)
        _check_fits(conn, ticket_id, path, size)
    version = (latest_version or 0) + 1
    stamp = to_db(now or utcnow())
    conn.execute(
        "INSERT INTO file_versions(owner_type, owner_id, path, version, content, mode, "
        "author_user_id, author_run_id, author_stage, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            OWNER_TYPE,
            ticket_id,
            path,
            version,
            content,
            mode,
            author_user_id,
            author_run_id,
            author_stage,
            stamp,
        ),
    )
    _touch_ticket(conn, ticket_id, path, content, stamp)
    return version


def current_bundle(conn: Conn, ticket_id: int) -> list[FileEntry]:
    """The ticket's current (non-deleted) files, ordered by path: the manifest's `sync=True` set."""
    rows = conn.execute(_SQL_CURRENT_CONTENTS, (ticket_id, ticket_id)).fetchall()
    return [FileEntry(path=r["path"], content=r["content"], mode=r["mode"]) for r in rows]


def _check_fits(conn: Conn, ticket_id: int, path: str, size: int) -> None:
    """422 on a case-insensitive clash with another current path; 413 if the bundle would grow
    past MAX_BUNDLE_BYTES with this file's new content."""
    total = size
    for r in conn.execute(_SQL_CURRENT_SIZES, (ticket_id, ticket_id)).fetchall():
        if r["path"] == path:
            continue
        if path_key(r["path"]) == path_key(path):
            raise ApiError(
                422,
                "validation_error",
                f"path: {path!r} collides with {r['path']!r} (paths are case-insensitive)",
            )
        total += r["size"]
    if total > MAX_BUNDLE_BYTES:
        raise ApiError(413, "too_large", f"bundle: {total} bytes exceeds {MAX_BUNDLE_BYTES}")


def _touch_ticket(conn: Conn, ticket_id: int, path: str, content: str | None, stamp: str) -> None:
    t = conn.execute(
        "SELECT key, slug, workspace_mode FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if t is None:
        raise ApiError(404, "ticket_not_found", f"No ticket with id {ticket_id}")
    tasks_md = names(t["key"], t["slug"], t["workspace_mode"]).tasks_md
    if path_key(path) != path_key(tasks_md):
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (stamp, ticket_id))
        return
    done: int | None = None
    total: int | None = None
    if content is not None:
        plan = parse_plan(content)
        done, total = plan.done, plan.total
    conn.execute(
        "UPDATE tickets SET tasks_done = ?, tasks_total = ?, updated_at = ? WHERE id = ?",
        (done, total, stamp, ticket_id),
    )


# ------------------------------------------------------------------ human routes


def list_files(db: Database, key: str) -> list[FileInfo]:
    with db.read() as conn:
        ticket_id = _ticket_id(conn, key)
        rows = conn.execute(_SQL_CURRENT_INFOS, (ticket_id, ticket_id)).fetchall()
    return [_info(r) for r in rows]


def get_file(db: Database, key: str, path: str, version: int | None) -> FileContent:
    """404 `file_not_found` if the path has no versions (or, without `version`, is deleted);
    404 `version_not_found` if that version doesn't exist or is a delete."""
    with db.read() as conn:
        ticket_id = _ticket_id(conn, key)
        row = _latest(conn, ticket_id, path)
        if row is None or (version is None and row["deleted"]):
            raise ApiError(404, "file_not_found", f"No file {path}")
        if version is not None and version != row["version"]:
            row = conn.execute(_SQL_ONE_VERSION, (ticket_id, path, version)).fetchone()
    if row is None or row["content"] is None:
        raise ApiError(404, "version_not_found", f"No version {version} of {path}")
    return FileContent(**_info(row).model_dump(), content=row["content"])


def put_file(
    db: Database, broker: Broker, key: str, path: str, body: FileWrite, user: Me
) -> FileInfo:
    """Human write: `base_version` 0 creates (409 `stale_version` if the file exists), otherwise it
    must be the current version. 409 `bundle_locked` while the ticket's run is `running`, or a
    client run of it waits (queued or dispatched).
    Unchanged content + mode appends nothing and returns the current info."""
    with db.tx() as conn:
        ticket_id = _ticket_id(conn, key)
        path = validate_path(path)
        _check_unlocked(conn, ticket_id)
        current = _current_version(conn, ticket_id, path)
        if body.base_version != current:
            why = "already exists" if body.base_version == 0 else "was changed by someone else"
            raise ApiError(409, "stale_version", f"{path} {why} (current version {current})")
        version = write_version(
            conn, ticket_id, path, body.content, body.mode, author_user_id=user.id
        )
        latest = _latest(conn, ticket_id, path)
    if latest is None:  # pragma: no cover - a version was just written
        raise RuntimeError(f"{path}: no version after a write")
    if version != current:
        broker.ticket(key)
    return _info(latest)


def delete_file(
    db: Database, broker: Broker, key: str, path: str, base_version: int, user: Me
) -> None:
    """Append a delete version. 404 if the file is absent; same lock and version rules as PUT."""
    with db.tx() as conn:
        ticket_id = _ticket_id(conn, key)
        latest = _latest(conn, ticket_id, path)
        if latest is None or latest["deleted"]:
            raise ApiError(404, "file_not_found", f"No file {path}")
        _check_unlocked(conn, ticket_id)
        if base_version != latest["version"]:
            raise ApiError(
                409,
                "stale_version",
                f"{path} was changed by someone else (current version {latest['version']})",
            )
        write_version(conn, ticket_id, path, None, latest["mode"], author_user_id=user.id)
    broker.ticket(key)


def list_versions(db: Database, key: str, path: str) -> list[FileVersionInfo]:
    """Every version of the path, deletes included, oldest first."""
    with db.read() as conn:
        ticket_id = _ticket_id(conn, key)
        rows = conn.execute(_SQL_VERSIONS, (ticket_id, path)).fetchall()
    if not rows:
        raise ApiError(404, "file_not_found", f"No file {path}")
    return [
        FileVersionInfo(
            version=r["version"],
            deleted=bool(r["deleted"]),
            mode=r["mode"],
            size=r["size"],
            author=_author(r),
            author_run_id=r["author_run_id"],
            created_at=from_db(r["created_at"]),
        )
        for r in rows
    ]


# ------------------------------------------------------------------ helpers


def _ticket_id(conn: Conn, key: str) -> int:
    row = conn.execute("SELECT id FROM tickets WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise ApiError(404, "ticket_not_found", f"No ticket {key}")
    ticket_id: int = row["id"]
    return ticket_id


def _check_unlocked(conn: Conn, ticket_id: int) -> None:
    """SPEC §6: the bundle is read-only while a run is claimed (`running`). A queued or
    dispatched server run doesn't lock it (its manifest is built at claim), but a waiting client
    run does: it runs on its owner's machine, so only what the owner dispatched may reach it."""
    row = conn.execute(
        "SELECT id FROM runs WHERE ticket_id = ? AND status = 'running'", (ticket_id,)
    ).fetchone()
    if row is not None:
        raise ApiError(
            409, "bundle_locked", f"Context is read-only while run #{row['id']} is running"
        )

    waiting = waiting_client_run_id(conn, ticket_id)
    if waiting is not None:
        raise ApiError(
            409, "bundle_locked", f"Context is read-only while run #{waiting} waits for a client"
        )


def _latest(conn: Conn, ticket_id: int, path: str) -> Row | None:
    row: Row | None = conn.execute(_SQL_LATEST, (ticket_id, path)).fetchone()
    return row


def _current_version(conn: Conn, ticket_id: int, path: str) -> int:
    """The current version number, or 0 when the file is absent (never written or deleted)."""
    latest = _latest(conn, ticket_id, path)
    if latest is None or latest["deleted"]:
        return 0
    version: int = latest["version"]
    return version


def _author(row: Row) -> Person | None:
    if row["author_user_id"] is None or row["author_name"] is None:
        return None
    return Person(
        id=row["author_user_id"], display_name=row["author_name"], avatar_url=row["author_avatar"]
    )


def _info(row: Row) -> FileInfo:
    return FileInfo(
        path=row["path"],
        mode=row["mode"],
        version=row["version"],
        size=row["size"],
        author=_author(row),
        author_run_id=row["author_run_id"],
        updated_at=from_db(row["created_at"]),
    )
