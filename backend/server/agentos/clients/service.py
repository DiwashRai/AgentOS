"""Client tokens: a user's own clients (`worker.py --client`), like personal access tokens.

A token is random (`secrets.token_urlsafe(32)`); it is returned once, by `create`, and only its
SHA-256 hex digest is stored. Revoking deletes the row, so the next poll with it gets 401. A token
acts for its user: it reaches only the runs that user dispatched to "My client".

This module is the one definition of "online": a token seen (polled) within ONLINE_WINDOW.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from agentos.auth.service import has_role, hash_token
from agentos.clock import from_db, from_db_opt, to_db, utcnow
from agentos.db import Conn, Database, Row
from agentos.errors import ApiError
from agentos.models import ClientToken, ClientTokenCreate, ClientTokenCreated, Me

ONLINE_WINDOW = timedelta(seconds=60)
# `runs.launcher` of a run dispatched to "My client". Never a configurable launcher name
# (LauncherName), so the dispatcher and `max_concurrency` never see these runs.
CLIENT_LAUNCHER = "client"

_SELECT_CLIENT = "SELECT id, name, created_at, last_seen_at FROM client_tokens"


@dataclass(frozen=True, slots=True)
class Client:
    """An authenticated client token: its id and name, and the user it acts for."""

    id: int
    name: str
    user: Me


def _token(row: Row, now: datetime) -> ClientToken:
    last_seen = from_db_opt(row["last_seen_at"])
    return ClientToken(
        id=row["id"],
        name=row["name"],
        created_at=from_db(row["created_at"]),
        last_seen_at=last_seen,
        online=last_seen is not None and last_seen >= now - ONLINE_WINDOW,
    )


def create(db: Database, user: Me, body: ClientTokenCreate, server_url: str) -> ClientTokenCreated:
    """A new token for `user`. The plaintext is in the answer only; the row holds its digest."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    with db.tx() as conn:
        cursor = conn.execute(
            "INSERT INTO client_tokens(user_id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (user.id, body.name, hash_token(token), to_db(now)),
        )
        row = conn.execute(_SELECT_CLIENT + " WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return ClientTokenCreated(client=_token(row, now), token=token, server_url=server_url)


def list_own(db: Database, user_id: int) -> list[ClientToken]:
    """The user's tokens, oldest first."""
    now = utcnow()
    with db.read() as conn:
        rows = conn.execute(
            _SELECT_CLIENT + " WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    return [_token(r, now) for r in rows]


def revoke_own(db: Database, user_id: int, client_id: int) -> None:
    """Delete one of the user's tokens. Another user's (or an unknown) id → 404 `not_found`."""
    with db.tx() as conn:
        cursor = conn.execute(
            "DELETE FROM client_tokens WHERE id = ? AND user_id = ?", (client_id, user_id)
        )
    if cursor.rowcount == 0:
        raise ApiError(404, "not_found", f"No client {client_id}")


def authenticate(db: Database, token: str) -> Client:
    """The client a token belongs to. 401 `unauthorized` if the token is unknown (or revoked) or
    its user is disabled or not approved yet; 403 `forbidden` below writer (running needs what
    dispatch needs)."""
    with db.read() as conn:
        row = conn.execute(
            "SELECT c.id AS client_id, c.name, u.id, u.display_name, u.email, u.avatar_url, "
            "u.role, u.disabled FROM client_tokens c JOIN users u ON u.id = c.user_id "
            "WHERE c.token_hash = ?",
            (hash_token(token),),
        ).fetchone()
    if row is None or row["disabled"] or row["role"] == "pending":
        raise ApiError(401, "unauthorized", "Unknown client token")
    user = Me(
        id=row["id"],
        display_name=row["display_name"],
        email=row["email"],
        avatar_url=row["avatar_url"],
        role=row["role"],
    )
    if not has_role(user, "writer"):
        raise ApiError(403, "forbidden", "Running a client requires the writer role")
    return Client(id=row["client_id"], name=row["name"], user=user)


def touch(db: Database, client_id: int, now: datetime) -> None:
    """Record that the client was seen (it polled)."""
    with db.tx() as conn:
        conn.execute(
            "UPDATE client_tokens SET last_seen_at = ? WHERE id = ?", (to_db(now), client_id)
        )


def has_client(conn: Conn, user_id: int) -> bool:
    """Whether the user has at least one client token."""
    row = conn.execute("SELECT 1 FROM client_tokens WHERE user_id = ? LIMIT 1", (user_id,))
    return row.fetchone() is not None


def waiting_client_run_id(conn: Conn, ticket_id: int) -> int | None:
    """The ticket's client run that is queued or dispatched (its manifest isn't built yet), if
    any. While one waits, what it will run (the context, the owner) must stay the dispatcher's."""
    row = conn.execute(
        "SELECT id FROM runs WHERE ticket_id = ? AND launcher = ? "
        "AND status IN ('queued', 'dispatched')",
        (ticket_id, CLIENT_LAUNCHER),
    ).fetchone()
    return None if row is None else int(row["id"])


def online_user_ids(conn: Conn, user_ids: Iterable[int], now: datetime) -> set[int]:
    """Which of these users have a client token seen within ONLINE_WINDOW (one query)."""
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM client_tokens "
        "WHERE user_id IN (SELECT value FROM json_each(?)) AND last_seen_at >= ?",
        (json.dumps(sorted(set(user_ids))), to_db(now - ONLINE_WINDOW)),
    ).fetchall()
    return {int(r["user_id"]) for r in rows}
