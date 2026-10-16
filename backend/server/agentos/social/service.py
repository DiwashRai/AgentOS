"""Ticket chat (SPEC §4): casual, human-only, never synced. Comments arrive in M1."""

from __future__ import annotations

from agentos.clock import from_db, to_db, utcnow
from agentos.db import Database, Row
from agentos.errors import ApiError
from agentos.models import ChatMessage, Me, Person
from agentos.tickets.service import get_ticket_row

_SELECT = (
    "SELECT c.id, c.body, c.created_at, c.user_id, u.display_name, u.avatar_url "
    "FROM chat_messages c JOIN users u ON u.id = c.user_id "
)
_SQL_AFTER = _SELECT + "WHERE c.ticket_id = ? AND c.id > ? ORDER BY c.id LIMIT ?"
_SQL_BEFORE = _SELECT + "WHERE c.ticket_id = ? AND c.id < ? ORDER BY c.id DESC LIMIT ?"
_SQL_LATEST = _SELECT + "WHERE c.ticket_id = ? ORDER BY c.id DESC LIMIT ?"
_SQL_ONE = _SELECT + "WHERE c.id = ?"


def list_chat(
    db: Database, key: str, *, after_id: int | None, before_id: int | None, limit: int
) -> list[ChatMessage]:
    """Oldest first. `after_id` → the next `limit` messages after it; `before_id` → the `limit`
    messages just before it; neither → the latest `limit`. `after_id` wins if both are given."""
    with db.read() as conn:
        ticket_id = get_ticket_row(conn, key)["id"]
        if after_id is not None:
            rows = conn.execute(_SQL_AFTER, (ticket_id, after_id, limit)).fetchall()
        elif before_id is not None:
            rows = conn.execute(_SQL_BEFORE, (ticket_id, before_id, limit)).fetchall()[::-1]
        else:
            rows = conn.execute(_SQL_LATEST, (ticket_id, limit)).fetchall()[::-1]
    return [_message(r, key) for r in rows]


def post_chat(db: Database, key: str, user: Me, body: str) -> ChatMessage:
    """Any signed-in user. 422 for a blank body (length limits come from `ChatPost`)."""
    if not body.strip():
        raise ApiError(422, "validation_error", "body: must not be blank")
    with db.tx() as conn:
        ticket_id = get_ticket_row(conn, key)["id"]
        cursor = conn.execute(
            "INSERT INTO chat_messages(ticket_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, user.id, body, to_db(utcnow())),
        )
        row = conn.execute(_SQL_ONE, (cursor.lastrowid,)).fetchone()
    return _message(row, key)


def _message(row: Row, key: str) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        ticket_key=key,
        author=Person(
            id=row["user_id"], display_name=row["display_name"], avatar_url=row["avatar_url"]
        ),
        body=row["body"],
        created_at=from_db(row["created_at"]),
    )
