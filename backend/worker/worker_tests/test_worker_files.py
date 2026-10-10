"""Writing manifest files and collecting the context to sync (SPEC §9.3 / §9.4.1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import worker
from worker_tests.worker_support import AGENTS_BLOCK, KEY, SKILL_DIR, make_manifest


def manifest() -> worker.Manifest:
    return worker.parse_manifest(make_manifest([]))


def materialise(root: Path, m: worker.Manifest) -> worker.SyncState:
    state = worker.SyncState(m)
    for f in m.files:
        problem = worker.write_entry(root, f, m.key)
        if problem:
            state.unsynced.add(f.path)
    return state


def symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    """A symlink; for directories on Windows without symlink rights, a junction instead
    (no privileges needed, and the worker must refuse it just the same)."""
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        if directory and sys.platform == "win32":
            done = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                check=False,
            )
            if done.returncode == 0:
                return
        pytest.skip(f"cannot create symlinks here: {exc}")


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
    symlink_or_skip(root / ".claude", outside, directory=True)
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
    symlink_or_skip(root / "CLAUDE.md", outside)
    entry = worker.ManifestFile("CLAUDE.md", "@AGENTS.md", "block", True)
    assert worker.write_entry(root, entry, KEY) == "CLAUDE.md is a symlink"
    assert outside.read_bytes() == b"precious\n"


def test_first_sync_sends_every_sync_file_and_blocks_only(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    (tmp_path / "AGENTS.md").write_bytes(
        b"# Repo\n" + (tmp_path / "AGENTS.md").read_bytes() + b"agent note\n"
    )
    batch = state.collect(tmp_path)
    assert batch.warnings == []
    assert sent(batch) == {
        f"{SKILL_DIR}/SKILL.md": ("# AOS-42\n", "replace"),
        f"{SKILL_DIR}/TASKS.md": ("# Tasks\n\n- [ ] Fix expiry\n- [ ] Add test\n", "replace"),
        f"{SKILL_DIR}/learnings.log": ("", "replace"),
        "AGENTS.md": (AGENTS_BLOCK, "block"),
        "CLAUDE.md": ("@AGENTS.md", "block"),
    }


def test_new_changed_and_vanished_files_under_sync_dirs(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    skill = tmp_path / SKILL_DIR
    (skill / "notes.md").write_bytes(b"one\n")
    (skill / "sub").mkdir()
    (skill / "sub" / "deep.txt").write_bytes(b"deep\n")
    first = state.collect(tmp_path)
    state.accept(first)
    assert sent(first)[f"{SKILL_DIR}/notes.md"] == ("one\n", "replace")
    assert sent(first)[f"{SKILL_DIR}/sub/deep.txt"] == ("deep\n", "replace")

    unchanged = state.collect(tmp_path)
    state.accept(unchanged)
    assert f"{SKILL_DIR}/notes.md" not in sent(unchanged)
    assert f"{SKILL_DIR}/TASKS.md" in sent(unchanged)  # sync=true files always go

    (skill / "notes.md").write_bytes(b"two\n")
    (skill / "sub" / "deep.txt").unlink()
    (skill / "TASKS.md").unlink()
    changed = state.collect(tmp_path)
    state.accept(changed)
    assert sent(changed)[f"{SKILL_DIR}/notes.md"] == ("two\n", "replace")
    assert sent(changed)[f"{SKILL_DIR}/sub/deep.txt"] == (None, "replace")
    assert sent(changed)[f"{SKILL_DIR}/TASKS.md"] == (None, "replace")

    again = state.collect(tmp_path)
    assert f"{SKILL_DIR}/sub/deep.txt" not in sent(again)  # a deletion is sent once
    assert f"{SKILL_DIR}/TASKS.md" not in sent(again)


def test_rejected_sync_is_resent(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    (tmp_path / SKILL_DIR / "notes.md").write_bytes(b"one\n")
    state.collect(tmp_path)  # not accepted: the server rejected it
    assert f"{SKILL_DIR}/notes.md" in sent(state.collect(tmp_path))


def test_skipped_files_are_warnings(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    skill = tmp_path / SKILL_DIR
    (skill / "big.md").write_bytes(b"x" * (m.max_file_bytes + 1))
    (skill / "binary.bin").write_bytes(b"\xff\xfe\x00")
    (skill / "café.md").write_bytes(b"bad name\n")
    (tmp_path / "CLAUDE.md").write_bytes(b"markers removed by the agent\n")
    batch = state.collect(tmp_path)
    paths = sent(batch)
    assert not any(p.endswith(("big.md", "binary.bin", "café.md")) for p in paths)
    assert "CLAUDE.md" not in paths
    assert len(batch.warnings) == 4
    assert any("CLAUDE.md" in w and "missing" in w for w in batch.warnings)


def test_library_files_never_sync(tmp_path: Path) -> None:
    m = manifest()
    state = materialise(tmp_path, m)
    batch = state.collect(tmp_path)
    assert ".claude/skills/review-checklist/SKILL.md" not in sent(batch)


def test_unwritten_manifest_files_are_not_synced_as_deleted(tmp_path: Path) -> None:
    m = manifest()
    state = worker.SyncState(m)
    state.unsynced.add(f"{SKILL_DIR}/TASKS.md")
    assert f"{SKILL_DIR}/TASKS.md" not in sent(state.collect(tmp_path))


def test_linked_dirs_in_sync_dirs_are_skipped(tmp_path: Path) -> None:
    outside_dir = tmp_path / "secrets"
    outside_dir.mkdir()
    (outside_dir / "key.txt").write_bytes(b"secret\n")
    root = tmp_path / "ws"
    root.mkdir()
    state = materialise(root, manifest())
    symlink_or_skip(root / SKILL_DIR / "linkdir", outside_dir, directory=True)
    batch = state.collect(root)
    assert not any("linkdir" in p for p in sent(batch))
    assert all("secret" not in str(f["content"]) for f in batch.files)
    assert sum("linkdir is a symlink" in w for w in batch.warnings) == 1


def test_symlinked_files_in_sync_dirs_are_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret\n")
    root = tmp_path / "ws"
    root.mkdir()
    state = materialise(root, manifest())
    symlink_or_skip(root / SKILL_DIR / "link.md", outside)
    batch = state.collect(root)
    assert not any("link" in p for p in sent(batch))
    assert all("secret" not in str(f["content"]) for f in batch.files)
    assert sum("link.md is a symlink" in w for w in batch.warnings) == 1


def test_a_linked_sync_dir_is_not_followed(tmp_path: Path) -> None:
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    (outside_dir / "notes.md").write_bytes(b"secret\n")
    root = tmp_path / "ws"
    (root / ".claude" / "skills").mkdir(parents=True)
    symlink_or_skip(root / SKILL_DIR, outside_dir, directory=True)
    state = worker.SyncState(manifest())
    batch = state.collect(root)
    assert all("secret" not in str(f["content"]) for f in batch.files)
    assert any("is a symlink" in w for w in batch.warnings)


def test_symlinked_sync_file_is_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret\n")
    root = tmp_path / "ws"
    root.mkdir()
    m = manifest()
    state = materialise(root, m)
    tasks = root / SKILL_DIR / "TASKS.md"
    tasks.unlink()
    symlink_or_skip(tasks, outside)
    batch = state.collect(root)
    assert f"{SKILL_DIR}/TASKS.md" not in sent(batch)
    assert any("TASKS.md" in w and "symlink" in w for w in batch.warnings)
