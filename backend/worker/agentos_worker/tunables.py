"""The timings tests shrink (`fast`): every module reads them as `tunables.X`, so a test's
patch reaches every reader.
"""

from __future__ import annotations

HTTP_TIMEOUT_S = 30.0  # per request
UPLOAD_TIMEOUT_S = 300.0  # per attempt of the sync_back upload (up to sync_back.max_bytes)
RETRY_BUDGET_S = 300.0  # network errors and 5xx are retried this long; then "unreachable"
BACKOFF_START_S = 0.5
BACKOFF_CAP_S = 10.0
FLUSH_INTERVAL_S = 1.0  # pending events are posted at least this often...
FLUSH_THRESHOLD = 200  # ...or as soon as this many are buffered
HEARTBEAT_S = 10.0  # an (empty) journal post at least this often
POLL_S = 0.2  # how often a running stage checks cancel / timeout / server loss
READER_JOIN_S = 5.0  # after a stage exits, wait this long for its output readers, then abandon
GIT_TIMEOUT_S = 1800.0
KILL_TIMEOUT_S = 30.0


# Client mode (see run_client).
CLIENT_POLL_WAIT_S = 25  # the server holds a lease this long at most while no run is queued
CLIENT_POLL_TIMEOUT_S = 40.0  # one lease's HTTP timeout: above the server's hold
CLIENT_BACKOFF_CAP_S = 60.0  # failed leases back off up to this, and are retried forever
CLIENT_REAP_S = 1.0  # with every slot busy, look for finished workers this often
CLIENT_HEARTBEAT_S = 30.0  # with every slot busy, a no-take lease this often keeps it online
WORKSPACE_MAX_AGE_S = 7 * 24 * 3600.0  # older run workspaces and logs go at client startup
