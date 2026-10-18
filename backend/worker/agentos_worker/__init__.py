"""The AgentOS worker (Python >= 3.11 standard library only, enforced by a test): runs one job of a
run and reports it as the run's journal, or in client mode starts a run worker per leased job.

The server serves this package and its entry script (backend/worker/worker.py) as one zip at
GET /api/worker/worker.py (`archive`); a checkout runs the entry script in place. The flow, the
failure paths and the three modes are in `cli`'s docstring, which is also `--help`. `main` picks
the mode; `run` executes a bound job; `client` leases jobs and starts the entry script, detached,
for each.
"""
