"""worker.py as this server serves it: the zip `agentos_worker.archive` builds from
`backend/worker`, built once per process. The download route, the mailbox upload, the lease's
`worker_sha256` and the `started` check all carry these bytes, so a runner's checksum holds."""

from __future__ import annotations

import functools

from agentos_worker.archive import WorkerFile, this_worker


@functools.cache
def worker_file() -> WorkerFile:
    return this_worker()
