"""db.py: migrations, transactions, read snapshots and the schema's invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.clock import from_db, from_db_opt, to_db, utcnow
from agentos.db import (
    MIGRATIONS_DIR,
    Conn,
    Database,
    IntegrityError,
    MigrationError,
    OperationalError,
    migrations,
)

TABLES = {
    "users",
    "user_identities",
    "sessions",
    "key_counters",
    "templates",
    "tickets",
    "file_versions",
    "runs",
    "stage_runs",
    "run_events",
    "chat_messages",
}
NOW = "2026-10-10T12:00:00.000000+00:00"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data" / "agentos.db")
    database.migrate()
    return database


def tables(conn: Conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def add_ticket(conn: Conn, key: str = "AOS-1") -> int:
    cursor = conn.execute(
        "INSERT INTO tickets(key, slug, title, workspace_mode, pipeline, created_at, updated_at)"
        " VALUES (?, 'slug', 'Title', 'none', '{}', ?, ?)",
        (key, NOW, NOW),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def add_run(conn: Conn, ticket_id: int, status: str) -> None:
    conn.execute(
        "INSERT INTO runs(ticket_id, status, from_stage, stage_ids, time_budget_s, launcher,"
        " created_at) VALUES (?, ?, 'plan', '[\"plan\"]', 600, 'local', ?)",
        (ticket_id, status, NOW),
    )


def test_migrate_creates_every_table(db: Database) -> None:
    assert db.user_version() == len(migrations()) == 3
    with db.read() as conn:
        assert tables(conn) == TABLES


def test_migrate_twice_is_a_noop(db: Database) -> None:
    with db.tx() as conn:
        add_ticket(conn)
    assert db.migrate() == len(migrations())
    with db.read() as conn:
        assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


def test_migrate_refuses_newer_database(db: Database) -> None:
    with db.tx() as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(MigrationError, match="newer database"):
        db.migrate()


def test_failed_migration_rolls_back(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_ok.sql").write_text("CREATE TABLE a(x);\n", encoding="utf-8")
    (migrations_dir / "0002_bad.sql").write_text(
        "CREATE TABLE b(x);\nINSERT INTO nope VALUES (1);\n", encoding="utf-8"
    )
    database = Database(tmp_path / "t.db", migrations_dir)
    with pytest.raises(MigrationError, match=r"0002_bad\.sql"):
        database.migrate()
    assert database.user_version() == 1
    with database.read() as conn:
        assert tables(conn) == {"a"}


def test_an_existing_0001_database_migrates_forward(tmp_path: Path) -> None:
    only_first = tmp_path / "migrations"
    only_first.mkdir()
    first = MIGRATIONS_DIR / "0001_init.sql"
    (only_first / first.name).write_bytes(first.read_bytes())
    path = tmp_path / "agentos.db"
    old = Database(path, only_first)
    assert old.migrate() == 1
    with old.tx() as conn:
        add_ticket(conn)

    db = Database(path)
    assert db.migrate() == 3
    with db.read() as conn:
        row = conn.execute("SELECT key, pr_url, repo FROM tickets").fetchone()
    assert dict(row) == {"key": "AOS-1", "pr_url": None, "repo": None}


def test_migration_numbering_must_be_contiguous(tmp_path: Path) -> None:
    (tmp_path / "0002_x.sql").write_text("", encoding="utf-8")
    with pytest.raises(MigrationError, match="without gaps"):
        migrations(tmp_path)


def count_tickets(conn: Conn) -> int:
    n: int = conn.execute("SELECT count(*) FROM tickets").fetchone()[0]
    return n


def test_reads_are_one_snapshot_and_never_wait_for_a_writer(db: Database) -> None:
    with db.read() as snapshot:
        assert count_tickets(snapshot) == 0
        with db.tx() as conn:  # a writer can commit while a read is open (WAL)
            add_ticket(conn)
            with db.read() as during:  # ... and a read during the write sees the last commit
                assert count_tickets(during) == 0
        assert count_tickets(snapshot) == 0  # the open read keeps its snapshot
    with db.read() as after:
        assert count_tickets(after) == 1


def test_foreign_keys_enforced(db: Database) -> None:
    with pytest.raises(IntegrityError), db.tx() as conn:
        add_run(conn, ticket_id=999, status="queued")


def test_tx_rolls_back_on_exception(db: Database) -> None:
    class Boom(Exception):
        pass

    def write_then_fail() -> None:
        with db.tx() as conn:
            add_ticket(conn)
            raise Boom

    with pytest.raises(Boom):
        write_then_fail()
    with db.read() as conn:
        assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0


def test_tx_commits(db: Database) -> None:
    with db.tx() as conn:
        add_ticket(conn)
    with db.read() as conn:
        row = conn.execute("SELECT key, labels, state FROM tickets").fetchone()
    assert dict(row) == {"key": "AOS-1", "labels": "[]", "state": "open"}


def test_read_refuses_writes(db: Database) -> None:
    with pytest.raises(OperationalError, match="readonly"), db.read() as conn:
        add_ticket(conn)


def test_one_active_run_per_ticket(db: Database) -> None:
    with db.tx() as conn:
        ticket = add_ticket(conn)
        add_run(conn, ticket, "succeeded")
        add_run(conn, ticket, "queued")
        add_run(conn, ticket, "failed")  # terminal runs never collide
    for status in ("queued", "dispatched", "running"):
        with pytest.raises(IntegrityError), db.tx() as conn:
            add_run(conn, ticket, status)
    with db.tx() as conn:
        other = add_ticket(conn, "AOS-2")
        add_run(conn, other, "running")  # other tickets are independent


def test_clock_round_trip() -> None:
    now = utcnow()
    text = to_db(now)
    assert text.endswith("+00:00")
    assert len(text) == len("2026-10-10T12:00:00.123456+00:00")
    assert from_db(text) == now
    assert from_db_opt(None) is None
    assert to_db(from_db(NOW)) == NOW
    with pytest.raises(ValueError, match="naive"):
        to_db(now.replace(tzinfo=None))
