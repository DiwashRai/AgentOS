"""A client's `--workdir-root`: its `runs` folder, the OS locks on its files (the client's
CLIENT_LOCK, each run worker's `<job id>.lock`) and the cleanup of old workspaces.
"""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path
from typing import IO

from agentos_worker import tunables
from agentos_worker.console import say
from agentos_worker.files import is_link, remove_tree

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


CLIENT_RUNS_DIR = "runs"  # under --workdir-root: the run workspaces and logs, all the client owns
CLIENT_LOCK = "client.lock"  # in CLIENT_RUNS_DIR: held by the one client on that root


# <run_id> workspaces and <run_id>.log logs (a <job id>.lock goes once its `gone` is acknowledged)
_RUN_ENTRY_RE = re.compile(r"[0-9]+(?:\.log)?")


def take_lock(lock: IO[bytes], wait: bool) -> bool:
    """Take an exclusive OS lock on an open file (`wait`: until it is free). False if not free."""
    lock.seek(0)
    try:
        if sys.platform == "win32":
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except OSError:
        if wait:
            raise
        return False
    return True


def hold_lock(path: Path) -> IO[bytes]:
    """Open `path` (created if missing) and take an exclusive OS lock on it, waiting while
    another holder has it (a client looking: `lock_held`). The lock lasts while the file stays
    open, and the OS releases it when the process ends, however it ends."""
    lock = path.open("a+b")
    try:
        take_lock(lock, wait=True)
    except BaseException:
        lock.close()
        raise
    return lock


def release_lock(lock: IO[bytes]) -> None:
    """Release a lock `hold_lock` (or `take_lock`) took, and close its file."""
    lock.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()


def lock_held(path: Path) -> bool:
    """Whether a process holds `path`'s lock (`hold_lock`): try to take it without waiting. A
    missing file is not created: nobody holds it."""
    try:
        lock = path.open("r+b")
    except OSError:
        return False  # gone, or not openable: nobody holds it
    with lock:
        if not take_lock(lock, wait=False):
            return True
        release_lock(lock)
    return False


def clean_workdir_root(root: Path, now: float) -> list[Path]:
    """Delete the run workspaces (`<run_id>`) and logs (`<run_id>.log`) directly under the
    client's own `root/CLIENT_RUNS_DIR`, last modified more than WORKSPACE_MAX_AGE_S before `now`
    (a POSIX timestamp). Nothing outside that folder is looked at, and anything else in it (lock
    files included: `Children.acked` deletes those) is left alone. Returns what was deleted."""
    runs = root / CLIENT_RUNS_DIR
    try:
        children = sorted(runs.iterdir())
    except OSError as exc:
        say(f"cannot list {runs} for old workspaces: {exc}")
        return []

    cutoff = now - tunables.WORKSPACE_MAX_AGE_S
    removed: list[Path] = []
    for child in children:
        if not _RUN_ENTRY_RE.fullmatch(child.name):
            continue
        try:
            st = child.lstat()
            if st.st_mtime >= cutoff:
                continue
            if stat.S_ISDIR(st.st_mode) and not is_link(st):
                remove_tree(child)
            else:
                child.unlink()
        except OSError as exc:
            say(f"cannot delete the old workspace {child}: {exc}")
            continue
        removed.append(child)
    return removed
