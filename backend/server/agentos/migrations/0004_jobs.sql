-- One job per run segment, built at dispatch; tokens and instance ids are SHA-256 hex digests.
CREATE TABLE jobs(id TEXT PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs ON DELETE CASCADE,
  segment INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE, instance_hash TEXT,
  package TEXT NOT NULL, first_seq INTEGER NOT NULL, budget_s INTEGER NOT NULL,
  not_after TEXT NOT NULL, created_at TEXT NOT NULL, alive_at TEXT, UNIQUE(run_id, segment));

ALTER TABLE runs ADD COLUMN journal_seq INTEGER NOT NULL DEFAULT 0;  -- the highest applied seq
ALTER TABLE runs ADD COLUMN head_commit TEXT;  -- the head the worker last pushed

-- dispatch_token_hash and worker_token_hash stay unused: SQLite can't drop a UNIQUE column.
ALTER TABLE runs DROP COLUMN manifest;
