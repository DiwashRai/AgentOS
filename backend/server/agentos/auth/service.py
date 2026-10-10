"""Users, identities and sessions (SPEC §5 "Identity", §11). No web imports: plain arguments in,
models out, `ApiError` for failures. Every write runs in one `db.tx()`.

Session tokens are random (`secrets.token_urlsafe(32)`); only their sha256 hex is stored.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from agentos.clock import to_db, utcnow
from agentos.config import Settings, is_loopback_host
from agentos.db import Conn, Database, Row
from agentos.errors import ApiError
from agentos.models import LoginRequest, Me, Person, Role, User, UserPatch

SESSION_TTL = timedelta(days=14)
# Dev auth refuses requests that came through a proxy (SPEC §11).
PROXY_HEADERS = ("x-forwarded-for", "cf-connecting-ip")
MAX_USERNAME_LEN = 64

_ROLE_RANK: dict[Role, int] = {"viewer": 0, "writer": 1, "admin": 2}
_SELECT_USER = (
    "SELECT id, display_name, email, avatar_url, role, disabled, created_at, last_login_at "
    "FROM users"
)


@dataclass(frozen=True)
class Session:
    """A freshly created session. `token` is the raw cookie value; it is never stored."""

    user: Me
    token: str
    expires_at: datetime


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def has_role(user: Me, minimum: Role) -> bool:
    return _ROLE_RANK[user.role] >= _ROLE_RANK[minimum]


def cookie_secure(settings: Settings) -> bool:
    """The session cookie is `Secure` iff PUBLIC_URL is https (SPEC §10)."""
    return urlsplit(settings.public_url).scheme == "https"


# ------------------------------------------------------------------ login / logout / lookup


def login(
    db: Database, settings: Settings, body: LoginRequest, *, host: str | None, proxied: bool
) -> Session:
    """Authenticate, upsert the user and create a session.

    `host` is the request's Host header; `proxied` is True when it carried any PROXY_HEADERS.
    """
    if not settings.dev_auth:
        raise ApiError(501, "not_implemented", "Jira login is not implemented yet")
    if proxied or not is_loopback_host(_host_name(host)):
        raise ApiError(403, "forbidden", "Dev auth only accepts direct loopback requests")
    username = (body.username or "").strip()
    if not username:
        raise ApiError(400, "missing_credentials", "Provide a username")
    if len(username) > MAX_USERNAME_LEN or not username.isprintable():
        raise ApiError(
            422,
            "validation_error",
            f"username: 1-{MAX_USERNAME_LEN} printable characters",
        )
    now = utcnow()
    with db.tx() as conn:
        user_id = _upsert_identity(
            conn, "dev", username, display_name=username, email=None, now=now
        )
        row = _user_row(conn, user_id)
        if row["disabled"]:
            raise ApiError(403, "user_disabled", "This account is disabled")
        # Dev users are always admins, re-applied on every login.
        conn.execute(
            "UPDATE users SET role = 'admin', last_login_at = ? WHERE id = ?",
            (to_db(now), user_id),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (to_db(now),))
        token, expires_at = _create_session(conn, user_id, now)
        user = _me(_user_row(conn, user_id))
    return Session(user=user, token=token, expires_at=expires_at)


def logout(db: Database, token: str | None) -> None:
    """Delete the session (if any). Never fails."""
    if not token:
        return
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def user_for_session(db: Database, token: str | None) -> Me:
    """The signed-in user for a session cookie value. 401 if missing, unknown, expired or disabled.

    An expired session row is deleted.
    """
    if not token:
        raise ApiError(401, "unauthorized", "Not signed in")
    token_hash = hash_token(token)
    now = to_db(utcnow())
    with db.read() as conn:
        row = conn.execute(
            "SELECT u.id, u.display_name, u.email, u.avatar_url, u.role, u.disabled, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
    if row is None:
        raise ApiError(401, "unauthorized", "Not signed in")
    if row["expires_at"] <= now:
        with db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        raise ApiError(401, "unauthorized", "Session expired")
    if row["disabled"]:
        raise ApiError(401, "unauthorized", "This account is disabled")
    return _me(row)


# ------------------------------------------------------------------ people & users


def list_people(db: Database) -> list[Person]:
    """Non-disabled users, for owner pickers."""
    with db.read() as conn:
        rows = conn.execute(
            "SELECT id, display_name, avatar_url FROM users WHERE disabled = 0 "
            "ORDER BY display_name COLLATE NOCASE, id"
        ).fetchall()
    return [Person.model_validate(dict(r)) for r in rows]


def list_users(db: Database) -> list[User]:
    with db.read() as conn:
        rows = conn.execute(_SELECT_USER + " ORDER BY display_name COLLATE NOCASE, id").fetchall()
    return [_user(r) for r in rows]


def patch_user(db: Database, settings: Settings, actor: Me, user_id: int, patch: UserPatch) -> User:
    """Admin edit of `role` / `disabled`. Disabling deletes the user's sessions.

    422 when an admin would disable or demote themselves, or demote an env admin (ADMINS).
    """
    with db.tx() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ApiError(404, "user_not_found", f"No user {user_id}")
        demote = patch.role is not None and patch.role != "admin"
        if user_id == actor.id:
            if patch.disabled:
                raise ApiError(422, "validation_error", "You can't disable yourself")
            if demote:
                raise ApiError(422, "validation_error", "You can't demote yourself")
        if demote and is_env_admin(conn, settings, user_id):
            raise ApiError(
                422,
                "validation_error",
                "This user is listed in AGENTOS_ADMINS and can't be demoted",
            )
        if patch.role is not None:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (patch.role, user_id))
        if patch.disabled is not None:
            conn.execute(
                "UPDATE users SET disabled = ? WHERE id = ?", (int(patch.disabled), user_id)
            )
            if patch.disabled:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return _user(_user_row(conn, user_id))


def is_env_admin(conn: Conn, settings: Settings, user_id: int) -> bool:
    """True when ADMINS lists one of the user's identity subjects or their email (ignoring case)."""
    admins = {a.casefold() for a in settings.admins}
    if not admins:
        return False
    subjects = conn.execute(
        "SELECT subject FROM user_identities WHERE user_id = ?", (user_id,)
    ).fetchall()
    email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    candidates = [r["subject"] for r in subjects]
    if email is not None and email["email"]:
        candidates.append(email["email"])
    return any(c.casefold() in admins for c in candidates)


# ------------------------------------------------------------------ helpers


def _upsert_identity(
    conn: Conn,
    provider: str,
    subject: str,
    *,
    display_name: str,
    email: str | None,
    now: datetime,
    role: Role = "viewer",
) -> int:
    """The user id for `(provider, subject)`, creating the user + identity on first sight."""
    row = conn.execute(
        "SELECT user_id FROM user_identities WHERE provider = ? AND subject = ?",
        (provider, subject),
    ).fetchone()
    if row is not None:
        existing: int = row["user_id"]
        return existing
    cur = conn.execute(
        "INSERT INTO users(display_name, email, role, created_at) VALUES (?, ?, ?, ?)",
        (display_name, email, role, to_db(now)),
    )
    user_id = cur.lastrowid
    if user_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
        raise RuntimeError("INSERT INTO users returned no rowid")
    conn.execute(
        "INSERT INTO user_identities(provider, subject, user_id, created_at) VALUES (?, ?, ?, ?)",
        (provider, subject, user_id, to_db(now)),
    )
    return user_id


def _create_session(conn: Conn, user_id: int, now: datetime) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = now + SESSION_TTL
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (hash_token(token), user_id, to_db(now), to_db(expires_at)),
    )
    return token, expires_at


def _user_row(conn: Conn, user_id: int) -> Row:
    row: Row | None = conn.execute(_SELECT_USER + " WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ApiError(404, "user_not_found", f"No user {user_id}")
    return row


def _host_name(host: str | None) -> str | None:
    """`127.0.0.1:8000` → `127.0.0.1`, `[::1]:8000` → `::1`; None if unparseable."""
    if not host:
        return None
    try:
        return urlsplit(f"//{host}").hostname
    except ValueError:
        return None


def _fields(row: Row, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: row[name] for name in names}


def _me(row: Row) -> Me:
    return Me.model_validate(_fields(row, ("id", "display_name", "email", "avatar_url", "role")))


def _user(row: Row) -> User:
    data = _fields(
        row,
        (
            "id",
            "display_name",
            "email",
            "avatar_url",
            "role",
            "created_at",
            "last_login_at",
        ),
    )
    data["disabled"] = bool(row["disabled"])
    return User.model_validate(data)
