-- Where a repo ticket's base branch came from, and its lock (request 22A).
-- `base_branch` keeps holding only the ticket's own branch (NULL = none of its own).
-- `base_branch_source`: 'default' (no own branch: the repo's default_branch applies, and may be
-- unset), 'manual' (set by hand, by `base_branch_set_by`) or 'mapping' (set or left blank by a
-- Jira field mapping; blank stays blank). The values live in models.BaseBranchSource (no CHECK).
-- `base_branch_source_value`: the raw source field text a mapping read; `base_branch_reason`: why a
-- mapping left it blank. `base_branch_locked_by_run`: the first run that got past workspace setup
-- (its first `stage_started`); while set, the base branch and repo can't change.
ALTER TABLE tickets ADD COLUMN base_branch_source TEXT NOT NULL DEFAULT 'default';
ALTER TABLE tickets ADD COLUMN base_branch_source_value TEXT;
ALTER TABLE tickets ADD COLUMN base_branch_reason TEXT;
ALTER TABLE tickets ADD COLUMN base_branch_set_by INTEGER REFERENCES users(id);
ALTER TABLE tickets ADD COLUMN base_branch_locked_by_run INTEGER REFERENCES runs(id);

UPDATE tickets SET base_branch_source = 'manual' WHERE base_branch IS NOT NULL;

-- Lock repo tickets whose work has started: the newest repo-mode run that got past workspace setup
-- (a stage started, or the worker sent a `stage_started`). A stage_run cancelled before it started
-- (a run that failed at checkout) doesn't count.
UPDATE tickets SET base_branch_locked_by_run = (
  SELECT MAX(r.id) FROM runs r
  WHERE r.ticket_id = tickets.id
    AND COALESCE(json_extract(r.manifest, '$.workspace.mode'), 'repo') = 'repo'
    AND (
      EXISTS (SELECT 1 FROM stage_runs sr WHERE sr.run_id = r.id
              AND (sr.started_at IS NOT NULL OR sr.status IN ('running', 'succeeded', 'failed')))
      OR EXISTS (SELECT 1 FROM run_events e WHERE e.run_id = r.id AND e.type = 'stage_started')
    )
)
WHERE repo IS NOT NULL;

-- A locked ticket that followed the repo default keeps the branch its work started from (the
-- locking run's manifest), so a later change to the repo's default_branch doesn't move it.
UPDATE tickets SET base_branch = (
  SELECT json_extract(r.manifest, '$.workspace.base_branch') FROM runs r
  WHERE r.id = tickets.base_branch_locked_by_run
)
WHERE base_branch IS NULL AND base_branch_locked_by_run IS NOT NULL;
