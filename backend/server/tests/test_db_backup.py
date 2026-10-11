"""db.py: the automatic backup taken before migrating an existing database (container upgrades)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.db import BACKUPS_KEPT, Database


def write_migrations(directory: Path, count: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for n in range(1, count + 1):
        (directory / f"{n:04d}_m{n}.sql").write_text(f"CREATE TABLE t{n}(x);\n", encoding="utf-8")
    return directory


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def backups(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "backups").glob("*"))


def test_a_new_database_is_not_backed_up(tmp_path: Path, data_dir: Path) -> None:
    db = Database(data_dir / "agentos.db", write_migrations(tmp_path / "m", 2))
    assert db.migrate() == 2
    assert backups(data_dir) == []


def test_nothing_pending_means_no_backup(tmp_path: Path, data_dir: Path) -> None:
    db = Database(data_dir / "agentos.db", write_migrations(tmp_path / "m", 2))
    db.migrate()
    assert db.migrate() == 2
    assert backups(data_dir) == []


def test_pending_migrations_back_up_the_old_database_first(tmp_path: Path, data_dir: Path) -> None:
    path = data_dir / "agentos.db"
    old = Database(path, write_migrations(tmp_path / "v1", 1))
    assert old.migrate() == 1
    with old.tx() as conn:
        conn.execute("INSERT INTO t1(x) VALUES ('kept')")

    new = Database(path, write_migrations(tmp_path / "v2", 2))
    assert new.migrate() == 2

    [backup] = backups(data_dir)
    assert backup.name.startswith("agentos-")
    assert backup.name.endswith("-v1.db")
    snapshot = Database(backup)  # the backup is the pre-migration database, readable on its own
    assert snapshot.user_version() == 1
    with snapshot.read() as conn:
        assert conn.execute("SELECT x FROM t1").fetchall()[0][0] == "kept"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 't2'").fetchone() is None


def test_only_the_newest_backups_are_kept(tmp_path: Path, data_dir: Path) -> None:
    folder = data_dir / "backups"
    folder.mkdir(parents=True)
    stale = [folder / f"agentos-2026010{i}T000000Z-v1.db" for i in range(1, 8)]
    for path in stale:
        path.write_bytes(b"")
    unrelated = folder / "notes.txt"
    unrelated.write_text("mine", encoding="utf-8")

    path = data_dir / "agentos.db"
    Database(path, write_migrations(tmp_path / "v1", 1)).migrate()
    Database(path, write_migrations(tmp_path / "v2", 2)).migrate()

    kept = [p for p in backups(data_dir) if p.suffix == ".db"]
    assert len(kept) == BACKUPS_KEPT == 5
    assert kept[-1].name.endswith("-v1.db")
    assert kept[-1] not in stale  # the new backup sorts after every older timestamp
    assert kept[:-1] == stale[-(BACKUPS_KEPT - 1) :]
    assert unrelated.exists()
