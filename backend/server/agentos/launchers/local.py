"""LocalProcessLauncher (SPEC §12, M0): runs `worker.py` as a detached child process.

The dev server runs under `uvicorn --reload`, which sends Ctrl+C to its console on every backend
edit. Workers therefore start in their own process group without a console window (Windows) or in
their own session (POSIX), so a reload never kills an in-flight run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values

if TYPE_CHECKING:
    from agentos.config import Settings

_WINDOWS = sys.platform == "win32"
_ENV_PREFIX = "AGENTOS_"


class LocalProcessLauncher:
    name = "local"

    def __init__(
        self,
        *,
        worker_path: Path,
        workspaces_dir: Path,
        logs_dir: Path,
        worker_env_file: Path | None,
        max_concurrency: int | None,
        python: str = sys.executable,
    ) -> None:
        self.worker_path = worker_path
        self.workspaces_dir = workspaces_dir
        self.logs_dir = logs_dir
        self.worker_env_file = worker_env_file
        self.max_concurrency = max_concurrency
        self.python = python
        # Children are never waited on; finished ones are polled away on the next launch so POSIX
        # children don't linger as zombies. The dispatcher calls launch() from worker threads.
        self._children: list[subprocess.Popen[bytes]] = []
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, worker_path: Path) -> LocalProcessLauncher:
        return cls(
            worker_path=worker_path,
            workspaces_dir=settings.workspaces_dir,
            logs_dir=settings.logs_dir,
            worker_env_file=settings.worker_env_file,
            max_concurrency=settings.local_max_concurrency,
        )

    def launch(self, run_id: int, server_url: str, dispatch_token: str) -> str:
        """Spawn the worker for `run_id` and return its PID. Raises if the spawn fails."""
        with self._lock:
            self._children = [child for child in self._children if child.poll() is None]

        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        env = self.child_env(dispatch_token)
        # The worker creates the workdir itself (it must not exist or be empty, SPEC §9.2).
        argv = [
            self.python,
            str(self.worker_path),
            "--server",
            server_url,
            "--workdir",
            str(self.workspaces_dir / str(run_id)),
        ]
        kwargs: dict[str, Any] = {}
        if _WINDOWS:
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True

        with (self.logs_dir / f"worker-{run_id}.log").open("ab") as log:
            # The child gets its own copy of the log handle; ours closes when this block exits.
            child = subprocess.Popen(  # noqa: S603 - fixed argv built from config, no shell
                argv,
                cwd=self.workspaces_dir,  # never the server's cwd, which is inside the AgentOS repo
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
        with self._lock:
            self._children.append(child)
        return str(child.pid)

    def child_env(self, dispatch_token: str) -> dict[str, str]:
        """Server env minus every AGENTOS_* var, then WORKER_ENV_FILE, then the token (SPEC §12).

        A key listed in the env file without a value (`FOO`) removes FOO from the child env.
        """
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(_ENV_PREFIX)}
        if self.worker_env_file is not None:
            if not self.worker_env_file.is_file():
                raise FileNotFoundError(f"worker env file {self.worker_env_file} does not exist")
            for key, value in dotenv_values(self.worker_env_file, encoding="utf-8").items():
                _env_set(env, key, value)
        _env_set(env, "PYTHONUNBUFFERED", "1")  # live log
        _env_set(env, "PYTHONIOENCODING", "utf-8")
        _env_set(env, "AGENTOS_TOKEN", dispatch_token)  # never on the command line
        return env


def _env_set(env: dict[str, str], key: str, value: str | None) -> None:
    """Set (or with None, remove) `key`; Windows env names are case-insensitive."""
    if _WINDOWS:
        for existing in [k for k in env if k.upper() == key.upper()]:
            del env[existing]
    else:
        env.pop(key, None)
    if value is not None:
        env[key] = value
