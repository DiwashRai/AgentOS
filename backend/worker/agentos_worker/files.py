"""The workspace's files, never through a link: paths resolved inside the workspace,
regular files read and context entries written, the regular files under a folder listed, and
a tree removed (`remove_tree`, read-only files included).
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from agentos_worker.blocks import BlockError, block_write
from agentos_worker.package import ContextFile
from agentos_worker.paths import path_error

_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks and junctions on Windows


def is_link(st: os.stat_result) -> bool:
    """A symlink, or on Windows any reparse point (junctions included)."""
    return stat.S_ISLNK(st.st_mode) or bool(
        int(getattr(st, "st_file_attributes", 0)) & _REPARSE_POINT
    )


def resolve_inside(root: Path, rel: str) -> tuple[Path, str | None]:
    """(absolute path, error) for `rel` under `root`. Refuses invalid paths and any existing
    component that is a symlink, so a write or read can never leave the workspace."""
    err = path_error(rel)
    if err:
        return root, err
    parts = rel.split("/")
    current = root
    for i, part in enumerate(parts):
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            return root, f"cannot stat {'/'.join(parts[: i + 1])}: {exc.strerror}"
        if is_link(st):
            return root, f"{'/'.join(parts[: i + 1])} is a symlink"
        if i < len(parts) - 1 and not stat.S_ISDIR(st.st_mode):
            return root, f"{'/'.join(parts[: i + 1])} is not a directory"
    return root.joinpath(*parts), None


def existing_entry(root: Path, rel: str) -> str | None:
    """The path of whatever already sits at `rel` under `root`, matched ignoring case at every
    level as on a case-insensitive filesystem, or None. Any entry counts, a dangling symlink
    included; only real folders are descended into."""
    here, found = root, []
    parts = rel.split("/")
    for i, part in enumerate(parts):
        try:
            with os.scandir(here) as entries:
                match = next((e for e in entries if e.name.lower() == part.lower()), None)
        except OSError:
            return None
        if match is None:
            return None
        found.append(match.name)
        if i == len(parts) - 1:
            return "/".join(found)
        if not match.is_dir(follow_symlinks=False):
            return None
        here = Path(match.path)
    return None


def read_regular(root: Path, rel: str, limit: int | None = None) -> tuple[bytes | None, str | None]:
    """(content, error) of a regular file of at most `limit` bytes. (None, None) when the file
    doesn't exist."""
    target, err = resolve_inside(root, rel)
    if err:
        return None, err
    try:
        st = target.lstat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"cannot stat: {exc.strerror}"
    if is_link(st):
        return None, "is a symlink"
    if not stat.S_ISREG(st.st_mode):
        return None, "is not a regular file"
    if limit is not None and st.st_size > limit:
        return None, f"is over {limit} bytes"
    try:
        return target.read_bytes(), None
    except OSError as exc:
        return None, f"cannot read: {exc.strerror}"


def walk_files(root: Path, rel_dir: str) -> tuple[list[str], list[str]]:
    """POSIX paths of the regular files under `rel_dir`, and of what was skipped there: links,
    special files, folders that can't be listed, and files or folders whose path breaks the path
    rules. Never follows links."""
    top, err = resolve_inside(root, rel_dir)
    if err:
        return [], [rel_dir]
    found: list[str] = []
    skipped: list[str] = []
    stack = [(rel_dir, top)]
    while stack:
        rel, here = stack.pop()
        try:
            children = sorted(here.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            skipped.append(f"{rel}/")
            continue
        for child in children:
            child_rel = f"{rel}/{child.name}"
            try:
                st = child.lstat()
            except OSError:
                continue
            if path_error(child_rel) or is_link(st):
                # A name that isn't valid UTF-8 is shown escaped, so the warning can be sent.
                skipped.append(child_rel.encode("utf-8", "backslashreplace").decode("utf-8"))
            elif stat.S_ISREG(st.st_mode):
                found.append(child_rel)
            elif stat.S_ISDIR(st.st_mode):
                stack.append((child_rel, child))
            else:
                skipped.append(child_rel)
    return sorted(found), sorted(skipped)


def write_entry(root: Path, f: ContextFile) -> str | None:
    """Materialise one context file or note (SPEC §9.3). Returns why it was skipped, or None."""
    target, err = resolve_inside(root, f.path)
    if err:
        return err
    try:
        existing: bytes | None = None
        try:
            st = target.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(st.st_mode):
                return "the target exists and is not a regular file"
            if f.mode == "block":
                existing = target.read_bytes()
        try:
            data = (
                block_write(existing, str(f.block), f.content)
                if f.mode == "block"
                else f.content.encode()
            )
        except BlockError as exc:
            return str(exc)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        return f"cannot write: {exc.strerror or exc}"
    return None


def remove_tree(path: Path) -> None:
    """shutil.rmtree that also removes read-only files (git packs on Windows)."""

    def retry(func: Callable[..., object], target: str, _exc: object) -> None:
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)
