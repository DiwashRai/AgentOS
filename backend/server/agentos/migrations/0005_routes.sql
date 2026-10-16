-- A run's route replaces `launcher`: `start` (push: a provider starts its worker; pull: its
-- owner's client leases it), `provider` (the provider's name; NULL for pull) and `report`
-- (live | file). The stop fields replace `cancel_requested_at`, on the run and on its job.
DROP INDEX runs_client_queue;
ALTER TABLE runs DROP COLUMN launcher;
ALTER TABLE runs DROP COLUMN cancel_requested_at;
ALTER TABLE runs ADD COLUMN start TEXT NOT NULL DEFAULT 'push';
ALTER TABLE runs ADD COLUMN provider TEXT;
ALTER TABLE runs ADD COLUMN report TEXT NOT NULL DEFAULT 'live';
ALTER TABLE runs ADD COLUMN stop_requested_at TEXT;
ALTER TABLE runs ADD COLUMN stop_reason TEXT;  -- cancel | budget | never_started
ALTER TABLE runs ADD COLUMN queue_reason TEXT;  -- why a queued run waits
ALTER TABLE jobs ADD COLUMN stop_requested_at TEXT;
ALTER TABLE jobs ADD COLUMN stop_reason TEXT;
ALTER TABLE jobs ADD COLUMN ended_at TEXT;  -- when its worker could no longer publish
ALTER TABLE jobs ADD COLUMN client_id INTEGER;  -- pull: the client that leased it

-- Each user's client queue: their queued pull runs, oldest first.
CREATE INDEX runs_client_queue ON runs(created_by, id) WHERE start = 'pull' AND status = 'queued';
