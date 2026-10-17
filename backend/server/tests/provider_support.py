"""SubprocessProvider: a test-only Provider that starts the worker.py this server serves (the
built zip, worker_py) as a local process, the way a runner would, for the end-to-end tests. The
server never starts workers itself; this lives in the tests only.

Each job's worker runs as `worker.py --server URL --workdir <root>/<run_id>`, detached
(`worker.spawn_detached`), with the env the client gives its run workers (`worker.child_env`:
ours minus AGENTOS_*, then `env`, then the job token as AGENTOS_TOKEN) and its output in
`<root>/<run_id>.log`. The ref is the job id.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import worker
from agentos.launchers.base import JobStart, JobStatus


class SubprocessProvider:
    def __init__(
        self, root: Path, worker_py: Path, env: Mapping[str, str | None] | None = None
    ) -> None:
        self.root = root
        self.worker_py = worker_py
        self.env = dict(env or {})
        self.children: dict[str, subprocess.Popen[bytes]] = {}

    def log(self, run_id: int) -> Path:
        return self.root / f"{run_id}.log"

    def start(self, job: JobStart) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        workdir = self.root / str(job.run_id)  # the worker creates it
        argv = [sys.executable, str(self.worker_py), "--server", job.server_url]
        argv += ["--workdir", str(workdir)]
        env = worker.child_env(job.job_token, self.env)
        self.children[job.job_id] = worker.spawn_detached(
            argv, self.root, env, self.log(job.run_id)
        )
        return job.job_id

    def status(self, ref: str) -> JobStatus:
        child = self.children.get(ref)
        if child is None:
            return JobStatus("unknown", detail="no such job")
        code = child.poll()
        return JobStatus("running") if code is None else JobStatus("ended", detail=f"exit {code}")

    def cancel(self, ref: str) -> None:
        """Kill the worker and everything it started."""
        child = self.children.get(ref)
        if child is not None and child.poll() is None:
            worker.kill_tree(child, worker._Job(child.pid))
            child.wait(10)

    def cleanup(self, ref: str) -> None:
        self.cancel(ref)
        self.children.pop(ref, None)
