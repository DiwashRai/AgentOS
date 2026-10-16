-- One job per run segment (one per run for now), built when the run is dispatched. `package` is
-- the job package as sent to the worker; `token_hash` the SHA-256 hex of the job token (never
-- stored in plaintext); `instance_hash` the worker instance bound to the job; `first_seq` the
-- seq of the job's first journal record; `alive_at` when a client last listed the job as alive.
CREATE TABLE jobs(id TEXT PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs ON DELETE CASCADE,
  segment INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE, instance_hash TEXT,
  package TEXT NOT NULL, first_seq INTEGER NOT NULL, budget_s INTEGER NOT NULL,
  not_after TEXT NOT NULL, created_at TEXT NOT NULL, alive_at TEXT, UNIQUE(run_id, segment));

-- `journal_seq`: the highest journal seq applied to the run; `head_commit`: the branch head the
-- worker last pushed.
ALTER TABLE runs ADD COLUMN journal_seq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN head_commit TEXT;

-- The package moved to `jobs`. `dispatch_token_hash` and `worker_token_hash` stay unused: SQLite
-- can't drop a UNIQUE column without rebuilding `runs` and every table that references it.
ALTER TABLE runs DROP COLUMN manifest;
