"""Writing the package's context and notes, and collecting what to sync (SPEC §9.3 / §9.4.1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

import worker
from worker_tests.worker_support import AGENTS_BLOCK, KEY, SKILL_DIR, make_package


def manifest() -> worker.Package:
    return worker.load_package(make_package([]))


def materialise(root: Path, m: worker.Package) -> worker.SyncState:
    state = worker.SyncState(m)
    for f in m.files:
        problem = worker.write_entry(root, f)
        if problem:
            state.unsynced.add(f.path)
    return state


LinkKind = Literal["file", "dir", "junction"]

# A directory link every machine can make: junctions need no privileges on Windows.
DIR_LINK: LinkKind = "junction" if sys.platform == "win32" else "dir"

LINK_KINDS = [
    pytest.param("file", id="file-symlink"),
    pytest.param("dir", id="dir-symlink"),
    pytest.param(
        "junction",
        marks=pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only"),
    ),
]


def make_link(kind: LinkKind, link: Path, target: Path) -> None:
    """A file/dir symlink or a Windows junction at `link`. Symlinks skip the test where the OS
    refuses them for lack of privilege; the worker must refuse every kind just the same."""
    if kind == "junction":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True
        )
        return
    try:
        link.symlink_to(target, target_is_directory=kind == "dir")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:  # ERROR_PRIVILEGE_NOT_HELD
            pytest.skip("symlinks: needs Developer Mode or admin on Windows")
        raise


def sent(batch: worker.SyncBatch) -> dict[str, tuple[str | None, str]]:
    return {f["path"]: (f["content"], f["mode"]) for f in batch.files}


def test_write_creates_files_and_blocks(tmp_path: Path) -> None:
    m = manifest()
    materialise(tmp_path, m)
    assert (tmp_path / SKILL_DIR / "TASKS.md").read_bytes().startswith(b"# Tasks\n")
    agents = (tmp_path / "AGENTS.md").read_bytes()
    assert worker.block_read(agents, KEY) == AGENTS_BLOCK
    assert b"\r\n" in agents  # content bytes are written as-is, no newline translation


def test_write_appends_the_block_to_an_existing_file(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"# Repo agents\r\n")
    materialise(tmp_path, manifest())
    agents = (tmp_path / "AGENTS.md").read_bytes()
    assert agents.startswith(b"# Repo agents\r\n\n<!-- agentos:begin AOS-42 -->\n")


def test_write_skips_a_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "ws"
    root.mkdir()
    make_link(DIR_LINK, root / ".claude", outside)
    m = manifest()
    state = materialise(root, m)
    assert list(outside.iterdir()) == []
    assert all(p.startswith(".claude/") for p in state.unsynced)
    assert len(state.unsynced) == 4
    assert (root / "AGENTS.md").exists()


def test_write_skips_a_symlinked_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"precious\n")
    root = tmp_path / "ws"
    root.mkdir()
    make_link("file", root / "CLAUDE.md", outside)
    entry = worker.ContextFile("CLAUDE.md", KEY, "@AGENTS.md", "instructions", "block", KEY)
    assert worker.write_entry(root, entry) == "CLAUDE.md is a symlink"
    assert outside.read_bytes() == b"precious\n"


def test_only_changed_blocks_are_sent(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(b"# Repo\n" + agents.read_bytes() + b"agent note\n")
    assert sent(state.collect(tmp_path)) == {}  # outside the block only

    agents.write_bytes(worker.block_write(agents.read_bytes(), KEY, "edited"))
    batch = state.collect(tmp_path)
    assert batch.warnings == []
    assert sent(batch) == {"AGENTS.md": ("edited", "block")}


def test_a_sent_change_is_not_sent_again(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    (tmp_path / SKILL_DIR / "TASKS.md").write_bytes(b"one\n")
    assert f"{SKILL_DIR}/TASKS.md" in sent(state.collect(tmp_path))
    assert sent(state.collect(tmp_path)) == {}


def test_unreadable_declared_files_are_warnings(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    skill = tmp_path / SKILL_DIR
    (skill / "TASKS.md").write_bytes(b"x" * (m.limits.max_file_bytes + 1))
    (skill / "SKILL.md").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "CLAUDE.md").write_bytes(b"markers removed by the agent\n")
    batch = state.collect(tmp_path)
    assert sent(batch) == {}
    assert len(batch.warnings) == 3
    assert any("CLAUDE.md" in w and "missing" in w for w in batch.warnings)


def test_not_kept_names_at_most_20_files(tmp_path: Path) -> None:
    state = materialise(tmp_path, manifest())
    skill = tmp_path / SKILL_DIR
    (skill / "sub").mkdir()
    for i in range(25):
        (skill / "sub" / f"f{i:02}.class").write_bytes(b"\xca\xfe")
    batch = state.collect(tmp_path)
    assert sent(batch) == {}
    [warning] = batch.warnings
    assert f"{SKILL_DIR}/sub/f19.class" in warning
    assert f"{SKILL_DIR}/sub/f20.class" not in warning
    assert warning.endswith("5 more")


def test_unwritten_manifest_files_are_not_synced_as_deleted(tmp_path: Path) -> None:
    m = manifest()
    state = worker.SyncState(m)
    state.unsynced.add(f"{SKILL_DIR}/TASKS.md")
    assert f"{SKILL_DIR}/TASKS.md" not in sent(state.collect(tmp_path))


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_links_in_protected_folders_are_skipped(tmp_path: Path, kind: LinkKind) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "key.md").write_bytes(b"secret\n")
    root = tmp_path / "ws"
    root.mkdir()
    state = materialise(root, manifest())
    make_link(kind, root / SKILL_DIR / "link", secrets / "key.md" if kind == "file" else secrets)
    batch = state.collect(root)
    assert not any(p.startswith(f"{SKILL_DIR}/link") for p in sent(batch))
    assert all("secret" not in str(f["content"]) for f in batch.files)
    [warning] = batch.warnings
    assert warning.startswith("not kept")
    assert f"{SKILL_DIR}/link" in warning


def test_a_linked_sync_dir_is_not_followed(tmp_path: Path) -> None:
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    (outside_dir / "notes.md").write_bytes(b"secret\n")
    root = tmp_path / "ws"
    (root / ".claude" / "skills").mkdir(parents=True)
    make_link(DIR_LINK, root / SKILL_DIR, outside_dir)
    state = worker.SyncState(manifest())
    batch = state.collect(root)
    assert all("secret" not in str(f["content"]) for f in batch.files)
    assert any(w.startswith("not kept") and SKILL_DIR in w for w in batch.warnings)


def test_a_symlinked_manifest_sync_file_is_neither_read_nor_deleted(tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret\n")
    root = tmp_path / "ws"
    root.mkdir()
    m = manifest()
    state = materialise(root, m)
    tasks = root / SKILL_DIR / "TASKS.md"
    tasks.unlink()
    make_link("file", tasks, outside)
    batch = state.collect(root)
    assert f"{SKILL_DIR}/TASKS.md" not in sent(batch)  # neither its target nor a deletion
    assert all("secret" not in str(f["content"]) for f in batch.files)
    assert any("TASKS.md" in w and "symlink" in w for w in batch.warnings)
