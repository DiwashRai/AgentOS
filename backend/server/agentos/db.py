"""SQLite access. The ONLY module that imports sqlite3 (import-linter enforces this).

Services use these re-exports, so swapping to Postgres later touches one module.

One connection per unit of work: `with db.tx() as conn:` for writes (BEGIN IMMEDIATE, commit on
success, rollback on error), `with db.read() as conn:` for reads (one snapshot, writes refused).
Connections run with `isolation_level=None`, so the only transactions are the explicit ones.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

Conn = sqlite3.Connection
Row = sqlite3.Row
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError  # e.g. a write inside read(), or a busy timeout

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

# SPEC §5: on every connection.
_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
)


class MigrationError(Exception):
    pass


def migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """`NNNN_name.sql` files in order; numbers must run 1..N with no gaps."""
    found: list[tuple[int, Path]] = []
    for path in directory.glob("*.sql"):
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            raise MigrationError(f"bad migration file name {path.name!r} (want NNNN_name.sql)")
        found.append((int(match.group(1)), path))
    found.sort()
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(found) + 1)):
        raise MigrationError(f"migrations must be numbered 1..N without gaps, got {numbers}")
    return found


class Database:
    def __init__(self, path: Path, migrations_dir: Path = MIGRATIONS_DIR) -> None:
        self.path = path
        self.migrations_dir = migrations_dir

    def connect(self) -> Conn:
        """A new connection with the SPEC §5 pragmas. The caller closes it."""
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            for pragma in _PRAGMAS:
                conn.execute(pragma).fetchall()
        except BaseException:
            conn.close()
            raise
        return conn

    @contextmanager
    def tx(self) -> Iterator[Conn]:
        """A write transaction (BEGIN IMMEDIATE): commit on success, roll back on any exception."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[Conn]:
        """Read-only work: one consistent snapshot; any write raises an OperationalError."""
        conn = self.connect()
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    def user_version(self) -> int:
        conn = self.connect()
        try:
            version: int = conn.execute("PRAGMA user_version").fetchone()[0]
            return version
        finally:
            conn.close()

    def migrate(self) -> int:
        """Apply pending migrations in order, each in its own transaction. Returns the version.

        Idempotent. Refuses to touch a database whose version is newer than this code knows.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        files = migrations(self.migrations_dir)
        known = len(files)
        conn = self.connect()
        try:
            current: int = conn.execute("PRAGMA user_version").fetchone()[0]
            if current > known:
                raise MigrationError(
                    f"{self.path} is at schema version {current}, but this code only knows "
                    f"{known}: refusing to run an older server against a newer database"
                )
            for number, path in files[current:]:
                sql = path.read_text(encoding="utf-8")
                # executescript() commits any open transaction first, so BEGIN/COMMIT live inside
                # the script; on error the script stops mid-transaction and we roll it back.
                try:
                    conn.executescript(
                        f"BEGIN IMMEDIATE;\n{sql}\n;PRAGMA user_version = {number};\nCOMMIT;"
                    )
                except sqlite3.Error as exc:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise MigrationError(f"migration {path.name} failed: {exc}") from exc
                current = number
            return current
        finally:
            conn.close()
