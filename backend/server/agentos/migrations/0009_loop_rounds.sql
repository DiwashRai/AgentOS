-- 0009: loop blocks (request 24). A stage in a loop block runs once per round: each execution is
-- its own stage_run, with its `round` (1 outside loops). A check's verdict in that round
-- ('pass' | 'fail'; NULL for other stages and until it is known).
ALTER TABLE stage_runs ADD COLUMN round INTEGER NOT NULL DEFAULT 1;
ALTER TABLE stage_runs ADD COLUMN verdict TEXT;
